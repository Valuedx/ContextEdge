"""H6: which change caused this, as a ranked list nobody has to trust blindly.

Three properties this must never lose, each of which produces a confident
wrong answer if it does:

- **A score is a rank, not a probability.** Nothing here may promote a
  candidate to `confirmed`; only governance can.
- **A change after onset cannot be the cause.** The database enforces it too,
  and this keeps the code from ever presenting a row the database would refuse.
- **Malformed input degrades, never crashes.** This runs over a whole corpus
  and one unreadable timestamp must not stop it — nor silently become a
  decision.

The last one has real history behind it: a single ServiceNow record dated
2035-05-28 (CHG0000003, "Roll back Windows SP2 patch") had pinned the
change_request keyset checkpoint nine years into the future, so every later
incremental sync returned zero rows and reported success. One bad timestamp
ended ingestion for an entire table, silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from contextedge.connectors.servicenow.connector import _is_future
from contextedge.services.change_correlation_service import (
    AFTER_ONSET,
    BEFORE_ONSET,
    CANDIDATE_SCORE,
    OVERLAPS_ONSET,
    SUSPECTED_SCORE,
    _parse_dt,
    _score_candidate,
    _status_for,
    _temporal,
)

ONSET = datetime(2026, 8, 10, 2, 40, tzinfo=UTC)


# --- the future-timestamp guard -------------------------------------------


def test_a_future_dated_row_never_becomes_the_checkpoint():
    """The regression. One row dated 2035 wedged an entire table's incremental
    sync, and every later run reported completed with zero items."""
    now = datetime(2026, 8, 21, tzinfo=UTC)
    assert _is_future("2035-05-28 12:30:56", now) is True
    assert _is_future("2026-08-20 10:00:00", now) is False


def test_modest_clock_skew_is_not_treated_as_bad_data():
    """Our clock and the instance's will disagree by seconds to minutes.
    Refusing those would stall the cursor for an ordinary reason."""
    now = datetime(2026, 8, 21, tzinfo=UTC)
    assert _is_future("2026-08-21 00:02:00", now) is False
    assert _is_future("2026-08-21 01:00:00", now) is True


def test_an_unreadable_timestamp_is_not_future():
    """Treating unparseable as future would stall the stream the same way a
    2035 row does, the next time an upstream format changes."""
    now = datetime(2026, 8, 21, tzinfo=UTC)
    for value in ("not-a-timestamp", "", None, "2026-13-45 99:99:99"):
        assert _is_future(value, now) is False, value


# --- temporal relation -----------------------------------------------------


def test_before_after_and_overlapping_onset():
    assert _temporal(ONSET - timedelta(minutes=70), ONSET) == (BEFORE_ONSET, -70)
    assert _temporal(ONSET + timedelta(minutes=70), ONSET) == (AFTER_ONSET, 70)
    # Within a few minutes either way the ordering is not meaningful.
    assert _temporal(ONSET + timedelta(minutes=2), ONSET)[0] == OVERLAPS_ONSET


def test_missing_times_are_unknown_not_guessed():
    assert _temporal(None, ONSET) == ("unknown", None)
    assert _temporal(ONSET, None) == ("unknown", None)


# --- scoring ---------------------------------------------------------------


def test_same_ci_outranks_one_hop_at_equal_proximity():
    """The whole point of situation-aware correlation over a same-CI lookup is
    that both are considered — and that they are not considered equal."""
    same, _, _ = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-70,
        out_of_window=False,
    )
    hop, _, _ = _score_candidate(
        distance=1, temporal_relation=BEFORE_ONSET, minutes_from_onset=-70,
        out_of_window=False,
    )
    assert same > hop


def test_closer_in_time_outranks_further():
    near, _, _ = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-30,
        out_of_window=False,
    )
    far, _, _ = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-60 * 24 * 5,
        out_of_window=False,
    )
    assert near > far


def test_out_of_window_execution_adds_signal():
    """Plenty of changes happen near an incident; far fewer happened at a time
    nobody approved."""
    plain, _, _ = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-70,
        out_of_window=False,
    )
    flagged, breakdown, reasons = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-70,
        out_of_window=True,
    )
    assert flagged > plain
    assert "out_of_window_execution" in breakdown
    assert any("approved window" in r for r in reasons)


def test_score_is_capped_and_explained():
    score, breakdown, reasons = _score_candidate(
        distance=0, temporal_relation=BEFORE_ONSET, minutes_from_onset=-10,
        out_of_window=True,
    )
    assert score <= 1.0
    # Every contributing factor is named, so a candidate can be argued with.
    assert breakdown and reasons


def test_an_unrelated_distance_scores_nothing_structural():
    score, breakdown, _ = _score_candidate(
        distance=None, temporal_relation="unknown", minutes_from_onset=None,
        out_of_window=False,
    )
    assert score == 0.0
    assert breakdown == {}


# --- the status ladder -----------------------------------------------------


def test_only_governance_confirms():
    """No score, however high, may promote a candidate. Allowing it would let
    inference launder itself into fact."""
    perfect = _status_for(
        score=1.0, temporal_relation=BEFORE_ONSET, distance=0, confirmed=False
    )
    assert perfect == "suspected"
    assert (
        _status_for(score=0.0, temporal_relation=BEFORE_ONSET, distance=0, confirmed=True)
        == "confirmed"
    )


def test_a_change_after_onset_is_never_causal():
    """The database refuses this combination outright; the code must never
    present a row the database would reject."""
    for score in (0.0, 0.5, 0.99):
        status = _status_for(
            score=score, temporal_relation=AFTER_ONSET, distance=0, confirmed=False
        )
        assert status not in ("suspected", "corroborated", "confirmed")


def test_a_post_onset_change_on_the_affected_ci_reads_as_remediation():
    """Usually somebody fixing it. What was tried matters even when it is not
    the cause, so it is recorded rather than dropped."""
    assert (
        _status_for(score=0.8, temporal_relation=AFTER_ONSET, distance=0, confirmed=False)
        == "remediation"
    )
    # One hop away and after onset is much weaker evidence of a fix.
    assert (
        _status_for(score=0.8, temporal_relation=AFTER_ONSET, distance=1, confirmed=False)
        == "weak_candidate"
    )


def test_the_ladder_thresholds_separate_suspected_from_candidate():
    assert (
        _status_for(
            score=SUSPECTED_SCORE, temporal_relation=BEFORE_ONSET, distance=0,
            confirmed=False,
        )
        == "suspected"
    )
    assert (
        _status_for(
            score=CANDIDATE_SCORE, temporal_relation=BEFORE_ONSET, distance=1,
            confirmed=False,
        )
        == "candidate"
    )
    assert (
        _status_for(
            score=0.1, temporal_relation=BEFORE_ONSET, distance=1, confirmed=False
        )
        == "weak_candidate"
    )


# --- malformed payloads ----------------------------------------------------


def test_change_window_parsing_degrades_on_anything_odd():
    """Reads raw connector payloads, which are whatever the source sent."""
    assert _parse_dt("2026-08-10 01:30:00") == datetime(2026, 8, 10, 1, 30, tzinfo=UTC)
    for value in (None, "", "yesterday", 12345, {"value": "2026-08-10"}, []):
        assert _parse_dt(value) is None, value
