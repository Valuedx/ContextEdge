"""Review-queue bundle endpoint.

Returns everything the reviewer console needs for a single session in one
round trip: session header, top-pending decision with confidence badge,
similar-decision aggregate, scoped decisions, execution runs, and recent
operational events.

Read-through cached on Redis (key `review_queue:{tenant_id}:{session_id}`).
The `prefetch_review_context` Celery task warms the cache on session
creation; this handler falls back to live compute and warms on miss.
"""

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.review_queue import ReviewQueueContext
from contextedge.services.review_queue_service import (
    build_review_context,
    read_cache,
    write_cache,
)

router = APIRouter()
logger = structlog.get_logger()


@router.get("/{session_id}/context", response_model=ReviewQueueContext)
async def get_review_context(
    session_id: UUID,
    request: Request,
    db: DbSession,
    user: AuthUser,
    decisions_limit: int = Query(20, ge=1, le=100),
    execution_runs_limit: int = Query(10, ge=1, le=50),
    events_limit: int = Query(20, ge=1, le=100),
    no_cache: bool = Query(
        False,
        description="Skip the Redis cache for this read (still warms on return)",
    ),
):
    redis = getattr(request.app.state, "redis", None)

    # Only honour the cache when the client uses the default limits — custom
    # limits produce different payload shapes that would poison the keyed slot.
    using_defaults = (
        decisions_limit == 20
        and execution_runs_limit == 10
        and events_limit == 20
    )

    if redis is not None and using_defaults and not no_cache:
        cached = await _safe_read_cache(
            redis, tenant_id=user.tenant_id, session_id=session_id,
        )
        if cached is not None:
            return cached

    bundle = await build_review_context(
        db,
        tenant_id=user.tenant_id,
        session_id=session_id,
        decisions_limit=decisions_limit,
        execution_runs_limit=execution_runs_limit,
        events_limit=events_limit,
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Resolution session not found")

    if redis is not None and using_defaults:
        await _safe_write_cache(
            redis,
            tenant_id=user.tenant_id,
            session_id=session_id,
            bundle=bundle,
        )

    return bundle


async def _safe_read_cache(redis, *, tenant_id, session_id):
    try:
        return await read_cache(redis, tenant_id=tenant_id, session_id=session_id)
    except Exception:
        logger.warning(
            "review_queue.cache_read_failed",
            tenant_id=str(tenant_id),
            session_id=str(session_id),
        )
        return None


async def _safe_write_cache(redis, *, tenant_id, session_id, bundle):
    try:
        await write_cache(
            redis, tenant_id=tenant_id, session_id=session_id, bundle=bundle,
        )
    except Exception:
        logger.warning(
            "review_queue.cache_write_failed",
            tenant_id=str(tenant_id),
            session_id=str(session_id),
        )
