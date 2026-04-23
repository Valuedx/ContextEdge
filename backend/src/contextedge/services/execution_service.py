"""Governed playbook execution: orchestration, safety-class enforcement, approval gates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

from contextedge.models.execution import (
    SAFETY_CLASSES,
    ApprovalRequest,
    ExecutionRun,
    ExecutionStepRun,
    ToolInvocation,
)
from contextedge.graph.builder import ensure_edge
from contextedge.models.playbook import Playbook, PlaybookVersion, is_shadow_mode
from contextedge.services.decision_trace_service import create_decision, record_outcome
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.session_service import append_trace_event


class ExecutionPolicyError(Exception):
    pass


def _safety_class_rank(cls: str) -> int:
    try:
        return SAFETY_CLASSES.index(cls)
    except ValueError:
        return 0


def _caller_max_safety_class(roles: list[str], automation_mode: str) -> str:
    """Derive the maximum safety class the caller can authorise."""
    if automation_mode == "suggest_only":
        return "read_only"
    admin_roles = {"platform_super_admin", "tenant_admin", "domain_admin"}
    if is_shadow_mode(automation_mode):
        # Shadow mode: every tool call is a dry-run, so the cap on real
        # side effects doesn't apply — admins get destructive so the
        # shadow trace mirrors what a real full_auto run would attempt.
        # Non-admins still cap at high_side_effect to keep destructive
        # shadows behind an admin approval.
        if set(roles) & admin_roles:
            return "destructive"
        return "high_side_effect"
    if set(roles) & admin_roles:
        if automation_mode == "full_auto":
            return "destructive"
        return "high_side_effect"
    if "knowledge_manager" in roles:
        return "low_side_effect"
    return "read_only"


async def start_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    roles: list[str],
    playbook_id: uuid.UUID,
    playbook_version_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    requested_max_safety_class: str = "read_only",
) -> ExecutionRun:
    """Create an execution run after enforcing safety-class and lifecycle policy."""
    playbook = await db.get(Playbook, playbook_id)
    if playbook is None or playbook.tenant_id != tenant_id:
        raise ExecutionPolicyError("Playbook not found")
    if playbook.lifecycle_state != "approved":
        raise ExecutionPolicyError(
            f"Cannot execute playbook in '{playbook.lifecycle_state}' state; only 'approved' playbooks may be executed"
        )
    # Review F-12: a playbook that transitioned to approved a long time
    # ago and now has an explicit expiry_at in the past must not be
    # executable. The drift detector already flips such playbooks to a
    # non-approved state on its own schedule, but between drift beats
    # an expired playbook is still ``lifecycle_state = 'approved'``, so
    # check it here too.
    if playbook.expiry_at is not None and playbook.expiry_at < datetime.now(UTC):
        raise ExecutionPolicyError(
            f"Playbook expired at {playbook.expiry_at.isoformat()} — re-validate before executing"
        )

    if playbook_version_id is not None:
        version = await db.get(PlaybookVersion, playbook_version_id)
        if version is None or version.playbook_id != playbook.id:
            raise ExecutionPolicyError("Playbook version not found")
        if version.published_at is None:
            raise ExecutionPolicyError("Cannot execute an unpublished playbook version")
    else:
        result = await db.execute(
            select(PlaybookVersion)
            .where(
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.published_at.is_not(None),
            )
            .order_by(PlaybookVersion.published_at.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise ExecutionPolicyError("No published version found for this playbook")

    caller_max = _caller_max_safety_class(roles, playbook.automation_mode)
    effective_max = min(
        _safety_class_rank(requested_max_safety_class),
        _safety_class_rank(caller_max),
    )
    effective_safety_class = SAFETY_CLASSES[effective_max]

    run = ExecutionRun(
        tenant_id=tenant_id,
        session_id=session_id,
        playbook_id=playbook.id,
        playbook_version_id=version.id,
        initiated_by=actor_id,
        status="running",
        automation_mode=playbook.automation_mode,
        max_safety_class=effective_safety_class,
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()

    steps = version.steps or []
    step_runs: list[ExecutionStepRun] = []
    for idx, step_data in enumerate(steps):
        step_safety = "read_only"
        step_title = None
        needs_approval = False
        if isinstance(step_data, dict):
            step_title = (
                step_data.get("title")
                or step_data.get("text")
                or step_data.get("action")
            )
            step_safety = step_data.get("safety_class", "read_only")
            needs_approval = bool(step_data.get("requires_approval", False))

        if _safety_class_rank(step_safety) > _safety_class_rank(effective_safety_class):
            needs_approval = True

        step_run = ExecutionStepRun(
            execution_run_id=run.id,
            tenant_id=tenant_id,
            step_index=idx,
            step_title=step_title,
            safety_class=step_safety,
            requires_approval=needs_approval,
            status="pending",
            inputs=step_data if isinstance(step_data, dict) else {"raw": str(step_data)},
        )
        db.add(step_run)
        step_runs.append(step_run)

    await db.flush()

    approval_count = 0
    for step_run in step_runs:
        if not step_run.requires_approval:
            continue
        await request_approval(
            db,
            tenant_id=tenant_id,
            execution_run_id=run.id,
            step_run_id=step_run.id,
            requested_by=actor_id,
            requested_action=f"execute_step:{step_run.step_index}",
            safety_class=step_run.safety_class,
            context={
                "playbook_id": str(playbook.id),
                "playbook_version_id": str(version.id),
                "step_index": step_run.step_index,
                "step_title": step_run.step_title,
            },
        )
        approval_count += 1

    await db.refresh(run)

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        entity_type="execution_run",
        entity_id=run.id,
        session_id=session_id,
        event_type="execution.started",
        payload={
            "playbook_id": str(playbook.id),
            "playbook_version_id": str(version.id),
            "automation_mode": playbook.automation_mode,
            "shadow": is_shadow_mode(playbook.automation_mode),
            "max_safety_class": effective_safety_class,
            "step_count": len(steps),
            "approval_request_count": approval_count,
            "status": run.status,
        },
    )

    if session_id is not None:
        await append_trace_event(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="execution_started",
            inputs={
                "playbook_id": str(playbook.id),
                "execution_run_id": str(run.id),
            },
            outputs={
                "step_count": len(steps),
                "approval_request_count": approval_count,
                "status": run.status,
            },
        )
        await ensure_edge(
            db, tenant_id, "session", session_id,
            "playbook", playbook.id, "executed_playbook",
            metadata={
                "execution_run_id": str(run.id),
                "automation_mode": playbook.automation_mode,
            },
        )

    await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type="execute_playbook",
        agent_step="remediation",
        actor_type="human",
        actor_id=actor_id,
        session_id=session_id,
        rationale_summary=f"Initiated execution of playbook {playbook.title or playbook.id}",
        compact_trace=f"Executing playbook {playbook.title or playbook.id} v{version.semantic_version}",
        confidence=None,
        context_snapshot={
            "playbook_id": str(playbook.id),
            "playbook_version_id": str(version.id),
            "execution_run_id": str(run.id),
            "automation_mode": playbook.automation_mode,
            "shadow": is_shadow_mode(playbook.automation_mode),
            "max_safety_class": effective_safety_class,
        },
        status="pending",
    )

    return run


async def get_execution_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_run_id: uuid.UUID,
) -> ExecutionRun | None:
    result = await db.execute(
        select(ExecutionRun)
        .where(
            ExecutionRun.id == execution_run_id,
            ExecutionRun.tenant_id == tenant_id,
        )
        .options(
            selectinload(ExecutionRun.step_runs).selectinload(ExecutionStepRun.tool_invocations),
            selectinload(ExecutionRun.approval_requests),
        )
    )
    return result.scalar_one_or_none()


async def list_execution_runs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    playbook_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    include_details: bool = False,
) -> list[ExecutionRun]:
    """List execution runs; set include_details=True to eager-load step_runs
    and approval_requests (needed when callers serialize the full run into a
    response model outside the request-scoped DB session)."""
    stmt = select(ExecutionRun).where(ExecutionRun.tenant_id == tenant_id)
    if session_id is not None:
        stmt = stmt.where(ExecutionRun.session_id == session_id)
    if playbook_id is not None:
        stmt = stmt.where(ExecutionRun.playbook_id == playbook_id)
    if status is not None:
        stmt = stmt.where(ExecutionRun.status == status)
    stmt = stmt.order_by(ExecutionRun.created_at.desc()).limit(limit)
    if include_details:
        stmt = stmt.options(
            selectinload(ExecutionRun.step_runs).selectinload(ExecutionStepRun.tool_invocations),
            selectinload(ExecutionRun.approval_requests),
        )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def record_step_completion(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    step_run_id: uuid.UUID,
    outputs: dict | None = None,
    error_message: str | None = None,
) -> ExecutionStepRun | None:
    step = await db.get(ExecutionStepRun, step_run_id)
    if step is None or step.tenant_id != tenant_id:
        return None
    now = datetime.now(UTC)
    if error_message:
        step.status = "failed"
        step.error_message = error_message
    else:
        step.status = "completed"
    step.outputs = outputs or {}
    step.completed_at = now
    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="execution_step_run",
        entity_id=step.id,
        event_type=f"execution_step.{step.status}",
        payload={
            "execution_run_id": str(step.execution_run_id),
            "step_index": step.step_index,
            "outputs": step.outputs,
            "error_message": error_message,
        },
    )
    return step


async def record_tool_invocation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    step_run_id: uuid.UUID,
    tool_name: str,
    tool_version: str | None = None,
    safety_class: str = "read_only",
    inputs: dict | None = None,
    outputs: dict | None = None,
    status: str = "completed",
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> ToolInvocation | None:
    step = await db.get(ExecutionStepRun, step_run_id)
    if step is None or step.tenant_id != tenant_id:
        return None

    run = await db.get(ExecutionRun, step.execution_run_id)
    shadow = run is not None and is_shadow_mode(run.automation_mode)

    now = datetime.now(UTC)
    invocation = ToolInvocation(
        step_run_id=step_run_id,
        tenant_id=tenant_id,
        tool_name=tool_name,
        tool_version=tool_version,
        safety_class=safety_class,
        inputs=inputs or {},
        # Shadow outputs are tagged explicitly so analytics can separate
        # real outcomes from dry-run traces when computing success rates.
        outputs={**(outputs or {}), "shadow": True} if shadow else (outputs or {}),
        status="shadow_executed" if shadow else status,
        error_message=error_message,
        duration_ms=duration_ms,
        started_at=now,
        completed_at=now,
    )
    db.add(invocation)
    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="tool_invocation",
        entity_id=invocation.id,
        event_type="tool.shadow_executed" if shadow else f"tool.{status}",
        payload={
            "execution_run_id": str(step.execution_run_id),
            "step_run_id": str(step_run_id),
            "tool_name": tool_name,
            "safety_class": safety_class,
            "duration_ms": duration_ms,
            "shadow": shadow,
        },
    )
    return invocation


async def request_approval(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_run_id: uuid.UUID,
    step_run_id: uuid.UUID | None = None,
    requested_by: uuid.UUID,
    requested_action: str,
    safety_class: str,
    context: dict | None = None,
) -> ApprovalRequest:
    req = ApprovalRequest(
        execution_run_id=execution_run_id,
        step_run_id=step_run_id,
        tenant_id=tenant_id,
        requested_by=requested_by,
        requested_action=requested_action,
        safety_class=safety_class,
        context=context or {},
        status="pending",
    )
    db.add(req)

    run = await db.get(ExecutionRun, execution_run_id)
    if run is not None and run.tenant_id == tenant_id:
        run.status = "awaiting_approval"

    if step_run_id is not None:
        step = await db.get(ExecutionStepRun, step_run_id)
        if step is not None and step.tenant_id == tenant_id:
            step.status = "awaiting_approval"

    await db.flush()
    await db.refresh(req)

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="approval_request",
        entity_id=req.id,
        event_type="approval.requested",
        payload={
            "execution_run_id": str(execution_run_id),
            "step_run_id": str(step_run_id) if step_run_id else None,
            "requested_action": requested_action,
            "safety_class": safety_class,
        },
    )
    return req


async def decide_approval(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    approval_request_id: uuid.UUID,
    decided_by: uuid.UUID,
    decision: str,
    comment: str | None = None,
) -> ApprovalRequest | None:
    # Review F-15: lock the row so two concurrent decide/modify calls
    # on the same approval can't both pass the pending check and both
    # mutate. SELECT ... FOR UPDATE serialises the read-then-write.
    req_result = await db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_request_id)
        .with_for_update()
    )
    req = req_result.scalar_one_or_none()
    if req is None or req.tenant_id != tenant_id:
        return None
    if req.status != "pending":
        raise ExecutionPolicyError(f"Approval request is already '{req.status}'")

    if decision not in ("approved", "denied"):
        raise ExecutionPolicyError("Decision must be 'approved' or 'denied'")

    now = datetime.now(UTC)
    req.status = decision
    req.decided_by = decided_by
    req.decided_at = now
    req.decision_comment = comment

    run = await db.get(ExecutionRun, req.execution_run_id)
    if decision == "denied":
        if run is not None and run.tenant_id == tenant_id:
            run.status = "aborted"
            run.completed_at = now
            run.outcome = "aborted"
            run.outcome_summary = f"Approval denied: {comment or 'no reason given'}"
        if req.step_run_id is not None:
            step = await db.get(ExecutionStepRun, req.step_run_id)
            # Review F-11: the step_run must be verified in-tenant before
            # mutation. `modify_approval` already does this check; this
            # branch previously did not, so a caller with another
            # tenant's approval-request id (obtainable only by guessing
            # a 128-bit UUID, but still a code-level invariant break)
            # could mark a foreign step as failed.
            if step is not None and step.tenant_id == tenant_id:
                step.status = "failed"
                step.error_message = "Approval denied"
                step.completed_at = now
    else:
        if run is not None and run.tenant_id == tenant_id and run.status == "awaiting_approval":
            run.status = "running"
        if req.step_run_id is not None:
            step = await db.get(ExecutionStepRun, req.step_run_id)
            # Mirror the same tenant guard (see F-11 note above).
            if (
                step is not None
                and step.tenant_id == tenant_id
                and step.status == "awaiting_approval"
            ):
                step.status = "running"

    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        actor_id=decided_by,
        entity_type="approval_request",
        entity_id=req.id,
        event_type=f"approval.{decision}",
        payload={
            "execution_run_id": str(req.execution_run_id),
            "step_run_id": str(req.step_run_id) if req.step_run_id else None,
            "comment": comment,
        },
    )

    edge_type = "approved_by" if decision == "approved" else "denied_by"
    await ensure_edge(
        db, tenant_id, "approval_request", req.id,
        "user", decided_by, edge_type,
        metadata={
            "comment": comment,
            "safety_class": req.safety_class,
            "execution_run_id": str(req.execution_run_id),
        },
    )

    session_id = run.session_id if run else None
    await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type=decision,
        agent_step="remediation",
        actor_type="human",
        actor_id=decided_by,
        session_id=session_id,
        rationale_summary=comment or f"Approval {decision}",
        compact_trace=f"Step {decision}: {req.requested_action}",
        approval_required=True,
        context_snapshot={
            "approval_request_id": str(req.id),
            "execution_run_id": str(req.execution_run_id),
            "safety_class": req.safety_class,
            "requested_action": req.requested_action,
        },
        status="completed",
    )

    return req


async def modify_approval(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    approval_request_id: uuid.UUID,
    decided_by: uuid.UUID,
    modification_diff: dict,
    modification_reason_code: str,
    comment: str | None = None,
) -> ApprovalRequest | None:
    """Approve an approval request with modifications to the step's inputs.

    Mirrors `decide_approval` but records the structured diff and reason code
    instead of a plain approve/deny. Treats "modified" as an approved-with-changes
    outcome — the run and step transition to running, and the diff is applied
    to the step's `inputs` JSONB when present.

    Creates a first-class `Decision(decision_type="modify")` with two options:
    the original action (selected=False, rejection_code=<reason>) and the
    modified action (selected=True), so the `considered`/`chose` graph
    invariant holds.
    """
    from contextedge.models.decision import REJECTION_REASON_CODES

    if modification_reason_code not in REJECTION_REASON_CODES:
        raise ExecutionPolicyError(
            f"modification_reason_code must be one of {REJECTION_REASON_CODES}",
        )
    if not isinstance(modification_diff, dict) or not modification_diff:
        raise ExecutionPolicyError("modification_diff must be a non-empty object")

    # Review F-15: row-lock the read so concurrent decide+modify on
    # the same approval can't both pass the pending check.
    req_result = await db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_request_id)
        .with_for_update()
    )
    req = req_result.scalar_one_or_none()
    if req is None or req.tenant_id != tenant_id:
        return None
    if req.status != "pending":
        raise ExecutionPolicyError(f"Approval request is already '{req.status}'")

    now = datetime.now(UTC)
    req.status = "modified"
    req.decided_by = decided_by
    req.decided_at = now
    req.decision_comment = comment
    req.modification_diff = modification_diff
    req.modification_reason_code = modification_reason_code

    run = await db.get(ExecutionRun, req.execution_run_id)
    if run is not None and run.tenant_id == tenant_id and run.status == "awaiting_approval":
        run.status = "running"

    step_run: ExecutionStepRun | None = None
    if req.step_run_id is not None:
        step_run = await db.get(ExecutionStepRun, req.step_run_id)
        if step_run is not None and step_run.tenant_id == tenant_id:
            if step_run.status == "awaiting_approval":
                step_run.status = "running"
            new_inputs = modification_diff.get("inputs")
            if isinstance(new_inputs, dict):
                step_run.inputs = {**(step_run.inputs or {}), **new_inputs}

    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        actor_id=decided_by,
        entity_type="approval_request",
        entity_id=req.id,
        event_type="approval.modified",
        payload={
            "execution_run_id": str(req.execution_run_id),
            "step_run_id": str(req.step_run_id) if req.step_run_id else None,
            "modification_reason_code": modification_reason_code,
            "modification_diff_keys": sorted(modification_diff.keys()),
            "comment": comment,
        },
    )

    await ensure_edge(
        db, tenant_id, "approval_request", req.id,
        "user", decided_by, "modified_by",
        metadata={
            "comment": comment,
            "safety_class": req.safety_class,
            "execution_run_id": str(req.execution_run_id),
            "modification_reason_code": modification_reason_code,
        },
    )

    session_id = run.session_id if run else None
    modified_action = (
        modification_diff.get("summary")
        or f"modified: {req.requested_action}"
    )
    await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type="modify",
        agent_step="remediation",
        actor_type="human",
        actor_id=decided_by,
        session_id=session_id,
        rationale_summary=comment or f"Approval modified ({modification_reason_code})",
        compact_trace=f"Step modified: {req.requested_action}",
        approval_required=True,
        context_snapshot={
            "approval_request_id": str(req.id),
            "execution_run_id": str(req.execution_run_id),
            "safety_class": req.safety_class,
            "requested_action": req.requested_action,
            "modification_reason_code": modification_reason_code,
        },
        options=[
            {
                "action": req.requested_action,
                "selected": False,
                "rejection_code": modification_reason_code,
                "rejection_reason": comment,
            },
            {
                "action": modified_action,
                "selected": True,
            },
        ],
        status="completed",
    )

    return req


async def complete_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_run_id: uuid.UUID,
    outcome: str,
    outcome_summary: str | None = None,
) -> ExecutionRun | None:
    run = await db.get(ExecutionRun, execution_run_id)
    if run is None or run.tenant_id != tenant_id:
        return None
    now = datetime.now(UTC)
    run.status = "completed" if outcome != "aborted" else "aborted"
    run.completed_at = now
    run.outcome = outcome
    run.outcome_summary = outcome_summary
    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="execution_run",
        entity_id=run.id,
        event_type=f"execution.{outcome}",
        payload={
            "outcome": outcome,
            "outcome_summary": outcome_summary,
        },
    )

    if run.session_id is not None:
        await append_trace_event(
            db,
            tenant_id=tenant_id,
            session_id=run.session_id,
            event_type="execution_completed",
            inputs={"execution_run_id": str(run.id)},
            outputs={"outcome": outcome, "outcome_summary": outcome_summary},
        )

    await ensure_edge(
        db, tenant_id, "execution_run", run.id,
        "playbook", run.playbook_id, "execution_outcome",
        metadata={
            "outcome": outcome,
            "outcome_summary": outcome_summary,
        },
    )

    # Prefer the execute_playbook Decision created for this specific run.
    # This avoids attaching outcomes to the wrong decision when multiple playbooks
    # are executed within a single session.
    from contextedge.models.decision import Decision
    exec_decision_res = await db.execute(
        select(Decision)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.decision_type == "execute_playbook",
            Decision.context_snapshot.op("@>")(
                cast({"execution_run_id": str(run.id)}, JSONB_TYPE)
            ),
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    )
    exec_decision = exec_decision_res.scalar_one_or_none()
    if exec_decision is not None:
        await record_outcome(
            db,
            tenant_id=tenant_id,
            decision_id=exec_decision.id,
            action_executed=f"execution_run:{run.id}",
            execution_result=outcome,
            result_details={
                "outcome_summary": outcome_summary,
                "playbook_id": str(run.playbook_id),
            },
        )

    return run


async def abort_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_run_id: uuid.UUID,
    reason: str | None = None,
) -> ExecutionRun | None:
    return await complete_execution(
        db,
        tenant_id=tenant_id,
        execution_run_id=execution_run_id,
        outcome="aborted",
        outcome_summary=reason or "Execution aborted by caller",
    )
