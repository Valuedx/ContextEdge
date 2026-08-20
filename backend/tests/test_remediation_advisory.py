"""Applicability and negative knowledge: mostly about not suppressing things.

An `excluded` verdict hides a remediation. When that is wrong the failure is
silent — the operator never learns the option existed, and nothing in the
output hints that something was removed. So exclusion is the conservative
direction here, and most of these tests check that it does NOT happen.

The reverse error is cheaper and visible: a fix recommended where it does not
apply gets rejected by whoever reads the rationale.

Measured on the reference corpus while these were written: 104 of 541 patterns
carry derived applicability, and exclusions track context — 0 with no context,
21 for on-prem, 97 for cloud.
"""

from __future__ import annotations

import uuid

from contextedge.services.efficacy_service import PatternEfficacy
from contextedge.services.remediation_advisory_service import (
    APPLICABLE,
    DO_NOT_RECOMMEND,
    EXCLUDED,
    INSUFFICIENT_EVIDENCE,
    RECOMMEND,
    RECOMMEND_WITH_CAUTION,
    UNKNOWN_APPLICABILITY,
    ApplicabilityVerdict,
    RemediationAdvice,
    _merge_applicability,
    _version_tuple,
    assess_applicability,
)

PID = uuid.uuid4()


def _advice(**kw) -> RemediationAdvice:
    args = {
        "pattern_id": PID,
        "title": "restart the pool",
        "efficacy": PatternEfficacy(pattern_id=PID, success=9, failure=1),
        "applicability": ApplicabilityVerdict(APPLICABLE),
        "known_failures": [],
    }
    args.update(kw)
    return RemediationAdvice(**args)


# --- version comparison ----------------------------------------------------


def test_versions_compare_by_component_not_string():
    assert _version_tuple("8.10.0") > _version_tuple("8.9.9")


def test_shorter_version_pads_rather_than_losing():
    """8.2 against 8.2.3 must compare as 8.2.0, not by length."""
    a = {"version_floor": {"ae": "8.2"}}
    assert assess_applicability(a, {"version": "8.2.3"}).verdict == APPLICABLE
    assert assess_applicability(a, {"version": "8.1.9"}).verdict == EXCLUDED


def test_unparseable_version_never_excludes():
    """An unreadable bound must not suppress a fix — the failure would be
    invisible."""
    a = {"version_floor": {"ae": "not-a-version"}}
    assert assess_applicability(a, {"version": "8.2.3"}).verdict != EXCLUDED


# --- exclusion needs both sides to speak -----------------------------------


def test_silence_on_either_side_is_unknown_not_excluded():
    assert assess_applicability(None, {"deployment": "cloud"}).verdict == UNKNOWN_APPLICABILITY
    assert assess_applicability({"deployment": "onprem"}, None).verdict == UNKNOWN_APPLICABILITY
    assert (
        assess_applicability({"deployment": "onprem"}, {"version": "8.2"}).verdict
        == UNKNOWN_APPLICABILITY
    )


def test_stated_deployment_conflict_excludes():
    verdict = assess_applicability({"deployment": "onprem"}, {"deployment": "cloud"})
    assert verdict.verdict == EXCLUDED
    assert "onprem" in verdict.reasons[0] and "cloud" in verdict.reasons[0]


def test_matching_deployment_is_applicable_and_says_which_dimension():
    verdict = assess_applicability({"deployment": "onprem"}, {"deployment": "onprem"})
    assert verdict.verdict == APPLICABLE
    assert "deployment" in verdict.matched_dimensions


def test_disjoint_environments_exclude_but_only_when_both_stated():
    assert (
        assess_applicability({"environments": ["prod"]}, {"environments": ["dev"]}).verdict
        == EXCLUDED
    )
    assert (
        assess_applicability({"environments": []}, {"environments": ["dev"]}).verdict
        == UNKNOWN_APPLICABILITY
    )


def test_component_mismatch_does_not_exclude():
    """Component vocabularies are LLM-extracted free text, so absence of
    overlap is as likely to be a naming difference as a real mismatch."""
    verdict = assess_applicability(
        {"components": ["process studio"]}, {"components": ["orchestrator"]}
    )
    assert verdict.verdict != EXCLUDED


def test_component_overlap_matches_and_is_explained():
    verdict = assess_applicability(
        {"components": ["process studio", "agent"]},
        {"components": ["agent"]},
    )
    assert verdict.verdict == APPLICABLE
    assert any("agent" in r for r in verdict.reasons)


# --- merging several documented sources ------------------------------------


def test_contested_deployment_becomes_unstated():
    """Two articles disagreeing is not grounds to suppress a fix in both."""
    merged = _merge_applicability(
        [{"deployment": "onprem"}, {"deployment": "cloud"}]
    )
    assert merged["deployment"] == ""
    assert assess_applicability(merged, {"deployment": "cloud"}).verdict != EXCLUDED


def test_agreed_deployment_survives_the_merge():
    merged = _merge_applicability([{"deployment": "onprem"}, {"deployment": "onprem"}])
    assert merged["deployment"] == "onprem"


def test_version_bounds_merge_to_the_loosest():
    """A floor that excludes is worse than one that does not."""
    merged = _merge_applicability(
        [{"version_floor": {"ae": "8.5"}}, {"version_floor": {"ae": "8.1"}}]
    )
    assert merged["version_floor"]["ae"] == "8.1"
    merged = _merge_applicability(
        [{"version_ceiling": {"ae": "8.5"}}, {"version_ceiling": {"ae": "9.0"}}]
    )
    assert merged["version_ceiling"]["ae"] == "9.0"


def test_components_union_across_sources():
    merged = _merge_applicability(
        [{"components": ["agent"]}, {"components": ["studio"]}]
    )
    assert set(merged["components"]) == {"agent", "studio"}


# --- the verdict -----------------------------------------------------------


def test_exclusion_beats_a_perfect_track_record():
    """A fix that cannot apply here is not improved by working elsewhere."""
    a = _advice(
        efficacy=PatternEfficacy(pattern_id=PID, success=50),
        applicability=ApplicabilityVerdict(EXCLUDED, ("wrong deployment",)),
    )
    assert a.recommendation == DO_NOT_RECOMMEND


def test_drifting_documentation_is_not_recommended():
    """Documented advice the record contradicts should not be recommended on
    the strength of the documentation."""
    a = _advice(
        efficacy=PatternEfficacy(
            pattern_id=PID, documented_support=1, success=5, failure=22
        )
    )
    assert a.efficacy.is_drifting is True
    assert a.recommendation == DO_NOT_RECOMMEND


def test_no_outcomes_is_insufficient_not_a_recommendation():
    a = _advice(efficacy=PatternEfficacy(pattern_id=PID, documented_support=3))
    assert a.recommendation == INSUFFICIENT_EVIDENCE


def test_a_thin_sample_is_insufficient_even_at_100_percent():
    """Two successes is not a track record."""
    a = _advice(efficacy=PatternEfficacy(pattern_id=PID, success=2))
    assert a.recommendation == INSUFFICIENT_EVIDENCE


def test_known_failures_downgrade_an_otherwise_clean_record():
    """The E3 point: surface what is known to go wrong WITH the
    recommendation, not instead of it."""
    clean = _advice(efficacy=PatternEfficacy(pattern_id=PID, success=20))
    assert clean.recommendation == RECOMMEND
    with_failures = _advice(
        efficacy=PatternEfficacy(pattern_id=PID, success=20),
        known_failures=["restarting the service did not clear the queue"],
    )
    assert with_failures.recommendation == RECOMMEND_WITH_CAUTION


def test_low_success_rate_downgrades():
    a = _advice(efficacy=PatternEfficacy(pattern_id=PID, success=4, failure=6))
    assert a.recommendation == RECOMMEND_WITH_CAUTION


def test_rationale_states_the_evidence_not_just_the_verdict():
    """A verdict nobody can inspect is one nobody can overrule."""
    a = _advice(
        efficacy=PatternEfficacy(pattern_id=PID, success=9, failure=1, documented_support=1),
        known_failures=["x"],
    )
    rationale = a.rationale
    assert "90% success" in rationale
    assert "10 outcome-bearing episodes" in rationale
    assert "MIXED" in rationale
    assert "1 known failure" in rationale
