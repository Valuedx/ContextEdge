"""F3 — the policy that runs is versioned, and every evaluation is recorded.

`approval_policy_service` enforces real rules and left no trace, so "which
policy version evaluated this, and what did it see?" had no answer for the
engine that actually runs. These tests pin the version semantics (rules, not
labels), the recording at both enforcement points, and the property that makes
the recording safe to add: it can never turn an allowed action into a failed
one.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.policy import POLICY_CHECK_RESULTS, PolicyCheck, TenantPolicy
from contextedge.services.approval_policy_service import ApprovalPolicy
from contextedge.services.policy_check_service import record_policy_check


def _db():
    added: list = []
    return SimpleNamespace(add=added.append, flush=AsyncMock()), added


# =========================================================================
# The recorder
# =========================================================================


@pytest.mark.asyncio
async def test_records_the_policy_version_not_just_the_policy():
    """Keyed to the version so a later edit cannot rewrite the history of
    what a run was judged under."""
    db, added = _db()
    policy_id = uuid.uuid4()
    row = await record_policy_check(
        db,
        tenant_id=uuid.uuid4(),
        policy_id=policy_id,
        policy_version=4,
        policy_type="approval",
        check_name="max_automation_mode",
        evaluated_entity_type="playbook",
        evaluated_entity_id=uuid.uuid4(),
        result="pass",
        input_snapshot={"requested_automation_mode": "supervised"},
    )
    assert isinstance(row, PolicyCheck)
    assert row.policy_id == policy_id
    assert row.policy_version == 4
    assert row.input_snapshot == {"requested_automation_mode": "supervised"}
    assert added == [row]


@pytest.mark.asyncio
async def test_an_unconfigured_policy_still_records_not_applicable():
    """"No rule applied" and "no check ran" are different answers, and an
    auditor has to be able to tell them apart."""
    db, added = _db()
    row = await record_policy_check(
        db,
        tenant_id=uuid.uuid4(),
        policy_id=None,
        policy_version=None,
        policy_type="approval",
        check_name="decider",
        evaluated_entity_type="approval_request",
        evaluated_entity_id=uuid.uuid4(),
        result="not_applicable",
    )
    assert row is not None
    assert row.result == "not_applicable"
    assert row.policy_id is None and row.policy_version is None
    assert len(added) == 1


@pytest.mark.asyncio
async def test_an_unknown_result_is_rejected_at_the_boundary():
    db, _ = _db()
    with pytest.raises(ValueError, match="result must be one of"):
        await record_policy_check(
            db,
            tenant_id=uuid.uuid4(),
            policy_id=None,
            policy_version=None,
            policy_type="approval",
            check_name="decider",
            evaluated_entity_type="approval_request",
            evaluated_entity_id=None,
            result="probably_fine",
        )
    assert set(POLICY_CHECK_RESULTS) == {"pass", "fail", "not_applicable"}


@pytest.mark.asyncio
async def test_a_broken_audit_write_never_breaks_the_action():
    """The gate has already decided by the time this runs. Additive evidence
    must not turn an allowed action into a failed one."""
    db = SimpleNamespace(add=lambda _o: None, flush=AsyncMock(side_effect=RuntimeError("db gone")))
    row = await record_policy_check(
        db,
        tenant_id=uuid.uuid4(),
        policy_id=None,
        policy_version=None,
        policy_type="approval",
        check_name="decider",
        evaluated_entity_type="approval_request",
        evaluated_entity_id=None,
        result="pass",
    )
    assert row is None


# =========================================================================
# Version semantics
# =========================================================================


def test_version_defaults_to_one_and_is_carried_onto_the_loaded_policy():
    policy = TenantPolicy(tenant_id=uuid.uuid4(), policy_type="approval", name="p")
    assert policy.version == 1 or policy.version is None  # server_default applies on flush
    assert ApprovalPolicy(policy_id=uuid.uuid4(), version=7).version == 7


@pytest.mark.asyncio
async def test_load_approval_policy_carries_the_row_version():
    from contextedge.services import approval_policy_service

    tenant_id, policy_id = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        tenant_id=tenant_id,
        policy_type="approval",
        is_active=True,
        config={"approver_roles": ["sre_lead"]},
        version=3,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=row))
    policy = await approval_policy_service.load_approval_policy(db, tenant_id, policy_id)
    assert policy.version == 3


def test_the_version_tracks_rules_not_labels():
    """Renaming or deactivating a policy does not change what a past decision
    was judged under; changing its config does."""
    import inspect

    from contextedge.api.v1 import policies

    source = inspect.getsource(policies.update_policy)
    config_branch = source.split("if body.config is not None:")[1]
    assert "row.version = (row.version or 1) + 1" in config_branch
    name_branch = source.split("if body.name is not None:")[1].split("if body.description")[0]
    assert "version" not in name_branch


# =========================================================================
# Enforcement points record
# =========================================================================


def _execution_harness(*, automation_mode="full_auto", approval_policy_id=None):
    from contextedge.models.playbook import Playbook, PlaybookVersion

    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    playbook_id, version_id = uuid.uuid4(), uuid.uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        automation_mode=automation_mode,
        title="Ordering Service Playbook",
        expiry_at=None,
        approval_policy_id=approval_policy_id,
        domain_id=None,
    )
    from datetime import UTC, datetime

    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=datetime.now(UTC),
        steps=[{"title": "Renew the gateway certificate"}],
        semantic_version="1.0.0",
    )
    added: list = []
    store = {(Playbook, playbook_id): playbook, (PlaybookVersion, version_id): version}

    async def get_side_effect(model, identity):
        return store.get((model, identity))

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        added.append(obj)
        store[(obj.__class__, obj.id)] = obj

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get_side_effect),
        add=add,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    return db, added, tenant_id, actor_id, playbook_id, version_id


@pytest.mark.asyncio
async def test_start_execution_records_the_automation_mode_check():
    from contextedge.services.execution_service import start_execution

    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        automation_mode="supervised", approval_policy_id=uuid.uuid4()
    )
    policy = ApprovalPolicy(
        policy_id=uuid.uuid4(), max_automation_mode="full_auto", version=2
    )
    with (
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.append_trace_event", AsyncMock()),
        patch("contextedge.services.execution_service.ensure_edge", AsyncMock()),
        patch("contextedge.services.execution_service.create_decision", AsyncMock()),
        patch(
            "contextedge.services.execution_service.load_approval_policy",
            AsyncMock(return_value=policy),
        ),
    ):
        await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=["domain_admin"],
            playbook_id=pb_id,
            playbook_version_id=v_id,
            requested_max_safety_class="read_only",
        )

    checks = [obj for obj in added if isinstance(obj, PolicyCheck)]
    assert len(checks) == 1
    check = checks[0]
    assert check.check_name == "max_automation_mode"
    assert check.result == "pass"
    assert check.policy_version == 2
    # Anchored to the playbook: the run row does not exist yet at gate time.
    assert check.evaluated_entity_type == "playbook"
    assert check.evaluated_entity_id == pb_id
    assert check.input_snapshot["requested_automation_mode"] == "supervised"
    assert check.input_snapshot["max_automation_mode"] == "full_auto"


@pytest.mark.asyncio
async def test_a_denied_run_records_the_failure_before_raising():
    """The denial is the evaluation most worth having, and it is the one a
    naive implementation loses by recording only on the success path."""
    from contextedge.services.execution_service import ExecutionPolicyError, start_execution

    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        automation_mode="full_auto", approval_policy_id=uuid.uuid4()
    )
    policy = ApprovalPolicy(
        policy_id=uuid.uuid4(), max_automation_mode="supervised", version=5
    )
    with (
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.ensure_edge", AsyncMock()),
        patch(
            "contextedge.services.execution_service.load_approval_policy",
            AsyncMock(return_value=policy),
        ),
        pytest.raises(ExecutionPolicyError, match="caps automation"),
    ):
        await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=["domain_admin"],
            playbook_id=pb_id,
            playbook_version_id=v_id,
        )

    checks = [obj for obj in added if isinstance(obj, PolicyCheck)]
    assert len(checks) == 1
    assert checks[0].result == "fail"
    assert checks[0].policy_version == 5
    assert "caps automation" in (checks[0].reason or "")


@pytest.mark.asyncio
async def test_no_configured_cap_records_not_applicable():
    from contextedge.services.execution_service import start_execution

    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness()
    with (
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.append_trace_event", AsyncMock()),
        patch("contextedge.services.execution_service.ensure_edge", AsyncMock()),
        patch("contextedge.services.execution_service.create_decision", AsyncMock()),
    ):
        await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=["domain_admin"],
            playbook_id=pb_id,
            playbook_version_id=v_id,
        )

    checks = [obj for obj in added if isinstance(obj, PolicyCheck)]
    assert [c.result for c in checks] == ["not_applicable"]
    assert checks[0].policy_version is None
