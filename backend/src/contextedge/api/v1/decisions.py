from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.decision import (
    DecisionChainResponse,
    DecisionCreate,
    DecisionOutcomeCreate,
    DecisionOutcomeResponse,
    DecisionProvenanceResponse,
    DecisionRejectRequest,
    DecisionResponse,
    SimilarDecisionsAggregateResponse,
)
from contextedge.services.decision_trace_service import (
    create_decision as svc_create_decision,
)
from contextedge.services.decision_trace_service import (
    find_similar_decisions as svc_find_similar,
)
from contextedge.services.decision_trace_service import (
    find_similar_decisions_aggregate as svc_find_similar_aggregate,
)
from contextedge.services.decision_trace_service import (
    get_decision as svc_get_decision,
)
from contextedge.services.decision_trace_service import (
    get_decision_chain as svc_get_chain,
)
from contextedge.services.decision_trace_service import (
    get_decision_effectiveness as svc_get_effectiveness,
)
from contextedge.services.decision_trace_service import (
    get_decision_provenance as svc_get_provenance,
)
from contextedge.services.decision_trace_service import (
    list_decisions as svc_list_decisions,
)
from contextedge.services.decision_trace_service import (
    record_outcome as svc_record_outcome,
)
from contextedge.services.decision_trace_service import (
    reject_decision as svc_reject_decision,
)

router = APIRouter()


@router.get("/similar", response_model=list[DecisionResponse])
async def find_similar_decisions(
    db: DbSession,
    user: AuthUser,
    decision_type: str = Query(...),
    workflow: str | None = None,
    environment: str | None = None,
    impacted_dependency: str | None = None,
    query_decision_id: UUID | None = Query(
        None,
        description=(
            "When set, results are ordered semantically using this decision's "
            "embedding as the query vector; otherwise JSONB containment + "
            "created_at ordering applies."
        ),
    ),
    query_text: str | None = Query(
        None,
        description=(
            "Free-text query embedded on the fly and used as the query vector. "
            "Ignored when query_decision_id is set."
        ),
    ),
    limit: int = Query(10, ge=1, le=50),
):
    ctx: dict[str, str] = {}
    if workflow:
        ctx["workflow"] = workflow
    if environment:
        ctx["environment"] = environment
    if impacted_dependency:
        ctx["impacted_dependency"] = impacted_dependency
    return await svc_find_similar(
        db,
        tenant_id=user.tenant_id,
        decision_type=decision_type,
        context_snapshot=ctx or None,
        query_decision_id=query_decision_id,
        query_text=query_text,
        limit=limit,
    )


@router.get(
    "/similar/aggregate",
    response_model=SimilarDecisionsAggregateResponse,
)
async def find_similar_decisions_with_aggregate(
    db: DbSession,
    user: AuthUser,
    decision_type: str = Query(...),
    workflow: str | None = None,
    environment: str | None = None,
    impacted_dependency: str | None = None,
    query_decision_id: UUID | None = Query(None),
    query_text: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    """Top-N similar decisions + total count + outcome aggregate in one call.

    Powers Zone 5's "based on 143 similar tickets, 87% succeeded" provenance
    line alongside the top few examples — no client-side fan-out across
    `/similar`, `/effectiveness`, and a count endpoint. When `query_decision_id`
    or `query_text` is set, the top-N list is ordered semantically via
    pgvector cosine distance; count and outcomes remain scoped by
    `decision_type` + structural context filters.
    """
    ctx: dict[str, str] = {}
    if workflow:
        ctx["workflow"] = workflow
    if environment:
        ctx["environment"] = environment
    if impacted_dependency:
        ctx["impacted_dependency"] = impacted_dependency
    return await svc_find_similar_aggregate(
        db,
        tenant_id=user.tenant_id,
        decision_type=decision_type,
        context_snapshot=ctx or None,
        query_decision_id=query_decision_id,
        query_text=query_text,
        limit=limit,
    )


@router.get("/effectiveness")
async def get_effectiveness(
    db: DbSession,
    user: AuthUser,
    decision_type: str = Query(...),
    workflow: str | None = None,
    environment: str | None = None,
    impacted_dependency: str | None = None,
):
    ctx: dict[str, str] = {}
    if workflow:
        ctx["workflow"] = workflow
    if environment:
        ctx["environment"] = environment
    if impacted_dependency:
        ctx["impacted_dependency"] = impacted_dependency
    return await svc_get_effectiveness(
        db,
        tenant_id=user.tenant_id,
        decision_type=decision_type,
        context_filters=ctx or None,
    )


@router.get("", response_model=list[DecisionResponse])
async def list_decisions(
    db: DbSession,
    user: AuthUser,
    session_id: UUID | None = None,
    decision_type: str | None = None,
    agent_step: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    actor_type: str | None = Query(None, pattern="^(ai|human|system)$"),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    max_confidence: float | None = Query(None, ge=0.0, le=1.0),
    sort: str = Query(
        "created_desc",
        pattern="^(created_desc|confidence_desc|confidence_asc)$",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        return await svc_list_decisions(
            db,
            tenant_id=user.tenant_id,
            session_id=session_id,
            decision_type=decision_type,
            agent_step=agent_step,
            status=status_filter,
            actor_type=actor_type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=DecisionResponse, status_code=status.HTTP_201_CREATED)
async def create_decision(
    body: DecisionCreate,
    db: DbSession,
    user: AuthUser,
):
    decision = await svc_create_decision(
        db,
        tenant_id=user.tenant_id,
        decision_type=body.decision_type,
        agent_step=body.agent_step,
        actor_type=body.actor_type,
        actor_id=body.actor_id or user.user_id,
        session_id=body.session_id,
        domain_id=body.domain_id,
        parent_decision_id=body.parent_decision_id,
        context_snapshot=body.context_snapshot,
        evidence_refs=[r.model_dump() for r in body.evidence_refs],
        options=[o.model_dump() for o in body.options],
        rationale_summary=body.rationale_summary,
        confidence=body.confidence,
        uncertainty_notes=body.uncertainty_notes,
        compact_trace=body.compact_trace,
        explanation=body.explanation,
        approval_required=body.approval_required,
        policy_refs=body.policy_refs,
        human_override=body.human_override,
        status=body.status,
    )
    return await svc_get_decision(db, tenant_id=user.tenant_id, decision_id=decision.id)


@router.get("/{decision_id}", response_model=DecisionResponse)
async def get_decision(
    decision_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    decision = await svc_get_decision(db, tenant_id=user.tenant_id, decision_id=decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision


@router.post(
    "/{decision_id}/outcome",
    response_model=DecisionOutcomeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_outcome(
    decision_id: UUID,
    body: DecisionOutcomeCreate,
    db: DbSession,
    user: AuthUser,
):
    outcome = await svc_record_outcome(
        db,
        tenant_id=user.tenant_id,
        decision_id=decision_id,
        action_executed=body.action_executed,
        execution_result=body.execution_result,
        result_details=body.result_details,
        follow_up_needed=body.follow_up_needed,
        follow_up_decision_id=body.follow_up_decision_id,
        feedback_received=body.feedback_received,
        feedback_by=body.feedback_by,
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return outcome


@router.get("/{decision_id}/chain", response_model=DecisionChainResponse)
async def get_decision_chain(
    decision_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    chain = await svc_get_chain(db, tenant_id=user.tenant_id, decision_id=decision_id)
    return DecisionChainResponse(decisions=chain)


@router.get(
    "/{decision_id}/provenance",
    response_model=DecisionProvenanceResponse,
)
async def get_decision_provenance(
    decision_id: UUID,
    db: DbSession,
    user: AuthUser,
    evidence_limit: int = Query(20, ge=1, le=100),
    episode_limit: int = Query(10, ge=1, le=50),
    pattern_limit: int = Query(10, ge=1, le=50),
):
    """Hydrate a decision's `based_on` references for Zone 5 drill-in."""
    bundle = await svc_get_provenance(
        db,
        tenant_id=user.tenant_id,
        decision_id=decision_id,
        evidence_limit=evidence_limit,
        episode_limit=episode_limit,
        pattern_limit=pattern_limit,
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return bundle


@router.post(
    "/{decision_id}/reject",
    response_model=DecisionOutcomeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reject_decision(
    decision_id: UUID,
    body: DecisionRejectRequest,
    db: DbSession,
    user: AuthUser,
):
    """Reject an AI-recommended decision with a structured reason code."""
    try:
        outcome = await svc_reject_decision(
            db,
            tenant_id=user.tenant_id,
            decision_id=decision_id,
            code=body.code,
            comment=body.comment,
            actor_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return outcome
