"""Efficacy measurement: the arithmetic, and the ways it goes quietly wrong.

Every failure mode pinned here produces a number that looks entirely plausible,
which is what makes them worth tests rather than review:

- "unresolved" contains "resolved", so rule order decides whether every failure
  in the corpus scores as a success
- a closed-without-response ticket counted either way moves the rate
- `unknown` in the denominator drives an unclassifiable corpus toward 0%, which
  reads as fixes that stopped working
- a success rate of 0.0 and "no data" are different claims
- drift flagged on a pattern with no documentation sends someone to edit an
  article that does not exist

Measured coverage on the live corpus at the time these were written: 10,247
episodes carrying outcome text in 9,014 distinct phrasings, 67.9% recognised by
some rule, 73.9% success over rate-bearing outcomes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from contextedge.services.efficacy_service import (
    DOCUMENTED_ONLY,
    EMPIRICAL,
    MIN_DRIFT_SAMPLE,
    MIXED,
    UNSUPPORTED,
    PatternEfficacy,
)
from contextedge.services.outcome_classification import (
    FAILURE,
    PARTIAL,
    SUCCESS,
    UNKNOWN,
    classify_outcome,
    classify_outcome_detailed,
    counts_toward_rate,
    support_role_for,
)

PID = uuid.uuid4()


# --- the ordering trap -----------------------------------------------------


def test_unresolved_is_not_read_as_resolved():
    """The single most expensive bug available here. A contains-check in the
    obvious order scores every failure in the corpus as a success."""
    assert classify_outcome("Unresolved.") == FAILURE
    assert classify_outcome("unresolved, investigation ongoing.") == FAILURE
    assert classify_outcome("Unresolved in the provided evidence.") == FAILURE


def test_negated_resolution_is_failure():
    assert classify_outcome("Issue was not resolved.") == FAILURE
    assert classify_outcome("The restart did not work.") == FAILURE


def test_plain_resolution_is_success():
    assert classify_outcome("Issue resolved.") == SUCCESS
    assert classify_outcome("Resolved") == SUCCESS
    assert classify_outcome("resolved by restarting process studio.") == SUCCESS


# --- abandoned is not failure, and not success -----------------------------


def test_closed_without_response_is_unknown_not_failure():
    """Nothing was tried and nothing was learned. Counting it as failure
    understates efficacy; as success, overstates it."""
    for text in (
        "Ticket closed due to lack of client response.",
        "Ticket closed due to no client response.",
        "Ticket closed due to client unresponsiveness.",
    ):
        assert classify_outcome(text) == UNKNOWN, text


def test_bare_closed_is_unknown():
    """A ticket ending says nothing about whether the fix worked."""
    assert classify_outcome("Ticket closed.") == UNKNOWN


def test_information_provided_is_not_a_resolution():
    """Answering a question is not fixing anything."""
    assert classify_outcome("Information provided, ticket closed.") == UNKNOWN


def test_declining_is_distinguished_from_not_recognising():
    """Both surface as `unknown` and mean opposite things about the
    classifier: one is a rule choosing not to call it, the other is a
    coverage gap. Reporting them as one number hides the gap."""
    _, recognised = classify_outcome_detailed("Ticket closed due to lack of client response.")
    assert recognised is True
    _, recognised = classify_outcome_detailed("Zorble the frobnicator sideways.")
    assert recognised is False


# --- partial ---------------------------------------------------------------


def test_workaround_is_partial_not_success():
    """Restoring service is not fixing the cause, and a pattern whose
    successes are mostly workarounds must not read as fully effective."""
    assert classify_outcome("Workaround provided.") == PARTIAL
    assert classify_outcome("Temporary service restoration via restarts.") == PARTIAL


# --- role and rate membership ----------------------------------------------


def test_only_failure_contradicts():
    """A failure recorded as supporting evidence is how a pattern keeps
    recommending something that stopped working."""
    assert support_role_for(FAILURE) == "contradicts_resolution"
    for outcome in (SUCCESS, PARTIAL, UNKNOWN):
        assert support_role_for(outcome) == "supports_resolution"


def test_unknown_is_excluded_from_the_rate():
    """Counting unknown against the rate lets an unclassifiable corpus read
    as fixes that stopped working."""
    assert counts_toward_rate(UNKNOWN) is False
    for outcome in (SUCCESS, PARTIAL, FAILURE):
        assert counts_toward_rate(outcome) is True


# --- rollup arithmetic -----------------------------------------------------


def test_no_outcomes_gives_none_not_zero():
    """"We do not know" and "it never works" must not share a
    representation — a 0.0 here would be read as a broken fix."""
    e = PatternEfficacy(pattern_id=PID, unknown=9)
    assert e.rate_base == 0
    assert e.success_rate is None


def test_partial_counts_in_the_denominator_only():
    e = PatternEfficacy(pattern_id=PID, success=7, partial=5, failure=0)
    assert e.rate_base == 12
    assert abs(e.success_rate - 7 / 12) < 1e-9


def test_unknown_does_not_move_the_rate():
    with_unknown = PatternEfficacy(pattern_id=PID, success=3, failure=1, unknown=96)
    without = PatternEfficacy(pattern_id=PID, success=3, failure=1)
    assert with_unknown.success_rate == without.success_rate


# --- confidence class ------------------------------------------------------


def test_confidence_class_distinguishes_documentation_from_observation():
    """Three KB articles and nineteen resolved incidents are not the same
    pattern, and episode_count cannot tell them apart."""
    assert (
        PatternEfficacy(pattern_id=PID, documented_support=3).confidence_class
        == DOCUMENTED_ONLY
    )
    assert PatternEfficacy(pattern_id=PID, success=19).confidence_class == EMPIRICAL
    assert (
        PatternEfficacy(
            pattern_id=PID, documented_support=3, success=19
        ).confidence_class
        == MIXED
    )
    assert PatternEfficacy(pattern_id=PID).confidence_class == UNSUPPORTED


def test_a_documented_only_pattern_graduates_when_incidents_arrive():
    """Cold start: the pattern graduates, the knowledge case does not."""
    before = PatternEfficacy(pattern_id=PID, documented_support=2)
    after = PatternEfficacy(pattern_id=PID, documented_support=2, success=6)
    assert before.confidence_class == DOCUMENTED_ONLY
    assert after.confidence_class == MIXED


# --- drift -----------------------------------------------------------------


def test_drift_needs_documentation_to_drift_from():
    """A purely empirical pattern with a low success rate is a hard problem,
    not stale knowledge. Flagging it sends someone to edit an article that
    does not exist."""
    empirical_only = PatternEfficacy(pattern_id=PID, success=1, failure=9)
    assert empirical_only.success_rate < 0.5
    assert empirical_only.is_drifting is False


def test_drift_needs_a_minimum_sample():
    """One failure against one documented article is not evidence the
    article is wrong."""
    thin = PatternEfficacy(pattern_id=PID, documented_support=1, failure=1)
    assert thin.rate_base < MIN_DRIFT_SAMPLE
    assert thin.is_drifting is False


def test_drift_fires_on_documented_advice_the_record_contradicts():
    """The headline capability: 'KB-108 recommends a restart; observed 19%
    success across 27 outcome-bearing episodes'."""
    drifting = PatternEfficacy(
        pattern_id=PID,
        documented_support=1,
        success=5,
        failure=22,
        last_observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert drifting.rate_base == 27
    assert drifting.success_rate < 0.2
    assert drifting.is_drifting is True


def test_a_well_performing_documented_pattern_does_not_drift():
    """Measured on the live corpus: 15 MIXED patterns cleared the sample bar
    and none fell below the threshold. The rule can fire; it did not."""
    healthy = PatternEfficacy(pattern_id=PID, documented_support=3, success=57, failure=12)
    assert healthy.rate_base >= MIN_DRIFT_SAMPLE
    assert healthy.is_drifting is False
