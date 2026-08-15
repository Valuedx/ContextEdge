"""PostgreSQL full-text search for evidence and playbooks."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.playbook import Playbook


async def search_evidence_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 50,
    *,
    exclude_policy_ids: list[uuid.UUID] | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    source_type: str | None = None,
) -> list[tuple]:
    """Full-text search evidence items using PostgreSQL ts_rank.

    Also matches ticket numbers (e.g. '408801') via an ILIKE fallback
    against the raw_evidence_objects payload, so reviewers can search
    by the number on the ticket without knowing its title.
    """
    tsquery = func.plainto_tsquery("english", query)
    rank = func.ts_rank(EvidenceItem.search_tsvector, tsquery)

    base_filters = [
        EvidenceItem.tenant_id == tenant_id,
    ]
    if evidence_type:
        base_filters.append(EvidenceItem.evidence_type == evidence_type)
    else:
        base_filters.append(EvidenceItem.evidence_type != "thread_message")

    if relevance_state:
        base_filters.append(EvidenceItem.relevance_state == relevance_state)

    if source_type:
        base_filters.append(EvidenceItem.source_type == source_type)

    # Primary: full-text search on title + body_text
    fts_match = EvidenceItem.search_tsvector.op("@@")(tsquery)

    # Fallback: ticket number search via raw_evidence_objects payload
    raw_number_match = EvidenceItem.raw_object_ref.in_(
        select(RawEvidenceObject.id).where(
            or_(
                RawEvidenceObject.raw_payload["ticket_number"].astext.ilike(f"%{query}%"),
                RawEvidenceObject.raw_payload["ticketNumber"].astext.ilike(f"%{query}%"),
                RawEvidenceObject.raw_payload["number"].astext.ilike(f"%{query}%"),
                RawEvidenceObject.external_id.ilike(f"%{query}%"),
            )
        )
    )

    # Title ILIKE fallback for partial matches
    title_match = EvidenceItem.title.ilike(f"%{query}%")

    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            *base_filters,
            or_(fts_match, raw_number_match, title_match),
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
