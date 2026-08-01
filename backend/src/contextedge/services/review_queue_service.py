"""Compose the review-queue bundle for the reviewer console.

One call returns session + top-pending decision + similar-decision
aggregate + execution runs + recent operational events, pre-shaped so
the 7-zone UI can render without fanning out across endpoints.

Includes cache helpers that back the prefetch-on-create Redis path:
`build_cache_key` / `write_cache` / `read_cache` / `invalidate_cache`.
The endpoint reads through the cache with a live-compute fallback, and
the `prefetch_review_context` Celery task warms the cache on session
creation so the UI's first render is under the sub-2s budget.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.config import settings
from contextedge.models.decision import Decision
from contextedge.schemas.review_queue import ReviewQueueContext
from contextedge.services.decision_trace_service import (
    count_similar_decisions,
    get_decision_effectiveness,
    list_decisions,
)
from contextedge.services.event_log_service import list_operational_events
from contextedge.services.execution_service import list_execution_runs
from contextedge.services.session_service import get_resolution_session

logger = structlog.get_logger()

REVIEW_CONTEXT_CACHE_TTL_SEC = 300
REVIEW_CONTEXT_CACHE_PREFIX = "review_queue"


def build_cache_key(tenant_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Tenant-scoped cache key so cross-tenant bleed is impossible."""
    return f"{REVIEW_CONTEXT_CACHE_PREFIX}:{tenant_id}:{session_id}"


async def write_cache(
    redis,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    bundle: dict,
    ttl_sec: int = REVIEW_CONTEXT_CACHE_TTL_SEC,
) -> None:
    """Serialize the bundle through ReviewQueueContext and SETEX into Redis.

    Uses the Pydantic response schema so cached bytes match the wire format,
    which lets the endpoint skip re-validation on hit.
    """
    payload = ReviewQueueContext.model_validate(bundle).model_dump_json()
    await redis.setex(build_cache_key(tenant_id, session_id), ttl_sec, payload)


async def read_cache(
    redis,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> ReviewQueueContext | None:
    """Return cached context or None on miss. Corrupt cache entries return
    None so the caller falls back to live compute rather than 500ing."""
    raw = await redis.get(build_cache_key(tenant_id, session_id))
    if not raw:
        return None
    try:
        return ReviewQueueContext.model_validate_json(raw)
    except Exception:
        return None


async def invalidate_cache(
    redis,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    """Drop the cached bundle. Safe to call even if no entry exists."""
    await redis.delete(build_cache_key(tenant_id, session_id))


async def invalidate_review_context(
    tenant_id: uuid.UUID,
    session_id: uuid.UUID | None,
) -> None:
    """Service-layer helper — opens a short-lived Redis client, drops the key,
    and swallows transport errors.

    Call this from mutation services after `db.flush()` when state changes
    that affect the bundle. Passing `session_id=None` is a no-op so callers
    can invoke it unconditionally without guarding on the session link.

    Caveat: invalidation fires before the outer transaction commits, so a
    concurrent read in the narrow post-flush / pre-commit window could
    re-populate the cache with slightly stale data. The 300s TTL backstops
    that race; a follow-up can hook this into a SQLAlchemy `after_commit`
    event if real-time correctness ever matters more than the current
    simplicity.
    """
    if session_id is None:
        return
    import redis.asyncio as aioredis

    redis = None
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await invalidate_cache(redis, tenant_id=tenant_id, session_id=session_id)
    except Exception:
        logger.warning(
            "review_queue.invalidate_failed",
            tenant_id=str(tenant_id),
            session_id=str(session_id),
        )
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass

_SIMILAR_CONTEXT_KEYS = ("workflow", "environment", "impacted_dependency")
_SUCCESSFUL_OUTCOMES = {"success"}
_COUNTED_OUTCOMES = {"success", "failure", "partial", "timeout", "rejected"}


def derive_badge_level(score: float | None) -> str | None:
    """green > 0.8, amber 0.5–0.8, red < 0.5; None → None."""
    if score is None:
        return None
    if score >= 0.8:
        return "green"
    if score >= 0.5:
        return "amber"
    return "red"


def _pick_top_decision(decisions: list[Decision]) -> Decision | None:
    """Prefer latest pending with a confidence score; else latest with
    confidence; else latest overall."""
    pending_with_conf = [
        d for d in decisions if d.status == "pending" and d.confidence is not None
    ]
    if pending_with_conf:
        return max(
            pending_with_conf,
            key=lambda d: (d.confidence or 0.0, d.created_at),
        )
    with_conf = [d for d in decisions if d.confidence is not None]
    if with_conf:
        return max(with_conf, key=lambda d: d.created_at)
    return decisions[0] if decisions else None


def _context_filters(decision: Decision) -> dict:
    snap = decision.context_snapshot or {}
    return {k: snap[k] for k in _SIMILAR_CONTEXT_KEYS if k in snap}


def _success_rate(outcomes: dict[str, int]) -> float | None:
    counted = {k: v for k, v in outcomes.items() if k in _COUNTED_OUTCOMES}
    total = sum(counted.values())
    if total == 0:
        return None
    succeeded = sum(v for k, v in counted.items() if k in _SUCCESSFUL_OUTCOMES)
    return succeeded / total


async def build_review_context(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    decisions_limit: int = 20,
    execution_runs_limit: int = 10,
    events_limit: int = 20,
) -> dict | None:
    """Return a dict shaped for `ReviewQueueContext` or None if session missing."""
    session = await get_resolution_session(
        db, tenant_id=tenant_id, session_id=session_id,
    )
    if session is None:
        return None

    decisions = await list_decisions(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        limit=decisions_limit,
        sort="created_desc",
    )
    runs = await list_execution_runs(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        limit=execution_runs_limit,
        include_details=True,
    )
    events = await list_operational_events(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        limit=events_limit,
    )

    top_decision = _pick_top_decision(decisions)
    top_badge = None
    similar = None
    if top_decision is not None:
        top_badge = {
            "score": top_decision.confidence,
            "level": derive_badge_level(top_decision.confidence),
        }
        ctx_filters = _context_filters(top_decision)
        total_count = await count_similar_decisions(
            db,
            tenant_id=tenant_id,
            decision_type=top_decision.decision_type,
            context_snapshot=ctx_filters or None,
        )
        effectiveness = await get_decision_effectiveness(
            db,
            tenant_id=tenant_id,
            decision_type=top_decision.decision_type,
            context_filters=ctx_filters or None,
        )
        outcomes = effectiveness.get("outcomes", {}) or {}
        similar = {
            "decision_type": top_decision.decision_type,
            "context_filters": ctx_filters,
            "total_count": total_count,
            "outcomes": outcomes,
            "success_rate": _success_rate(outcomes),
        }

    return {
        "session": session,
        "top_decision": top_decision,
        "top_decision_badge": top_badge,
        "similar": similar,
        "decisions": decisions,
        "execution_runs": runs,
        "recent_events": events,
    }
