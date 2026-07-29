from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.execution import ApprovalRequest, ExecutionRun
from contextedge.schemas.execution import (
    ApprovalDecision,
    ApprovalModificationRequest,
    ApprovalRequestResponse,
    ExecutionRunResponse,
    StartExecutionRequest,
)
from contextedge.services.execution_service import (
    ExecutionPolicyError,
    abort_execution,
    complete_execution,
    decide_approval,
    get_execution_run,
    list_execution_runs,
    modify_approval,
    record_step_completion,
    record_tool_invocation,
    request_approval,
    start_execution,
)

router = APIRouter()


async def _require_run_control(db, user, run_id: UUID) -> ExecutionRun:
    """Lifecycle mutations (abort/complete) are restricted to the run's
    initiator or a domain admin — not any authenticated user."""
    run = await db.get(ExecutionRun, run_id)
    if run is None or run.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Execution run not found")
    if run.initiated_by != user.user_id and not user.has_role("domain_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the run initiator or a domain admin may change this run",
        )
    return run


async def _require_approval_on_run(db, user, run_id: UUID, approval_id: UUID) -> None:
    """The approval being decided must belong to the run in the URL —
    otherwise any pending approval in the tenant can be decided through
    any run's endpoint."""
    req = await db.get(ApprovalRequest, approval_id)
    if (
        req is None
        or req.tenant_id != user.tenant_id
        or req.execution_run_id != run_id
    ):
        raise HTTPException(
            status_code=404, detail="Approval request not found for this run"
        )


@router.post("/runs", response_model=ExecutionRunResponse, status_code=status.HTTP_201_CREATED)
async def create_execution_run(
    body: StartExecutionRequest,
    db: DbSession,
    user: AuthUser,
):
    """Start governed execution of an approved playbook."""
    try:
        run = await start_execution(
            db,
            tenant_id=user.tenant_id,
            actor_id=user.user_id,
            roles=user.roles,
            playbook_id=body.playbook_id,
            playbook_version_id=body.playbook_version_id,
            session_id=body.session_id,
            requested_max_safety_class=body.max_safety_class,
        )
        await db.commit()
        loaded = await get_execution_run(db, tenant_id=user.tenant_id, execution_run_id=run.id)
        return loaded
    except ExecutionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.get("/runs", response_model=list[ExecutionRunResponse])
async def list_runs(
    db: DbSession,
    user: AuthUser,
    session_id: UUID | None = None,
    playbook_id: UUID | None = None,
    run_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
):
    runs = await list_execution_runs(
        db,
        tenant_id=user.tenant_id,
        session_id=session_id,
        playbook_id=playbook_id,
        status=run_status,
        limit=limit,
    )
    return runs


@router.get("/runs/{run_id}", response_model=ExecutionRunResponse)
async def get_run(
    run_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    run = await get_execution_run(db, tenant_id=user.tenant_id, execution_run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Execution run not found")
    return run


@router.post("/runs/{run_id}/abort", response_model=ExecutionRunResponse)
async def abort_run(
    run_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    """Abort a running execution."""
    await _require_run_control(db, user, run_id)
    run = await abort_execution(
        db, tenant_id=user.tenant_id, execution_run_id=run_id, reason="Aborted by user",
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Execution run not found")
    await db.commit()
    return await get_execution_run(db, tenant_id=user.tenant_id, execution_run_id=run_id)


@router.post("/runs/{run_id}/complete", response_model=ExecutionRunResponse)
async def complete_run(
    run_id: UUID,
    db: DbSession,
    user: AuthUser,
    outcome: str = Query("success"),
    outcome_summary: str | None = None,
):
    """Mark execution as completed with an outcome."""
    await _require_run_control(db, user, run_id)
    try:
        run = await complete_execution(
            db,
            tenant_id=user.tenant_id,
            execution_run_id=run_id,
            outcome=outcome,
            outcome_summary=outcome_summary,
        )
    except ExecutionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="Execution run not found")
    await db.commit()
    return await get_execution_run(db, tenant_id=user.tenant_id, execution_run_id=run_id)


@router.post(
    "/runs/{run_id}/approvals/{approval_id}/decide",
    response_model=ApprovalRequestResponse,
)
async def decide_on_approval(
    run_id: UUID,
    approval_id: UUID,
    body: ApprovalDecision,
    db: DbSession,
    user: AuthUser,
):
    """Approve or deny a pending approval request."""
    user.require_role("domain_admin")
    await _require_approval_on_run(db, user, run_id, approval_id)
    try:
        req = await decide_approval(
            db,
            tenant_id=user.tenant_id,
            approval_request_id=approval_id,
            decided_by=user.user_id,
            decision=body.decision,
            comment=body.comment,
            decider_roles=list(user.roles or []),
        )
    except ExecutionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    await db.commit()
    return req


@router.post(
    "/runs/{run_id}/approvals/{approval_id}/modify",
    response_model=ApprovalRequestResponse,
)
async def modify_on_approval(
    run_id: UUID,
    approval_id: UUID,
    body: ApprovalModificationRequest,
    db: DbSession,
    user: AuthUser,
):
    """Approve a pending approval request with modifications to the step's inputs."""
    user.require_role("domain_admin")
    await _require_approval_on_run(db, user, run_id, approval_id)
    try:
        req = await modify_approval(
            db,
            tenant_id=user.tenant_id,
            approval_request_id=approval_id,
            decided_by=user.user_id,
            modification_diff=body.modification_diff,
            modification_reason_code=body.modification_reason_code,
            comment=body.comment,
            decider_roles=list(user.roles or []),
        )
    except ExecutionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    await db.commit()
    return req


@router.get("/approvals/pending", response_model=list[ApprovalRequestResponse])
async def list_pending_approvals(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
):
    user.require_role("domain_admin")
    result = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id == user.tenant_id,
            ApprovalRequest.status == "pending",
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
