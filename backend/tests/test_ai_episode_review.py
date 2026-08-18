"""AI first-pass episode review: fail-closed parsing, deterministic floors,
governance (a dispatch can never escalate the configured mode), and wiring.

The classifier proposes; deterministic policy disposes. A wrong "hold"
costs one human review that was going to happen anyway; a wrong "approve"
feeds patterns and playbooks — so every ambiguous shape below must land
on hold.
"""

import uuid
from types import SimpleNamespace

import pytest

from contextedge.ai.classifiers.episode_review import _parse
from contextedge.services.episode_review_service import (
    MIN_EVIDENCE,
    MIN_VERDICT_CONFIDENCE,
    passes_auto_approve_floors,
)

# ---------------------------------------------------------------------------
# Classifier parsing — every malformed shape fails closed to hold.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"verdict": "approved"},          # out-of-vocabulary
        {"verdict": "APPROVE"},           # case is vocabulary too
        {"verdict": True},                # truthy is not a verdict
    ],
)
def test_malformed_verdicts_hold(raw):
    parsed = _parse(raw)
    assert parsed["verdict"] == "hold"
    assert parsed["confidence"] == 0.0


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        ("NaN", 0.0),
        (float("inf"), 0.0),
        (True, 0.0),      # bool would float() to 1.0 — must not
        (None, 0.0),
        ("0.93", 0.93),   # numeric strings are fine
        (1.7, 1.0),       # clamp
        (-0.2, 0.0),      # clamp
    ],
)
def test_confidence_is_finite_and_clamped(confidence, expected):
    parsed = _parse({"verdict": "approve", "confidence": confidence})
    assert parsed["confidence"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Deterministic floors — the model's approve is necessary, never sufficient.
# ---------------------------------------------------------------------------


def _episode(evidence_count=3, outcome="Certificate renewed and users confirmed recovery."):
    return SimpleNamespace(
        evidence_ids=[str(uuid.uuid4()) for _ in range(evidence_count)],
        final_outcome=outcome,
    )


def _approving_verdict(confidence=0.9):
    return {"verdict": "approve", "confidence": confidence}


def test_full_pass_approves():
    ok, failed = passes_auto_approve_floors(_episode(), _approving_verdict())
    assert ok and failed == []


def test_thin_evidence_holds_despite_model_approval():
    ok, failed = passes_auto_approve_floors(
        _episode(evidence_count=MIN_EVIDENCE - 1), _approving_verdict()
    )
    assert not ok
    assert any("evidence" in reason for reason in failed)


def test_missing_outcome_holds():
    ok, failed = passes_auto_approve_floors(
        _episode(outcome="resolved."), _approving_verdict()
    )
    assert not ok
    assert "no_substantive_outcome" in failed


def test_unconfident_approval_holds():
    ok, failed = passes_auto_approve_floors(
        _episode(), _approving_verdict(confidence=MIN_VERDICT_CONFIDENCE - 0.01)
    )
    assert not ok


def test_non_list_evidence_ids_hold_rather_than_crash():
    episode = SimpleNamespace(evidence_ids={"not": "a list"}, final_outcome="x" * 40)
    ok, failed = passes_auto_approve_floors(episode, _approving_verdict())
    assert not ok


# ---------------------------------------------------------------------------
# Governance and wiring.
# ---------------------------------------------------------------------------


def test_beat_schedule_includes_ai_review():
    from contextedge.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "ai-review-episodes-hourly" in schedule
    assert schedule["ai-review-episodes-hourly"]["task"] == "evaluation.ai_review_episodes"
    assert schedule["ai-review-episodes-hourly"]["args"] == ("all",)


def test_task_registered_and_routed_to_evaluation():
    import contextedge.workers.evaluation_tasks  # noqa: F401 — registers the task
    from contextedge.workers.celery_app import celery_app

    assert "evaluation.ai_review_episodes" in celery_app.tasks
    assert celery_app.conf.task_routes.get("evaluation.*") == {"queue": "evaluation"}


def test_sweep_is_a_noop_while_disabled(monkeypatch):
    """EPISODE_AI_REVIEW=off must cost nothing: no DB session, no queries."""
    from contextedge.config import settings
    from contextedge.workers import evaluation_tasks

    monkeypatch.setattr(settings, "episode_ai_review", "off")

    def _explodes(*a, **k):  # any attempt to run work is a failure
        raise AssertionError("disabled sweep must not open a session")

    monkeypatch.setattr(evaluation_tasks, "run_async", _explodes)
    out = evaluation_tasks.ai_review_episodes.run("all")
    assert out == {"status": "disabled"}


def test_prompt_family_registered():
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("episode_review", None)
    assert prompt.version == "v1"
    assert "hold" in prompt.system
