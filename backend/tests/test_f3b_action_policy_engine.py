"""F3b — the action policy finally decides something.

`action_policies` shipped in 0029 with a verdict vocabulary, scope axes and
precedence columns whose own docstring said the engine was "on the design
roadmap". Nothing wrote the table, nothing queried it outside the agent
projection, and `Decision.policy_result` — documented as "the verdict the
executor checks" — had no verdict to hold.

Precedence is the part everyone gets wrong, so most of these tests are about
which policy wins and why.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.action_policy import POLICY_RESULTS, ActionPolicy
from contextedge.services.action_policy_service import (
    BLOCKING_RESULTS,
    RESTRICTIVENESS,
    ActionPolicyError,
    in_effect,
    restrictiveness,
    select_policy,
    specificity,
    validate_policy_fields,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _policy(**kw) -> ActionPolicy:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        policy_name="p",
        action_name="restart_service",
        policy_result="allowed_auto",
        risk_level="medium",
        is_active=True,
        priority=100,
        conflict_resolution="most_restrictive",
        workflow_entity_id=None,
        environment=None,
        business_unit=None,
        data_domain=None,
        effective_from=None,
        effective_to=None,
        version=1,
    )
    defaults.update(kw)
    return ActionPolicy(**defaults)


def _request(**kw) -> dict:
    base = {
        "workflow_entity_id": None,
        "environment": None,
        "business_unit": None,
        "data_domain": None,
    }
    base.update(kw)
    return base


# =========================================================================
# Restrictiveness
# =========================================================================


def test_the_ordering_covers_the_whole_vocabulary():
    assert set(RESTRICTIVENESS) == set(POLICY_RESULTS)


def test_allowed_auto_is_least_and_restricted_is_most():
    assert restrictiveness("allowed_auto") == 0
    assert restrictiveness("restricted") == len(RESTRICTIVENESS) - 1


def test_an_unknown_verdict_ranks_most_restrictive():
    """Fail closed, for the same reason _safety_class_rank does: a typo in a
    policy must never read as allowed_auto."""
    assert restrictiveness("allowd_auto") > restrictiveness("restricted")


def test_only_non_executable_verdicts_block():
    assert set(BLOCKING_RESULTS) == {"recommendation_only", "manual_only", "restricted"}
    assert "approval_required" not in BLOCKING_RESULTS  # it gates, it does not refuse
    assert "allowed_auto" not in BLOCKING_RESULTS


# =========================================================================
# Scope matching and specificity
# =========================================================================


def test_a_null_axis_means_any():
    policy = _policy(environment=None)
    assert select_policy([policy], _request(environment="production"), NOW) is policy


def test_a_declared_axis_must_match():
    policy = _policy(environment="production")
    assert select_policy([policy], _request(environment="dev"), NOW) is None
    assert select_policy([policy], _request(environment="production"), NOW) is policy


def test_the_more_specific_policy_wins():
    """A rule about this action on THIS workflow in production is more about
    the situation than one about the action everywhere — precedence that
    ignored that would make narrow rules pointless to write."""
    workflow = uuid.uuid4()
    broad = _policy(policy_name="broad", policy_result="restricted")
    narrow = _policy(
        policy_name="narrow",
        policy_result="allowed_auto",
        workflow_entity_id=workflow,
        environment="production",
    )
    winner = select_policy(
        [broad, narrow], _request(workflow_entity_id=workflow, environment="production"), NOW
    )
    assert winner is narrow
    assert specificity(narrow) == 2 and specificity(broad) == 0


def test_specificity_beats_priority():
    """Priority only breaks a tie. A high-priority broad rule must not
    override a rule written for exactly this situation."""
    narrow = _policy(policy_name="narrow", environment="production", priority=1)
    broad = _policy(policy_name="broad", priority=999, policy_result="restricted")
    assert select_policy([narrow, broad], _request(environment="production"), NOW) is narrow


# =========================================================================
# Conflict resolution
# =========================================================================


def test_a_tie_defaults_to_the_most_restrictive_reading():
    """When two equally specific rules disagree about whether something may
    run unattended, the safe reading is the one that asks a human."""
    lenient = _policy(policy_name="a", policy_result="allowed_auto")
    strict = _policy(policy_name="b", policy_result="manual_only")
    assert select_policy([lenient, strict], _request(), NOW) is strict


def test_highest_priority_is_available_when_both_rules_ask_for_it():
    lenient = _policy(
        policy_name="a", policy_result="allowed_auto", priority=10,
        conflict_resolution="highest_priority",
    )
    strict = _policy(
        policy_name="b", policy_result="restricted", priority=1,
        conflict_resolution="highest_priority",
    )
    assert select_policy([lenient, strict], _request(), NOW) is lenient


def test_disagreement_about_the_strategy_resolves_most_restrictively():
    """If the rules cannot even agree how to resolve their conflict, the safe
    reading wins that argument too."""
    lenient = _policy(
        policy_name="a", policy_result="allowed_auto", priority=999,
        conflict_resolution="highest_priority",
    )
    strict = _policy(
        policy_name="b", policy_result="restricted", priority=1,
        conflict_resolution="most_restrictive",
    )
    assert select_policy([lenient, strict], _request(), NOW) is strict


def test_a_full_tie_is_broken_deterministically_by_name():
    """Row order is not a decision anyone made, so it must not decide."""
    a = _policy(policy_name="aaa", policy_result="manual_only")
    b = _policy(policy_name="bbb", policy_result="manual_only")
    assert select_policy([a, b], _request(), NOW) is a
    assert select_policy([b, a], _request(), NOW) is a


# =========================================================================
# Effective windows and activity
# =========================================================================


def test_a_policy_outside_its_window_does_not_apply():
    future = _policy(effective_from=NOW + timedelta(days=1))
    past = _policy(effective_to=NOW - timedelta(days=1))
    assert in_effect(future, NOW) is False
    assert in_effect(past, NOW) is False
    assert select_policy([future, past], _request(), NOW) is None


def test_a_naive_window_boundary_is_read_as_utc():
    policy = _policy(effective_from=(NOW + timedelta(hours=1)).replace(tzinfo=None))
    assert in_effect(policy, NOW) is False


def test_an_inactive_policy_never_applies():
    assert select_policy([_policy(is_active=False)], _request(), NOW) is None


# =========================================================================
# Validation
# =========================================================================


def test_the_vocabularies_are_enforced_at_the_boundary():
    validate_policy_fields(
        policy_result="allowed_auto",
        conflict_resolution="most_restrictive",
        risk_level="medium",
    )
    for bad in (
        {"policy_result": "probably_fine"},
        {"conflict_resolution": "coin_flip"},
        {"risk_level": "spicy"},
    ):
        kwargs = {
            "policy_result": "allowed_auto",
            "conflict_resolution": "most_restrictive",
            "risk_level": "medium",
            **bad,
        }
        with pytest.raises(ActionPolicyError):
            validate_policy_fields(**kwargs)


# =========================================================================
# Enforcement — the verdict can tighten, never loosen
# =========================================================================


@pytest.mark.asyncio
async def test_a_blocking_verdict_refuses_the_run_and_records_why():
    from contextedge.services.execution_service import (
        ExecutionPolicyError,
        _apply_action_policy,
    )

    policy = _policy(policy_name="no-restarts-in-prod", policy_result="restricted", version=3)
    db = SimpleNamespace()
    with (
        patch(
            "contextedge.services.action_policy_service.evaluate_action",
            AsyncMock(return_value=policy),
        ),
        patch(
            "contextedge.services.execution_service.record_policy_check", AsyncMock()
        ) as check,
        pytest.raises(ExecutionPolicyError, match="restricted"),
    ):
        await _apply_action_policy(
            db,
            uuid.uuid4(),
            playbook=SimpleNamespace(id=uuid.uuid4(), environment="production"),
            action_name="restart_service",
            step_index=2,
            actor_id=uuid.uuid4(),
        )
    assert check.await_args.kwargs["result"] == "fail"
    assert check.await_args.kwargs["policy_version"] == 3


@pytest.mark.asyncio
async def test_allowed_auto_is_recorded_but_grants_nothing():
    """It means "this policy does not object", not "this may run unattended".
    Safety class, role and trust have already had their say, and a policy that
    could overturn them would be a way to grant privilege by writing a row."""
    from contextedge.services.execution_service import _apply_action_policy

    policy = _policy(policy_result="allowed_auto")
    with (
        patch(
            "contextedge.services.action_policy_service.evaluate_action",
            AsyncMock(return_value=policy),
        ),
        patch(
            "contextedge.services.execution_service.record_policy_check", AsyncMock()
        ) as check,
    ):
        verdict = await _apply_action_policy(
            SimpleNamespace(),
            uuid.uuid4(),
            playbook=SimpleNamespace(id=uuid.uuid4(), environment=None),
            action_name="restart_service",
            step_index=0,
            actor_id=uuid.uuid4(),
        )
    assert verdict == "allowed_auto"
    assert check.await_args.kwargs["result"] == "pass"


@pytest.mark.asyncio
async def test_a_step_with_no_declared_action_is_not_evaluated():
    """An undeclared action cannot be looked up, and inferring one from the
    step title is exactly what F1 refused to do."""
    from contextedge.services.execution_service import _apply_action_policy

    with patch(
        "contextedge.services.execution_service.record_policy_check", AsyncMock()
    ) as check:
        verdict = await _apply_action_policy(
            SimpleNamespace(),
            uuid.uuid4(),
            playbook=SimpleNamespace(id=uuid.uuid4(), environment=None),
            action_name=None,
            step_index=0,
            actor_id=uuid.uuid4(),
        )
    assert verdict is None
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_matching_policy_records_not_applicable():
    """"No rule existed" and "a rule permitted it" are different facts."""
    from contextedge.services.execution_service import _apply_action_policy

    with (
        patch(
            "contextedge.services.action_policy_service.evaluate_action",
            AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.execution_service.record_policy_check", AsyncMock()
        ) as check,
    ):
        verdict = await _apply_action_policy(
            SimpleNamespace(),
            uuid.uuid4(),
            playbook=SimpleNamespace(id=uuid.uuid4(), environment=None),
            action_name="restart_service",
            step_index=0,
            actor_id=uuid.uuid4(),
        )
    assert verdict is None
    assert check.await_args.kwargs["result"] == "not_applicable"


def test_the_run_carries_its_most_governed_steps_verdict():
    """A run cannot be more permissive than its strictest step."""
    from contextedge.services.execution_service import _strictest_verdict

    assert _strictest_verdict(["allowed_auto", "approval_required"]) == "approval_required"
    assert _strictest_verdict([None, None]) is None
    assert _strictest_verdict([]) is None


# =========================================================================
# Versioning: rules, not labels
# =========================================================================


def test_the_version_tracks_rules_not_labels():
    import inspect

    from contextedge.api.v1 import action_policies

    assert "policy_result" in action_policies._RULE_FIELDS
    assert "environment" in action_policies._RULE_FIELDS
    assert "conflict_resolution" in action_policies._RULE_FIELDS
    # A rename or a deactivation does not change what a past execution was
    # judged under, so neither bumps the version.
    assert "policy_name" not in action_policies._RULE_FIELDS
    assert "is_active" not in action_policies._RULE_FIELDS
    assert "description" not in action_policies._RULE_FIELDS

    source = inspect.getsource(action_policies.update_action_policy)
    assert "row.version = (row.version or 1) + 1" in source
