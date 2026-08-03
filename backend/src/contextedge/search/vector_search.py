"""pgvector-based semantic search for evidence, chunk-aware with rollup.

Search hits ``evidence_chunks`` first (CHUNKING_DESIGN.md §6): oversample
chunk-level ANN, MMR for diversity, roll up to one hit per parent
evidence scored by its closest chunk — then merge a parent-embedding
pass so evidence without chunks (pre-chunking rows, chunker failures)
still surfaces. Both passes share one query embedding and one distance
space (cosine over the same model), so their scores merge directly.

Results are 3-tuples ``(EvidenceItem, distance, best_chunk | None)``:
consumers that index ``row[1]`` (the hybrid ranker) keep working;
``best_chunk`` carries the specific chunk id, ``parent_section``
breadcrumb, kind, and a snippet so context surfaces can render the
matching passage instead of the whole parent body.
"""

import uuid

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.models.evidence import EvidenceChunk, EvidenceItem
from contextedge.models.playbook import PlaybookEvidenceLink, PlaybookVersion
from contextedge.search.chunk_rollup import (
    ChunkCandidate,
    mmr_order,
    rollup_best_chunk_per_evidence,
)
from contextedge.search.vector_ops import halfvec_cosine_distance, tune_ann_recall

logger = structlog.get_logger()

# Oversample bounds for the chunk pass: the ANN returns this many chunks,
# MMR + rollup compress them down to `limit` parents. §6 recommends
# 50–100 for typical limits; the floor keeps small-limit searches diverse,
# the ceiling keeps the MMR similarity matrix and the embedding transfer
# (oversample × 3072 floats) bounded for large-limit callers.
CHUNK_OVERSAMPLE_MIN = 80
CHUNK_OVERSAMPLE_MAX = 240
SNIPPET_CHARS = 240


def _oversample_for(limit: int) -> int:
    return min(max(CHUNK_OVERSAMPLE_MIN, limit * 3), CHUNK_OVERSAMPLE_MAX)


def _visibility_predicates(exclude_policy_ids: list[uuid.UUID] | None) -> list:
    """Search-surface safety: chunk text is content, so results must
    honor the same exclusions the agent surface applies — legal hold,
    pending redaction, and role-excluded access policies."""
    predicates = [
        or_(
            EvidenceItem.sensitivity_label.is_(None),
            EvidenceItem.sensitivity_label != "legal_hold",
        ),
        or_(
            EvidenceItem.redaction_status.is_(None),
            EvidenceItem.redaction_status.notin_(("pending", "pending_redaction")),
        ),
    ]
    if exclude_policy_ids:
        predicates.append(
            or_(
                EvidenceItem.access_policy_id.is_(None),
                EvidenceItem.access_policy_id.notin_(exclude_policy_ids),
            )
        )
    return predicates


async def _chunk_candidates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    emb: list[float],
    *,
    exclude_policy_ids: list[uuid.UUID] | None,
    limit: int,
    playbook_id: uuid.UUID | None = None,
    playbook_version_id: uuid.UUID | None = None,
) -> list[ChunkCandidate]:
    distance = halfvec_cosine_distance(EvidenceChunk.embedding, emb).label("distance")
    stmt = (
        select(
            EvidenceChunk.id,
            EvidenceChunk.evidence_id,
            distance,
            EvidenceChunk.embedding,
            EvidenceChunk.parent_section,
            EvidenceChunk.chunk_kind,
            func.substr(EvidenceChunk.text, 1, SNIPPET_CHARS),
        )
        .join(EvidenceItem, EvidenceChunk.evidence_id == EvidenceItem.id)
        .where(
            EvidenceChunk.tenant_id == tenant_id,
            EvidenceChunk.embedding.is_not(None),
            EvidenceItem.tenant_id == tenant_id,
            *_visibility_predicates(exclude_policy_ids),
        )
        .order_by(distance)
        .limit(_oversample_for(limit))
    )
    if playbook_id is not None:
        stmt = stmt.join(
            PlaybookEvidenceLink,
            (PlaybookEvidenceLink.evidence_id == EvidenceChunk.evidence_id)
            & (PlaybookEvidenceLink.evidence_id.is_not(None)),
        ).join(
            PlaybookVersion,
            PlaybookVersion.id == PlaybookEvidenceLink.playbook_version_id,
        ).where(
            PlaybookVersion.playbook_id == playbook_id,
            PlaybookVersion.id == playbook_version_id,
            PlaybookVersion.published_at.is_not(None),
        )

    rows = (await db.execute(stmt)).all()

    if playbook_id is not None and not rows:
        # The playbook scope is an INNER join through
        # playbook_evidence_links. An empty result here is ambiguous: it
        # means either "no chunk matched" or "this version has no
        # provenance rows at all" — and for a long time it was always the
        # second, because nothing in the codebase wrote that table, so
        # every playbook-scoped search returned nothing and looked like a
        # legitimate miss. Say which it is rather than returning [] and
        # letting the caller guess.
        link_count = (
            await db.execute(
                select(func.count())
                .select_from(PlaybookEvidenceLink)
                .where(PlaybookEvidenceLink.playbook_version_id == playbook_version_id)
            )
        ).scalar_one()
        if not link_count:
            logger.warning(
                "search.playbook_scope_has_no_evidence_links",
                playbook_id=str(playbook_id),
                playbook_version_id=str(playbook_version_id),
                hint=(
                    "version has no playbook_evidence_links rows; "
                    "playbook-scoped semantic search cannot match anything"
                ),
            )

    return [
        ChunkCandidate(
            chunk_id=row[0],
            evidence_id=row[1],
            distance=float(row[2]),
            embedding=row[3],
            parent_section=row[4],
            chunk_kind=row[5],
            snippet=row[6],
        )
        for row in rows
    ]


async def _merge_with_parent_pass(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rolled: list[ChunkCandidate],
    parent_stmt,
    limit: int,
) -> list[tuple]:
    """Fetch parents for rolled chunk hits, merge the parent-embedding
    pass for unchunked evidence, order by shared distance space.

    Multiple chunker versions can coexist per evidence (re-chunks write
    alongside legacy rows until GC); their near-duplicate chunks are
    naturally handled — MMR demotes, the rollup keeps one per parent.
    """
    results: list[tuple] = []
    seen: set[uuid.UUID] = set()

    if rolled:
        parent_rows = (
            await db.execute(
                select(EvidenceItem).where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.id.in_([c.evidence_id for c in rolled]),
                )
            )
        ).scalars().all()
        parents = {item.id: item for item in parent_rows}
        for candidate in rolled:
            item = parents.get(candidate.evidence_id)
            if item is None:
                continue
            results.append((item, candidate.distance, candidate.context()))
            seen.add(item.id)

    for item, dist in (await db.execute(parent_stmt)).all():
        if item.id in seen:
            continue
        results.append((item, float(dist), None))

    results.sort(key=lambda row: row[1])
    return results[:limit]


async def search_evidence_semantic(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query_text: str,
    limit: int = 20,
    *,
    query_embedding: list[float] | None = None,
    exclude_policy_ids: list[uuid.UUID] | None = None,
) -> list[tuple]:
    """Chunk-aware semantic search with parent rollup.

    Returns ``(EvidenceItem, distance, best_chunk | None)`` tuples,
    closest first.
    """
    emb = query_embedding if query_embedding is not None else await generate_embedding(query_text)

    await tune_ann_recall(db)
    candidates = await _chunk_candidates(
        db, tenant_id, emb, exclude_policy_ids=exclude_policy_ids, limit=limit
    )
    # MMR decides WHICH candidates survive (set selection); the rollup's
    # re-sort by distance decides their final rank. Diversity shapes the
    # set, relevance orders it — deliberate, not a lost ordering.
    diversified = mmr_order(candidates, select_n=max(limit * 4, limit))
    rolled = rollup_best_chunk_per_evidence(diversified)[:limit]

    parent_stmt = (
        select(
            EvidenceItem,
            halfvec_cosine_distance(EvidenceItem.embedding, emb).label("distance"),
        )
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.embedding.is_not(None),
            *_visibility_predicates(exclude_policy_ids),
        )
        .order_by("distance")
        .limit(limit)
    )
    return await _merge_with_parent_pass(db, tenant_id, rolled, parent_stmt, limit)


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
    """Semantic search over one published playbook version's linked
    evidence — chunk-aware with the same rollup. Row shape stays
    ``row[0]``=item / ``row[1]``=distance for the hybrid ranker."""
    emb = query_embedding if query_embedding is not None else await generate_embedding(query_text)

    await tune_ann_recall(db)
    candidates = await _chunk_candidates(
        db,
        tenant_id,
        emb,
        exclude_policy_ids=exclude_policy_ids,
        limit=limit,
        playbook_id=playbook_id,
        playbook_version_id=playbook_version_id,
    )
    diversified = mmr_order(candidates, select_n=max(limit * 4, limit))
    rolled = rollup_best_chunk_per_evidence(diversified)[:limit]

    parent_stmt = (
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
            *_visibility_predicates(exclude_policy_ids),
        )
        .order_by("distance")
        .limit(limit)
    )
    return await _merge_with_parent_pass(db, tenant_id, rolled, parent_stmt, limit)
