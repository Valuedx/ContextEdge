"""Hybrid ranker combining FTS, vector, graph, and quality signals."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge
from contextedge.models.playbook import Playbook
from contextedge.search.pg_fts import search_playbooks_fts
from contextedge.search.risk_policy import risk_within_cap
from contextedge.search.vector_search import search_evidence_semantic


@dataclass
class RankingWeights:
    keyword: float = 0.25
    semantic: float = 0.30
    graph_distance: float = 0.15
    evidence_quality: float = 0.10
    recency: float = 0.10
    freshness: float = 0.05
    negative_penalty: float = 0.05


@dataclass
class RankedPlaybook:
    playbook: Playbook
    score: float
    confidence: float
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
) -> float:
    q = select(func.count()).where(
        GraphEdge.tenant_id == tenant_id,
        or_(
            (GraphEdge.source_node_type == "playbook") & (GraphEdge.source_node_id == playbook_id),
            (GraphEdge.target_node_type == "playbook") & (GraphEdge.target_node_id == playbook_id),
        ),
    )
    n = (await db.execute(q)).scalar() or 0
    return min(1.0, float(n) / 5.0)


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
) -> list[RankedPlaybook]:
    """Rank approved playbooks using hybrid signals.

    When ``domain_id`` is set, keep playbooks in that domain or tenant-wide (``domain_id`` NULL).
    When ``max_risk_tier`` is set, drop playbooks above that tier (e.g. cap at ``medium``).
    """
    weights = weights or RankingWeights()

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
    if max_risk_tier is not None:
        approved_playbooks = [
            pb for pb in approved_playbooks if risk_within_cap(pb.risk_tier, max_risk_tier)
        ]
    if not approved_playbooks:
        return []

    fts_scores: dict[uuid.UUID, float] = {}
    if query_text.strip():
        fts_results = await search_playbooks_fts(db, tenant_id, query_text, limit=50)
        max_rank = max((r for _, r in fts_results), default=1.0) or 1.0
        for playbook, rank in fts_results:
            fts_scores[playbook.id] = float(rank) / max_rank

    sem_rows: list = []
    if query_text.strip():
        try:
            sem_rows = await search_evidence_semantic(db, tenant_id, query_text, limit=10)
        except Exception:
            sem_rows = []
    semantic_score_global, evidence_hits = _semantic_corpus_score(sem_rows)

    ranked = []
    now = datetime.now(timezone.utc)
    for pb in approved_playbooks:
        keyword_score = fts_scores.get(pb.id, 0.0)
        graph_score = await _graph_score_for_playbook(db, tenant_id, pb.id)
        semantic_score = min(1.0, semantic_score_global * (0.6 + 0.4 * keyword_score))
        quality_score = 0.5
        freshness = _compute_freshness(pb, now)
        recency_score = freshness

        total = (
            weights.keyword * keyword_score
            + weights.semantic * semantic_score
            + weights.graph_distance * graph_score
            + weights.evidence_quality * quality_score
            + weights.recency * recency_score
            + weights.freshness * freshness
        )

        freshness_status = "fresh" if freshness > 0.7 else ("aging" if freshness > 0.3 else "stale")

        ranked.append(RankedPlaybook(
            playbook=pb,
            score=total,
            confidence=total,
            freshness_status=freshness_status,
            evidence_count=evidence_hits,
            breakdown={
                "keyword": keyword_score,
                "semantic": semantic_score,
                "graph": graph_score,
                "quality": quality_score,
                "recency": recency_score,
                "freshness": freshness,
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
