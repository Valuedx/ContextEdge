"""What merges into one situation, and — mostly — what must not.

A situation asserts that many signals describe one occurrence. The expensive
direction of error is over-merging: it produces a confident, plausible,
completely fabricated account of a three-week outage that never happened, and
nothing downstream can tell it from a real one.

So most of these tests are about refusal. The fixtures they mirror are in
`evals/fixtures/servicenow_scenarios.py`:

  S1  one change, one major incident, five duplicates -> ONE situation
  S4  four unrelated incidents on a shared domain controller -> NOT one
  S5  three recurrences of a known error, weeks apart -> NOT one

S1 and S5 are the pair that matters. S1's children share a `parent_incident`:
one occurrence seen five times in three hours. S5's incidents share a
`problem_id` and nothing else: three occurrences, weeks apart, of one
unresolved known error. Merging on the second produces the fabricated outage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from contextedge.services.situation_correlation_service import (
    AUTHORITATIVE_EDGE_TYPES,
    HUB_CI_INCIDENT_THRESHOLD,
    MIN_SITUATION_MEMBERS,
    NON_MERGING_EDGE_TYPES,
    _Candidate,
    _group_fingerprint,
    _hub_cis,
    _may_merge_inferred,
    _situation_type,
    _Union,
)

WINDOW = timedelta(hours=24)
T0 = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
CI_A, CI_B = uuid.uuid4(), uuid.uuid4()
SIG_A, SIG_B = uuid.uuid4(), uuid.uuid4()


def _candidate(
    *,
    minutes: int = 0,
    cis=(CI_A,),
    sigs=(SIG_A,),
    environment: str | None = None,
) -> _Candidate:
    return _Candidate(
        evidence_id=uuid.uuid4(),
        title="incident",
        occurred_at=T0 + timedelta(minutes=minutes),
        ci_ids=frozenset(cis),
        signature_ids=frozenset(sigs),
        environment=environment,
    )


# --- the deliberate non-merges -------------------------------------------


def test_a_shared_problem_is_not_a_merge_signal():
    """S5. `related_problem` is human-authored and asserts same root CAUSE,
    which spans occurrences by definition. It is the most tempting wrong join
    available, so its exclusion is asserted rather than assumed."""
    assert "related_problem" not in AUTHORITATIVE_EDGE_TYPES
    assert "related_problem" in NON_MERGING_EDGE_TYPES


def test_a_shared_ci_is_not_a_merge_signal():
    """S4. A domain controller serves password resets, DNS, GPO and disk
    alerts in one afternoon. Shared infrastructure, not a shared occurrence."""
    assert "affects_ci" not in AUTHORITATIVE_EDGE_TYPES
    assert "affects_ci" in NON_MERGING_EDGE_TYPES


def test_same_ci_and_window_without_symptom_agreement_does_not_merge():
    """The S4 shape exactly: same CI, minutes apart, different symptoms."""
    a = _candidate(minutes=0, sigs=(SIG_A,))
    b = _candidate(minutes=30, sigs=(SIG_B,))
    assert _may_merge_inferred(a, b, WINDOW, set()) is False


def test_same_ci_and_symptom_outside_the_window_does_not_merge():
    """The S5 shape: the same failure on the same box, three weeks later, is
    a recurrence — a second occurrence, not a longer first one."""
    a = _candidate(minutes=0)
    b = _candidate(minutes=60 * 24 * 21)
    assert _may_merge_inferred(a, b, WINDOW, set()) is False


def test_same_symptom_and_window_on_different_cis_does_not_merge():
    a = _candidate(cis=(CI_A,))
    b = _candidate(minutes=10, cis=(CI_B,))
    assert _may_merge_inferred(a, b, WINDOW, set()) is False


def test_all_three_conditions_together_do_merge():
    """Any two of these are satisfied constantly by unrelated work on shared
    infrastructure. All three is the weakest defensible claim."""
    a = _candidate(minutes=0)
    b = _candidate(minutes=45)
    assert _may_merge_inferred(a, b, WINDOW, set()) is True


# --- vetoes ----------------------------------------------------------------


def test_hub_ci_cannot_anchor_an_inferred_merge():
    """On shared infrastructure "same CI" carries almost no information, and
    the false-merge rate rises with the CI's popularity."""
    a = _candidate(minutes=0)
    b = _candidate(minutes=45)
    assert _may_merge_inferred(a, b, WINDOW, {CI_A}) is False


def test_hub_detection_needs_more_than_the_threshold():
    below = [_candidate() for _ in range(HUB_CI_INCIDENT_THRESHOLD)]
    assert _hub_cis(below) == set()
    above = below + [_candidate()]
    assert _hub_cis(above) == {CI_A}


def test_stated_environment_disagreement_vetoes():
    a = _candidate(minutes=0, environment="prod")
    b = _candidate(minutes=45, environment="staging")
    assert _may_merge_inferred(a, b, WINDOW, set()) is False


def test_unknown_environment_is_not_disagreement():
    """No row in the current corpus states an environment. Treating unknown
    as a mismatch would veto every inferred merge and read as a working
    veto — the worst kind of dead code."""
    a = _candidate(minutes=0, environment=None)
    b = _candidate(minutes=45, environment="prod")
    assert _may_merge_inferred(a, b, WINDOW, set()) is True


def test_missing_timestamps_never_merge():
    """A signal with no time cannot be shown to be inside any window, and a
    merge that cannot be shown is one that should not happen."""
    a = _candidate(minutes=0)
    b = _candidate(minutes=45)
    b.occurred_at = None
    assert _may_merge_inferred(a, b, WINDOW, set()) is False


# --- grouping mechanics ----------------------------------------------------


def test_authoritative_reason_outranks_inferred_for_the_same_member():
    """A membership has to be able to say what put it there, and the stronger
    justification is the honest one to record."""
    union = _Union()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    union.union(a, b, "inferred")
    union.union(a, c, "authoritative")
    assert union.reasons[a] == "authoritative"
    assert union.reasons[b] == "inferred"


def test_union_is_transitive_so_a_chain_is_one_situation():
    """S1: the major incident links to five children individually; all six
    have to land in one group."""
    union = _Union()
    major = uuid.uuid4()
    children = [uuid.uuid4() for _ in range(5)]
    for child in children:
        union.union(major, child, "authoritative")
    groups = union.groups()
    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 6


def test_a_singleton_is_not_a_situation():
    """One incident is an incident. A situation claims several signals
    describe one thing, and a claim about one signal is just the signal."""
    assert MIN_SITUATION_MEMBERS == 2


# --- identity --------------------------------------------------------------


def test_fingerprint_is_order_independent_and_set_specific():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert _group_fingerprint([a, b, c]) == _group_fingerprint([c, a, b])
    assert _group_fingerprint([a, b]) != _group_fingerprint([a, b, c])


def test_situation_type_reflects_scale_and_evidence_strength():
    assert _situation_type(6, True) == "incident_storm"
    assert _situation_type(2, True) == "degradation"
    # No authoritative link means nothing asserts these are one thing except
    # the correlator, and `unknown` says so.
    assert _situation_type(2, False) == "unknown"
