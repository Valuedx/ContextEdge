from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.execution import (
    ApprovalDecision,
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
    record_step_completion,
    record_tool_invocation,
    request_approval,
    start_execution,
)

router = APIRouter()


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
    run = await complete_execution(
        db,
        tenant_id=user.tenant_id,
        execution_run_id=run_id,
        outcome=outcome,
        outcome_summary=outcome_summary,
    )
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
    try:
        req = await decide_approval(
            db,
            tenant_id=user.tenant_id,
            approval_request_id=approval_id,
            decided_by=user.user_id,
            decision=body.decision,
            comment=body.comment,
        )
    except ExecutionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    await db.commit()
    return req
