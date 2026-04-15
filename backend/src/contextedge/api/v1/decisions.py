from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.decision import (
    DecisionChainResponse,
    DecisionCreate,
    DecisionOutcomeCreate,
    DecisionOutcomeResponse,
    DecisionResponse,
)
from contextedge.services.decision_trace_service import (
    create_decision as svc_create_decision,
    find_similar_decisions as svc_find_similar,
    get_decision as svc_get_decision,
    get_decision_chain as svc_get_chain,
    get_decision_effectiveness as svc_get_effectiveness,
    list_decisions as svc_list_decisions,
    record_outcome as svc_record_outcome,
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await svc_list_decisions(
        db,
        tenant_id=user.tenant_id,
        session_id=session_id,
        decision_type=decision_type,
        agent_step=agent_step,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


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
