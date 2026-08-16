"""The execution ledger becomes reachable over HTTP.

`record_tool_invocation` and `record_step_completion` carry F7's artifact
binding, F8's duplicate refusal and the attempt ledger — and until now they
had no caller and no route. Every safety control in that chain was therefore
unreachable by any external executor, which is the same shape of gap F1
exists to stop: built, correct, wired to nothing.

These tests are mostly about what the endpoints refuse.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from contextedge.api.v1 import execution as execution_api
from contextedge.schemas.execution import (
    TOOL_INVOCATION_STATUSES,
    StepCompletionRequest,
    ToolInvocationRequest,
)
from contextedge.services.execution_service import ExecutionPolicyError

from .conftest import make_user


def _step(tenant_id, run_id, *, step_id=None, safety_class="read_only", status="running"):
    return SimpleNamespace(
        id=step_id or uuid.uuid4(),
        tenant_id=tenant_id,
        execution_run_id=run_id,
        step_index=0,
        safety_class=safety_class,
        status=status,
        duplicate_check_status=None,
        idempotency_key=None,
    )


def _db(*objects):
    """A `db.get` that answers from the objects it was given, by id."""
    by_id = {obj.id: obj for obj in objects}

    async def get(_model, obj_id):
        return by_id.get(obj_id)

    return SimpleNamespace(get=get, commit=AsyncMock(), flush=AsyncMock())


# =========================================================================
# Scoping — the run id in the URL has to mean something
# =========================================================================


@pytest.mark.asyncio
async def test_a_step_from_another_run_is_not_reachable_through_this_one():
    """Without this, any step in the tenant can be driven through any run's
    endpoint and the run id in the audit trail stops meaning anything."""
    user = make_user(roles=["domain_admin"])
    run_id, other_run_id = uuid.uuid4(), uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(user.tenant_id, other_run_id)

    with pytest.raises(HTTPException) as excinfo:
        await execution_api.record_invocation(
            run_id=run_id, step_run_id=step.id,
            body=ToolInvocationRequest(tool_name="check_disk"),
            db=_db(run, step), user=user,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_another_tenants_step_is_not_reachable_at_all():
    user = make_user(roles=["domain_admin"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(uuid.uuid4(), run_id)  # different tenant

    with pytest.raises(HTTPException) as excinfo:
        await execution_api.complete_step(
            run_id=run_id, step_run_id=step.id, body=StepCompletionRequest(),
            db=_db(run, step), user=user,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_a_bystander_cannot_drive_someone_elses_run():
    """Recording execution is a lifecycle mutation — same gate as abort and
    complete, not "any authenticated caller"."""
    user = make_user(roles=["viewer"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=uuid.uuid4())
    step = _step(user.tenant_id, run_id)

    with pytest.raises(HTTPException) as excinfo:
        await execution_api.record_invocation(
            run_id=run_id, step_run_id=step.id,
            body=ToolInvocationRequest(tool_name="check_disk"),
            db=_db(run, step), user=user,
        )
    assert excinfo.value.status_code == 403


# =========================================================================
# The refusals the service owns, surfaced as 409 rather than 500
# =========================================================================


@pytest.mark.asyncio
async def test_a_refused_invocation_is_a_conflict_not_a_crash():
    """A duplicate replay and a stale approval binding are both well-formed
    requests that the state refuses — the caller needs to tell that apart
    from a bug, and from a bad request it could fix."""
    user = make_user(roles=["domain_admin"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(user.tenant_id, run_id)

    with patch.object(
        execution_api, "record_tool_invocation",
        AsyncMock(side_effect=ExecutionPolicyError("recognised as a duplicate")),
    ), pytest.raises(HTTPException) as excinfo:
        await execution_api.record_invocation(
            run_id=run_id, step_run_id=step.id,
            body=ToolInvocationRequest(tool_name="restart_service"),
            db=_db(run, step), user=user,
        )
    assert excinfo.value.status_code == 409
    assert "duplicate" in excinfo.value.detail


@pytest.mark.asyncio
async def test_completing_a_step_awaiting_approval_is_refused():
    user = make_user(roles=["domain_admin"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(user.tenant_id, run_id, status="awaiting_approval")

    with patch.object(
        execution_api, "record_step_completion",
        AsyncMock(side_effect=ExecutionPolicyError("Step is awaiting approval")),
    ), pytest.raises(HTTPException) as excinfo:
        await execution_api.complete_step(
            run_id=run_id, step_run_id=step.id, body=StepCompletionRequest(),
            db=_db(run, step), user=user,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_a_recorded_invocation_is_committed():
    user = make_user(roles=["domain_admin"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(user.tenant_id, run_id)
    db = _db(run, step)
    invocation = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        execution_api, "record_tool_invocation", AsyncMock(return_value=invocation)
    ):
        result = await execution_api.record_invocation(
            run_id=run_id, step_run_id=step.id,
            body=ToolInvocationRequest(tool_name="check_disk"),
            db=db, user=user,
        )
    assert result is invocation
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_completed_step_is_returned_with_its_invocations_loaded():
    user = make_user(roles=["domain_admin"])
    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=user.tenant_id, initiated_by=user.user_id)
    step = _step(user.tenant_id, run_id)
    db = _db(run, step)
    loaded = SimpleNamespace(id=step.id, tool_invocations=[])

    with patch.object(execution_api, "record_step_completion", AsyncMock(return_value=step)), \
         patch.object(execution_api, "get_step_run", AsyncMock(return_value=loaded)) as loader:
        result = await execution_api.complete_step(
            run_id=run_id, step_run_id=step.id,
            body=StepCompletionRequest(outputs={"exit_code": 0}), db=db, user=user,
        )
    assert result is loaded
    db.commit.assert_awaited_once()
    loader.assert_awaited_once()


def test_the_step_response_is_built_from_an_eager_load():
    """`ExecutionStepRunResponse` embeds `tool_invocations`. On an async
    session a lazily-loaded relationship raises `MissingGreenlet` from inside
    the serializer — a failure this suite cannot reproduce, because it runs
    without a live Postgres. So the loader is pinned instead."""
    import inspect

    from contextedge.services.execution_service import get_step_run

    assert "selectinload" in inspect.getsource(get_step_run)
    assert "tool_invocations" in set(
        __import__("contextedge.schemas.execution", fromlist=["x"])
        .ExecutionStepRunResponse.model_fields
    )


# =========================================================================
# The request body
# =========================================================================


def test_the_caller_cannot_declare_an_attempt_number_or_an_idempotency_key():
    """Both are derived from what is already recorded. A caller that can
    renumber history, or hand in the key the duplicate check tests against,
    can defeat the control by asserting the answer."""
    fields = set(ToolInvocationRequest.model_fields)
    assert "attempt_number" not in fields
    assert "idempotency_key" not in fields


def test_a_status_the_attempt_ledger_cannot_store_is_rejected_at_the_edge():
    """`execution_attempts.status` has a CHECK constraint, so an unvalidated
    status reaches the database as a 500 instead of a 422."""
    with pytest.raises(ValueError):
        ToolInvocationRequest(tool_name="x", status="banana")
    assert "running" not in TOOL_INVOCATION_STATUSES
    assert "deduplicated" not in TOOL_INVOCATION_STATUSES


def test_step_completion_has_no_status_field():
    """"completed with an error_message" and "failed with none" are both
    incoherent; a body that can express them invites a self-contradicting
    ledger."""
    assert "status" not in set(StepCompletionRequest.model_fields)


# =========================================================================
# A call cannot out-rank the step it belongs to
# =========================================================================


@pytest.mark.asyncio
async def test_an_invocation_may_not_exceed_its_steps_authorised_class():
    """The step's class is what policy, the approval gate and the caller's
    own max_safety_class were evaluated against. A destructive call recorded
    under a read_only step leaves every upstream control reading as
    satisfied."""
    from contextedge.services.execution_service import record_tool_invocation

    tenant_id = uuid.uuid4()
    step = _step(tenant_id, uuid.uuid4())
    db = _db(step)

    with pytest.raises(ExecutionPolicyError) as excinfo:
        await record_tool_invocation(
            db, tenant_id=tenant_id, step_run_id=step.id,
            tool_name="delete_user", safety_class="destructive",
        )
    assert "exceeds" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_milder_call_than_the_step_allows_is_fine():
    """The rule is a ceiling, not an equality — a destructive step that only
    ran a read-only probe has not done anything wrong."""
    from contextedge.services.execution_service import record_tool_invocation

    tenant_id = uuid.uuid4()
    step = _step(tenant_id, uuid.uuid4(), safety_class="destructive")
    db = _db(step)
    db.add = lambda row: None

    with patch("contextedge.services.execution_service.assert_approved_artifact_unchanged",
               AsyncMock(return_value=None)), \
         patch("contextedge.services.execution_service._record_attempt", AsyncMock()), \
         patch("contextedge.services.execution_service.append_operational_event", AsyncMock()):
        invocation = await record_tool_invocation(
            db, tenant_id=tenant_id, step_run_id=step.id,
            tool_name="check_disk", safety_class="read_only",
        )
    assert invocation.safety_class == "read_only"


@pytest.mark.asyncio
async def test_an_unknown_safety_class_is_refused_rather_than_ranked_low():
    """`_safety_class_rank` fails closed; this pins that the invocation path
    inherits that behaviour instead of treating a typo as read_only."""
    from contextedge.services.execution_service import record_tool_invocation

    tenant_id = uuid.uuid4()
    step = _step(tenant_id, uuid.uuid4())
    with pytest.raises(ExecutionPolicyError):
        await record_tool_invocation(
            db=_db(step), tenant_id=tenant_id, step_run_id=step.id,
            tool_name="x", safety_class="mostly_harmless",
        )
