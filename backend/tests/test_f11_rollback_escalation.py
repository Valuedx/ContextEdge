"""F11 — undoing, and handing over with the evidence.

Rollback was free text (`rollback_notes`, `rollback_hint`) and `reversible` was
a flag nothing consumed. Escalation was a decision type and a case status, so a
human received a notification rather than what the system saw.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.remediation import (
    ESCALATION_PRIORITIES,
    ROLLBACK_PLAN_STATUSES,
    Escalation,
    RollbackPlan,
)
from contextedge.services.remediation_service import (
    acknowledge_escalation,
    derive_rollback_plan,
    raise_escalation,
)


def _step(index, *, title, status="completed", inputs=None):
    return SimpleNamespace(
        step_index=index, step_title=title, status=status, inputs=inputs or {}
    )


def _db(steps):
    added: list = []

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: steps)

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        added.append(obj)

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result()),
        add=add,
        flush=AsyncMock(),
        get=AsyncMock(return_value=None),
    )
    db.added = added
    return db


# =========================================================================
# Deriving the plan
# =========================================================================


@pytest.mark.asyncio
async def test_a_hint_counts_as_a_way_back():
    """Weaker than a registered rollback skill — nobody can execute it
    automatically — but it is what a responder needs at 3am, so discarding it
    would be worse than recording it as manual."""
    steps = [_step(0, title="Renew the cert", inputs={"rollback_hint": "restore the old cert"})]
    db = _db(steps)
    plan = await derive_rollback_plan(
        db, uuid.uuid4(), run=SimpleNamespace(id=uuid.uuid4())
    )
    assert plan.status == "proposed"
    assert plan.actions[0]["method"] == "manual"
    assert plan.actions[0]["instruction"] == "restore the old cert"
    assert plan.irreversible_steps == []


@pytest.mark.asyncio
async def test_irreversible_steps_are_named_not_omitted():
    """A plan that silently drops them reads as complete when it is partial."""
    steps = [
        _step(1, title="Delete the old certificate"),
        _step(0, title="Renew the cert", inputs={"rollback_hint": "restore the old cert"}),
    ]
    plan = await derive_rollback_plan(
        _db(steps), uuid.uuid4(), run=SimpleNamespace(id=uuid.uuid4())
    )
    assert len(plan.actions) == 1
    assert plan.irreversible_steps == [
        {"step_index": 1, "step_title": "Delete the old certificate"}
    ]


@pytest.mark.asyncio
async def test_a_plan_with_no_way_back_is_recorded_as_infeasible():
    """"We cannot undo this" is the most important thing a responder can learn
    early, and a missing row reads as "nobody checked"."""
    plan = await derive_rollback_plan(
        _db([_step(0, title="Delete the old certificate")]),
        uuid.uuid4(),
        run=SimpleNamespace(id=uuid.uuid4()),
    )
    assert plan.status == "infeasible"
    assert plan.actions == []
    assert plan.irreversible_steps


@pytest.mark.asyncio
async def test_steps_that_never_ran_are_not_undone():
    for status in ("skipped", "pending"):
        plan = await derive_rollback_plan(
            _db([_step(0, title="x", status=status, inputs={"rollback_hint": "y"})]),
            uuid.uuid4(),
            run=SimpleNamespace(id=uuid.uuid4()),
        )
        assert plan.actions == []
        assert plan.irreversible_steps == []


@pytest.mark.asyncio
async def test_a_registered_rollback_skill_beats_a_hint():
    from contextedge.services import remediation_service

    rollback_skill = SimpleNamespace(
        id=uuid.uuid4(), skill_key="restore_certificate", version="1.0.0",
        safety_class="high_side_effect",
    )
    forward_skill = SimpleNamespace(rollback_skill_id=rollback_skill.id)
    steps = [
        _step(
            0,
            title="Renew the cert",
            inputs={"tool_ref": "renew_certificate", "rollback_hint": "do it by hand"},
        )
    ]
    db = _db(steps)
    db.get = AsyncMock(return_value=rollback_skill)

    with patch.object(
        remediation_service,
        "_rollback_action_for",
        wraps=remediation_service._rollback_action_for,
    ):
        with patch(
            "contextedge.services.skill_registry_service.resolve_skill",
            AsyncMock(return_value=forward_skill),
        ):
            plan = await derive_rollback_plan(
                db, uuid.uuid4(), run=SimpleNamespace(id=uuid.uuid4())
            )

    assert plan.actions[0]["method"] == "skill"
    assert plan.actions[0]["tool_ref"] == "restore_certificate@1.0.0"


@pytest.mark.asyncio
async def test_actions_are_in_reverse_step_order():
    """The order is the plan: you undo the last thing you did first."""
    steps = [
        _step(2, title="c", inputs={"rollback_hint": "undo c"}),
        _step(1, title="b", inputs={"rollback_hint": "undo b"}),
        _step(0, title="a", inputs={"rollback_hint": "undo a"}),
    ]
    plan = await derive_rollback_plan(
        _db(steps), uuid.uuid4(), run=SimpleNamespace(id=uuid.uuid4())
    )
    assert [a["step_index"] for a in plan.actions] == [2, 1, 0]


# =========================================================================
# A rollback is an execution
# =========================================================================


def test_a_rollback_run_is_just_a_run_that_points_at_another():
    """v6 models RollbackExecution as its own class. Running an undo needs
    steps, approvals, attempts, an artifact binding and a verification — all
    of which ExecutionRun already has, and a parallel hierarchy would
    duplicate every one of them and then drift."""
    from contextedge.models.execution import ExecutionRun

    assert hasattr(ExecutionRun, "rolls_back_run_id")


# =========================================================================
# Escalation carries the bundle
# =========================================================================


@pytest.mark.asyncio
async def test_an_escalation_carries_refs_not_copies():
    """A copy would be a second version of the truth that ages away from the
    first, and the point of the bundle is that the human sees what the system
    saw."""
    db = _db([])
    assessment_id, run_id = uuid.uuid4(), uuid.uuid4()
    escalation = await raise_escalation(
        db,
        uuid.uuid4(),
        reason="verification failed on vpn-gw-east-01",
        escalated_by="execution_verification_sweep",
        execution_run_id=run_id,
        priority="high",
        evidence_bundle={
            "verification_assessment_id": str(assessment_id),
            "execution_run_id": str(run_id),
        },
        recommended_next_actions=["review rollback plan"],
    )
    assert isinstance(escalation, Escalation)
    assert escalation.evidence_bundle["verification_assessment_id"] == str(assessment_id)
    assert escalation.recommended_next_actions == ["review rollback plan"]
    assert escalation.status == "open"
    assert escalation.priority in ESCALATION_PRIORITIES


@pytest.mark.asyncio
async def test_acknowledgement_records_how_long_it_sat():
    """Stored rather than computed later, so the number survives an edit of
    either timestamp and "how long do escalations sit?" is one query."""
    created = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    escalation = Escalation(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), reason="x", escalated_by="sweep",
        status="open", priority="normal", created_at=created,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=escalation), flush=AsyncMock())
    result = await acknowledge_escalation(
        db,
        escalation.tenant_id,
        escalation_id=escalation.id,
        acknowledged_by=uuid.uuid4(),
        now=created + timedelta(minutes=25),
    )
    assert result.status == "acknowledged"
    assert result.acknowledgement_latency_min == 25


@pytest.mark.asyncio
async def test_acknowledging_twice_does_not_restart_the_clock():
    created = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    escalation = Escalation(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), reason="x", escalated_by="sweep",
        status="acknowledged", priority="normal", created_at=created,
        acknowledgement_latency_min=5,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=escalation), flush=AsyncMock())
    result = await acknowledge_escalation(
        db, escalation.tenant_id, escalation_id=escalation.id,
        acknowledged_by=uuid.uuid4(), now=created + timedelta(hours=3),
    )
    assert result.acknowledgement_latency_min == 5


@pytest.mark.asyncio
async def test_a_foreign_tenants_escalation_is_not_acknowledgeable():
    escalation = Escalation(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), reason="x", escalated_by="sweep",
        status="open", priority="normal",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=escalation), flush=AsyncMock())
    assert await acknowledge_escalation(
        db, uuid.uuid4(), escalation_id=escalation.id, acknowledged_by=uuid.uuid4()
    ) is None


# =========================================================================
# Vocabularies
# =========================================================================


def test_infeasible_is_a_first_class_plan_status():
    assert "infeasible" in ROLLBACK_PLAN_STATUSES
    assert set(ROLLBACK_PLAN_STATUSES) >= {"proposed", "approved", "executed", "rejected"}


def test_a_monitor_verdict_carries_a_window_and_others_do_not():
    from contextedge.services.verification_criteria_service import (
        MONITOR_WINDOW_SEC,
        CriterionResult,
        aggregate,
    )

    monitor = aggregate(
        [
            CriterionResult(
                criterion_type="incident_absence", criterion_name="a", status="pass"
            ),
            CriterionResult(
                criterion_type="user_confirmation", criterion_name="b", status="inconclusive"
            ),
        ]
    )
    assert monitor.overall_result == "monitor_required"
    assert monitor.monitoring_window_hint == MONITOR_WINDOW_SEC

    settled = aggregate(
        [CriterionResult(criterion_type="incident_absence", criterion_name="a", status="pass")]
    )
    assert settled.overall_result == "success"
    # A monitoring window on a settled verdict would be a number nobody acts on.
    assert settled.monitoring_window_hint is None


def test_rollback_plan_is_a_model_not_a_free_text_field():
    assert hasattr(RollbackPlan, "actions")
    assert hasattr(RollbackPlan, "irreversible_steps")
