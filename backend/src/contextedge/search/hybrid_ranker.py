"""Hybrid ranker combining FTS, vector, graph, and quality signals."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.models.episode import CorrelationEdge
from contextedge.models.pattern import GraphEdge, NegativeKnowledgeItem
from contextedge.models.playbook import Playbook, PlaybookEvidenceLink, PlaybookVersion
from contextedge.search.access_control import resolve_excluded_access_policy_ids
from contextedge.search.pg_fts import search_playbooks_fts
from contextedge.search.risk_policy import risk_within_cap
from contextedge.search.vector_search import search_evidence_semantic_for_playbook
from contextedge.services.identity_service import resolve_identity_ids_for_terms


@dataclass
class RankingWeights:
    keyword: float = 0.25
    semantic: float = 0.30
    graph_distance: float = 0.15
    evidence_quality: float = 0.10
    identity: float = 0.05
    recency: float = 0.10
    freshness: float = 0.05
    negative_penalty: float = 0.05


@dataclass
class RankedPlaybook:
    playbook: Playbook
    score: float
    confidence: float
    playbook_confidence: float
    freshness_status: str
    evidence_count: int
    breakdown: dict = field(default_factory=dict)


def _semantic_corpus_score(rows: list) -> tuple[float, int]:
    """Map best semantic distance to [0,1]; cosine distance typically in [0, 2]."""
    if not rows:
        return 0.0, 0
    distances = [float(r[1]) for r in rows if r[1] is not None]
    if not distances:
        return 0.0, len(rows)
    best = min(distances)
    score = max(0.0, 1.0 - (best / 2.0))
    return score, len(rows)


async def _graph_score_for_playbook(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    semantic_evidence_ids: set[uuid.UUID] | None = None,
    domain_id: uuid.UUID | None = None,
) -> float:
    """Score based on direct graph connectivity plus correlation co-occurrence."""
    graph_q = select(func.count()).where(
        GraphEdge.tenant_id == tenant_id,
        or_(
            (GraphEdge.source_node_type == "playbook") & (GraphEdge.source_node_id == playbook_id),
            (GraphEdge.target_node_type == "playbook") & (GraphEdge.target_node_id == playbook_id),
        ),
    )
    if domain_id is not None:
        graph_q = graph_q.where(
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        )
    n = (await db.execute(graph_q)).scalar() or 0
    graph_count_score = min(1.0, float(n) / 5.0)

    correlation_boost = 0.0
    if semantic_evidence_ids:
        pb_evidence_q = select(PlaybookEvidenceLink.evidence_id).where(
            PlaybookEvidenceLink.playbook_version_id.in_(
                select(PlaybookVersion.id).where(
                    PlaybookVersion.playbook_id == playbook_id,
                    PlaybookVersion.published_at.is_not(None),
                )
            ),
            PlaybookEvidenceLink.evidence_id.is_not(None),
        )
        pb_evidence_result = await db.execute(pb_evidence_q)
        pb_evidence_ids = set(pb_evidence_result.scalars().all())

        if pb_evidence_ids:
            sem_ids = tuple(semantic_evidence_ids)
            pb_ids = tuple(pb_evidence_ids)
            corr_q = select(func.count()).where(
                CorrelationEdge.tenant_id == tenant_id,
                or_(
                    and_(
                        CorrelationEdge.source_evidence_id.in_(pb_ids),
                        CorrelationEdge.target_evidence_id.in_(sem_ids),
                    ),
                    and_(
                        CorrelationEdge.source_evidence_id.in_(sem_ids),
                        CorrelationEdge.target_evidence_id.in_(pb_ids),
                    ),
                ),
            )
            corr_count = (await db.execute(corr_q)).scalar() or 0
            correlation_boost = min(1.0, float(corr_count) / 3.0)

    return graph_count_score * 0.7 + correlation_boost * 0.3


async def _identity_score_for_playbook(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    identity_ids: set[uuid.UUID],
    domain_id: uuid.UUID | None = None,
) -> float:
    if not identity_ids:
        return 0.0
    q = select(func.count(func.distinct(GraphEdge.target_node_id))).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "playbook",
        GraphEdge.source_node_id == playbook_id,
        GraphEdge.target_node_type == "identity",
        GraphEdge.edge_type == "references_identity",
        GraphEdge.target_node_id.in_(tuple(identity_ids)),
    )
    if domain_id is not None:
        q = q.where(
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        )
    hits = (await db.execute(q)).scalar() or 0
    return min(1.0, float(hits) / max(1, len(identity_ids)))


async def _negative_penalty_for_playbook(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    domain_id: uuid.UUID | None,
) -> float:
    """Compute a penalty score based on contradictions and negative knowledge."""
    contradiction_q = select(func.count()).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "playbook",
        GraphEdge.source_node_id == playbook_id,
        GraphEdge.edge_type == "contradicts",
    )
    contradiction_count = (await db.execute(contradiction_q)).scalar() or 0

    nk_count = 0
    if domain_id is not None:
        nk_q = select(func.count()).where(
            NegativeKnowledgeItem.tenant_id == tenant_id,
            NegativeKnowledgeItem.domain_id == domain_id,
        )
        nk_count = (await db.execute(nk_q)).scalar() or 0

    return min(1.0, contradiction_count * 0.3 + nk_count * 0.1)


async def _latest_published_version_id(
    db: AsyncSession,
    playbook_id: uuid.UUID,
) -> uuid.UUID | None:
    r = await db.execute(
        select(PlaybookVersion.id)
        .where(
            PlaybookVersion.playbook_id == playbook_id,
            PlaybookVersion.published_at.is_not(None),
        )
        .order_by(PlaybookVersion.published_at.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def rank_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query_text: str,
    symptoms: list[str] | None = None,
    entities: list[str] | None = None,
    top_k: int = 5,
    weights: RankingWeights | None = None,
    *,
    domain_id: uuid.UUID | None = None,
    max_risk_tier: str | None = None,
    allowed_domain_ids: list[uuid.UUID] | None = None,
    caller_roles: list[str] | None = None,
) -> list[RankedPlaybook]:
    """Rank approved playbooks using hybrid signals.

    When ``domain_id`` is set, keep playbooks in that domain or tenant-wide (``domain_id`` NULL).
    When ``max_risk_tier`` is set, drop playbooks above that tier (e.g. cap at ``medium``).
    When ``allowed_domain_ids`` is set (service tokens), keep only tenant-wide playbooks or those
    in the allowed set.
    """
    weights = weights or RankingWeights()
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, tenant_id, caller_roles)

    approved_result = await db.execute(
        select(Playbook).where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
        )
    )
    approved_playbooks = list(approved_result.scalars().all())
    if domain_id is not None:
        approved_playbooks = [
            pb
            for pb in approved_playbooks
            if pb.domain_id is None or pb.domain_id == domain_id
        ]
    if allowed_domain_ids is not None:
        allowed = set(allowed_domain_ids)
        approved_playbooks = [
            pb for pb in approved_playbooks if pb.domain_id is None or pb.domain_id in allowed
        ]
    if max_risk_tier is not None:
        approved_playbooks = [
            pb for pb in approved_playbooks if risk_within_cap(pb.risk_tier, max_risk_tier)
        ]
    if not approved_playbooks:
        return []

    query_identity_ids = await resolve_identity_ids_for_terms(db, tenant_id, entities or [])

    fts_scores: dict[uuid.UUID, float] = {}
    if query_text.strip():
        fts_results = await search_playbooks_fts(db, tenant_id, query_text, limit=50)
        max_rank = max((r for _, r in fts_results), default=1.0) or 1.0
        for playbook, rank in fts_results:
            fts_scores[playbook.id] = float(rank) / max_rank

    query_embedding: list[float] | None = None
    if query_text.strip():
        try:
            query_embedding = await generate_embedding(query_text)
        except Exception:
            query_embedding = None

    ranked = []
    now = datetime.now(UTC)
    for pb in approved_playbooks:
        pv_id = await _latest_published_version_id(db, pb.id)
        if pv_id is None:
            continue
        pv = await db.get(PlaybookVersion, pv_id)
        keyword_score = fts_scores.get(pb.id, 0.0)

        sem_rows: list = []
        semantic_evidence_ids: set[uuid.UUID] = set()
        if query_text.strip() and query_embedding is not None:
            try:
                sem_rows = await search_evidence_semantic_for_playbook(
                    db,
                    tenant_id,
                    pb.id,
                    pv_id,
                    query_text,
                    limit=10,
                    query_embedding=query_embedding,
                    exclude_policy_ids=excluded_policy_ids,
                )
                semantic_evidence_ids = {row[0].id for row in sem_rows if row[0] is not None}
            except Exception:
                sem_rows = []

        graph_score = await _graph_score_for_playbook(
            db, tenant_id, pb.id,
            semantic_evidence_ids=semantic_evidence_ids or None,
            domain_id=domain_id,
        )
        identity_score = await _identity_score_for_playbook(
            db,
            tenant_id,
            pb.id,
            query_identity_ids,
            domain_id=domain_id,
        )
        neg_score = await _negative_penalty_for_playbook(
            db, tenant_id, pb.id, pb.domain_id,
        )

        semantic_score_pb, evidence_hits_pb = _semantic_corpus_score(sem_rows)
        semantic_score = min(1.0, semantic_score_pb * (0.6 + 0.4 * keyword_score))
        quality_score = 0.5
        freshness = _compute_freshness(pb, now)
        recency_score = freshness
        playbook_confidence = float(pv.playbook_confidence) if pv is not None else 0.0

        total = (
            weights.keyword * keyword_score
            + weights.semantic * semantic_score
            + weights.graph_distance * graph_score
            + weights.evidence_quality * quality_score
            + weights.identity * identity_score
            + weights.recency * recency_score
            + weights.freshness * freshness
            - weights.negative_penalty * neg_score
        )

        freshness_status = "fresh" if freshness > 0.7 else ("aging" if freshness > 0.3 else "stale")

        ranked.append(RankedPlaybook(
            playbook=pb,
            score=total,
            confidence=total,
            playbook_confidence=playbook_confidence,
            freshness_status=freshness_status,
            evidence_count=evidence_hits_pb,
            breakdown={
                "keyword": keyword_score,
                "semantic": semantic_score,
                "graph": graph_score,
                "quality": quality_score,
                "identity": identity_score,
                "recency": recency_score,
                "freshness": freshness,
                "negative_penalty": neg_score,
            },
        ))

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:top_k]


def _compute_freshness(playbook: Playbook, now: datetime) -> float:
    """Compute freshness score based on last validation and expiry."""
    if playbook.expiry_at and playbook.expiry_at < now:
        return 0.0
    if playbook.last_validated_at:
        days_since = (now - playbook.last_validated_at).days
        return max(0.0, 1.0 - (days_since / 180))
    return 0.5
