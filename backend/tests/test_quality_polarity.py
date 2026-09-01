"""Overlap across a polarity gap is not support.

The regression: Stage C scored "Restart the agent" against "Do not restart the
agent" at **1.00** — the strongest possible entailment, awarded to a step doing
exactly what its source forbids. Token and bigram overlap cannot see negation,
and the two sentences share every content word.

It ran the other way too. ``contradicts_negative`` flagged "Do not re-register
the agent" as contradicting the known-failed action "Re-register the agent" —
a major finding against a step that was declining the failed action, which is
the correct behaviour.

Both directions are pinned here, because a lexical scorer will regress to this
the moment someone adds a new comparison path.
"""

from __future__ import annotations

import pytest

from contextedge.quality.polarity import describe_conflict, is_negated, polarity_agrees
from contextedge.quality.semantic_match import (
    best_polarity_conflict,
    best_support_score,
    contradicts_negative,
)

RESTART = "Restart the AutomationEdge Agent service on the affected host."
FORBID_RESTART = "Do not restart the AutomationEdge Agent service on the affected host."


def _sources(*texts: str) -> list[tuple[str, str]]:
    return [(text, "required_actions") for text in texts]


# --------------------------------------------------------------- cue detection


@pytest.mark.parametrize(
    "text",
    [
        "Do not restart the service.",
        "Never replace the JAR.",
        "The agent must not be re-registered.",
        "Avoid changing the plugin version.",
        "This is not recommended.",
        "We do not suggest changing the JAR.",
    ],
)
def test_prohibitions_are_detected(text):
    assert is_negated(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Restart the service.",
        "Replace the JAR with the supported build.",
        "Confirm the agent shows Running.",
    ],
)
def test_instructions_are_not_negated(text):
    assert is_negated(text) is False


def test_only_the_main_clause_sets_polarity():
    """"Restart X, but do not delete Y" is an instruction to restart.

    Reading the whole string would classify it as a prohibition and invert
    every comparison that used it.
    """
    assert is_negated("Restart the agent, but do not delete the lock file.") is False
    assert is_negated("Do not restart the agent, but you may stop the broker.") is True


def test_ambiguous_negations_are_left_alone():
    """The cue list excludes "no" and "without" on purpose.

    "No longer responding" negates a noun and "without restarting, check X"
    negates a subordinate clause; treating either as a prohibition would
    invert a correct verdict, and abstaining only leaves the score as it was.
    """
    assert is_negated("The service is no longer responding.") is False
    assert is_negated("Without restarting, check the queue depth.") is False


# ------------------------------------------------------------------- scoring


def test_a_forbidden_action_is_not_scored_as_support():
    """The headline regression. This used to return 1.00."""
    score, matched = best_support_score(RESTART, _sources(FORBID_RESTART))
    assert score == 0.0
    assert matched is None


def test_a_forbidden_action_surfaces_as_a_conflict_instead():
    """Not merely suppressed — high overlap plus opposite polarity is the
    single most valuable signal this module can produce without a model."""
    score, matched = best_polarity_conflict(RESTART, _sources(FORBID_RESTART))
    assert score > 0.9
    assert matched == FORBID_RESTART


def test_a_required_action_still_scores_as_support():
    # The guard must not cost us the true positives.
    score, matched = best_support_score(RESTART, _sources(RESTART))
    assert score > 0.9
    assert matched == RESTART


def test_two_prohibitions_agree():
    """A step telling the operator not to do something, backed by a source
    saying the same, is supported — not a conflict."""
    step = "Do not replace the GUI automation JAR."
    source = "Never replace the GUI automation JAR."
    score, _ = best_support_score(step, _sources(source))
    conflict, _ = best_polarity_conflict(step, _sources(source))
    assert score > 0.5
    assert conflict == 0.0


def test_declining_a_known_failed_action_is_not_a_contradiction():
    """The mirror-image bug: a step doing the right thing was flagged major."""
    score, matched = contradicts_negative(
        "Do not re-register the agent; it loses the host binding.",
        ["Re-register the agent"],
    )
    assert score == 0.0
    assert matched is None


def test_performing_a_known_failed_action_is_still_a_contradiction():
    score, matched = contradicts_negative(
        "Re-register the agent on the affected host.", ["Re-register the agent"]
    )
    assert score > 0.5
    assert matched == "Re-register the agent"


def test_support_prefers_an_agreeing_source_over_a_contradicting_one():
    score, matched = best_support_score(RESTART, _sources(FORBID_RESTART, RESTART))
    assert matched == RESTART
    assert score > 0.9


# ------------------------------------------------------------------ messaging


def test_the_conflict_message_names_the_direction():
    """"They disagree" is not actionable; the two directions need different
    fixes, so the reviewer has to be told which one this is."""
    forbidden = describe_conflict(RESTART, FORBID_RESTART)
    assert forbidden and "forbids this action while the step performs it" in forbidden

    declined = describe_conflict(FORBID_RESTART, RESTART)
    assert declined and "declines an action the source requires" in declined


def test_no_message_when_polarity_agrees():
    assert describe_conflict(RESTART, RESTART) is None
    assert polarity_agrees(RESTART, RESTART) is True
