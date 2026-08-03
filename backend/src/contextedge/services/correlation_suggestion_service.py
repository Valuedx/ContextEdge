"""Gated semantic correlation suggestions (P3 of the correlation review).

Semantic similarity alone must never correlate evidence — every VPN
outage reads alike, and near-identical wording across unrelated
incidents is exactly how mega-clusters form. So this service only ever
writes **suggestions**, and only for candidate pairs that clear BOTH
gates:

1. similarity floor: best chunk-to-chunk cosine similarity >=
   ``SIMILARITY_FLOOR`` (using the stored chunk embeddings — no new
   embedding calls);
2. a non-semantic corroborator: a shared trusted identity or a shared
   active case membership. Temporal proximity alone is deliberately NOT
   a corroborator.

A reviewer accepts (creating an ordinary ``CorrelationEdge`` — only
then does the episode cluster resolver expand through the pair) or
rejects (remembered; the normalized pair is never re-suggested).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.correlation_suggestion import CorrelationSuggestion
from contextedge.models.episode import (
    CanonicalIdentity,
    CorrelationEdge,
    EvidenceIdentityLink,
)
from contextedge.models.evidence import EvidenceChunk, EvidenceItem
from contextedge.search.vector_ops import halfvec_cosine_distance, tune_ann_recall
from contextedge.search.vector_search import _visibility_predicates
from contextedge.services.correlation_service import HUB_DEGREE_MIN, create_correlation

logger = structlog.get_logger()

# Floor on 1 - cosine_distance. Below this the pair is not similar
# enough to bother a reviewer with, corroborated or not.
SIMILARITY_FLOOR = 0.7
# Learning from reviewer decisions (C1) — counting, not ML: a source
# pair whose suggestions reviewers keep rejecting gets a raised floor.
# Ticket↔ticket text is boilerplate-heavy; chat↔chat is not — the
# reviewers' accept rate is the ground truth for which is which.
LEARNING_MIN_DECIDED = 10
LEARNING_LOW_ACCEPT_RATE = 0.2
LEARNING_FLOOR_RAISE = 0.05
LEARNING_FLOOR_CAP = 0.85
# Per-tenant pending-queue cap (C4): a backfill storm must not bury
# reviewers. Generation pauses while the queue is at the cap and
# resumes as reviewers decide.
SUGGESTION_QUEUE_CAP = 500
# Confidence stamped on the edge a reviewer's accept creates — a human
# confirmed it, but the signal source is still semantic.
ACCEPTED_EDGE_CONFIDENCE = 0.6
# Query-side caps: chunks of the seed evidence used as query vectors,
# ANN rows fetched per chunk, and suggestions written per run.
MAX_QUERY_CHUNKS = 6
ANN_ROWS_PER_CHUNK = 20
MAX_SUGGESTIONS_PER_RUN = 5


def source_pair_key(type_a: str | None, type_b: str | None) -> str:
    return "|".join(sorted([type_a or "unknown", type_b or "unknown"]))


def similarity_floor_for(pair: str, pair_stats: dict) -> float:
    """Per-pair floor: the base floor, raised when reviewers keep
    rejecting this pair's suggestions. Floors only ever RAISE — learning
    can make the generator stricter, never looser."""
    stat = pair_stats.get(pair) or {}
    accepted = stat.get("accepted", 0)
    rejected = stat.get("rejected", 0)
    decided = accepted + rejected
    if decided >= LEARNING_MIN_DECIDED and (
        accepted / decided
    ) < LEARNING_LOW_ACCEPT_RATE:
        return min(SIMILARITY_FLOOR + LEARNING_FLOOR_RAISE, LEARNING_FLOOR_CAP)
    return SIMILARITY_FLOOR


async def suggestion_review_stats(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Reviewer-outcome aggregates: per source pair and per corroborator
    type. Pure counting over decided suggestions; feeds the per-pair
    floor and the reviewer-facing stats endpoint."""
    from sqlalchemy.orm import aliased

    from contextedge.models.source import Source

    ev_low = aliased(EvidenceItem)
    ev_high = aliased(EvidenceItem)
    src_low = aliased(Source)
    src_high = aliased(Source)
    rows = (
        await db.execute(
            select(
                CorrelationSuggestion.status,
                CorrelationSuggestion.corroborators,
                src_low.source_type,
                src_high.source_type,
            )
            .join(ev_low, ev_low.id == CorrelationSuggestion.evidence_id_low)
            .join(ev_high, ev_high.id == CorrelationSuggestion.evidence_id_high)
            .outerjoin(src_low, src_low.id == ev_low.source_id)
            .outerjoin(src_high, src_high.id == ev_high.source_id)
            .where(
                CorrelationSuggestion.tenant_id == tenant_id,
                CorrelationSuggestion.status.in_(("accepted", "rejected")),
            )
            .limit(5000)
        )
    ).all()
    pairs: dict[str, dict[str, int]] = {}
    corroborators: dict[str, dict[str, int]] = {}
    for status, corroborator_list, type_low, type_high in rows:
        pair = source_pair_key(type_low, type_high)
        pairs.setdefault(pair, {"accepted": 0, "rejected": 0})[status] += 1
        for reason in corroborator_list or []:
            kind = str(reason).split(":", 1)[0]
            corroborators.setdefault(kind, {"accepted": 0, "rejected": 0})[
                status
            ] += 1
    return {"pairs": pairs, "corroborators": corroborators}


def _pair_key(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Normalized (low, high) ordering so the symmetric duplicate cannot
    exist — mirrors the table's unique constraint."""
    return (a, b) if a.bytes < b.bytes else (b, a)


async def _semantic_candidates(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_id: uuid.UUID
) -> dict[uuid.UUID, float]:
    """Best similarity per other evidence, using the seed's stored chunk
    embeddings as query vectors."""
    query_chunks = (
        (
            await db.execute(
                select(EvidenceChunk.embedding)
                .where(
                    EvidenceChunk.tenant_id == tenant_id,
                    EvidenceChunk.evidence_id == evidence_id,
                    EvidenceChunk.embedding.is_not(None),
                )
                .order_by(EvidenceChunk.chunk_index.asc())
                .limit(MAX_QUERY_CHUNKS)
            )
        )
        .scalars()
        .all()
    )

    best: dict[uuid.UUID, float] = {}
    if query_chunks:
        # Post-filter recall: the 0032 HNSW indexes are global while this
        # query filters by tenant — raise ef_search for the transaction.
        await tune_ann_recall(db)
    for emb in query_chunks:
        distance = halfvec_cosine_distance(EvidenceChunk.embedding, emb).label(
            "distance"
        )
        rows = (
            await db.execute(
                select(EvidenceChunk.evidence_id, distance)
                .join(EvidenceItem, EvidenceChunk.evidence_id == EvidenceItem.id)
                .where(
                    EvidenceChunk.tenant_id == tenant_id,
                    EvidenceChunk.evidence_id != evidence_id,
                    EvidenceChunk.embedding.is_not(None),
                    EvidenceItem.tenant_id == tenant_id,
                    # Same content fence as the search surface: legal
                    # hold and pending redaction never surface as
                    # suggestions either.
                    *_visibility_predicates(None),
                )
                .order_by(distance)
                .limit(ANN_ROWS_PER_CHUNK)
            )
        ).all()
        for other_id, dist in rows:
            similarity = 1.0 - float(dist)
            if similarity > best.get(other_id, 0.0):
                best[other_id] = similarity
    return {
        other_id: sim for other_id, sim in best.items() if sim >= SIMILARITY_FLOOR
    }


async def _corroborators_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    """Non-semantic corroborators per candidate: shared trusted identity
    or shared active case membership. Batched — two queries total."""
    corroborators: dict[uuid.UUID, list[str]] = {c: [] for c in candidate_ids}

    # A corroborating identity must itself be a meaningful signal: the
    # identity tier's rules apply — trusted (resolved/verified, active),
    # non-person (one shared person is the classic Teams false positive:
    # the same operator works many incidents), and non-hub (P2: an
    # identity linked to hundreds of evidence items proves nothing).
    seed_identities = set(
        (
            await db.execute(
                select(EvidenceIdentityLink.identity_id)
                .join(
                    CanonicalIdentity,
                    CanonicalIdentity.id == EvidenceIdentityLink.identity_id,
                )
                .where(
                    EvidenceIdentityLink.tenant_id == tenant_id,
                    EvidenceIdentityLink.evidence_id == evidence_id,
                    CanonicalIdentity.tenant_id == tenant_id,
                    CanonicalIdentity.is_active.is_(True),
                    CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
                    CanonicalIdentity.entity_type != "person",
                )
            )
        )
        .scalars()
        .all()
    )
    if seed_identities:
        degree_rows = await db.execute(
            select(
                EvidenceIdentityLink.identity_id,
                func.count(func.distinct(EvidenceIdentityLink.evidence_id)),
            )
            .where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(seed_identities)),
            )
            .group_by(EvidenceIdentityLink.identity_id)
        )
        seed_identities = {
            identity_id
            for identity_id, degree in degree_rows.all()
            if int(degree) < HUB_DEGREE_MIN
        }
    if seed_identities:
        rows = await db.execute(
            select(
                EvidenceIdentityLink.evidence_id, EvidenceIdentityLink.identity_id
            ).where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.evidence_id.in_(tuple(candidate_ids)),
                EvidenceIdentityLink.identity_id.in_(tuple(seed_identities)),
            )
        )
        for cand_id, identity_id in rows.all():
            corroborators[cand_id].append(f"shared_identity:{identity_id}")

    seed_cases = set(
        (
            await db.execute(
                select(EvidenceCaseMembership.canonical_case_id).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id == evidence_id,
                    EvidenceCaseMembership.status == "active",
                    # Digest guard: a mentioned_only membership is
                    # deliberately non-load-bearing everywhere.
                    EvidenceCaseMembership.relationship_type != "mentioned_only",
                )
            )
        )
        .scalars()
        .all()
    )
    if seed_cases:
        rows = await db.execute(
            select(
                EvidenceCaseMembership.evidence_id,
                EvidenceCaseMembership.canonical_case_id,
            ).where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.evidence_id.in_(tuple(candidate_ids)),
                EvidenceCaseMembership.canonical_case_id.in_(tuple(seed_cases)),
                EvidenceCaseMembership.status == "active",
                EvidenceCaseMembership.relationship_type != "mentioned_only",
            )
        )
        for cand_id, case_id in rows.all():
            corroborators[cand_id].append(f"shared_case:{case_id}")
    return corroborators


async def suggest_semantic_correlations(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_id: uuid.UUID
) -> dict:
    """Generate pending suggestions for one evidence item. Idempotent:
    pairs already suggested (any status — a rejection is permanent) or
    already correlated are skipped."""
    counts = {"suggested": 0, "candidates": 0, "uncorroborated": 0}
    # C4: hard per-tenant queue cap — reviewers must never face an
    # unbounded backlog. Cheap count first; generation resumes as the
    # queue drains.
    pending_count = (
        await db.execute(
            select(func.count(CorrelationSuggestion.id)).where(
                CorrelationSuggestion.tenant_id == tenant_id,
                CorrelationSuggestion.status == "pending",
            )
        )
    ).scalar_one()
    if pending_count >= SUGGESTION_QUEUE_CAP:
        counts["queue_capped"] = True
        logger.info(
            "correlation_suggestions.queue_capped",
            tenant_id=str(tenant_id),
            pending=pending_count,
        )
        return counts
    # The seed must pass the same content fence as the targets: a
    # legal-hold or pending-redaction item must not surface via the
    # suggestion queue either.
    seed_visible = (
        await db.execute(
            select(EvidenceItem.id).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id == evidence_id,
                *_visibility_predicates(None),
            )
        )
    ).scalar_one_or_none()
    if seed_visible is None:
        return counts
    candidates = await _semantic_candidates(db, tenant_id, evidence_id)
    if not candidates:
        return counts
    counts["candidates"] = len(candidates)

    candidate_ids = list(candidates)
    # Pairs that already have an edge need no suggestion.
    edge_rows = await db.execute(
        select(
            CorrelationEdge.source_evidence_id, CorrelationEdge.target_evidence_id
        ).where(
            CorrelationEdge.tenant_id == tenant_id,
            or_(
                CorrelationEdge.source_evidence_id == evidence_id,
                CorrelationEdge.target_evidence_id == evidence_id,
            ),
        )
    )
    already_linked = set()
    for src, tgt in edge_rows.all():
        already_linked.add(src)
        already_linked.add(tgt)

    corroborators = await _corroborators_for(
        db, tenant_id, evidence_id, candidate_ids
    )

    # C1: per-pair learned floors. One batched source-type lookup for
    # the seed + all candidates; a pair reviewers keep rejecting must
    # clear a higher bar. Floors only raise, never lower.
    stats = await suggestion_review_stats(db, tenant_id)
    from contextedge.models.source import Source as _Source

    type_rows = (
        await db.execute(
            select(EvidenceItem.id, _Source.source_type)
            .outerjoin(_Source, _Source.id == EvidenceItem.source_id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id.in_(tuple([evidence_id, *candidate_ids])),
            )
        )
    ).all()
    source_types = {row[0]: row[1] for row in type_rows}
    seed_type = source_types.get(evidence_id)

    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    for other_id, similarity in ranked:
        if counts["suggested"] >= MAX_SUGGESTIONS_PER_RUN:
            break
        if other_id in already_linked:
            continue
        pair = source_pair_key(seed_type, source_types.get(other_id))
        if similarity < similarity_floor_for(pair, stats["pairs"]):
            counts["floor_filtered"] = counts.get("floor_filtered", 0) + 1
            continue
        reasons = corroborators.get(other_id, [])
        if not reasons:
            counts["uncorroborated"] += 1
            continue
        low, high = _pair_key(evidence_id, other_id)
        try:
            async with db.begin_nested():
                db.add(
                    CorrelationSuggestion(
                        tenant_id=tenant_id,
                        evidence_id_low=low,
                        evidence_id_high=high,
                        similarity=round(similarity, 4),
                        corroborators=reasons,
                        status="pending",
                    )
                )
                await db.flush()
            counts["suggested"] += 1
        except IntegrityError:
            continue  # already suggested (or rejected) — permanent skip
    if counts["suggested"]:
        logger.info(
            "correlation_suggestions.generated",
            tenant_id=str(tenant_id),
            evidence_id=str(evidence_id),
            **counts,
        )
    return counts


async def accept_suggestion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    suggestion: CorrelationSuggestion,
    reviewed_by: str,
) -> CorrelationEdge:
    """Reviewer accept: the suggestion becomes an ordinary correlation
    edge — only from this point does the cluster resolver expand
    through the pair."""
    edge = await create_correlation(
        db,
        tenant_id,
        suggestion.evidence_id_low,
        suggestion.evidence_id_high,
        "semantic_suggestion",
        ACCEPTED_EDGE_CONFIDENCE,
        explanation=(
            f"Accepted semantic suggestion (similarity {suggestion.similarity}); "
            f"corroborators: {', '.join(suggestion.corroborators)}"
        ),
        created_by=reviewed_by,
    )
    suggestion.status = "accepted"
    suggestion.reviewed_by = reviewed_by
    suggestion.reviewed_at = datetime.now(UTC)
    await db.flush()
    return edge


async def reject_suggestion(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    suggestion: CorrelationSuggestion,
    reviewed_by: str,
) -> None:
    """Reviewer reject: permanent — the unique pair row stays, so the
    generator can never re-suggest it."""
    suggestion.status = "rejected"
    suggestion.reviewed_by = reviewed_by
    suggestion.reviewed_at = datetime.now(UTC)
    await db.flush()
