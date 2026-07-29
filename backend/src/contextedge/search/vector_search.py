"""pgvector-based semantic search for evidence and playbooks."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.models.evidence import EvidenceItem
from contextedge.models.playbook import PlaybookEvidenceLink, PlaybookVersion
from contextedge.search.vector_ops import halfvec_cosine_distance


async def search_evidence_semantic(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query_text: str,
    limit: int = 20,
    *,
    query_embedding: list[float] | None = None,
    exclude_policy_ids: list[uuid.UUID] | None = None,
) -> list[tuple]:
    """Semantic search evidence items using pgvector cosine similarity."""
    emb = query_embedding if query_embedding is not None else await generate_embedding(query_text)

    stmt = (
        select(
            EvidenceItem,
            halfvec_cosine_distance(EvidenceItem.embedding, emb).label("distance"),
        )
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.embedding.is_not(None),
        )
        .order_by("distance")
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


async def search_evidence_semantic_for_playbook(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    playbook_version_id: uuid.UUID,
    query_text: str,
    limit: int = 10,
    *,
    query_embedding: list[float] | None = None,
    exclude_policy_ids: list[uuid.UUID] | None = None,
) -> list[tuple]:
    """Semantic search for evidence linked to one **published** playbook version."""
    emb = query_embedding if query_embedding is not None else await generate_embedding(query_text)

    stmt = (
        select(
            EvidenceItem,
            halfvec_cosine_distance(EvidenceItem.embedding, emb).label("distance"),
        )
        .join(
            PlaybookEvidenceLink,
            (PlaybookEvidenceLink.evidence_id == EvidenceItem.id)
            & (PlaybookEvidenceLink.evidence_id.is_not(None)),
        )
        .join(PlaybookVersion, PlaybookVersion.id == PlaybookEvidenceLink.playbook_version_id)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.embedding.is_not(None),
            PlaybookVersion.playbook_id == playbook_id,
            PlaybookVersion.id == playbook_version_id,
            PlaybookVersion.published_at.is_not(None),
        )
        .order_by("distance")
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
