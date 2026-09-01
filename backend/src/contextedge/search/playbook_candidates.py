"""Four recall arms → one candidate union for playbook ranking.

R1 Playbook.embedding ANN on symptom_text
R2 OR-composed websearch over title/description + lexical_search_text
R3 signature / pattern → playbook
R4 evidence FTS → PlaybookEvidenceLink reverse lookup

Each arm returns a rank list. Union is capped at 60. All later signals
are computed batched over that set (G5.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.models.episode import Episode
from contextedge.models.playbook import Playbook, PlaybookEvidenceLink, PlaybookVersion
from contextedge.search.pg_fts import or_composed_websearch_tsquery, search_evidence_fts
from contextedge.search.risk_policy import risk_within_cap
from contextedge.search.vector_ops import halfvec_cosine_distance, tune_ann_recall
from contextedge.search.quality_filter import filter_runtime_eligible

logger = structlog.get_logger()

R1_CAP = 50
R2_CAP = 50
R3_CAP = 30
R4_CAP = 30
UNION_CAP = 60
# Domain/risk filters run after LIMIT; oversample so a scoped token is
# not starved by ineligible global hits (GAP-16).
_OVERSAMPLE = 3


@dataclass(slots=True)
class CandidateSet:
    playbooks: dict[uuid.UUID, Playbook] = field(default_factory=dict)
    arm_ranks: dict[str, list[uuid.UUID]] = field(default_factory=dict)
    evidence_hit_ids: list[uuid.UUID] = field(default_factory=list)


def _eligible(
    pb: Playbook,
    *,
    domain_id: uuid.UUID | None,
    max_risk_tier: str | None,
    allowed_domain_ids: list[uuid.UUID] | None,
) -> bool:
    if pb.lifecycle_state != "approved":
        return False
    if domain_id is not None and pb.domain_id not in (None, domain_id):
        return False
    if allowed_domain_ids is not None and pb.domain_id is not None:
        if pb.domain_id not in allowed_domain_ids:
            return False
    if max_risk_tier is not None and not risk_within_cap(pb.risk_tier, max_risk_tier):
        return False
    return True


async def generate_playbook_candidates(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    frame: CaseFrame,
    domain_id: uuid.UUID | None = None,
    max_risk_tier: str | None = None,
    allowed_domain_ids: list[uuid.UUID] | None = None,
    caller_roles: list[str] | None = None,
) -> CandidateSet:
    from contextedge.search.access_control import resolve_excluded_access_policy_ids
    from contextedge.services.case_frame_service import resolve_case_frame

    frame = await resolve_case_frame(db, tenant_id, frame)
    exclude_policy_ids = await resolve_excluded_access_policy_ids(
        db, tenant_id, caller_roles
    )

    arm_ranks: dict[str, list[uuid.UUID]] = {}
    collected: dict[uuid.UUID, Playbook] = {}

    r1 = await _arm_embedding(
        db, tenant_id, frame, domain_id, max_risk_tier, allowed_domain_ids
    )
    r2 = await _arm_lexical(
        db, tenant_id, frame, domain_id, max_risk_tier, allowed_domain_ids
    )
    r3 = await _arm_signature(
        db, tenant_id, frame, domain_id, max_risk_tier, allowed_domain_ids
    )
    r4, evidence_hit_ids = await _arm_evidence(
        db,
        tenant_id,
        frame,
        domain_id,
        max_risk_tier,
        allowed_domain_ids,
        exclude_policy_ids=exclude_policy_ids,
    )
    arm_ranks["r1_embedding"] = [pb.id for pb, _ in r1]
    arm_ranks["r2_lexical"] = [pb.id for pb, _ in r2]
    arm_ranks["r3_signature"] = [pb.id for pb in r3]
    arm_ranks["r4_evidence"] = [pb.id for pb in r4]

    for rows in (r1, r2):
        for pb, _dist in rows:
            collected.setdefault(pb.id, pb)
    for pb in r3 + r4:
        collected.setdefault(pb.id, pb)

    if len(collected) > UNION_CAP:
        # Prefer playbooks that appear in more / earlier arms.
        order: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for arm in ("r3_signature", "r1_embedding", "r2_lexical", "r4_evidence"):
            for pid in arm_ranks.get(arm, []):
                if pid not in seen:
                    seen.add(pid)
                    order.append(pid)
                if len(order) >= UNION_CAP:
                    break
            if len(order) >= UNION_CAP:
                break
        collected = {pid: collected[pid] for pid in order if pid in collected}
        for arm, ids in list(arm_ranks.items()):
            arm_ranks[arm] = [pid for pid in ids if pid in collected]

    collected = await filter_runtime_eligible(db, tenant_id, collected)

    logger.info(
        "playbook_candidates.generated",
        tenant_id=str(tenant_id),
        r1=len(arm_ranks["r1_embedding"]),
        r2=len(arm_ranks["r2_lexical"]),
        r3=len(arm_ranks["r3_signature"]),
        r4=len(arm_ranks["r4_evidence"]),
        union=len(collected),
    )
    return CandidateSet(
        playbooks=collected,
        arm_ranks=arm_ranks,
        evidence_hit_ids=evidence_hit_ids,
    )


async def _arm_embedding(
    db, tenant_id, frame: CaseFrame, domain_id, max_risk_tier, allowed_domain_ids
) -> list[tuple[Playbook, float]]:
    text = (frame.symptom_text or "").strip()
    if not text:
        return []
    try:
        embedding = await generate_embedding(text, tenant_id=tenant_id, db=db)
    except Exception:
        return []
    await tune_ann_recall(db)
    distance = halfvec_cosine_distance(Playbook.embedding, embedding)
    q = (
        select(Playbook, distance.label("distance"))
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            Playbook.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(R1_CAP * _OVERSAMPLE)
    )
    if domain_id is not None:
        q = q.where((Playbook.domain_id == domain_id) | Playbook.domain_id.is_(None))
    rows = (await db.execute(q)).all()
    out = []
    for pb, dist in rows:
        if _eligible(
            pb,
            domain_id=domain_id,
            max_risk_tier=max_risk_tier,
            allowed_domain_ids=allowed_domain_ids,
        ):
            out.append((pb, float(dist) if dist is not None else 2.0))
            if len(out) >= R1_CAP:
                break
    return out


async def _arm_lexical(
    db, tenant_id, frame: CaseFrame, domain_id, max_risk_tier, allowed_domain_ids
) -> list[tuple[Playbook, float]]:
    query = " ".join(frame.lexical_terms) or frame.symptom_text
    tsquery = or_composed_websearch_tsquery(query)
    if tsquery is None:
        return []
    lexical_tsv = func.to_tsvector(
        "english", func.coalesce(Playbook.lexical_search_text, "")
    )
    rank = func.greatest(
        func.ts_rank(Playbook.search_tsvector, tsquery),
        func.ts_rank(lexical_tsv, tsquery),
    )
    q = (
        select(Playbook, rank.label("rank"))
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            or_(
                Playbook.search_tsvector.op("@@")(tsquery),
                lexical_tsv.op("@@")(tsquery),
            ),
        )
        .order_by(rank.desc())
        .limit(R2_CAP * _OVERSAMPLE)
    )
    if domain_id is not None:
        q = q.where((Playbook.domain_id == domain_id) | Playbook.domain_id.is_(None))
    rows = (await db.execute(q)).all()
    out = []
    for pb, rk in rows:
        if _eligible(
            pb,
            domain_id=domain_id,
            max_risk_tier=max_risk_tier,
            allowed_domain_ids=allowed_domain_ids,
        ):
            out.append((pb, float(rk or 0.0)))
            if len(out) >= R2_CAP:
                break
    return out


async def _arm_signature(
    db, tenant_id, frame: CaseFrame, domain_id, max_risk_tier, allowed_domain_ids
) -> list[Playbook]:
    """Issue/Error signature → approved episode → pattern → playbook (GAP-3)."""
    from contextedge.models.error_signature import ErrorSignature, FixPattern
    from contextedge.models.issue_signature import EpisodeIssueSignature
    from contextedge.models.pattern import GraphEdge

    signature_ids = await _signature_ids_for_frame(db, tenant_id, frame)
    pattern_ids: set[uuid.UUID] = set()
    playbook_ids: set[uuid.UUID] = set()

    if signature_ids:
        episode_ids = set(
            (
                await db.execute(
                    select(EpisodeIssueSignature.episode_id)
                    .join(Episode, Episode.id == EpisodeIssueSignature.episode_id)
                    .where(
                        EpisodeIssueSignature.tenant_id == tenant_id,
                        EpisodeIssueSignature.issue_signature_id.in_(tuple(signature_ids)),
                        Episode.reviewer_state == "approved",
                    )
                    .limit(80)
                )
            ).scalars().all()
        )
        edge_episodes = (
            await db.execute(
                select(GraphEdge.source_node_id)
                .join(
                    Episode,
                    (Episode.id == GraphEdge.source_node_id)
                    & (Episode.tenant_id == tenant_id),
                )
                .where(
                    GraphEdge.tenant_id == tenant_id,
                    GraphEdge.edge_type == "has_signature",
                    GraphEdge.source_node_type == "episode",
                    GraphEdge.target_node_type.in_(("issue_signature", "error_signature")),
                    GraphEdge.target_node_id.in_(tuple(signature_ids)),
                    Episode.reviewer_state == "approved",
                )
                .limit(80)
            )
        ).scalars().all()
        episode_ids.update(edge_episodes)
        if episode_ids:
            pattern_ids.update(
                (
                    await db.execute(
                        select(GraphEdge.target_node_id).where(
                            GraphEdge.tenant_id == tenant_id,
                            GraphEdge.edge_type == "belongs_to",
                            GraphEdge.source_node_type == "episode",
                            GraphEdge.target_node_type == "pattern",
                            GraphEdge.source_node_id.in_(tuple(episode_ids)),
                        )
                    )
                ).scalars().all()
            )

    if frame.error_signature_id:
        error_row = await db.get(ErrorSignature, frame.error_signature_id)
        if error_row is not None and error_row.pattern_id:
            pattern_ids.add(error_row.pattern_id)
        fix_ids = (
            await db.execute(
                select(FixPattern.recommended_playbook_id).where(
                    FixPattern.tenant_id == tenant_id,
                    FixPattern.error_signature_id == frame.error_signature_id,
                    FixPattern.is_active.is_(True),
                    FixPattern.recommended_playbook_id.is_not(None),
                )
            )
        ).scalars().all()
        playbook_ids.update(pid for pid in fix_ids if pid)

    if not pattern_ids and not playbook_ids:
        return []

    filters = [
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state == "approved",
    ]
    id_clauses = []
    if pattern_ids:
        id_clauses.append(Playbook.pattern_id.in_(tuple(pattern_ids)))
    if playbook_ids:
        id_clauses.append(Playbook.id.in_(tuple(playbook_ids)))
    q = select(Playbook).where(*filters, or_(*id_clauses)).limit(R3_CAP * _OVERSAMPLE)
    if domain_id is not None:
        q = q.where((Playbook.domain_id == domain_id) | Playbook.domain_id.is_(None))
    rows = (await db.execute(q)).scalars().all()
    return [
        pb
        for pb in rows
        if _eligible(
            pb,
            domain_id=domain_id,
            max_risk_tier=max_risk_tier,
            allowed_domain_ids=allowed_domain_ids,
        )
    ][:R3_CAP]


async def _signature_ids_for_frame(db, tenant_id, frame: CaseFrame) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    if frame.issue_signature_id:
        ids.append(frame.issue_signature_id)
    if frame.error_signature_id:
        ids.append(frame.error_signature_id)
        from contextedge.models.issue_signature import IssueSignature

        linked = (
            await db.execute(
                select(IssueSignature.id).where(
                    IssueSignature.tenant_id == tenant_id,
                    IssueSignature.error_signature_id == frame.error_signature_id,
                )
            )
        ).scalars().all()
        ids.extend(linked)
    # Dedupe, preserve order.
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


async def _arm_evidence(
    db,
    tenant_id,
    frame: CaseFrame,
    domain_id,
    max_risk_tier,
    allowed_domain_ids,
    *,
    exclude_policy_ids: list[uuid.UUID] | None = None,
) -> tuple[list[Playbook], list[uuid.UUID]]:
    query = " ".join(frame.lexical_terms) or frame.symptom_text
    if not query.strip():
        return [], []
    hits = await search_evidence_fts(
        db,
        tenant_id,
        query,
        limit=40,
        exclude_policy_ids=exclude_policy_ids,
        compose="or",
    )
    evidence_ids = [row[0].id for row in hits if row and row[0] is not None]
    if not evidence_ids:
        return [], []
    q = (
        select(Playbook)
        .join(PlaybookVersion, PlaybookVersion.playbook_id == Playbook.id)
        .join(
            PlaybookEvidenceLink,
            PlaybookEvidenceLink.playbook_version_id == PlaybookVersion.id,
        )
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            PlaybookVersion.published_at.is_not(None),
            PlaybookEvidenceLink.evidence_id.in_(tuple(evidence_ids)),
        )
        .distinct()
        .limit(R4_CAP * _OVERSAMPLE)
    )
    if domain_id is not None:
        q = q.where((Playbook.domain_id == domain_id) | Playbook.domain_id.is_(None))
    rows = (await db.execute(q)).scalars().all()
    playbooks = [
        pb
        for pb in rows
        if _eligible(
            pb,
            domain_id=domain_id,
            max_risk_tier=max_risk_tier,
            allowed_domain_ids=allowed_domain_ids,
        )
    ][:R4_CAP]
    return playbooks, evidence_ids
