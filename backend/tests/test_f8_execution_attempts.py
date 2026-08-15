"""F8 — retries are first-class and the idempotency key finally does something.

`uq_execution_step_runs_idempotency_key` shipped in 0029 described as "the
single most important banking-grade safety control in the alignment". Nothing
ever wrote the column, so the index guarded a value that was always NULL and
the control was inert. `ExecutionStepRun` also carried one status, so a step
that timed out and was retried overwrote its own history.

The tests below are mostly about what must NOT get a key: re-running a
diagnostic is normal, and a control that suppressed it would be a bug wearing
a safety control's clothes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.attempt import (
    ATTEMPT_STATUSES,
    TERMINAL_ATTEMPT_STATUSES,
    ExecutionAttempt,
)
from contextedge.models.execution import SAFETY_CLASSES
from contextedge.services.idempotency_service import (
    DUPLICATE_CHECK_DUPLICATE,
    DUPLICATE_CHECK_NOT_APPLICABLE,
    DUPLICATE_CHECK_PASSED,
    KEYED_SAFETY_CLASSES,
    derive_idempotency_key,
    needs_idempotency_key,
)

# =========================================================================
# Which steps get a key
# =========================================================================


def test_a_read_only_step_gets_no_key():
    """Re-running a diagnostic is normal and useful. A key that suppressed the
    second status check would be a bug wearing a safety control's clothes."""
    assert needs_idempotency_key("read_only", None) is False
    assert needs_idempotency_key("read_only", "CALLER_KEY") is False
    assert "read_only" not in KEYED_SAFETY_CLASSES


def test_every_side_effecting_class_gets_a_key():
    for safety_class in SAFETY_CLASSES:
        if safety_class == "read_only":
            continue
        assert needs_idempotency_key(safety_class, None) is True


def test_a_natively_idempotent_skill_gets_no_caller_key():
    """The tool is already safe to replay; a caller key would impose a
    suppression the tool did not ask for and the operator did not expect."""
    assert needs_idempotency_key("destructive", "NATIVE") is False
    assert needs_idempotency_key("destructive", "CALLER_KEY") is True
    assert needs_idempotency_key("destructive", "DEDUPE_ONLY") is True


def test_an_unbound_side_effecting_step_still_gets_a_key():
    """Without a contract we cannot know the tool is safe to replay, and the
    conservative answer is the one that suppresses."""
    assert needs_idempotency_key("high_side_effect", None) is True


# =========================================================================
# What makes two executions the same action
# =========================================================================


def test_the_same_action_in_the_same_case_derives_the_same_key():
    tenant, case = uuid.uuid4(), uuid.uuid4()
    args = dict(tenant_id=tenant, scope_id=case, artifact_hash="sha256:abc")
    assert derive_idempotency_key(**args) == derive_idempotency_key(**args)


def test_the_same_action_in_another_case_is_another_action():
    """A different case is a different incident and legitimately does the
    thing again."""
    tenant = uuid.uuid4()
    a = derive_idempotency_key(
        tenant_id=tenant, scope_id=uuid.uuid4(), artifact_hash="sha256:abc"
    )
    b = derive_idempotency_key(
        tenant_id=tenant, scope_id=uuid.uuid4(), artifact_hash="sha256:abc"
    )
    assert a != b


def test_a_changed_payload_is_another_action():
    tenant, case = uuid.uuid4(), uuid.uuid4()
    a = derive_idempotency_key(tenant_id=tenant, scope_id=case, artifact_hash="sha256:aaa")
    b = derive_idempotency_key(tenant_id=tenant, scope_id=case, artifact_hash="sha256:bbb")
    assert a != b


def test_tenants_cannot_collide():
    case = uuid.uuid4()
    a = derive_idempotency_key(
        tenant_id=uuid.uuid4(), scope_id=case, artifact_hash="sha256:abc"
    )
    b = derive_idempotency_key(
        tenant_id=uuid.uuid4(), scope_id=case, artifact_hash="sha256:abc"
    )
    assert a != b


def test_the_key_does_not_leak_tenant_identity():
    """The unique index is global, so other tenants' rows share it. A readable
    key would put tenant ids in a structure they can see the shape of."""
    tenant = uuid.uuid4()
    key = derive_idempotency_key(
        tenant_id=tenant, scope_id=uuid.uuid4(), artifact_hash="sha256:abc"
    )
    assert str(tenant) not in key
    assert key.startswith("idem_")


def test_a_caseless_run_cannot_be_a_duplicate_of_anything():
    """An ad-hoc execution outside a case has no prior occurrence to be a
    duplicate of, so it is scoped to itself."""
    tenant = uuid.uuid4()
    key = derive_idempotency_key(tenant_id=tenant, scope_id=None, artifact_hash="sha256:a")
    same = derive_idempotency_key(tenant_id=tenant, scope_id=None, artifact_hash="sha256:a")
    # Deterministic (so a genuine replay of the same caseless run is caught)…
    assert key == same
    # …but distinct from the same action inside a case.
    assert key != derive_idempotency_key(
        tenant_id=tenant, scope_id=uuid.uuid4(), artifact_hash="sha256:a"
    )


# =========================================================================
# Attempts
# =========================================================================


def test_deduplicated_is_a_terminal_attempt_status():
    """The durable evidence that a replay arrived and was recognised — the
    difference between an idempotency control that works and one nobody can
    prove worked."""
    assert "deduplicated" in ATTEMPT_STATUSES
    assert "deduplicated" in TERMINAL_ATTEMPT_STATUSES
    assert set(TERMINAL_ATTEMPT_STATUSES) < set(ATTEMPT_STATUSES)
    assert "running" not in TERMINAL_ATTEMPT_STATUSES


def test_timeout_and_cancelled_are_distinct_from_failed():
    """A timeout is not a failure — it is an unknown outcome, and conflating
    them tells the retry logic the wrong thing."""
    for status in ("timeout", "cancelled", "failed"):
        assert status in ATTEMPT_STATUSES


@pytest.mark.asyncio
async def test_attempt_numbers_are_derived_not_supplied():
    """A caller cannot renumber history, and a retry lands as N+1 without the
    caller having to know what N was."""
    from contextedge.services.execution_service import _record_attempt

    added: list = []
    counts = iter([0, 1, 2])

    class _Count:
        def scalar_one(self):
            return next(counts)

    db = SimpleNamespace(
        add=added.append, flush=AsyncMock(), execute=AsyncMock(return_value=_Count())
    )
    step = SimpleNamespace(id=uuid.uuid4(), idempotency_key="idem_x")
    tenant_id = uuid.uuid4()

    first = await _record_attempt(db, tenant_id=tenant_id, step=step, status="timeout")
    second = await _record_attempt(db, tenant_id=tenant_id, step=step, status="failed")
    third = await _record_attempt(db, tenant_id=tenant_id, step=step, status="succeeded")

    assert [a.attempt_number for a in (first, second, third)] == [1, 2, 3]
    assert [a.status for a in (first, second, third)] == ["timeout", "failed", "succeeded"]
    # The key travels onto every attempt, so the replay evidence is queryable
    # from the attempt side too.
    assert {a.idempotency_key for a in (first, second, third)} == {"idem_x"}


@pytest.mark.asyncio
async def test_an_unknown_attempt_status_is_refused():
    from contextedge.services.execution_service import ExecutionPolicyError, _record_attempt

    class _Count:
        def scalar_one(self):
            return 0

    db = SimpleNamespace(
        add=lambda _o: None, flush=AsyncMock(), execute=AsyncMock(return_value=_Count())
    )
    with pytest.raises(ExecutionPolicyError, match="attempt status must be one of"):
        await _record_attempt(
            db,
            tenant_id=uuid.uuid4(),
            step=SimpleNamespace(id=uuid.uuid4(), idempotency_key=None),
            status="probably_fine",
        )


@pytest.mark.asyncio
async def test_a_deduplicated_step_may_not_invoke_a_tool():
    """The suppression has to hold at the call site too — flagging a step and
    then letting it run would be a control that only looks like one."""
    from contextedge.services.execution_service import (
        ExecutionPolicyError,
        record_tool_invocation,
    )

    tenant_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        step_index=2,
        duplicate_check_status=DUPLICATE_CHECK_DUPLICATE,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=step))
    with pytest.raises(ExecutionPolicyError, match="recognised as a duplicate"):
        await record_tool_invocation(
            db, tenant_id=tenant_id, step_run_id=step.id, tool_name="restart_service"
        )


# =========================================================================
# The status vocabulary is used, not just declared
# =========================================================================


def test_duplicate_check_vocabulary_is_three_distinct_answers():
    answers = {
        DUPLICATE_CHECK_PASSED,
        DUPLICATE_CHECK_DUPLICATE,
        DUPLICATE_CHECK_NOT_APPLICABLE,
    }
    assert len(answers) == 3


@pytest.mark.asyncio
async def test_start_execution_marks_read_only_steps_not_applicable():
    """Not "passed" — the check did not apply, and an auditor has to be able
    to tell that apart from a check that ran and found nothing."""
    from contextedge.models.playbook import Playbook, PlaybookVersion
    from contextedge.services.execution_service import start_execution

    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    playbook_id, version_id = uuid.uuid4(), uuid.uuid4()
    playbook = SimpleNamespace(
        id=playbook_id, tenant_id=tenant_id, lifecycle_state="approved",
        automation_mode="full_auto", title="Diagnostics", expiry_at=None,
        approval_policy_id=None, domain_id=None,
    )
    version = SimpleNamespace(
        id=version_id, playbook_id=playbook_id, published_at=datetime.now(UTC),
        steps=[{"title": "Check the certificate expiry on vpn-gw-east-01"}],
        semantic_version="1.0.0",
    )
    added: list = []
    store = {(Playbook, playbook_id): playbook, (PlaybookVersion, version_id): version}

    async def _get(model, identity):
        return store.get((model, identity))

    def _add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        added.append(obj)

    class _NoRows:
        # F10's trust-suspension scan runs before the steps are built.
        def scalars(self):
            return SimpleNamespace(all=list)

        def scalar_one(self):
            return 0

        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(
        get=AsyncMock(side_effect=_get),
        add=_add,
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(return_value=_NoRows()),
    )

    with (
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.append_trace_event", AsyncMock()),
        patch("contextedge.services.execution_service.ensure_edge", AsyncMock()),
        patch("contextedge.services.execution_service.create_decision", AsyncMock()),
    ):
        await start_execution(
            db, tenant_id=tenant_id, actor_id=actor_id, roles=["domain_admin"],
            playbook_id=playbook_id, playbook_version_id=version_id,
        )

    from contextedge.models.execution import ExecutionStepRun

    steps = [o for o in added if isinstance(o, ExecutionStepRun)]
    assert len(steps) == 1
    assert steps[0].duplicate_check_status == DUPLICATE_CHECK_NOT_APPLICABLE
    assert steps[0].idempotency_key is None
    assert not [o for o in added if isinstance(o, ExecutionAttempt)]
