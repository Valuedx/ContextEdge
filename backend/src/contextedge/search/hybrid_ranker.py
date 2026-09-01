"""Hybrid ranker: candidate union → batched signals → RRF → applicability."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge
from contextedge.models.playbook import (
    Playbook,
    PlaybookEvidenceLink,
    PlaybookNegativeKnowledge,
    PlaybookVersion,
)
from contextedge.search.fusion import rrf_max, rrf_scores
from contextedge.search.quality_filter import filter_runtime_eligible
from contextedge.services.case_frame_service import CaseFrame, build_case_frame
from contextedge.services.identity_service import resolve_identity_ids_for_terms
from contextedge.services.playbook_applicability import evaluate_trigger_conditions
from contextedge.services.score_calibration import (
    calibrate_confidence,
    load_active_calibration,
)

logger = structlog.get_logger()

MIN_RECOMMENDATION_SCORE = 0.35
DEFAULT_MARGIN = 0.02
ABSTAIN_CONFIDENCE = 0.55
# Positive fused terms sum to 1.0 so two strong candidates cannot both
# clamp at 1.0 and wipe selection_margin (GAP-6).
FUSED_RRF = 0.50
FUSED_QUALITY = 0.14
FUSED_FRESHNESS = 0.14
FUSED_APPLY = 0.10
FUSED_PRECEDENT = 0.07
FUSED_IDENTITY = 0.05


@dataclass
class RankingWeights:
    keyword: float = 0.25
    semantic: float = 0.30
    graph_distance: float = 0.15
    evidence_quality: float = 0.10
    identity: float = 0.05
    recency: float = 0.0  # removed (G2.6); kept so old callers do not explode
    freshness: float = 0.15
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
    playbook_version_id: uuid.UUID | None = None
    semantic_version: str | None = None
    applicability: str | None = None
    applicability_factors: list[str] | None = None
    applicability_differences: list[str] | None = None
    confidence_calibrated: float | None = None
    selection_margin: float | None = None
    linear_score: float = 0.0


async def _latest_published_versions(
    db: AsyncSession, playbook_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PlaybookVersion]:
    if not playbook_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(PlaybookVersion)
                .where(
                    PlaybookVersion.playbook_id.in_(tuple(playbook_ids)),
                    PlaybookVersion.published_at.is_not(None),
                )
                .order_by(
                    PlaybookVersion.playbook_id,
                    PlaybookVersion.published_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    latest: dict[uuid.UUID, PlaybookVersion] = {}
    for pv in rows:
        latest.setdefault(pv.playbook_id, pv)
    return latest


async def _batch_graph_counts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_ids: list[uuid.UUID],
    domain_id: uuid.UUID | None,
) -> dict[uuid.UUID, int]:
    if not playbook_ids:
        return {}
    counts: dict[uuid.UUID, int] = {pid: 0 for pid in playbook_ids}
    src_q = (
        select(GraphEdge.source_node_id, func.count())
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.source_node_type == "playbook",
            GraphEdge.source_node_id.in_(tuple(playbook_ids)),
        )
        .group_by(GraphEdge.source_node_id)
    )
    tgt_q = (
        select(GraphEdge.target_node_id, func.count())
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.target_node_type == "playbook",
            GraphEdge.target_node_id.in_(tuple(playbook_ids)),
        )
        .group_by(GraphEdge.target_node_id)
    )
    if domain_id is not None:
        src_q = src_q.where((GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None))
        tgt_q = tgt_q.where((GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None))
    for pid, n in (await db.execute(src_q)).all():
        counts[pid] = counts.get(pid, 0) + int(n)
    for pid, n in (await db.execute(tgt_q)).all():
        counts[pid] = counts.get(pid, 0) + int(n)
    return counts


async def _batch_identity_hits(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_ids: list[uuid.UUID],
    identity_ids: set[uuid.UUID],
    domain_id: uuid.UUID | None,
) -> dict[uuid.UUID, int]:
    if not playbook_ids or not identity_ids:
        return {pid: 0 for pid in playbook_ids}
    q = (
        select(GraphEdge.source_node_id, func.count(func.distinct(GraphEdge.target_node_id)))
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.source_node_type == "playbook",
            GraphEdge.source_node_id.in_(tuple(playbook_ids)),
            GraphEdge.target_node_type == "identity",
            GraphEdge.edge_type == "references_identity",
            GraphEdge.target_node_id.in_(tuple(identity_ids)),
        )
        .group_by(GraphEdge.source_node_id)
    )
    if domain_id is not None:
        q = q.where((GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None))
    hits = {pid: 0 for pid in playbook_ids}
    for pid, n in (await db.execute(q)).all():
        hits[pid] = int(n)
    return hits


async def _batch_contradiction_counts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not playbook_ids:
        return {}
    q = (
        select(GraphEdge.source_node_id, func.count())
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.source_node_type == "playbook",
            GraphEdge.source_node_id.in_(tuple(playbook_ids)),
            GraphEdge.edge_type == "contradicts",
        )
        .group_by(GraphEdge.source_node_id)
    )
    counts = {pid: 0 for pid in playbook_ids}
    for pid, n in (await db.execute(q)).all():
        counts[pid] = int(n)
    nk_q = (
        select(PlaybookNegativeKnowledge.playbook_id, func.count())
        .where(
            PlaybookNegativeKnowledge.tenant_id == tenant_id,
            PlaybookNegativeKnowledge.playbook_id.in_(tuple(playbook_ids)),
        )
        .group_by(PlaybookNegativeKnowledge.playbook_id)
    )
    for pid, n in (await db.execute(nk_q)).all():
        counts[pid] = counts.get(pid, 0) + int(n)
    return counts


async def _batch_precedent_counts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Resolved-episode validated_fix edges, not raw degree (G2.4)."""
    if not playbook_ids:
        return {}
    q = (
        select(GraphEdge.target_node_id, func.count())
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "validated_fix",
            GraphEdge.target_node_type == "playbook",
            GraphEdge.target_node_id.in_(tuple(playbook_ids)),
        )
        .group_by(GraphEdge.target_node_id)
    )
    counts = {pid: 0 for pid in playbook_ids}
    for pid, n in (await db.execute(q)).all():
        counts[pid] = int(n)
    return counts


async def _negative_penalty_for_playbook(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    domain_id: uuid.UUID | None,
) -> float:
    """Per-playbook contradiction penalty. Domain-wide NK is NOT used (G2.5)."""
    del domain_id
    counts = await _batch_contradiction_counts(db, tenant_id, [playbook_id])
    n = counts.get(playbook_id, 0)
    return min(1.0, n * 0.3)


def _arm_rank_score(arm_ranks: dict, arm: str, playbook_id: uuid.UUID) -> float:
    ordered = list(arm_ranks.get(arm) or [])
    try:
        return 1.0 / (1 + ordered.index(playbook_id))
    except ValueError:
        return 0.0


def _legacy_linear_score(
    weights: RankingWeights,
    *,
    keyword: float,
    semantic: float,
    graph: float,
    quality: float,
    identity_score: float,
    freshness: float,
    neg: float,
) -> float:
    """Pre-RRF linear mix over the same candidate set (shadow serve path)."""
    total = (
        weights.keyword * keyword
        + weights.semantic * semantic
        + weights.graph_distance * graph
        + weights.evidence_quality * quality
        + weights.identity * identity_score
        + weights.freshness * freshness
        - weights.negative_penalty * neg
    )
    return max(0.0, min(1.0, total))


def _shadow_mode() -> bool:
    try:
        from contextedge.config import settings

        return bool(settings.ranking_shadow_mode)
    except Exception:
        return False


def _assign_margins(ranked: list[RankedPlaybook]) -> None:
    if len(ranked) >= 2:
        ranked[0].selection_margin = round(ranked[0].score - ranked[1].score, 4)
    elif ranked:
        ranked[0].selection_margin = round(ranked[0].score, 4)


def _quality_score(playbook_confidence: float, evidence_hits: int) -> float:
    support = min(evidence_hits / 5.0, 1.0)
    return min(max(0.6 * playbook_confidence + 0.4 * support, 0.0), 1.0)


def _compute_freshness(playbook: Playbook, now: datetime) -> float:
    if playbook.expiry_at and playbook.expiry_at < now:
        return 0.0
    if playbook.last_validated_at:
        days_since = (now - playbook.last_validated_at).days
        return max(0.0, 1.0 - (days_since / 180))
    return 0.35


_APPLICABILITY_WEIGHT = {
    "exact": 1.0,
    "strong": 0.85,
    "partial": 0.6,
    "unvalidated": 0.4,
    "contradicted": 0.0,
}


async def rank_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query_text: str,
    entities: list[str] | None = None,
    top_k: int = 5,
    weights: RankingWeights | None = None,
    *,
    domain_id: uuid.UUID | None = None,
    max_risk_tier: str | None = None,
    allowed_domain_ids: list[uuid.UUID] | None = None,
    caller_roles: list[str] | None = None,
    min_score: float | None = None,
    case_frame: CaseFrame | None = None,
    environment: dict | None = None,
) -> list[RankedPlaybook]:
    weights = weights or RankingWeights()
    frame = case_frame or build_case_frame(
        query_text=query_text,
        entities=entities or [],
        environment=environment,
        domain_id=domain_id,
        symptoms=[query_text] if query_text and not (entities or []) else [],
    )
    if not frame.symptom_text and query_text:
        frame = build_case_frame(
            query_text=query_text,
            entities=entities or [],
            environment=environment,
            domain_id=domain_id,
        )

    shadow = _shadow_mode()
    candidates = await generate_playbook_candidates(
        db,
        tenant_id=tenant_id,
        frame=frame,
        domain_id=domain_id,
        max_risk_tier=max_risk_tier,
        allowed_domain_ids=allowed_domain_ids,
        caller_roles=caller_roles,
    )
    playbooks = list(candidates.playbooks.values())
    if not playbooks:
        return []

    now = datetime.now(UTC)
    ids = [pb.id for pb in playbooks]
    latest_versions = await _latest_published_versions(db, ids)
    eligible_map = await filter_runtime_eligible(
        db,
        tenant_id,
        {pb.id: pb for pb in playbooks},
        versions_by_playbook=latest_versions,
    )
    playbooks = [eligible_map[pid] for pid in ids if pid in eligible_map]
    if not playbooks:
        return []
    ids = [pb.id for pb in playbooks]
    identity_ids = await resolve_identity_ids_for_terms(db, tenant_id, entities or [])
    graph_counts = (
        await _batch_graph_counts(db, tenant_id, ids, domain_id) if shadow else {}
    )
    identity_hits = await _batch_identity_hits(
        db, tenant_id, ids, identity_ids, domain_id
    )
    contradictions = await _batch_contradiction_counts(db, tenant_id, ids)
    precedents = await _batch_precedent_counts(db, tenant_id, ids)
    evidence_hits = await _batch_evidence_link_counts(
        db, ids, evidence_ids=candidates.evidence_hit_ids
    )

    calibration = None
    try:
        calibration = await load_active_calibration(db, tenant_id)
    except Exception:
        calibration = None
    arm_weights = calibration.arm_weights if calibration else None
    fused = rrf_scores(candidates.arm_ranks, weights=arm_weights)
    denom = rrf_max(arm_weights) or 1.0

    ranked: list[RankedPlaybook] = []
    for pb in playbooks:
        pv = latest_versions.get(pb.id)
        if pv is None:
            continue
        if pb.expiry_at is not None and pb.expiry_at < now:
            continue
        verdict = evaluate_trigger_conditions(pv, frame, playbook=pb, now=now)
        if verdict.drop:
            continue

        rrf_norm = min(1.0, fused.get(pb.id, 0.0) / denom)
        playbook_confidence = float(pv.playbook_confidence or 0.0)
        hits = evidence_hits.get(pb.id, 0)
        quality = _quality_score(playbook_confidence, hits)
        freshness = _compute_freshness(pb, now)
        identity_score = min(
            1.0, identity_hits.get(pb.id, 0) / max(1, len(identity_ids) or 1)
        ) if identity_ids else 0.0
        neg = min(1.0, contradictions.get(pb.id, 0) * 0.3)
        precedent = min(1.0, precedents.get(pb.id, 0) / 5.0)
        apply_w = _APPLICABILITY_WEIGHT.get(verdict.level, 0.4)
        keyword = _arm_rank_score(candidates.arm_ranks, "r2_lexical", pb.id)
        semantic = _arm_rank_score(candidates.arm_ranks, "r1_embedding", pb.id)
        graph = min(1.0, graph_counts.get(pb.id, 0) / 5.0)
        linear = _legacy_linear_score(
            weights,
            keyword=keyword,
            semantic=semantic,
            graph=graph,
            quality=quality,
            identity_score=identity_score,
            freshness=freshness,
            neg=neg,
        )

        total = (
            FUSED_RRF * rrf_norm
            + FUSED_QUALITY * quality
            + FUSED_FRESHNESS * freshness
            + FUSED_APPLY * apply_w
            + FUSED_PRECEDENT * precedent
            + FUSED_IDENTITY * identity_score
            - weights.negative_penalty * neg
        )
        clamped = max(0.0, min(1.0, total))
        calibrated = calibrate_confidence(clamped, calibration)
        freshness_status = (
            "fresh" if freshness > 0.7 else ("aging" if freshness > 0.3 else "stale")
        )
        ranked.append(
            RankedPlaybook(
                playbook=pb,
                score=total,
                confidence=clamped,
                playbook_confidence=playbook_confidence,
                freshness_status=freshness_status,
                evidence_count=hits,
                playbook_version_id=pv.id,
                semantic_version=pv.semantic_version,
                applicability=verdict.level,
                applicability_factors=verdict.matched_factors or None,
                applicability_differences=verdict.differences or None,
                confidence_calibrated=calibrated,
                linear_score=linear,
                breakdown={
                    "rrf": round(rrf_norm, 4),
                    "keyword": keyword,
                    "semantic": semantic,
                    "graph": graph,
                    "quality": quality,
                    "identity": identity_score,
                    "freshness": freshness,
                    "precedent": precedent,
                    "applicability": apply_w,
                    "negative_penalty": neg,
                    "fused_score": round(clamped, 4),
                    "linear_score": round(linear, 4),
                },
            )
        )

    ranked.sort(key=lambda r: r.score, reverse=True)
    _assign_margins(ranked)
    for row in ranked:
        row.score = max(0.0, min(1.0, row.score))

    threshold = MIN_RECOMMENDATION_SCORE if min_score is None else min_score
    fused_confident = [r for r in ranked if r.score >= threshold]
    if min_score is None and ranked:
        top = ranked[0]
        margin = top.selection_margin
        conf = (
            top.confidence_calibrated
            if top.confidence_calibrated is not None
            else top.confidence
        )
        low_margin = margin is not None and margin < DEFAULT_MARGIN
        low_conf = conf < ABSTAIN_CONFIDENCE
        if low_margin or low_conf:
            logger.info(
                "ranking.abstained",
                tenant_id=str(tenant_id),
                reason="low_margin" if low_margin else "low_confidence",
                margin=margin,
                confidence=conf,
                top_score=round(top.score, 3),
            )
            fused_confident = []
    if ranked and not fused_confident:
        logger.info(
            "ranking.abstained",
            tenant_id=str(tenant_id),
            candidates=len(ranked),
            top_score=round(ranked[0].score, 3),
            threshold=threshold,
        )
    logger.info(
        "ranking.fused",
        tenant_id=str(tenant_id),
        candidates=len(ranked),
        returned=len(fused_confident[:top_k]),
        top_score=round(ranked[0].score, 3) if ranked else None,
        margin=ranked[0].selection_margin if ranked else None,
    )

    if shadow:
        linear_ranked = list(ranked)
        for row in linear_ranked:
            row.breakdown = {
                **row.breakdown,
                "shadow_served": "linear",
                "shadow_policy": "log_fused_serve_linear",
            }
            row.score = row.linear_score
            row.confidence = row.linear_score
        linear_ranked.sort(key=lambda r: r.score, reverse=True)
        _assign_margins(linear_ranked)
        linear_confident = [r for r in linear_ranked if r.score >= threshold]
        fused_top = str(fused_confident[0].playbook.id) if fused_confident else None
        linear_top = str(linear_confident[0].playbook.id) if linear_confident else None
        logger.info(
            "ranking.shadow",
            tenant_id=str(tenant_id),
            fused_top=fused_top,
            linear_top=linear_top,
            agreement=fused_top == linear_top,
            fused_returned=len(fused_confident[:top_k]),
            linear_returned=len(linear_confident[:top_k]),
        )
        return linear_confident[:top_k]

    return fused_confident[:top_k]


async def _batch_evidence_link_counts(
    db: AsyncSession,
    playbook_ids: list[uuid.UUID],
    *,
    evidence_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID, int]:
    if not playbook_ids:
        return {}
    if not evidence_ids:
        return {pid: 0 for pid in playbook_ids}
    q = (
        select(PlaybookVersion.playbook_id, func.count(PlaybookEvidenceLink.id))
        .join(
            PlaybookEvidenceLink,
            PlaybookEvidenceLink.playbook_version_id == PlaybookVersion.id,
        )
        .where(
            PlaybookVersion.playbook_id.in_(tuple(playbook_ids)),
            PlaybookVersion.published_at.is_not(None),
            PlaybookEvidenceLink.evidence_id.in_(tuple(evidence_ids)),
        )
        .group_by(PlaybookVersion.playbook_id)
    )
    counts = {pid: 0 for pid in playbook_ids}
    for pid, n in (await db.execute(q)).all():
        counts[pid] = int(n)
    return counts
