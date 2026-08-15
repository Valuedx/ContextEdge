"""F10 — trust is scoped, measured, and can only ever say no.

Autonomy was a mode on the playbook: a configuration, not a track record. It
could not answer the question that should gate an unattended action — has THIS
agent done THIS action on THIS class of thing in THIS environment, and did it
hold?

The v6 §25 worked example is the acceptance test at the bottom.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.trust import AUTONOMY_LEVELS, UNSCOPED, TrustProfile
from contextedge.services.trust_service import (
    AUTONOMOUS_MIN_LOWER_BOUND,
    FAILURE_RESULTS,
    SUCCESS_RESULTS,
    SUSPEND_AFTER_CONSECUTIVE_FAILURES,
    evaluate_autonomy,
    record_outcome,
    scope_key,
    wilson_lower_bound,
)


def _profile(**kw) -> TrustProfile:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        agent_ref="agent-a",
        action_type="restart_service",
        resource_class="windows_endpoint",
        environment="production",
        business_criticality="non_critical",
        sample_size=0,
        verified_successes=0,
        failures=0,
        inconclusive=0,
        consecutive_failures=0,
        confidence_lower_bound=0.0,
    )
    defaults.update(kw)
    return TrustProfile(**defaults)


# =========================================================================
# The lower bound, not the rate
# =========================================================================


def test_a_perfect_tiny_sample_does_not_look_like_a_proven_one():
    """3/3 is a rate of 1.0 and means almost nothing; 340/350 is 0.97 and
    means a great deal. This is the whole reason the bound is stored."""
    tiny = wilson_lower_bound(3, 3)
    large = wilson_lower_bound(340, 350)
    assert tiny < large
    assert tiny < AUTONOMOUS_MIN_LOWER_BOUND
    assert large >= AUTONOMOUS_MIN_LOWER_BOUND


def test_an_unexercised_scope_scores_zero_not_a_half():
    """Starting anywhere above the floor would let a scope become autonomous
    by never being tried."""
    assert wilson_lower_bound(0, 0) == 0.0


def test_the_bound_rises_with_evidence_and_never_exceeds_the_rate():
    previous = 0.0
    for n in (5, 20, 100, 500):
        bound = wilson_lower_bound(n, n)
        assert bound > previous
        assert bound <= 1.0
        previous = bound


def test_failures_pull_the_bound_down():
    assert wilson_lower_bound(90, 100) > wilson_lower_bound(60, 100)


def test_impossible_counts_are_clamped_rather_than_trusted():
    assert 0.0 <= wilson_lower_bound(50, 10) <= 1.0
    assert wilson_lower_bound(-5, 10) >= 0.0


# =========================================================================
# Recent failure beats the long-run average
# =========================================================================


def test_a_failure_streak_suspends_a_profile_with_an_excellent_history():
    """400 verified successes and three failures in a row is not trustworthy
    right now — and demoting it must not require waiting for the average to
    move, or a deploy."""
    level, reason = evaluate_autonomy(
        _profile(
            sample_size=403,
            verified_successes=400,
            failures=3,
            consecutive_failures=SUSPEND_AFTER_CONSECUTIVE_FAILURES,
            confidence_lower_bound=0.98,
        )
    )
    assert level == "suspended"
    assert "consecutive" in reason


def test_suspension_is_checked_before_the_average():
    """Order matters: a good average must not rescue a bad streak."""
    profile = _profile(
        sample_size=1000,
        verified_successes=997,
        consecutive_failures=SUSPEND_AFTER_CONSECUTIVE_FAILURES,
        confidence_lower_bound=0.99,
    )
    assert evaluate_autonomy(profile)[0] == "suspended"


# =========================================================================
# The levels
# =========================================================================


def test_an_unproven_scope_is_advisory_not_forbidden():
    """Treating "unproven" as "forbidden" would stop every new action from
    ever earning a record — the failure mode that gets trust systems switched
    off."""
    level, reason = evaluate_autonomy(_profile())
    assert level == "advisory"
    assert "no outcomes" in reason


def test_a_strong_record_reaches_autonomous_but_says_policy_still_decides():
    level, reason = evaluate_autonomy(
        _profile(sample_size=350, verified_successes=347, confidence_lower_bound=0.96)
    )
    assert level == "autonomous"
    assert "policy must still permit" in reason


def test_a_middling_record_is_supervised():
    level, _ = evaluate_autonomy(
        _profile(sample_size=20, verified_successes=15, confidence_lower_bound=0.55)
    )
    assert level == "supervised"


def test_every_level_is_in_the_declared_vocabulary():
    for profile in (
        _profile(),
        _profile(sample_size=10, verified_successes=10, confidence_lower_bound=0.75),
        _profile(sample_size=400, verified_successes=400, confidence_lower_bound=0.99),
        _profile(consecutive_failures=9),
    ):
        assert evaluate_autonomy(profile)[0] in AUTONOMY_LEVELS


# =========================================================================
# Scope
# =========================================================================


def test_unknown_scope_dimensions_become_a_literal_not_null():
    """NULLs in a unique key would let two "unknown environment" profiles
    coexist for the same agent and action, quietly splitting the record."""
    scope = scope_key(
        agent_ref=None,
        action_type=None,
        resource_class=None,
        environment=None,
        business_criticality=None,
    )
    assert set(scope.values()) == {UNSCOPED}


def test_scope_values_are_truncated_to_their_columns():
    scope = scope_key(
        agent_ref="a" * 500,
        action_type="b" * 500,
        resource_class="c" * 500,
        environment="d" * 500,
        business_criticality="e" * 500,
    )
    assert len(scope["agent_ref"]) == 120
    assert len(scope["action_type"]) == 60
    assert len(scope["environment"]) == 30


# =========================================================================
# Recording
# =========================================================================


def _db(existing=None):
    added: list = []

    class _Result:
        def scalar_one_or_none(self):
            return existing

    db = SimpleNamespace(
        add=added.append, flush=AsyncMock(), execute=AsyncMock(return_value=_Result())
    )
    db.added = added
    return db


@pytest.mark.asyncio
async def test_only_success_counts_as_a_verified_success():
    """partial_success and monitor_required are NOT successes, for the same
    reason F9 refuses to map them onto `verified`."""
    assert SUCCESS_RESULTS == ("success",)
    assert "partial_success" in FAILURE_RESULTS
    assert "monitor_required" not in SUCCESS_RESULTS


@pytest.mark.asyncio
async def test_an_inconclusive_outcome_is_neither_success_nor_failure():
    """It drags the bound down — we tried and learned nothing — without
    pretending the action broke something."""
    profile = _profile()
    db = _db(profile)
    await record_outcome(
        db, profile.tenant_id, scope=scope_key(
            agent_ref="agent-a", action_type="restart_service",
            resource_class="windows_endpoint", environment="production",
            business_criticality="non_critical",
        ),
        assessment_result="inconclusive",
    )
    assert profile.sample_size == 1
    assert profile.verified_successes == 0
    assert profile.failures == 0
    assert profile.inconclusive == 1
    assert profile.confidence_lower_bound == 0.0


@pytest.mark.asyncio
async def test_a_success_resets_the_failure_streak():
    profile = _profile(sample_size=2, failures=2, consecutive_failures=2)
    db = _db(profile)
    scope = scope_key(
        agent_ref="agent-a", action_type="restart_service",
        resource_class="windows_endpoint", environment="production",
        business_criticality="non_critical",
    )
    await record_outcome(db, profile.tenant_id, scope=scope, assessment_result="success")
    assert profile.consecutive_failures == 0
    assert profile.verified_successes == 1


# =========================================================================
# The v6 §25 worked example — the acceptance test
# =========================================================================


def test_the_v6_worked_example_resolves_as_specified():
    """v6 §25: a high-sample restart on a non-critical service reaches
    AUTONOMOUS while a 3-sample Oracle failover on a payment service does
    not — from the same code, on the strength of the evidence alone."""
    restart = _profile(
        agent_ref="agent-a",
        action_type="restart_service",
        resource_class="windows_service",
        environment="production",
        business_criticality="non_critical",
        sample_size=372,
        verified_successes=369,
        failures=3,
    )
    restart.confidence_lower_bound = wilson_lower_bound(
        restart.verified_successes, restart.sample_size
    )

    failover = _profile(
        agent_ref="agent-a",
        action_type="failover_database",
        resource_class="oracle_database",
        environment="production",
        business_criticality="business_critical",
        sample_size=3,
        verified_successes=3,
    )
    failover.confidence_lower_bound = wilson_lower_bound(
        failover.verified_successes, failover.sample_size
    )

    assert evaluate_autonomy(restart)[0] == "autonomous"
    assert evaluate_autonomy(failover)[0] != "autonomous"
    # Same agent, same 100%-or-near-it record. The scope and the sample size
    # are what separate them — which is the entire point of the item.
    assert restart.agent_ref == failover.agent_ref


# =========================================================================
# Trust vetoes; it never grants
# =========================================================================


@pytest.mark.asyncio
async def test_a_suspended_scope_blocks_the_run_and_records_why():
    from contextedge.services.execution_service import (
        ExecutionPolicyError,
        _enforce_trust_suspension,
    )

    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(
        tenant_id=tenant_id,
        agent_ref=str(actor_id),
        autonomy_level="suspended",
        autonomy_reason="3 consecutive non-successes",
        consecutive_failures=3,
    )

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: [profile])

    db = SimpleNamespace(execute=AsyncMock(return_value=_Result()), add=lambda _o: None,
                         flush=AsyncMock())
    playbook = SimpleNamespace(id=uuid.uuid4())

    with (
        patch(
            "contextedge.services.execution_service.record_policy_check", AsyncMock()
        ) as check,
        pytest.raises(ExecutionPolicyError, match="is suspended"),
    ):
        await _enforce_trust_suspension(
            db, tenant_id, playbook=playbook, actor_id=actor_id
        )

    assert check.await_count == 1
    assert check.await_args.kwargs["check_name"] == "trust_scope"
    assert check.await_args.kwargs["result"] == "fail"


@pytest.mark.asyncio
async def test_an_unproven_or_supervised_scope_does_not_block():
    """Only `suspended` blocks. Trust can veto; it cannot grant, and it must
    not forbid what it has simply never seen."""
    from contextedge.services.execution_service import _enforce_trust_suspension

    class _Empty:
        def scalars(self):
            return SimpleNamespace(all=list)

    db = SimpleNamespace(execute=AsyncMock(return_value=_Empty()))
    await _enforce_trust_suspension(
        db, uuid.uuid4(), playbook=SimpleNamespace(id=uuid.uuid4()), actor_id=uuid.uuid4()
    )
