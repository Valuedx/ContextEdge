"""Retention and data governance service.

Handles retention policies, legal holds, and data lifecycle management.

Retention happens in two phases:

1. **Archive** — ``apply_retention_policy`` marks evidence past its
   memory-class retention window as ``relevance_state = "archived"``.
   Items under legal hold (``sensitivity_label == "legal_hold"``) are
   excluded from this pass. Archived rows stay in the database and are
   still searchable via FTS and similarity (they still have embeddings).

2. **Purge** — ``purge_archived_evidence`` removes (or scrubs) rows that
   have been archived long enough to pass the configured grace window.
   Two modes:

   - ``hard_delete`` — ``DELETE`` the row. Cascades via existing FK
     constraints to ``attachment_artifacts``, ``correlation_edges``,
     ``contradiction_scan_state`` (all ``ON DELETE CASCADE``). Leaves
     ``graph_edges`` entries referencing the deleted evidence id
     orphaned — those are cleaned up by a separate graph-compact job.
     Use when the customer wants true deletion (GDPR right-to-erasure).
   - ``soft_purge`` — NULLs ``embedding``, ``body_text``, ``body_summary``
     and replaces ``title`` with ``"[purged]"``. Row stays for audit /
     reference linking but the content is unrecoverable and similarity
     search no longer matches. Use when the customer wants content
     removed but IDs / links preserved.

Legal hold is honoured in both archive and purge paths — items with
``sensitivity_label == "legal_hold"`` are always skipped.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.services.memory_service import (
    LONG_TERM_MEMORY,
    SHORT_TERM_MEMORY,
    classify_evidence_memory_class,
    memory_retention_windows,
)

import structlog

logger = structlog.get_logger()

# How long an archived item must sit in ``relevance_state = "archived"``
# before it's eligible for purge. Customer-configurable per tenant later;
# start at 30 days which matches typical GDPR recovery-window norms.
DEFAULT_ARCHIVE_GRACE_DAYS = 30

PurgeMode = Literal["hard_delete", "soft_purge"]


async def apply_retention_policy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    retention_days: int,
    source_class: str | None = None,
) -> int:
    """Archive or delete evidence items past their retention window.

    Items under legal hold are excluded.
    """
    now = datetime.now(timezone.utc)
    windows = memory_retention_windows(retention_days)

    q = select(EvidenceItem).where(
        EvidenceItem.tenant_id == tenant_id,
        or_(
            EvidenceItem.sensitivity_label.is_(None),
            EvidenceItem.sensitivity_label != "legal_hold",
        ),
    )
    if source_class:
        q = q.where(EvidenceItem.evidence_type == source_class)

    result = await db.execute(q)
    items = result.scalars().all()

    archived = 0
    archived_by_memory_class = {
        SHORT_TERM_MEMORY: 0,
        LONG_TERM_MEMORY: 0,
    }
    for item in items:
        memory_class = classify_evidence_memory_class(item)
        cutoff = now - timedelta(days=windows[memory_class])
        if item.ingested_at >= cutoff:
            continue
        item.relevance_state = "archived"
        archived += 1
        archived_by_memory_class[memory_class] = archived_by_memory_class.get(memory_class, 0) + 1

    await db.flush()
    logger.info(
        "retention.applied",
        tenant_id=str(tenant_id),
        archived=archived,
        retention_windows=windows,
        archived_by_memory_class=archived_by_memory_class,
    )
    return archived


async def apply_legal_hold(
    db: AsyncSession,
    evidence_ids: list[uuid.UUID],
) -> int:
    """Mark evidence items as held, preventing deletion."""
    count = 0
    for eid in evidence_ids:
        item = await db.get(EvidenceItem, eid)
        if item:
            item.sensitivity_label = "legal_hold"
            count += 1
    await db.flush()
    return count


async def purge_archived_evidence(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    archive_grace_days: int = DEFAULT_ARCHIVE_GRACE_DAYS,
    mode: PurgeMode = "hard_delete",
    dry_run: bool = False,
    limit: int = 1000,
) -> dict:
    """Purge or scrub evidence items archived long enough to be eligible.

    Parameters
    ----------
    archive_grace_days : int
        Items whose ``updated_at`` is older than ``now - archive_grace_days``
        and whose ``relevance_state == "archived"`` become purge candidates.
        Legal-hold items are always excluded regardless of age.
    mode : "hard_delete" | "soft_purge"
        See module docstring.
    dry_run : bool
        When True, returns the candidate count without mutating data.
        Used by the admin cost dashboard / pre-purge preview flows.
    limit : int
        Maximum rows touched per invocation — keeps a single cron tick
        from churning through millions of rows at once. The cron re-runs
        on its normal schedule; large backlogs drain over several ticks.

    Returns
    -------
    dict with keys: ``candidate_count``, ``processed_count``, ``mode``,
    ``dry_run``, ``limit_reached``.
    """
    if mode not in ("hard_delete", "soft_purge"):
        raise ValueError(f"mode must be 'hard_delete' or 'soft_purge', got {mode!r}")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=archive_grace_days)

    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.relevance_state == "archived",
            or_(
                EvidenceItem.sensitivity_label.is_(None),
                EvidenceItem.sensitivity_label != "legal_hold",
            ),
            # updated_at is a proxy for "when it last changed state". Exact
            # archived_at would require a new column — defer unless a
            # customer needs minute-accurate GDPR compliance; day-accurate
            # is fine with the 30-day default grace.
            EvidenceItem.updated_at < cutoff,
        )
        .limit(limit)
    )
    candidates = list((await db.execute(stmt)).scalars().all())

    if dry_run:
        return {
            "candidate_count": len(candidates),
            "processed_count": 0,
            "mode": mode,
            "dry_run": True,
            "limit_reached": len(candidates) == limit,
        }

    for item in candidates:
        if mode == "hard_delete":
            await db.delete(item)
        else:  # soft_purge
            item.embedding = None
            item.body_text = None
            item.body_summary = None
            item.title = "[purged]"

    await db.flush()
    logger.info(
        "retention.purged",
        tenant_id=str(tenant_id),
        mode=mode,
        processed_count=len(candidates),
        archive_grace_days=archive_grace_days,
    )
    return {
        "candidate_count": len(candidates),
        "processed_count": len(candidates),
        "mode": mode,
        "dry_run": False,
        "limit_reached": len(candidates) == limit,
    }
