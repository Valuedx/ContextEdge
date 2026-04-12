"""PostgreSQL full-text search for evidence and playbooks."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.models.playbook import Playbook


async def search_evidence_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 50,
    *,
    exclude_policy_ids: list[uuid.UUID] | None = None,
) -> list[tuple]:
    """Full-text search evidence items using PostgreSQL ts_rank."""
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(EvidenceItem.search_tsvector, tsquery)

    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.search_tsvector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    if exclude_policy_ids:
        stmt = stmt.where(
            or_(
                EvidenceItem.access_policy_id.is_(None),
                EvidenceItem.access_policy_id.notin_(exclude_policy_ids),
            )
        )
    result = await db.execute(stmt)
    return result.all()


async def search_playbooks_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> list[tuple]:
    """Full-text search playbooks by title and description."""
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(Playbook.search_tsvector, tsquery)

    stmt = (
        select(Playbook, rank.label("rank"))
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            Playbook.search_tsvector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()
