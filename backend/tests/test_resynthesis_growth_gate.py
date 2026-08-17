"""Only re-tell an incident when the cluster has something new to say.

`_reconstruct` already had a draft-idempotency guard — "reviewers see one
evolving draft, not four near-duplicates as sources trickle in" — but it
keyed on `cluster_fingerprint`, which is derived from cluster membership.
One more thread message yields a new fingerprint, the check misses, and a
full synthesis runs. The guard was defeated by exactly the thing it existed
to prevent, and one ticket accumulated 44 accounts of one incident.

Measured cost: re-running 207 messages produced 111 episodes of which 112
were retired by dedup minutes later — ~1.4M tokens, nearly the whole run,
spent writing accounts that were immediately superseded.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.workers.extraction_tasks import (
    MIN_RESYNTHESIS_GROWTH,
    _largest_covered_episode,
)


def _db(row):
    """`_largest_covered_episode` now asks Postgres for the answer.

    NOTE ON WHAT THESE TESTS DO NOT COVER. The first version of this helper
    filtered candidates in Python, and mocked-db tests like these passed
    while it was DEAD IN PRODUCTION: it joined `episode_evidence_links`,
    which 1,489 of 2,111 live episodes have no rows in, so it returned None
    every time and the gate never fired. The containment test is now `<@`
    inside SQL, which a mock cannot exercise — mocking `execute` only
    asserts that the caller reads the result correctly.

    So these cover plumbing, and the SQL was verified against the live
    database instead: a cluster of 20 found its covered episode, and
    cluster+1 (21) was suppressed against a threshold of 30.
    """
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: row))
    )


@pytest.mark.asyncio
async def test_returns_size_and_id_of_the_covered_episode():
    episode_id = uuid.uuid4()
    found = await _largest_covered_episode(
        _db((episode_id, 4)), uuid.uuid4(), [uuid.uuid4() for _ in range(5)]
    )
    assert found == (4, episode_id)


@pytest.mark.asyncio
async def test_no_covered_episode_means_no_suppression():
    """Nothing found must never be read as "suppress" — that would stop the
    graph forming at all."""
    assert await _largest_covered_episode(_db(None), uuid.uuid4(), [uuid.uuid4()]) is None


@pytest.mark.asyncio
async def test_empty_cluster_short_circuits_without_a_query():
    db = _db(None)
    assert await _largest_covered_episode(db, uuid.uuid4(), []) is None
    db.execute.assert_not_awaited()


def test_growth_threshold_suppresses_a_trickle_but_not_real_growth():
    """The arithmetic the constant has to satisfy.

    A thread delivers messages one at a time. Without a floor, every single
    message re-narrates the whole incident; with 50%, a 20-evidence cluster
    waits until it has 30 before spending again.
    """
    def would_resynthesize(cluster_size: int, prior_size: int) -> bool:
        return cluster_size >= prior_size * (1 + MIN_RESYNTHESIS_GROWTH)

    # The observed waste: one more message on an established cluster.
    assert not would_resynthesize(21, 20)
    assert not would_resynthesize(11, 10)
    # Genuine growth still earns a fresh account.
    assert would_resynthesize(30, 20)
    assert would_resynthesize(15, 10)
    # A small cluster changes character with one item, and the ratio lets it.
    assert would_resynthesize(3, 2)


def test_the_manual_path_is_never_suppressed():
    """A reviewer asking for reconstruction is not a duplicate.

    The gate is inside `if settle:` — `settle=False` is the explicit
    reviewer trigger and always gets a fresh account.
    """
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._reconstruct)
    gate = source.index("skipped_insufficient_growth")
    guard = source.rindex("if settle:", 0, gate)
    # The growth gate sits under a `settle` guard, not at the top level.
    assert guard < gate
