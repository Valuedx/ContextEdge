"""PostgreSQL full-text search for evidence and playbooks."""

import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.playbook import Playbook
from contextedge.search.vector_search import _visibility_predicates

# Same shape the graph seed resolver uses (repository.py): OR-composed
# websearch_to_tsquery so a 10–60 token operational query can still match.
# plainto_tsquery ANDs every lexeme and is unsatisfiable for that input.
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}")
_MAX_PLAYBOOK_FTS_TERMS = 24


def or_composed_websearch_tsquery(query: str, *, max_terms: int = _MAX_PLAYBOOK_FTS_TERMS):
    """OR-composed tsquery from distinct tokens, capped at *max_terms*."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _FTS_TOKEN_RE.findall(query or ""):
        lowered = raw.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(lowered)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    return func.websearch_to_tsquery("english", " OR ".join(terms))


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
    compose: str = "plain",
) -> list[tuple]:
    """Full-text search evidence items using PostgreSQL ts_rank.

    ``compose="plain"`` keeps ``plainto_tsquery`` for short UI queries.
    Ranking arms must pass ``compose="or"`` so a symptom blob remains
    satisfiable (GAP-2).
    """
    tokens = _query_tokens(query)
    if compose == "or":
        tsquery = or_composed_websearch_tsquery(query)
        if tsquery is None:
            return []
    else:
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

    fts_match = EvidenceItem.search_tsvector.op("@@")(tsquery)

    number_terms = [token for token in tokens if any(ch.isdigit() for ch in token)][:8]
    if compose == "or":
        if number_terms:
            raw_number_match = EvidenceItem.raw_object_ref.in_(
                select(RawEvidenceObject.id).where(
                    RawEvidenceObject.tenant_id == tenant_id,
                    or_(
                        *(
                            clause
                            for term in number_terms
                            for clause in (
                                RawEvidenceObject.raw_payload["ticket_number"].astext.ilike(
                                    f"%{term}%"
                                ),
                                RawEvidenceObject.raw_payload["ticketNumber"].astext.ilike(
                                    f"%{term}%"
                                ),
                                RawEvidenceObject.raw_payload["number"].astext.ilike(f"%{term}%"),
                                RawEvidenceObject.external_id.ilike(f"%{term}%"),
                            )
                        )
                    ),
                )
            )
        else:
            raw_number_match = None
        title_terms = tokens[:8]
        title_match = (
            or_(*(EvidenceItem.title.ilike(f"%{term}%") for term in title_terms))
            if title_terms
            else None
        )
    else:
        raw_number_match = EvidenceItem.raw_object_ref.in_(
            select(RawEvidenceObject.id).where(
                RawEvidenceObject.tenant_id == tenant_id,
                or_(
                    RawEvidenceObject.raw_payload["ticket_number"].astext.ilike(f"%{query}%"),
                    RawEvidenceObject.raw_payload["ticketNumber"].astext.ilike(f"%{query}%"),
                    RawEvidenceObject.raw_payload["number"].astext.ilike(f"%{query}%"),
                    RawEvidenceObject.external_id.ilike(f"%{query}%"),
                ),
            )
        )
        title_match = EvidenceItem.title.ilike(f"%{query}%")

    match_clauses = [fts_match]
    if raw_number_match is not None:
        match_clauses.append(raw_number_match)
    if title_match is not None:
        match_clauses.append(title_match)

    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            *base_filters,
            or_(*match_clauses),
            *_visibility_predicates(exclude_policy_ids),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()


def _query_tokens(query: str, *, max_terms: int = _MAX_PLAYBOOK_FTS_TERMS) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _FTS_TOKEN_RE.findall(query or ""):
        lowered = raw.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(lowered)
        if len(terms) >= max_terms:
            break
    return terms


async def search_playbooks_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> list[tuple]:
    """Full-text search playbooks by title and description."""
    tsquery = or_composed_websearch_tsquery(query)
    if tsquery is None:
        return []
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
