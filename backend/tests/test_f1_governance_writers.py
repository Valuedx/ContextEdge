"""F1 — the 0029 governance columns get written, or stay honestly NULL.

Migration 0029 provisioned an execution/approval/decision governance spine and
nothing populated it. These tests pin the writers added by F1, and — just as
importantly — pin the cases that must stay NULL: an action identity inferred
from a step title, or an approver role invented where no policy named one,
would be worse than the empty column, because the policy engine (F3) and the
skill registry (F6) will match on these exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.decision import DECISION_INTENTS, INTENT_BY_DECISION_TYPE
from contextedge.models.execution import (
    ACTION_TYPES,
    APPROVAL_STATUSES,
    ApprovalRequest,
    ExecutionStepRun,
)
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.services.approval_policy_service import ApprovalPolicy
from contextedge.services.execution_service import (
    _approver_role_label,
    _step_action_identity,
    start_execution,
)

# =========================================================================
# Step action identity — declared or nothing
# =========================================================================


def test_declared_action_identity_is_taken_verbatim():
    name, kind = _step_action_identity(
        {"title": "Restart the ordering service", "action_name": "restart_service",
         "action_type": "remediation"}
    )
    assert name == "restart_service"
    assert kind == "remediation"


def test_action_name_is_never_inferred_from_the_title():
    """A title is prose. Policy matches action names exactly, so a value
    derived from prose would match the wrong rule with full confidence."""
    name, kind = _step_action_identity({"title": "Restart the ordering service"})
    assert name is None
    assert kind is None


def test_unknown_action_type_is_dropped_not_stored():
    name, kind = _step_action_identity(
        {"action_name": "frobnicate", "action_type": "frobnication"}
    )
    assert name == "frobnicate"
    assert kind is None, "an unrecognised action_type must not enter the vocabulary"


def test_blank_and_non_string_action_identity_degrade_to_null():
    assert _step_action_identity({"action_name": "   ", "action_type": 7}) == (None, None)
    assert _step_action_identity({"action_name": None}) == (None, None)


def test_action_name_is_truncated_to_the_column_width():
    name, _ = _step_action_identity({"action_name": "x" * 500})
    assert name is not None and len(name) == 120


# =========================================================================
# Approver role — the role consulted, or NULL
# =========================================================================


def test_approver_role_lists_every_role_the_policy_accepts():
    """check_decider accepts any one of them, so recording an arbitrary
    single pick would misrepresent what was required."""
    policy = ApprovalPolicy(policy_id=uuid4(), approver_roles=("sre_lead", "domain_admin"))
    assert _approver_role_label(policy) == "domain_admin, sre_lead"


def test_approver_role_is_null_when_no_policy_names_one():
    assert _approver_role_label(ApprovalPolicy(policy_id=None)) is None
    assert _approver_role_label(ApprovalPolicy(policy_id=uuid4(), approver_roles=())) is None


def test_approver_role_overflow_drops_whole_roles():
    """A mid-word cut at 120 chars would read as a role that does not exist."""
    roles = tuple(f"role_{i:02d}_" + "x" * 20 for i in range(10))
    label = _approver_role_label(ApprovalPolicy(policy_id=uuid4(), approver_roles=roles))
    assert label is not None and len(label) <= 120
    for part in label.split(", "):
        assert part in roles


# =========================================================================
# start_execution writes the step-level governance columns
# =========================================================================


def _execution_harness(steps, *, automation_mode="full_auto", approval_policy_id=None):
    tenant_id, actor_id = uuid4(), uuid4()
    playbook_id, version_id = uuid4(), uuid4()
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
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=datetime.now(UTC),
        steps=steps,
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
    return db, added, tenant_id, actor_id, playbook_id, version_id


async def _run(db, tenant_id, actor_id, playbook_id, version_id, *, policy=None):
    patches = [
        patch("contextedge.services.execution_service.append_operational_event", AsyncMock()),
        patch("contextedge.services.execution_service.append_trace_event", AsyncMock()),
        patch("contextedge.services.execution_service.ensure_edge", AsyncMock()),
        patch("contextedge.services.execution_service.create_decision", AsyncMock()),
    ]
    if policy is not None:
        patches.append(
            patch(
                "contextedge.services.execution_service.load_approval_policy",
                AsyncMock(return_value=policy),
            )
        )
    with patches[0], patches[1], patches[2], patches[3]:
        if policy is not None:
            with patches[4]:
                return await start_execution(
                    db,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    roles=["domain_admin"],
                    playbook_id=playbook_id,
                    playbook_version_id=version_id,
                    requested_max_safety_class="high_side_effect",
                )
        return await start_execution(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            roles=["domain_admin"],
            playbook_id=playbook_id,
            playbook_version_id=version_id,
            requested_max_safety_class="high_side_effect",
        )


@pytest.mark.asyncio
async def test_step_run_records_action_identity_mode_and_executor():
    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        [
            {
                "title": "Restart the ordering service",
                "action_name": "restart_service",
                "action_type": "remediation",
                "safety_class": "low_side_effect",
            }
        ],
        automation_mode="supervised",
    )
    run = await _run(db, tenant_id, actor_id, pb_id, v_id)

    steps = [obj for obj in added if isinstance(obj, ExecutionStepRun)]
    assert len(steps) == 1
    step = steps[0]
    assert step.action_name == "restart_service"
    assert step.action_type == "remediation"
    # Exact denormalisations, so a step row is self-describing without a join.
    assert step.execution_mode == run.automation_mode == "supervised"
    assert step.executed_by == actor_id


@pytest.mark.asyncio
async def test_step_run_without_declared_action_keeps_columns_null():
    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        [{"title": "Check the certificate expiry on vpn-gw-east-01"}]
    )
    await _run(db, tenant_id, actor_id, pb_id, v_id)

    step = next(obj for obj in added if isinstance(obj, ExecutionStepRun))
    assert step.action_name is None
    assert step.action_type is None
    assert step.step_title == "Check the certificate expiry on vpn-gw-east-01"
    # The mode/executor columns are always knowable, so they are always set.
    assert step.execution_mode == "full_auto"
    assert step.executed_by == actor_id


@pytest.mark.asyncio
async def test_gated_approval_carries_the_step_action_name():
    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        [
            {
                "title": "Reimage host",
                "action_name": "reimage_host",
                "action_type": "remediation",
                "safety_class": "destructive",
            }
        ]
    )
    await _run(db, tenant_id, actor_id, pb_id, v_id)

    approval = next(obj for obj in added if isinstance(obj, ApprovalRequest))
    assert approval.action_name == "reimage_host"
    # requested_action stays the free-text label it always was.
    assert approval.requested_action.startswith("execute_step:")
    # No approval policy configured → no role was consulted.
    assert approval.approver_role is None


@pytest.mark.asyncio
async def test_gated_approval_records_the_consulted_role_when_a_policy_names_one():
    policy = ApprovalPolicy(policy_id=uuid4(), approver_roles=("sre_lead",))
    db, added, tenant_id, actor_id, pb_id, v_id = _execution_harness(
        [{"title": "Reimage host", "safety_class": "destructive"}],
        approval_policy_id=uuid4(),
    )
    await _run(db, tenant_id, actor_id, pb_id, v_id, policy=policy)

    approval = next(obj for obj in added if isinstance(obj, ApprovalRequest))
    assert approval.approver_role == "sre_lead"
    assert approval.action_name is None, "the step declared no action name"


# =========================================================================
# Decision intent and trace-level risk
# =========================================================================


def _decision_db():
    added: list = []
    db = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
        get=AsyncMock(return_value=None),
    )
    return db, added


async def _create(db, **kwargs):
    from contextedge.services.decision_trace_service import create_decision

    with (
        patch(
            "contextedge.services.decision_trace_service.append_operational_event",
            AsyncMock(),
        ),
        patch(
            "contextedge.services.decision_trace_service.append_trace_event", AsyncMock()
        ),
        patch(
            "contextedge.services.decision_trace_service.link_decision_option",
            AsyncMock(),
        ),
        patch("contextedge.services.decision_trace_service.REASONING_MEMORY", None),
    ):
        return await create_decision(db, **kwargs)


@pytest.mark.asyncio
async def test_decision_intent_is_derived_from_decision_type():
    db, _ = _decision_db()
    decision = await _create(
        db,
        tenant_id=uuid4(),
        decision_type="restart_workflow",
        agent_step="remediation",
        rationale_summary="The ordering service is wedged after the 10:14 deploy",
    )
    assert decision.decision_intent == "remediation"
    assert decision.decision_intent in DECISION_INTENTS


@pytest.mark.asyncio
async def test_unknown_decision_type_leaves_intent_null():
    db, _ = _decision_db()
    decision = await _create(
        db,
        tenant_id=uuid4(),
        decision_type="something_new",
        agent_step="triage",
        rationale_summary="A decision type the intent map has not been taught",
    )
    assert decision.decision_intent is None


@pytest.mark.asyncio
async def test_explicit_intent_wins_and_is_validated():
    db, _ = _decision_db()
    decision = await _create(
        db,
        tenant_id=uuid4(),
        decision_type="restart_workflow",
        agent_step="remediation",
        rationale_summary="Recorded as a recommendation, not a remediation",
        decision_intent="recommendation",
    )
    assert decision.decision_intent == "recommendation"

    with pytest.raises(ValueError, match="decision_intent must be one of"):
        await _create(
            db,
            tenant_id=uuid4(),
            decision_type="restart_workflow",
            agent_step="remediation",
            rationale_summary="An intent outside the governed vocabulary",
            decision_intent="vibes",
        )


@pytest.mark.asyncio
async def test_trace_risk_comes_from_the_selected_option():
    """Trace-level risk is the risk of the path TAKEN, not the riskiest
    alternative that was considered and rejected."""
    db, _ = _decision_db()
    decision = await _create(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        agent_step="remediation",
        rationale_summary="Certificate renewal beat a gateway failover on blast radius",
        options=[
            {"action": "failover_gateway", "risk_level": "high", "selected": False},
            {"action": "renew_certificate", "risk_level": "medium", "selected": True},
        ],
    )
    assert decision.risk_level == "medium"


@pytest.mark.asyncio
async def test_trace_risk_is_null_when_no_option_was_selected():
    db, _ = _decision_db()
    decision = await _create(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        agent_step="remediation",
        rationale_summary="Options were enumerated but none chosen yet",
        options=[{"action": "renew_certificate", "risk_level": "medium", "selected": False}],
    )
    assert decision.risk_level is None


# =========================================================================
# Vocabularies describe what the code writes
# =========================================================================


def test_approval_statuses_contains_the_status_the_expiry_sweep_writes():
    """approval_expiry_service writes 'expired'; the tuple omitted it."""
    assert "expired" in APPROVAL_STATUSES


def test_intent_map_only_produces_declared_intents():
    assert set(INTENT_BY_DECISION_TYPE.values()) <= set(DECISION_INTENTS)


def test_intent_map_covers_every_declared_decision_type():
    from contextedge.models.decision import DECISION_TYPES

    assert set(DECISION_TYPES) == set(INTENT_BY_DECISION_TYPE)


def test_playbook_step_rejects_an_unknown_action_type():
    from contextedge.schemas.playbook import PlaybookStep

    step = PlaybookStep(action_name="restart_service", action_type="remediation")
    assert step.action_type == "remediation"
    assert set(ACTION_TYPES) >= {"remediation", "diagnostic"}

    with pytest.raises(ValueError, match="action_type must be one of"):
        PlaybookStep(action_type="frobnication")
