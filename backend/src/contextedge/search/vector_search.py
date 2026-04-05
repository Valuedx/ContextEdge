"""pgvector-based semantic search for evidence and playbooks."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.models.evidence import EvidenceItem


async def search_evidence_semantic(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query_text: str,
    limit: int = 20,
) -> list[tuple]:
    """Semantic search evidence items using pgvector cosine similarity."""
    query_embedding = await generate_embedding(query_text)

    stmt = (
        select(
            EvidenceItem,
            EvidenceItem.embedding.cosine_distance(query_embedding).label("distance"),
        )
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.embedding.is_not(None),
        )
        .order_by("distance")
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()
