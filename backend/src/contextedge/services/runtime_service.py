"""Runtime retrieval service - orchestrates hybrid search and ranking."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.search.hybrid_ranker import RankedPlaybook, rank_playbooks


async def match_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    symptoms: list[str],
    entities: list[str],
    context: str | None = None,
    top_k: int = 5,
) -> list[RankedPlaybook]:
    """Match case context to approved playbooks using hybrid ranking."""
    query_text = " ".join(symptoms + entities)
    if context:
        query_text += " " + context

    return await rank_playbooks(
        db,
        tenant_id=tenant_id,
        query_text=query_text,
        entities=entities,
        top_k=top_k,
    )
