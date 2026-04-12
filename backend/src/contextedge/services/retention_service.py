"""Retention and data governance service.

Handles retention policies, legal holds, and data lifecycle management.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.services.memory_service import (
    LONG_TERM_MEMORY,
    SHORT_TERM_MEMORY,
    classify_evidence_memory_class,
    memory_retention_windows,
)

import structlog

logger = structlog.get_logger()


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
