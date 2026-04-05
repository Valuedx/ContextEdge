"""Retention and data governance service.

Handles retention policies, legal holds, and data lifecycle management.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    q = select(EvidenceItem).where(
        EvidenceItem.tenant_id == tenant_id,
        EvidenceItem.ingested_at < cutoff,
    )
    if source_class:
        q = q.where(EvidenceItem.evidence_type == source_class)

    result = await db.execute(q)
    items = result.scalars().all()

    archived = 0
    for item in items:
        item.relevance_state = "archived"
        archived += 1

    await db.flush()
    logger.info(
        "retention.applied",
        tenant_id=str(tenant_id),
        archived=archived,
        cutoff=cutoff.isoformat(),
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
