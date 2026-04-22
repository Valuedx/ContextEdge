"""Tests for P2a: governed execution, safety-class enforcement, approval gates."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.execution import ApprovalRequest, ExecutionStepRun, ExecutionRun, SAFETY_CLASSES
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.services.execution_service import (
    ExecutionPolicyError,
    _caller_max_safety_class,
    _safety_class_rank,
    decide_approval,
    modify_approval,
    start_execution,
)


def test_safety_class_ordering():
    assert _safety_class_rank("read_only") < _safety_class_rank("low_side_effect")
    assert _safety_class_rank("low_side_effect") < _safety_class_rank("high_side_effect")
    assert _safety_class_rank("high_side_effect") < _safety_class_rank("destructive")


def test_caller_max_safety_suggest_only():
    assert _caller_max_safety_class(["platform_super_admin"], "suggest_only") == "read_only"
    assert _caller_max_safety_class(["analyst"], "suggest_only") == "read_only"


def test_caller_max_safety_supervised():
    assert _caller_max_safety_class(["domain_admin"], "supervised") == "high_side_effect"
    assert _caller_max_safety_class(["knowledge_manager"], "supervised") == "low_side_effect"
    assert _caller_max_safety_class(["analyst"], "supervised") == "read_only"


def test_caller_max_safety_full_auto():
    assert _caller_max_safety_class(["tenant_admin"], "full_auto") == "destructive"
    assert _caller_max_safety_class(["analyst"], "full_auto") == "read_only"


@pytest.mark.asyncio
async def test_start_execution_rejects_unapproved_playbook():
    tenant_id = uuid4()
    playbook_id = uuid4()

    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="candidate",
        automation_mode="suggest_only",
    )

    db = SimpleNamespace(
        get=AsyncMock(return_value=playbook),
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with pytest.raises(ExecutionPolicyError, match="only 'approved' playbooks"):
        await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=uuid4(),
            roles=["domain_admin"],
            playbook_id=playbook_id,
        )


@pytest.mark.asyncio
async def test_start_execution_rejects_missing_playbook():
    tenant_id = uuid4()
    db = SimpleNamespace(
        get=AsyncMock(return_value=None),
    )

    with pytest.raises(ExecutionPolicyError, match="not found"):
        await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=uuid4(),
            roles=["domain_admin"],
            playbook_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_decide_approval_rejects_invalid_decision():
    tenant_id = uuid4()
    req_id = uuid4()
    approval = SimpleNamespace(
        id=req_id,
        tenant_id=tenant_id,
        status="pending",
        execution_run_id=uuid4(),
        step_run_id=None,
    )

    db = SimpleNamespace(
        get=AsyncMock(return_value=approval),
        flush=AsyncMock(),
    )

    with pytest.raises(ExecutionPolicyError, match="approved.*denied"):
        await decide_approval(
            db,
            tenant_id=tenant_id,
            approval_request_id=req_id,
            decided_by=uuid4(),
            decision="maybe",
        )


@pytest.mark.asyncio
async def test_decide_approval_rejects_already_decided():
    tenant_id = uuid4()
    req_id = uuid4()
    approval = SimpleNamespace(
        id=req_id,
        tenant_id=tenant_id,
        status="approved",
        execution_run_id=uuid4(),
        step_run_id=None,
    )

    db = SimpleNamespace(
        get=AsyncMock(return_value=approval),
    )

    with pytest.raises(ExecutionPolicyError, match="already"):
        await decide_approval(
            db,
            tenant_id=tenant_id,
            approval_request_id=req_id,
            decided_by=uuid4(),
            decision="denied",
        )


@pytest.mark.asyncio
async def test_start_execution_creates_pending_approval_for_gated_step():
    tenant_id = uuid4()
    actor_id = uuid4()
    playbook_id = uuid4()
    version_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        automation_mode="full_auto",
        title="Reimage Host Playbook",
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=datetime.now(timezone.utc),
        steps=[{"title": "Reimage host", "safety_class": "destructive"}],
        semantic_version="1.0.0",
    )

    added: list[object] = []
    store: dict[tuple[type, object], object] = {
        (Playbook, playbook_id): playbook,
        (PlaybookVersion, version_id): version,
    }

    async def get_side_effect(model, identity):
        return store.get((model, identity))

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        added.append(obj)
        store[(obj.__class__, obj.id)] = obj

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get_side_effect),
        add=add,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.append_trace_event", AsyncMock()),
        patch("contextedge.services.execution_service.create_decision", AsyncMock()),
    ):
        run = await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=["domain_admin"],
            playbook_id=playbook_id,
            playbook_version_id=version_id,
            requested_max_safety_class="high_side_effect",
        )

    assert isinstance(run, ExecutionRun)
    assert run.status == "awaiting_approval"
    step_runs = [obj for obj in added if isinstance(obj, ExecutionStepRun)]
    approvals = [obj for obj in added if isinstance(obj, ApprovalRequest)]
    assert len(step_runs) == 1
    assert step_runs[0].requires_approval is True
    assert step_runs[0].status == "awaiting_approval"
    assert len(approvals) == 1
    assert approvals[0].execution_run_id == run.id
    assert approvals[0].step_run_id == step_runs[0].id
    assert approvals[0].status == "pending"


# =========================================================================
# A3 — modify_approval (approve-with-changes)
# =========================================================================


@pytest.mark.asyncio
async def test_modify_approval_rejects_invalid_code():
    """An unknown modification_reason_code raises ExecutionPolicyError."""
    tenant_id = uuid4()
    approval = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, status="pending",
        execution_run_id=uuid4(), step_run_id=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=approval))

    with pytest.raises(ExecutionPolicyError, match="modification_reason_code"):
        await modify_approval(
            db,
            tenant_id=tenant_id,
            approval_request_id=approval.id,
            decided_by=uuid4(),
            modification_diff={"inputs": {"x": 1}},
            modification_reason_code="not_a_real_code",
        )


@pytest.mark.asyncio
async def test_modify_approval_rejects_empty_diff():
    """An empty diff raises ExecutionPolicyError."""
    tenant_id = uuid4()
    approval = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, status="pending",
        execution_run_id=uuid4(), step_run_id=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=approval))

    with pytest.raises(ExecutionPolicyError, match="non-empty"):
        await modify_approval(
            db,
            tenant_id=tenant_id,
            approval_request_id=approval.id,
            decided_by=uuid4(),
            modification_diff={},
            modification_reason_code="plan_incomplete",
        )


@pytest.mark.asyncio
async def test_modify_approval_rejects_already_decided():
    tenant_id = uuid4()
    approval = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, status="approved",
        execution_run_id=uuid4(), step_run_id=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=approval))

    with pytest.raises(ExecutionPolicyError, match="already"):
        await modify_approval(
            db,
            tenant_id=tenant_id,
            approval_request_id=approval.id,
            decided_by=uuid4(),
            modification_diff={"inputs": {"x": 1}},
            modification_reason_code="plan_incomplete",
        )


@pytest.mark.asyncio
async def test_modify_approval_returns_none_for_missing():
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    result = await modify_approval(
        db,
        tenant_id=uuid4(),
        approval_request_id=uuid4(),
        decided_by=uuid4(),
        modification_diff={"inputs": {"x": 1}},
        modification_reason_code="plan_incomplete",
    )
    assert result is None


@pytest.mark.asyncio
@patch("contextedge.services.execution_service.create_decision", new_callable=AsyncMock)
@patch("contextedge.services.execution_service.ensure_edge", new_callable=AsyncMock)
@patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock)
async def test_modify_approval_happy_path(mock_op_event, mock_edge, mock_create_decision):
    """Modify flips approval to modified, merges inputs into step, emits modified_by
    edge, and creates a Decision(decision_type='modify') with two options."""
    tenant_id = uuid4()
    approval_id = uuid4()
    run_id = uuid4()
    step_run_id = uuid4()
    decider = uuid4()
    session_id = uuid4()

    approval = ApprovalRequest(
        id=approval_id,
        tenant_id=tenant_id,
        execution_run_id=run_id,
        step_run_id=step_run_id,
        requested_by=uuid4(),
        requested_action="Renew certificate via internal CA",
        safety_class="medium_side_effect",
        context={},
        status="pending",
    )
    run = ExecutionRun(
        id=run_id,
        tenant_id=tenant_id,
        playbook_id=uuid4(),
        playbook_version_id=uuid4(),
        initiated_by=uuid4(),
        status="awaiting_approval",
        automation_mode="supervised",
        max_safety_class="high_side_effect",
        session_id=session_id,
    )
    step = ExecutionStepRun(
        id=step_run_id,
        execution_run_id=run_id,
        tenant_id=tenant_id,
        step_index=0,
        step_title="Renew cert",
        safety_class="medium_side_effect",
        requires_approval=True,
        status="awaiting_approval",
        inputs={"ttl_days": 90},
        outputs={},
    )

    async def _get(model, *args, **kwargs):
        if model is ApprovalRequest:
            return approval
        if model is ExecutionRun:
            return run
        if model is ExecutionStepRun:
            return step
        return None

    db = SimpleNamespace(get=_get, flush=AsyncMock())

    req = await modify_approval(
        db,
        tenant_id=tenant_id,
        approval_request_id=approval_id,
        decided_by=decider,
        modification_diff={"inputs": {"ttl_days": 30, "notify": True}, "summary": "shorter ttl"},
        modification_reason_code="plan_incomplete",
        comment="per cert policy",
    )

    assert req is approval
    assert approval.status == "modified"
    assert approval.decided_by == decider
    assert approval.modification_diff == {
        "inputs": {"ttl_days": 30, "notify": True},
        "summary": "shorter ttl",
    }
    assert approval.modification_reason_code == "plan_incomplete"
    assert approval.decision_comment == "per cert policy"

    assert run.status == "running"
    assert step.status == "running"
    assert step.inputs == {"ttl_days": 30, "notify": True}

    mock_op_event.assert_awaited_once()
    event_payload = mock_op_event.call_args.kwargs.get("payload", {})
    assert event_payload["modification_reason_code"] == "plan_incomplete"
    assert sorted(event_payload["modification_diff_keys"]) == ["inputs", "summary"]

    mock_edge.assert_awaited_once()
    edge_args = mock_edge.call_args.args
    assert edge_args[2] == "approval_request"
    assert edge_args[4] == "user"
    assert edge_args[6] == "modified_by"

    mock_create_decision.assert_awaited_once()
    dec_kwargs = mock_create_decision.call_args.kwargs
    assert dec_kwargs["decision_type"] == "modify"
    assert dec_kwargs["actor_type"] == "human"
    assert dec_kwargs["session_id"] == session_id
    options = dec_kwargs["options"]
    assert len(options) == 2
    original = next(o for o in options if not o["selected"])
    modified = next(o for o in options if o["selected"])
    assert original["rejection_code"] == "plan_incomplete"
    assert original["action"] == "Renew certificate via internal CA"
    assert modified["action"] == "shorter ttl"


# =========================================================================
# ApprovalModificationRequest schema
# =========================================================================


def test_approval_modification_request_accepts_valid_payload():
    from contextedge.schemas.execution import ApprovalModificationRequest

    body = ApprovalModificationRequest(
        modification_diff={"inputs": {"x": 1}},
        modification_reason_code="plan_incomplete",
        comment="shorter ttl",
    )
    assert body.modification_reason_code == "plan_incomplete"


def test_approval_modification_request_rejects_empty_diff():
    from contextedge.schemas.execution import ApprovalModificationRequest

    with pytest.raises(ValueError, match="non-empty"):
        ApprovalModificationRequest(
            modification_diff={},
            modification_reason_code="plan_incomplete",
        )


def test_approval_modification_request_rejects_invalid_code():
    from contextedge.schemas.execution import ApprovalModificationRequest

    with pytest.raises(ValueError):
        ApprovalModificationRequest(
            modification_diff={"inputs": {}},
            modification_reason_code="bogus",
        )


# ---------------------------------------------------------------------------
# Shadow automation_mode (W5-6.1)
# ---------------------------------------------------------------------------


def test_caller_max_safety_shadow_admin_gets_destructive():
    """Shadow mode lets admins attempt destructive actions since nothing
    real executes — the whole point is to surface what a full_auto run
    would do without causing side effects."""
    assert _caller_max_safety_class(["tenant_admin"], "shadow") == "destructive"
    assert _caller_max_safety_class(["platform_super_admin"], "shadow") == "destructive"
    assert _caller_max_safety_class(["domain_admin"], "shadow") == "destructive"


def test_caller_max_safety_shadow_nonadmin_capped_at_high_side_effect():
    """Non-admins can still shadow through high_side_effect — destructive
    shadows stay gated behind an admin role so a sales rep can't dry-run
    `delete-prod-db` without explicit clearance."""
    assert _caller_max_safety_class(["knowledge_manager"], "shadow") == "high_side_effect"
    assert _caller_max_safety_class(["analyst"], "shadow") == "high_side_effect"


def test_is_shadow_mode_helper():
    from contextedge.models.playbook import is_shadow_mode

    assert is_shadow_mode("shadow") is True
    assert is_shadow_mode("suggest_only") is False
    assert is_shadow_mode("full_auto") is False
    assert is_shadow_mode(None) is False
    assert is_shadow_mode("") is False


def test_automation_modes_constant_contains_shadow():
    from contextedge.models.playbook import AUTOMATION_MODES

    assert "shadow" in AUTOMATION_MODES
    # Ordering matters — code reasons about monotonic permissiveness.
    assert AUTOMATION_MODES.index("suggest_only") < AUTOMATION_MODES.index("shadow")
    assert AUTOMATION_MODES.index("shadow") < AUTOMATION_MODES.index("full_auto")


def test_playbook_create_accepts_shadow_and_rejects_garbage():
    from contextedge.schemas.playbook import PlaybookCreate

    ok = PlaybookCreate(title="t", automation_mode="shadow")
    assert ok.automation_mode == "shadow"

    with pytest.raises(ValueError, match="automation_mode must be one of"):
        PlaybookCreate(title="t", automation_mode="silent_mode")


def test_playbook_update_accepts_none_and_shadow():
    from contextedge.schemas.playbook import PlaybookUpdate

    # None passthrough — lets callers PATCH without touching automation.
    assert PlaybookUpdate(automation_mode=None).automation_mode is None
    assert PlaybookUpdate(automation_mode="shadow").automation_mode == "shadow"

    with pytest.raises(ValueError, match="automation_mode must be one of"):
        PlaybookUpdate(automation_mode="nope")


@pytest.mark.asyncio
async def test_record_tool_invocation_under_shadow_tags_outputs_and_status():
    """When the parent run is in shadow mode, record_tool_invocation must
    tag outputs with ``shadow=True``, force status to ``shadow_executed``,
    and fire a ``tool.shadow_executed`` operational event."""
    from contextedge.services.execution_service import record_tool_invocation

    tenant_id = uuid4()
    step_run_id = uuid4()
    execution_run_id = uuid4()

    step = SimpleNamespace(
        id=step_run_id, tenant_id=tenant_id, execution_run_id=execution_run_id,
    )
    run = SimpleNamespace(id=execution_run_id, automation_mode="shadow")

    async def get(model, obj_id):
        # First call returns step, second returns the run.
        if obj_id == step_run_id:
            return step
        return run

    captured: dict = {}

    async def fake_append(db, **kwargs):
        captured["event"] = kwargs

    captured_add: list = []
    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        add=lambda obj: captured_add.append(obj),
        flush=AsyncMock(),
    )

    with patch(
        "contextedge.services.execution_service.append_operational_event",
        new=AsyncMock(side_effect=fake_append),
    ):
        invocation = await record_tool_invocation(
            db,
            tenant_id=tenant_id,
            step_run_id=step_run_id,
            tool_name="delete_user",
            safety_class="destructive",
            outputs={"deleted_count": 5},
        )

    assert invocation is not None
    assert invocation.status == "shadow_executed"
    assert invocation.outputs["shadow"] is True
    assert invocation.outputs["deleted_count"] == 5
    assert captured["event"]["event_type"] == "tool.shadow_executed"
    assert captured["event"]["payload"]["shadow"] is True


@pytest.mark.asyncio
async def test_record_tool_invocation_non_shadow_unchanged():
    """Non-shadow runs keep the exact prior behavior — no ``shadow`` key
    leaks into outputs, status is whatever the caller passed."""
    from contextedge.services.execution_service import record_tool_invocation

    tenant_id = uuid4()
    step_run_id = uuid4()
    execution_run_id = uuid4()
    step = SimpleNamespace(
        id=step_run_id, tenant_id=tenant_id, execution_run_id=execution_run_id,
    )
    run = SimpleNamespace(id=execution_run_id, automation_mode="full_auto")

    async def get(model, obj_id):
        return step if obj_id == step_run_id else run

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        add=lambda obj: None,
        flush=AsyncMock(),
    )

    captured: dict = {}

    async def fake_append(db, **kwargs):
        captured["event"] = kwargs

    with patch(
        "contextedge.services.execution_service.append_operational_event",
        new=AsyncMock(side_effect=fake_append),
    ):
        invocation = await record_tool_invocation(
            db,
            tenant_id=tenant_id,
            step_run_id=step_run_id,
            tool_name="ping",
            outputs={"rtt_ms": 12},
        )

    assert invocation.status == "completed"
    assert "shadow" not in invocation.outputs
    assert captured["event"]["event_type"] == "tool.completed"
    assert captured["event"]["payload"]["shadow"] is False
