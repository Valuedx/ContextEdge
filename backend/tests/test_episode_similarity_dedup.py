"""Retiring episodes that re-tell one incident from a different slice.

Containment dedup handles a cluster that GREW. This handles repeated
reconstructions of the SAME cluster, where the extractor splits it
differently each run and yields equal-sized, overlapping-but-not-nested
accounts — "Trend Micro Quarantines ChromeDriver 149" beside "Trend Micro
Quarantines ChromeDriver".

The load-bearing rule is the refusal: shared evidence is required. At cosine
>= 0.85 the live corpus held 319 pairs sharing evidence and 29 sharing none,
and the disjoint ones ("SSO 403 Forbidden Error" vs "SSO Configuration and
Login Failure (HTTP 403 Forbidden)") are indistinguishable by embedding from
a recurrence of the same problem weeks later. Merging those would fuse two
real occurrences and destroy the recurrence signal.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.services.episode_service import (
    SIMILAR_EPISODE_MIN_COSINE,
    supersede_similar_episodes,
)


def _ep(evidence, *, title="t", confidence=0.9, state="pending_review"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        evidence_ids=[str(e) for e in evidence],
        reviewer_state=state,
        extraction_confidence=confidence,
        embedding=[0.1, 0.2],
        created_at=None,
    )


def _db(episodes, pairs):
    """First execute() returns the episodes, second the candidate pairs."""
    ep_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: list(episodes))
    )
    pair_result = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: list(pairs)))
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[ep_result, pair_result]),
        flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_similar_episodes_sharing_evidence_are_merged():
    a = _ep([1, 2, 3], title="Trend Micro Quarantines ChromeDriver 149")
    b = _ep([2, 3], title="Trend Micro Quarantines ChromeDriver")
    db = _db([a, b], [{"a_id": a.id, "b_id": b.id, "cosine": 0.977}])

    out = await supersede_similar_episodes(db, uuid.uuid4(), dry_run=True)

    assert out["retired"] == 1
    # The fullest account survives, not merely the first seen.
    assert out["examples"][0]["kept"] == "Trend Micro Quarantines ChromeDriver 149"


@pytest.mark.asyncio
async def test_no_shared_evidence_is_refused_however_similar():
    """The recurrence guard — the one rule that must not be relaxed.

    An embedding cannot tell one incident from its recurrence next month.
    Only shared evidence can, so without it the pass declines to act even at
    near-identical wording.
    """
    a = _ep([1, 2], title="SSO 403 Forbidden Error")
    b = _ep([8, 9], title="SSO Configuration and Login Failure (HTTP 403 Forbidden)")
    db = _db([a, b], [{"a_id": a.id, "b_id": b.id, "cosine": 0.918}])

    out = await supersede_similar_episodes(db, uuid.uuid4(), dry_run=True)

    assert out["retired"] == 0
    assert out["refused_no_shared_evidence"] == 1


@pytest.mark.asyncio
async def test_a_pair_below_the_threshold_is_left_alone():
    """Distinct incidents on one ticket topped out at 0.578 on real data."""
    a = _ep([1, 2], title="BOT Failures After OS Upgrade")
    b = _ep([2, 3], title="Agent Unknown State Investigation")
    # Below threshold pairs never reach the loop — SQL filters them — so the
    # candidate list is empty, which is what this asserts the caller relies on.
    db = _db([a, b], [])

    out = await supersede_similar_episodes(db, uuid.uuid4(), dry_run=True)
    assert out["retired"] == 0


@pytest.mark.asyncio
async def test_an_episode_is_retired_at_most_once():
    """Three mutually-similar episodes must collapse to one survivor, and a
    retired episode must never then be used as a merge target."""
    a = _ep([1, 2, 3], title="A")
    b = _ep([1, 2], title="B")
    c = _ep([1], title="C")
    db = _db(
        [a, b, c],
        [
            {"a_id": a.id, "b_id": b.id, "cosine": 0.95},
            {"a_id": a.id, "b_id": c.id, "cosine": 0.93},
            {"a_id": b.id, "b_id": c.id, "cosine": 0.92},
        ],
    )

    out = await supersede_similar_episodes(db, uuid.uuid4(), dry_run=True)

    assert out["retired"] == 2
    assert out["live_after"] == 1


@pytest.mark.asyncio
async def test_dry_run_writes_nothing():
    a = _ep([1, 2, 3], title="A")
    b = _ep([2, 3], title="B")
    db = _db([a, b], [{"a_id": a.id, "b_id": b.id, "cosine": 0.95}])

    out = await supersede_similar_episodes(db, uuid.uuid4(), dry_run=True)

    assert out["retired"] == 1
    db.flush.assert_not_awaited()
    assert b.reviewer_state == "pending_review"


def test_threshold_is_clear_of_the_observed_false_pairs():
    """0.85 is a measured choice, not a default.

    Clearly-different incidents on the same ticket peaked at cosine 0.578 on
    the live corpus; re-narrations of one incident ran 0.81-0.98.
    """
    assert SIMILAR_EPISODE_MIN_COSINE >= 0.80
