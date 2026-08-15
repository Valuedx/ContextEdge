"""F9 — the verdict says what was checked, and silence stops meaning success.

The old sweep asked one question and answered in one of three words. Its worst
case was silent: a CI that had stopped reporting looked exactly like a service
that recovered, and both fed the cohort counters and the knowledge-support
signal as success.

Most of these tests are about the two distinctions the aggregation turns on:
absence of bad news vs presence of good news, and "could not apply" vs "applied
and could not decide".
"""

from __future__ import annotations

import pytest

from contextedge.models.verification import (
    ASSESSMENT_RESULTS,
    OBSERVATION_STATUSES,
    POSITIVE_CRITERION_TYPES,
)
from contextedge.services.verification_criteria_service import (
    CriterionResult,
    aggregate,
    legacy_status,
)


def _absence(status, criterion_type="incident_absence"):
    return CriterionResult(
        criterion_type=criterion_type,
        criterion_name=f"{criterion_type} on vpn-gw-east-01",
        status=status,
    )


def _confirmation(status):
    return CriterionResult(
        criterion_type="user_confirmation",
        criterion_name="someone confirmed the issue is resolved",
        status=status,
    )


# =========================================================================
# The case F9 exists for
# =========================================================================


def test_silence_from_a_source_that_never_reports_is_inconclusive():
    """THE regression this item exists to prevent. The old sweep called this
    `verified` and fed it to the cohort counters as success."""
    verdict = aggregate(
        [
            _absence("not_observable"),
            _absence("not_observable", "alert_absence"),
            _confirmation("not_observable"),
        ]
    )
    assert verdict.overall_result == "inconclusive"
    assert legacy_status(verdict.overall_result) == "unverifiable"
    assert verdict.escalation_required is True
    assert "not evidence" in verdict.summary


def test_silence_from_a_source_that_does_report_is_still_success():
    """The other half of the calibration: 0036's telemetry verification was
    genuinely useful, and demoting all of it would throw the signal away."""
    verdict = aggregate(
        [
            _absence("pass"),
            _absence("pass", "alert_absence"),
            _confirmation("not_observable"),
        ]
    )
    assert verdict.overall_result == "success"
    assert legacy_status(verdict.overall_result) == "verified"


# =========================================================================
# not_observable vs inconclusive
# =========================================================================


def test_could_not_apply_does_not_hold_back_a_verdict():
    """`not_observable` means the criterion could not apply — no conversation
    to read. It neither supports nor undermines what the others found."""
    assert aggregate([_absence("pass"), _confirmation("not_observable")]).overall_result == (
        "success"
    )


def test_an_open_question_does_hold_the_verdict_at_monitor():
    """`inconclusive` means the criterion DID apply and could not decide —
    people were talking and nobody confirmed. That is worth watching."""
    verdict = aggregate([_absence("pass"), _confirmation("inconclusive")])
    assert verdict.overall_result == "monitor_required"
    # And it does NOT read as verified downstream.
    assert legacy_status(verdict.overall_result) == "unverifiable"


def test_the_two_unresolved_statuses_are_distinct_values():
    assert "not_observable" in OBSERVATION_STATUSES
    assert "inconclusive" in OBSERVATION_STATUSES


# =========================================================================
# Failure, and what to do about it
# =========================================================================


def test_a_recurrence_recommends_rollback():
    """An incident came back: there is a change to consider undoing."""
    verdict = aggregate([_absence("fail"), _confirmation("not_observable")])
    assert verdict.overall_result == "rollback_required"
    assert verdict.rollback_recommended is True
    assert verdict.retry_recommended is False
    assert legacy_status(verdict.overall_result) == "failed"


def test_alerts_alone_fail_without_recommending_a_rollback():
    """Alert noise is not a recurrence of the incident. Nothing obvious to
    undo, so a human is asked instead."""
    verdict = aggregate([_absence("fail", "alert_absence")])
    assert verdict.overall_result == "failed"
    assert verdict.rollback_recommended is False
    assert verdict.escalation_required is True


def test_confirmation_alongside_failure_is_partial_not_success():
    """Something recovered and something recurred. Reporting either alone is
    wrong, and the learning loop must not count this as a verified success —
    the same reason the projection has a partially_validated_fix edge."""
    verdict = aggregate([_absence("fail"), _confirmation("pass")])
    assert verdict.overall_result == "partial_success"
    assert legacy_status(verdict.overall_result) == "failed"
    assert verdict.escalation_required is True


# =========================================================================
# Positive signals
# =========================================================================


def test_a_confirmation_is_the_only_positive_signal_today():
    assert POSITIVE_CRITERION_TYPES == ("user_confirmation",)
    assert _confirmation("pass").is_positive_signal is True
    assert _absence("pass").is_positive_signal is False


def test_a_confirmed_success_says_so():
    verdict = aggregate([_absence("pass"), _confirmation("pass")])
    assert verdict.overall_result == "success"
    assert "confirmed" in verdict.summary


# =========================================================================
# Boundaries
# =========================================================================


def test_no_criteria_at_all_is_inconclusive_and_escalates():
    verdict = aggregate([])
    assert verdict.overall_result == "inconclusive"
    assert verdict.escalation_required is True


def test_an_unknown_observation_status_is_refused_at_construction():
    with pytest.raises(ValueError, match="status must be one of"):
        CriterionResult(
            criterion_type="incident_absence", criterion_name="x", status="probably_fine"
        )


def test_every_verdict_maps_to_a_legacy_word():
    for result in ASSESSMENT_RESULTS:
        assert legacy_status(result) in ("verified", "failed", "unverifiable")


def test_only_success_maps_to_verified():
    """Anything less than success must not reach the cohort counters or the
    knowledge-support signal as a verified outcome — that inflation is what
    F9 exists to stop."""
    verified = [r for r in ASSESSMENT_RESULTS if legacy_status(r) == "verified"]
    assert verified == ["success"]


def test_an_unknown_result_degrades_to_unverifiable():
    assert legacy_status("something_new") == "unverifiable"
