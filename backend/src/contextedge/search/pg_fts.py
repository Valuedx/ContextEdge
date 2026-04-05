"""PostgreSQL full-text search for evidence and playbooks."""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.models.playbook import Playbook, PlaybookVersion


async def search_evidence_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 50,
) -> list[tuple]:
    """Full-text search evidence items using PostgreSQL ts_rank."""
    tsquery = func.plainto_tsquery("english", query)
    tsvector = func.to_tsvector(
        "english",
        func.coalesce(EvidenceItem.title, "") + " " + func.coalesce(EvidenceItem.body_text, ""),
    )
    rank = func.ts_rank(tsvector, tsquery)

    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            EvidenceItem.tenant_id == tenant_id,
            tsvector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
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
    tsvector = func.to_tsvector(
        "english",
        func.coalesce(Playbook.title, "") + " " + func.coalesce(Playbook.description, ""),
    )
    rank = func.ts_rank(tsvector, tsquery)

    stmt = (
        select(Playbook, rank.label("rank"))
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            tsvector.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()
