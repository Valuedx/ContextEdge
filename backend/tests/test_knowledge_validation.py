"""Phase 3 first slice: empirical validation of knowledge.

Answers "has this procedure ever actually worked?" from data already
recorded — no new LLM extraction. Deliberately does NOT judge whether an
article is correct; empirical support is one dimension, and collapsing
it into a single trust score invites automation against evidence that
does not support automation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.services.knowledge_validation_service import (
    CONTESTED_FAILURE_RATIO,
    PROVEN_MIN_VERIFIED,
    SUPPORT_CONTESTED,
    SUPPORT_EMERGING,
    SUPPORT_PROVEN,
    SUPPORT_UNPROVEN,
    KnowledgeValidation,
    classify_support,
    validate_knowledge_item,
)

# --- support classification --------------------------------------------------


def test_never_exercised_is_unproven_not_failing():
    """Most knowledge is simply not used often. Treating absence as a
    negative signal would demote the whole corpus on day one."""
    assert (
        classify_support(
            executions=0, verified_successes=0, reported_successes=0, failures=0
        )
        == SUPPORT_UNPROVEN
    )


def test_repeated_verified_success_is_proven():
    assert (
        classify_support(
            executions=5,
            verified_successes=PROVEN_MIN_VERIFIED,
            reported_successes=0,
            failures=0,
        )
        == SUPPORT_PROVEN
    )


def test_a_single_verified_success_is_only_emerging():
    """One success is an anecdote. Repetition is what distinguishes
    "worked once" from "works"."""
    assert (
        classify_support(
            executions=1, verified_successes=1, reported_successes=0, failures=0
        )
        == SUPPORT_EMERGING
    )


def test_reported_success_alone_never_reaches_proven():
    """execution_runs distinguishes a run that CLAIMED success from one
    whose effect was re-checked against telemetry. Conflating them lets
    a playbook that always reports success look proven."""
    assert (
        classify_support(
            executions=20,
            verified_successes=0,
            reported_successes=20,
            failures=0,
        )
        == SUPPORT_EMERGING
    )


def test_failures_alongside_successes_read_as_contested_not_proven():
    """A procedure with many verified successes AND many failures is not
    proven — it is inconsistent, which is the more actionable fact."""
    support = classify_support(
        executions=20, verified_successes=10, reported_successes=0, failures=10
    )
    assert support == SUPPORT_CONTESTED


def test_contested_is_checked_before_proven():
    """Ordering matters: plenty of verified successes must not mask a
    failure rate above the threshold."""
    verified = PROVEN_MIN_VERIFIED + 10
    failures = int((verified / (1 - CONTESTED_FAILURE_RATIO)) * CONTESTED_FAILURE_RATIO) + 1
    assert (
        classify_support(
            executions=verified + failures,
            verified_successes=verified,
            reported_successes=0,
            failures=failures,
        )
        == SUPPORT_CONTESTED
    )


def test_failure_ratio_is_reported_for_the_reviewer():
    v = KnowledgeValidation(
        evidence_id=uuid.uuid4(),
        title="SOP",
        verified_successes=3,
        failures=1,
    )
    assert v.failure_ratio == 0.25
    assert KnowledgeValidation(evidence_id=uuid.uuid4(), title="x").failure_ratio == 0.0


# --- attribution -------------------------------------------------------------


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


@pytest.mark.asyncio
async def test_knowledge_with_no_playbook_is_unproven_and_costs_no_queries():
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="Unused SOP")),
        execute=AsyncMock(return_value=_Result([])),
    )
    out = await validate_knowledge_item(db, uuid.uuid4(), uuid.uuid4())
    assert out.support == SUPPORT_UNPROVEN
    assert out.playbook_versions == 0
    assert out.executions == 0
    # Only the link lookup ran; no execution query for an uncited item.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_outcomes_are_attributed_to_the_citing_version_only():
    """A knowledge item cited by v1 must not collect credit for v3's
    executions — v3 may have dropped the very step the article
    supported, so those runs say nothing about it.
    """
    version_id = uuid.uuid4()
    captured: list[object] = []

    runs = [
        SimpleNamespace(outcome="success", verification_status="verified"),
        SimpleNamespace(outcome="success", verification_status="verified"),
        SimpleNamespace(outcome="success", verification_status="verified"),
        SimpleNamespace(outcome="success", verification_status=None),
        SimpleNamespace(outcome="failed", verification_status=None),
    ]

    async def execute(stmt):
        captured.append(str(stmt))
        return _Result([version_id] if len(captured) == 1 else runs)

    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="VPN SOP")), execute=execute
    )
    out = await validate_knowledge_item(db, uuid.uuid4(), uuid.uuid4())

    assert out.playbook_versions == 1
    assert out.verified_successes == 3
    assert out.reported_successes == 1
    assert out.failures == 1
    # The execution query filters on the version, not the playbook.
    assert "playbook_version_id" in captured[1]
    assert "execution_runs.playbook_id IN" not in captured[1]


@pytest.mark.asyncio
async def test_a_run_whose_verification_failed_counts_as_a_failure():
    """It reported success and the re-check disagreed. Counting it as a
    success would let post-action verification find a problem and have
    no effect on how the knowledge is judged."""
    version_id = uuid.uuid4()
    calls = {"n": 0}

    async def execute(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Result([version_id])
        return _Result(
            [SimpleNamespace(outcome="success", verification_status="failed")]
        )

    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="SOP")), execute=execute
    )
    out = await validate_knowledge_item(db, uuid.uuid4(), uuid.uuid4())
    assert out.failures == 1
    assert out.verified_successes == 0


@pytest.mark.asyncio
async def test_serialization_exposes_counts_not_a_single_score():
    """A single trust number invites automation against evidence that
    does not support automation. The dimensions stay separate."""
    version_id = uuid.uuid4()
    calls = {"n": 0}

    async def execute(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Result([version_id])
        return _Result(
            [SimpleNamespace(outcome="success", verification_status="verified")]
        )

    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(title="SOP")), execute=execute
    )
    payload = (await validate_knowledge_item(db, uuid.uuid4(), uuid.uuid4())).as_dict()

    for key in (
        "support",
        "executions",
        "verified_successes",
        "reported_successes",
        "failures",
        "failure_ratio",
        "version_ids",
    ):
        assert key in payload
    assert "trust_score" not in payload
    assert "score" not in payload
