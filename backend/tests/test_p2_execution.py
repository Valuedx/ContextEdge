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
