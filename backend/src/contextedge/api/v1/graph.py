from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from contextedge.deps import AuthUser, DbSession
from contextedge.graph.agent.contracts import AgentGraphRequest, AgentGraphSubset
from contextedge.graph.agent.service import (
    AgentGraphProjectionService,
    build_agent_graph_scope,
)
from contextedge.graph.queries import get_entity_subgraph, get_graph_stats, get_neighbors
from contextedge.graph.temporal import normalize_graph_as_of

router = APIRouter()


@router.post("/agent-subsets", response_model=AgentGraphSubset)
async def create_agent_graph_subset(
    body: AgentGraphRequest,
    db: DbSession,
    user: AuthUser,
):
    """Return a ranked, bounded, authorization-filtered agent graph projection."""
    effective = body.model_copy(update={"as_of": normalize_graph_as_of(body.as_of)})
    scope = await build_agent_graph_scope(db, user, effective.domain_id)
    return await AgentGraphProjectionService(db).project(
        effective,
        scope,
        invocation_mode="api",
    )


@router.get("/cmdb-topology")
async def cmdb_topology(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="CI display name (e.g. vpn-gw-east-01) or 32-hex sys_id.",
    ),
):
    """Live ±1-hop CMDB neighborhood for a CI, write-through cached into
    entities / graph_edges; falls back to the cached view (marked stale)
    when ServiceNow is unreachable."""
    from contextedge.services.cmdb_topology_service import lookup_topology

    return await lookup_topology(db, user.tenant_id, ci)


@router.post("/fix-outcomes")
async def record_fix_outcome_endpoint(
    db: DbSession,
    user: AuthUser,
    fix_pattern_id: UUID,
    ci: str = Query(..., min_length=1, max_length=500),
    success: bool = Query(...),
):
    """Record a fix outcome against a CI (B5): updates per-cohort
    counters and mints review-gated promotion candidates when the
    ladder's thresholds are met. Scope only broadens via review."""
    user.require_role("knowledge_manager")
    from contextedge.services.cmdb_topology_service import resolve_ci_entity
    from contextedge.services.fix_cohort_service import record_fix_outcome

    entity = await resolve_ci_entity(db, user.tenant_id, ci)
    if entity is None:
        raise HTTPException(status_code=404, detail="CI not found")
    result = await record_fix_outcome(
        db, user.tenant_id, fix_pattern_id, entity, success
    )
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/fix-applicability")
async def fix_applicability(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(..., min_length=1, max_length=500),
):
    """Deterministic fix-applicability assessment for a CI: which known
    fixes validate against its recorded traits, at which level of the
    7-level ladder, and whether review is required (B4)."""
    user.require_role("knowledge_manager")
    from contextedge.services.cmdb_topology_service import resolve_ci_entity
    from contextedge.services.fix_applicability_service import (
        assess_fix_applicability,
    )

    entity = await resolve_ci_entity(db, user.tenant_id, ci)
    if entity is None:
        raise HTTPException(status_code=404, detail="CI not found")
    return await assess_fix_applicability(db, user.tenant_id, entity)


@router.get("/change-risk")
async def change_risk(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="CI display name (e.g. vpn-gw-east-01) or 32-hex sys_id.",
    ),
    window_days: int = Query(180, ge=1, le=730),
):
    """Deterministic change-risk profile for a CI from operational history:
    change→incident blame rate, incident pressure, alert activity, and
    cached blast radius — every factor explained."""
    from contextedge.services.change_risk_service import assess_change_risk

    return await assess_change_risk(db, user.tenant_id, ci, window_days=window_days)


@router.get("/coverage")
async def coverage(db: DbSession, user: AuthUser):
    """What this deployment can and cannot answer, per facet.

    Roadmap H2. Every facet reports one of `available`, `stale`, `empty`,
    `pending`, `not_selected`, `unavailable`, `unsupported` or
    `not_configured`, and `blind_spots` lists the facets where an empty
    result must NOT be read as a zero.

    The distinction this exists for: without it, "no change caused this
    incident" and "nothing here can see changes" are the same empty list,
    and an agent cannot tell a finding from a missing connector.
    """
    from contextedge.services.coverage_service import build_coverage_report

    report = await build_coverage_report(db, user.tenant_id)
    return report.as_dict()


@router.get("/situations")
async def list_situations(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
):
    """Operational situations: what is happening, assembled from many signals.

    Roadmap H3. Each membership says how it got there (`correlation_method`,
    `membership_status`), because a merge is a factual claim and a claim
    nobody can inspect is one nobody can retract.
    """
    from sqlalchemy import select

    from contextedge.models.situation import (
        OperationalSituation,
        SituationEvidenceMembership,
    )

    rows = await db.execute(
        select(OperationalSituation)
        .where(
            OperationalSituation.tenant_id == user.tenant_id,
            OperationalSituation.state.not_in(("merged", "invalidated")),
        )
        .order_by(OperationalSituation.onset_at.desc().nulls_last())
        .limit(limit)
    )
    situations = list(rows.scalars().all())
    if not situations:
        return {"situations": []}

    member_rows = await db.execute(
        select(SituationEvidenceMembership).where(
            SituationEvidenceMembership.situation_id.in_([s.id for s in situations])
        )
    )
    by_situation: dict[UUID, list] = {}
    for m in member_rows.scalars().all():
        by_situation.setdefault(m.situation_id, []).append(m)

    return {
        "situations": [
            {
                "id": str(s.id),
                "title": s.title,
                "state": s.state,
                "situation_type": s.situation_type,
                "confidence": s.situation_confidence,
                "onset_at": s.onset_at.isoformat() if s.onset_at else None,
                "last_signal_at": (
                    s.last_signal_at.isoformat() if s.last_signal_at else None
                ),
                "incident_count": s.incident_count,
                "correlation_version": s.correlation_version,
                "members": [
                    {
                        "evidence_id": str(m.evidence_id),
                        "role": m.evidence_role,
                        "status": m.membership_status,
                        "method": m.correlation_method,
                        "confidence": m.membership_confidence,
                    }
                    for m in by_situation.get(s.id, [])
                ],
            }
            for s in situations
        ]
    }


@router.get("/situations/{situation_id}/change-candidates")
async def situation_change_candidates(
    situation_id: UUID,
    db: DbSession,
    user: AuthUser,
    persist: bool = Query(
        False,
        description=(
            "Store the ranked candidates. Idempotent, and never overwrites a "
            "row a human reviewed or rejected."
        ),
    ),
):
    """Which change could explain this situation (roadmap H6).

    A RANKED LIST, never a verdict. `correlation_score` is a rank under an
    explainable additive model — 0.85 means "strong on the factors below",
    not "85% likely". Only `confirmed` is a claim, and it comes solely from
    governed evidence such as a ServiceNow `caused_by` a human filled in;
    no score promotes a candidate to it.
    """
    from contextedge.models.situation import OperationalSituation
    from contextedge.services.change_correlation_service import (
        correlate_changes_for_situation,
        persist_candidates,
    )

    situation = await db.get(OperationalSituation, situation_id)
    if situation is None or situation.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Situation not found")

    candidates = await correlate_changes_for_situation(db, user.tenant_id, situation)
    written = None
    if persist and candidates:
        written = await persist_candidates(db, user.tenant_id, situation, candidates)

    return {
        "situation_id": str(situation.id),
        "onset_at": situation.onset_at.isoformat() if situation.onset_at else None,
        "candidates": [c.as_dict() for c in candidates],
        "persisted": written,
    }


@router.get("/diagnostic-context/{incident_evidence_id}")
async def diagnostic_context(
    incident_evidence_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    """Everything known around one incident, as facets (roadmap H7).

    The acceptance criterion the roadmap was written for: an agent handed a
    single incident identifier obtains the operational context around it
    rather than reasoning from the description alone.

    Read `blind_spots` before concluding anything from an empty facet. It
    merges two different absences with the same consequence — a facet that
    could not answer for this incident, and a dimension this deployment
    cannot answer at all.
    """
    from contextedge.services.diagnostic_context_service import (
        build_diagnostic_context,
    )

    context = await build_diagnostic_context(
        db,
        user.tenant_id,
        incident_evidence_id,
        allowed_domain_ids=user.allowed_domain_ids,
    )
    if context is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return context.as_dict()


@router.post("/situations/lifecycle")
async def evaluate_situation_lifecycle(
    db: DbSession,
    user: AuthUser,
    apply: bool = Query(
        False,
        description=(
            "Write the transitions. Defaults to a dry assessment, because this "
            "moves states other things read."
        ),
    ),
):
    """Move situations along their lifecycle on evidence (roadmap H8).

    A situation is only ever moved toward `resolved` by member incidents
    carrying a resolution in the source system. **Absence of signal is never
    recovery** — going quiet happens when a thing is fixed, when everyone gave
    up, and when a connector broke, and the silence does not distinguish them.
    """
    from contextedge.services.situation_lifecycle_service import (
        evaluate_all_situations,
    )

    return await evaluate_all_situations(db, user.tenant_id, apply=apply)


class SituationMergeRequest(BaseModel):
    survivor_situation_id: UUID
    reason: str


@router.post("/situations/{situation_id}/merge")
async def merge_situation(
    situation_id: UUID,
    body: SituationMergeRequest,
    db: DbSession,
    user: AuthUser,
):
    """Fold this situation into another, keeping the lineage.

    A governed action: merging rewrites what somebody may already have acted
    on, so it needs authority rather than a score. The loser keeps pointing at
    its survivor, and the database refuses a merged row that names none.

    Splitting is deliberately absent. One situation that turns out to be two is
    a real case and an unsafe automation — a proposal is safe, an automatic
    split silently rewrites history with no way to tell afterwards which half a
    reader saw.
    """
    user.require_role("knowledge_manager")
    from contextedge.services.situation_lifecycle_service import merge_situations

    result = await merge_situations(
        db,
        user.tenant_id,
        situation_id,
        body.survivor_situation_id,
        reason=body.reason,
        reviewed_by=user.user_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/edge-proposals")
async def list_edge_proposals_endpoint(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(100, ge=1, le=500),
):
    """Pending agent-proposed dependencies awaiting review. Proposals
    never enter the maf.v1 projection; this queue is how they become
    authored topology (or audit history)."""
    user.require_role("knowledge_manager")
    from contextedge.services.edge_proposal_service import list_edge_proposals

    return {
        "proposals": await list_edge_proposals(
            db,
            user.tenant_id,
            limit=limit,
            allowed_domain_ids=user.allowed_domain_ids,
        )
    }


@router.post("/edge-proposals/{edge_id}/approve")
async def approve_edge_proposal_endpoint(
    edge_id: UUID,
    db: DbSession,
    user: AuthUser,
    note: str | None = Query(None, max_length=500),
):
    """Promote a proposal to an authored depends_on edge with review
    provenance; the proposal edge closes (supersede, never delete)."""
    user.require_role("knowledge_manager")
    from contextedge.services.edge_proposal_service import approve_edge_proposal

    result = await approve_edge_proposal(
        db,
        user.tenant_id,
        edge_id,
        reviewed_by=str(user.user_id),
        note=note,
        allowed_domain_ids=user.allowed_domain_ids,
    )
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/edge-proposals/{edge_id}/reject")
async def reject_edge_proposal_endpoint(
    edge_id: UUID,
    db: DbSession,
    user: AuthUser,
    note: str | None = Query(None, max_length=500),
):
    user.require_role("knowledge_manager")
    from contextedge.services.edge_proposal_service import reject_edge_proposal

    result = await reject_edge_proposal(
        db,
        user.tenant_id,
        edge_id,
        reviewed_by=str(user.user_id),
        note=note,
        allowed_domain_ids=user.allowed_domain_ids,
    )
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/neighbors")
async def graph_neighbors(
    db: DbSession,
    user: AuthUser,
    node_type: str = Query(
        ...,
        description="Type of the origin node (e.g. playbook, pattern, episode)",
    ),
    node_id: UUID = Query(..., description="ID of the origin node"),
    edge_type: str | None = Query(None, description="Filter by edge type"),
    max_depth: int = Query(1, ge=1, le=3, description="BFS traversal depth (1-3)"),
    domain_id: UUID | None = Query(
        None,
        description="Scope to a domain (includes domain-less edges)",
    ),
    as_of: datetime | None = Query(None, description="Point-in-time traversal timestamp"),
):
    """Return neighboring nodes reachable via graph edges up to *max_depth* hops."""
    return await get_neighbors(
        db,
        tenant_id=user.tenant_id,
        node_type=node_type,
        node_id=node_id,
        edge_type=edge_type,
        max_depth=max_depth,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )


@router.get("/subgraph/{entity_type}/{entity_id}")
async def graph_subgraph(
    entity_type: str,
    entity_id: UUID,
    db: DbSession,
    user: AuthUser,
    max_depth: int = Query(1, ge=1, le=3),
    domain_id: UUID | None = Query(None),
    as_of: datetime | None = Query(None),
):
    """Return the subgraph around any entity as nodes + edges suitable for visualization."""
    return await get_entity_subgraph(
        db,
        tenant_id=user.tenant_id,
        node_type=entity_type,
        node_id=entity_id,
        max_depth=max_depth,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )


@router.get("/stats")
async def graph_stats(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None),
    as_of: datetime | None = Query(None),
):
    """Return aggregate edge-type and node-type counts for the tenant."""
    return await get_graph_stats(
        db,
        tenant_id=user.tenant_id,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )
