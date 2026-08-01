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
     ``contradiction_scan_state`` (all ``ON DELETE CASCADE``), plus
     ``playbook_evidence_links.evidence_id`` which is
     ``ON DELETE SET NULL`` (migration ``0027``) so the link row
     survives as an audit record with a NULL pointer.
     Leaves two classes of orphans that the daily
     ``evaluation.cleanup_hard_deleted_evidence`` Beat task reaps:
     (a) ``raw_evidence_objects`` rows + their MinIO blobs (no FK
     from evidence to raw), and (b) ``graph_edges`` entries whose
     ``source_node_id`` / ``target_node_id`` pointed at the deleted
     evidence (the edge columns are plain UUIDs, no FK). See
     ``workers/cleanup_tasks.py``. Use when the customer wants true
     deletion (GDPR right-to-erasure).
   - ``soft_purge`` — NULLs ``embedding``, ``body_text``, ``body_summary``,
     ``canonical_entity_refs`` (strips extracted identity + decision
     names — see review F-17) and ``raw_object_ref`` (so the S3 blob
     can be lifecycle-reaped and a re-ingest can't rehydrate the
     content); replaces ``title`` with ``"[purged]"``. Row stays for
     audit / reference linking but content is unrecoverable and
     similarity search no longer matches. Use when the customer wants
     content removed but IDs / links preserved.

Legal hold is honoured in both archive and purge paths — items with
``sensitivity_label == "legal_hold"`` are always skipped.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.services.evidence_filters import exclude_legal_hold
from contextedge.services.memory_service import (
    LONG_TERM_MEMORY,
    SHORT_TERM_MEMORY,
    classify_evidence_memory_class,
    memory_retention_windows,
)

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
    now = datetime.now(UTC)
    windows = memory_retention_windows(retention_days)

    q = select(EvidenceItem).where(
        EvidenceItem.tenant_id == tenant_id,
        exclude_legal_hold(),
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
    *,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> int:
    """Mark evidence items as held, preventing deletion.

    Tenant-scoped: an evidence id belonging to another tenant is silently
    skipped rather than held (or leaked)."""
    count = 0
    for eid in evidence_ids:
        item = await db.get(EvidenceItem, eid)
        if item is not None and item.tenant_id == tenant_id:
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

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=archive_grace_days)

    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.relevance_state == "archived",
            exclude_legal_hold(),
            # updated_at is a proxy for "when it last changed state". Exact
            # archived_at would require a new column — defer unless a
            # customer needs minute-accurate GDPR compliance; day-accurate
            # is fine with the 30-day default grace.
            EvidenceItem.updated_at < cutoff,
        )
        # Review F-16: deterministic oldest-first drain across ticks. Without
        # the ORDER BY, LIMIT picks any matching rows and the "drain backlog
        # over several ticks" pattern the docstring promises doesn't hold —
        # genuinely ancient rows can linger while arbitrary recent archives
        # get hit first.
        .order_by(EvidenceItem.updated_at.asc())
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

    soft_purged_ids: list[uuid.UUID] = []
    for item in candidates:
        if mode == "hard_delete":
            await db.delete(item)
        else:  # soft_purge
            item.embedding = None
            item.body_text = None
            item.body_summary = None
            item.title = "[purged]"
            # Identity / decision refs carry extracted person and service
            # names in clear text — NULL them so "content unrecoverable"
            # actually holds (review F-17). Drop the pointer into the raw
            # payload too, so a re-ingest can't rehydrate the content
            # from S3.
            item.canonical_entity_refs = None
            item.raw_object_ref = None
            soft_purged_ids.append(item.id)

    if soft_purged_ids:
        # Chunk rows carry the same content and embeddings as the parent
        # body — a "content unrecoverable" purge must remove them too.
        # (hard_delete cascades via the evidence_chunks FK; soft_purge
        # keeps the parent row, so the chunks need an explicit delete.)
        from sqlalchemy import delete as sa_delete

        from contextedge.models.evidence import EvidenceChunk

        await db.execute(
            sa_delete(EvidenceChunk)
            .where(
                EvidenceChunk.tenant_id == tenant_id,
                EvidenceChunk.evidence_id.in_(soft_purged_ids),
            )
            .execution_options(synchronize_session=False)
        )

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
