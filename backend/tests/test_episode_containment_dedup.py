"""Retiring episodes whose evidence is wholly contained in a larger one.

Title-based dedup cannot fire on the real failure. Measured on the live
corpus: one ticket held 44 live episodes of a single "Agent Unknown State"
incident with ZERO exact-title matches between any pair, because each time a
thread message landed the extractor wrote a fresh, differently-worded
account. 97 pairs had one evidence set fully containing another. 190 of 434
covered tickets carried 4+ episodes.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.services.episode_service import supersede_contained_episodes


def _ep(evidence, *, state="pending_review", confidence=0.9, title="t"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=title,
        evidence_ids=[str(e) for e in evidence],
        reviewer_state=state,
        extraction_confidence=confidence,
        created_at=None,
    )


def _db(episodes):
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: list(episodes))
    )
    return SimpleNamespace(execute=AsyncMock(return_value=result), flush=AsyncMock())


@pytest.mark.asyncio
async def test_contained_episode_is_retired():
    """The whole point: a regrown cluster retires its earlier telling."""
    big = _ep([1, 2, 3, 4, 5], title="Troubleshooting Agent VSM01 'Unknown State'")
    small = _ep([2, 3], title="'Agent Unknown' Issue Investigation")
    out = await supersede_contained_episodes(_db([big, small]), uuid.uuid4(), dry_run=True)

    assert out["retired"] == 1
    assert out["live_after"] == 1
    assert out["pairs"] == [(str(small.id), str(big.id))]


@pytest.mark.asyncio
async def test_partial_overlap_is_left_alone():
    """The safety property, and the reason there is no threshold.

    On the live ticket, 148 pairs overlapped WITHOUT containment at Jaccard
    0.04-0.33 — "BOT Failures After OS Upgrade" against "Agent VSM01 Unknown
    State". Those share a ticket, not an incident. Any threshold low enough
    to catch them fuses genuinely different problems.
    """
    a = _ep([1, 2, 3, 4, 5], title="BOT Failures After OS Upgrade")
    b = _ep([5, 6, 7, 8], title="Agent VSM01 in Unknown State")
    out = await supersede_contained_episodes(_db([a, b]), uuid.uuid4(), dry_run=True)

    assert out["retired"] == 0
    assert out["live_after"] == 2


@pytest.mark.asyncio
async def test_a_chain_collapses_in_one_pass():
    """A ⊇ B ⊇ C must all fold into A, not leave C pointing at a retired B."""
    a = _ep([1, 2, 3, 4])
    b = _ep([1, 2, 3])
    c = _ep([1, 2])
    out = await supersede_contained_episodes(_db([c, b, a]), uuid.uuid4(), dry_run=True)

    assert out["retired"] == 2
    assert out["live_after"] == 1
    # Everything folds into the largest, never into an already-retired one.
    assert {p[1] for p in out["pairs"]} == {str(a.id)}


@pytest.mark.asyncio
async def test_identical_sets_keep_exactly_one():
    a = _ep([1, 2, 3], confidence=0.9)
    b = _ep([1, 2, 3], confidence=0.5)
    out = await supersede_contained_episodes(_db([a, b]), uuid.uuid4(), dry_run=True)

    assert out["retired"] == 1
    assert out["live_after"] == 1


@pytest.mark.asyncio
async def test_disjoint_episodes_are_untouched():
    a = _ep([1, 2])
    b = _ep([3, 4])
    out = await supersede_contained_episodes(_db([a, b]), uuid.uuid4(), dry_run=True)
    assert out["retired"] == 0


@pytest.mark.asyncio
async def test_episodes_without_evidence_are_skipped():
    """An empty set is a subset of everything — retiring on that would
    delete every episode the extractor failed to attribute."""
    empty = _ep([])
    real = _ep([1, 2, 3])
    out = await supersede_contained_episodes(_db([empty, real]), uuid.uuid4(), dry_run=True)

    assert out["retired"] == 0
    # Both survive: the empty one is ignored by the containment test rather
    # than being swept up by it.
    assert out["live_after"] == 2


@pytest.mark.asyncio
async def test_dry_run_writes_nothing():
    big, small = _ep([1, 2, 3]), _ep([1, 2])
    db = _db([big, small])
    out = await supersede_contained_episodes(db, uuid.uuid4(), dry_run=True)

    assert out["retired"] == 1
    db.flush.assert_not_awaited()
    assert small.reviewer_state == "pending_review"  # untouched


def test_the_dedup_entry_point_runs_both_new_passes():
    """Wiring, not logic.

    Both passes are correct in isolation and worthless if nothing calls
    them: the graph re-inflates between manual runs. `deduplicate_
    patterns_and_playbooks` is the single entry point the API and the
    pattern task both use, so both passes belong in it.
    """
    import inspect

    from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks

    source = inspect.getsource(deduplicate_patterns_and_playbooks)
    assert "supersede_contained_episodes" in source
    assert "supersede_similar_episodes" in source
    # Containment is strict and cheap; running it first leaves less for the
    # judgement-based pass to weigh.
    assert source.index("supersede_contained_episodes") < source.index(
        "supersede_similar_episodes"
    )


def test_playbook_dedup_skips_retired_audit_rows():
    import inspect

    from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks

    source = inspect.getsource(deduplicate_patterns_and_playbooks)
    playbook_section = source.split("# 2. Deduplicate active playbooks", 1)[1]
    assert 'lifecycle_state.notin_(("retired", "deprecated"))' in playbook_section


@pytest.mark.asyncio
async def test_merge_fold_leaves_steps_with_the_duplicate():
    """Steps must NOT move onto the canonical: every dedup sweep that did
    so concatenated whole narrations — 949 live episodes ended up with
    timelines repeating the same complaint dozens of times (worst: 319
    steps from ~13 tellings). The canonical keeps its own narrative; the
    superseded duplicate keeps its steps as audit history. Pinned on the
    source because the fold is shared by the title sweep and the
    containment sweep."""
    import inspect

    from contextedge.services.episode_service import _merge_episode_into

    source = inspect.getsource(_merge_episode_into)
    assert "step.episode_id = canonical.id" not in source
    assert "Steps deliberately STAY" in source
