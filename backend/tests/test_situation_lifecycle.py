"""Situation lifecycle: recovery is evidenced, never inferred from silence.

The rule the rest follows from, and the one most likely to be quietly relaxed
by someone fixing a dashboard: **a situation going quiet is not a situation
recovering**. Tickets stop arriving when the thing is fixed, when everyone gave
up, when the reporters went home, and when a connector broke. Only one of those
is recovery and nothing in the silence tells them apart.

The other distinction pinned here is reopen versus recurrence. Treating a
recurrence as a reopen produces one situation with an onset weeks in the past
and an MTTR spanning the gap between two unrelated outages. Treating a reopen
as a recurrence doubles the incident count and hides that the first fix did not
hold -- which is precisely the signal the efficacy ledger exists to catch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from contextedge.services.situation_lifecycle_service import (
    ACTIVE,
    EMERGING,
    INVALIDATED,
    MERGED,
    REOPENED,
    RESOLVED,
    STABILIZING,
    assess_lifecycle,
)


def _situation(state: str = ACTIVE) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), state=state)


# --- the headline rule -----------------------------------------------------


def test_silence_is_not_recovery():
    """No member carries a resolution. However quiet it has gone, the state
    does not move toward resolved."""
    assessment = assess_lifecycle(_situation(ACTIVE), [None, None, None])
    assert assessment.proposed_state == ACTIVE
    assert assessment.changed is False
    assert "silence is not recovery" in assessment.reason


def test_resolved_requires_every_member_to_carry_a_resolution():
    assert assess_lifecycle(
        _situation(ACTIVE), ["resolved", "resolved", "resolved"]
    ).proposed_state == RESOLVED
    # One straggler is not recovery, it is partial recovery.
    assert assess_lifecycle(
        _situation(ACTIVE), ["resolved", "resolved", None]
    ).proposed_state == STABILIZING


def test_stabilizing_says_recovery_was_evidenced_not_assumed():
    assessment = assess_lifecycle(_situation(ACTIVE), ["resolved", None])
    assert assessment.proposed_state == STABILIZING
    assert "not merely quieter" in assessment.reason


def test_a_cancelled_case_is_not_a_resolved_one():
    """A withdrawn report is not a fixed problem, and counting it as one
    resolves situations nobody fixed."""
    assessment = assess_lifecycle(_situation(ACTIVE), ["cancelled", "cancelled"])
    assert assessment.proposed_state == ACTIVE


def test_case_state_matching_is_case_insensitive_and_null_safe():
    assert assess_lifecycle(_situation(ACTIVE), ["RESOLVED", "Closed"]).proposed_state == RESOLVED
    assert assess_lifecycle(_situation(ACTIVE), [None, ""]).proposed_state == ACTIVE


# --- reopen ----------------------------------------------------------------


def test_a_recovered_situation_gaining_an_unresolved_signal_reopens():
    """Same occurrence, resumed. It keeps its identity, onset and history --
    which is what makes it different from a recurrence."""
    assessment = assess_lifecycle(_situation(RESOLVED), ["resolved", None])
    assert assessment.proposed_state == REOPENED
    assert "after the situation had recovered" in assessment.reason


def test_reopen_applies_from_stabilizing_too():
    assessment = assess_lifecycle(_situation(STABILIZING), ["resolved", None])
    assert assessment.proposed_state == REOPENED


def test_a_situation_that_never_recovered_does_not_reopen():
    """Reopen means recovery did not hold. Without a recovery there is
    nothing to reopen, and calling it that would invent an event."""
    assessment = assess_lifecycle(_situation(EMERGING), ["resolved", None])
    assert assessment.proposed_state == STABILIZING


# --- decisions are not recomputed -----------------------------------------


def test_terminal_states_are_never_moved_automatically():
    """Merged and invalidated are decisions somebody made. Recomputing over
    them is how a system teaches people that deciding is pointless."""
    for state in (MERGED, INVALIDATED):
        assessment = assess_lifecycle(_situation(state), ["resolved", "resolved"])
        assert assessment.proposed_state == state
        assert assessment.changed is False


def test_a_situation_with_no_live_members_is_left_alone():
    assessment = assess_lifecycle(_situation(ACTIVE), [])
    assert assessment.changed is False
    assert "no live members" in assessment.reason


# --- the assessment explains itself ---------------------------------------


def test_every_assessment_carries_its_counts_and_a_reason():
    """A transition nobody can inspect is one nobody can overrule."""
    assessment = assess_lifecycle(_situation(ACTIVE), ["resolved", None, None])
    payload = assessment.as_dict()
    assert payload["members"] == 3
    assert payload["resolved_members"] == 1
    assert payload["reason"]
    assert payload["current_state"] == ACTIVE
    assert payload["proposed_state"] == STABILIZING
