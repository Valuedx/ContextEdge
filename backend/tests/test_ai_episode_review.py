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


# ---------------------------------------------------------------------------
# 2026-08-18 external-review fixes.
# ---------------------------------------------------------------------------


def test_transient_provider_failure_is_not_persisted():
    """A provider outage must leave the draft retryable: the classifier
    marks the verdict transient, the service persists nothing, and the
    next sweep (which selects ai_review IS NULL) picks the draft up
    again. Stamping the outage turned a one-hour blip into a permanent
    'never reviewed' for a whole batch."""
    from contextedge.ai.classifiers.episode_review import HELD

    transient = dict(HELD)
    transient["transient_failure"] = True
    assert transient["verdict"] == "hold"
    # The service-side contract: transient verdicts return early and do
    # not touch episode.ai_review — pinned by inspecting the source, the
    # same wiring-not-logic style as the dedup entry-point test.
    import inspect

    from contextedge.services import episode_review_service

    source = inspect.getsource(episode_review_service.ai_review_episode)
    assert "transient_failure" in source
    transient_pos = source.index("transient_failure")
    stamp_pos = source.index("episode.ai_review =")
    assert transient_pos < stamp_pos, (
        "the transient short-circuit must run BEFORE the assessment is stamped"
    )


def test_sweep_dispatches_clustering_per_approved_domain():
    """cluster_episodes(None, tenant) clusters only NULL-domain episodes
    (domain-safe mining), and on the live graph every episode is
    domain-scoped — so the sweep must dispatch per approved domain."""
    import inspect

    from contextedge.workers import evaluation_tasks

    source = inspect.getsource(evaluation_tasks.ai_review_episodes)
    assert "approved_domains" in source
    assert "for domain_id in approved_domains" in source
    # The old bug: a single unconditional None dispatch.
    assert "cluster_episodes.delay(None," not in source


# ---------------------------------------------------------------------------
# 2026-08-19 auto-approve hardening: commit-before-dispatch and the
# concurrent-writer lock. These are the two findings that blocked
# auto_approve mode; the ordering contracts are pinned the same
# wiring-not-logic way as the transient and clustering tests above.
# ---------------------------------------------------------------------------


def test_service_never_dispatches_inside_transaction():
    """The review service runs inside an open transaction; a message
    sent from there can be consumed before the commit lands, and the
    signature task no-ops WITHOUT retry on the uncommitted state. All
    dispatching lives in the sweep task, after the per-episode commit."""
    import inspect

    from contextedge.services import episode_review_service

    source = inspect.getsource(episode_review_service.ai_review_episode)
    assert "extract_issue_signature_task" not in source
    assert "domain_id" in source  # caller needs it to dispatch clustering


def test_sweep_commits_each_episode_before_dispatching():
    import inspect

    from contextedge.workers import evaluation_tasks

    source = inspect.getsource(evaluation_tasks.ai_review_episodes)
    commit_pos = source.index("await db.commit()")
    approved_pos = source.index('totals["approved"] += 1')
    assert commit_pos < approved_pos, (
        "the per-episode commit must precede the approved-branch dispatch"
    )
    # A failed review rolls back alone instead of poisoning the session
    # for the rest of the batch (the PendingRollbackError lesson).
    assert "await db.rollback()" in source
    assert "skipped_state_changed" in source


def test_review_write_is_lock_guarded():
    """Between the LLM call (~14s, no locks) and the write, a human may
    decide or dedup may supersede. The service must re-read FOR UPDATE
    — with populate_existing, or the identity map returns the stale
    pre-review attributes and the check is vacuous — and skip if the
    state moved. Concurrent decisions always win over the model's."""
    import inspect

    from contextedge.services import episode_review_service

    source = inspect.getsource(episode_review_service.ai_review_episode)
    lock_pos = source.index("with_for_update")
    stamp_pos = source.index("episode.ai_review =")
    assert lock_pos < stamp_pos
    assert "populate_existing" in source
    assert 'reviewer_state != "pending_review"' in source


async def test_state_change_during_review_skips_write(monkeypatch):
    """Functional pin for the lock guard: the draft is approved by a
    human while the model is thinking; the service must skip without
    stamping anything."""
    import uuid as _uuid
    from types import SimpleNamespace

    from contextedge.services.episode_review_service import ai_review_episode

    async def fake_llm(**kwargs):
        return {"verdict": "approve", "confidence": 0.95, "reasons": []}

    monkeypatch.setattr(
        "contextedge.ai.classifiers.episode_review.review_episode_llm", fake_llm
    )

    async def fake_excerpts(db, episode, steps=None):
        return ""

    monkeypatch.setattr(
        "contextedge.services.episode_review_service._evidence_excerpts",
        fake_excerpts,
    )

    episode = SimpleNamespace(
        id=_uuid.uuid4(),
        tenant_id=_uuid.uuid4(),
        title="t",
        root_cause_summary="r" * 30,
        final_outcome="o" * 30,
        contradictions=[],
        evidence_ids=[str(_uuid.uuid4()), str(_uuid.uuid4())],
        reviewer_state="pending_review",
        ai_review=None,
        status="pending",
        domain_id=None,
    )

    class FakeResult:
        def __init__(self, steps=None, fresh=None):
            self._steps, self._fresh = steps, fresh

        def scalars(self):
            return SimpleNamespace(all=lambda: self._steps or [])

        def scalar_one_or_none(self):
            return self._fresh

    class FakeDB:
        def __init__(self):
            self.calls = 0
            self.flushed = False

        async def execute(self, *a, **k):
            self.calls += 1
            if self.calls == 1:  # steps query
                return FakeResult(steps=[])
            # the FOR UPDATE re-read: a human approved meanwhile
            return FakeResult(
                fresh=SimpleNamespace(
                    reviewer_state="approved", ai_review=None
                )
            )

        async def flush(self):
            self.flushed = True

    db = FakeDB()
    out = await ai_review_episode(
        db, episode.tenant_id, episode, mode="auto_approve"
    )
    assert out["skipped_state_changed"] is True
    assert out["approved"] is False
    assert episode.ai_review is None, "nothing may be stamped over a human decision"
    assert episode.status == "pending", "state must not change"
    assert db.flushed is False


def test_orphaned_auto_approvals_get_signature_redispatch():
    """Crash-after-commit recovery: an auto-approved episode with no
    signature link gets its dispatch re-sent (bounded, idempotent,
    scoped to auto-approvals so the pre-signature era is untouched)."""
    import inspect

    from contextedge.workers import evaluation_tasks

    source = inspect.getsource(evaluation_tasks.ai_review_episodes)
    assert "EpisodeIssueSignature" in source
    assert "auto_approved" in source
    assert ".exists()" in source


def test_human_approve_endpoints_commit_before_dispatch():
    import inspect

    from contextedge.api.v1 import episodes as episodes_api

    for endpoint in (episodes_api.approve_episode, episodes_api.bulk_approve_episodes):
        source = inspect.getsource(endpoint)
        commit_pos = source.index("await db.commit()")
        dispatch_pos = source.index("extract_issue_signature_task.delay")
        assert commit_pos < dispatch_pos, (
            f"{endpoint.__name__} must make the approval durable before workers hear of it"
        )
