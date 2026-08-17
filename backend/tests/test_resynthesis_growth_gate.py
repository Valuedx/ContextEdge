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


def _db(rows):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    )


@pytest.mark.asyncio
async def test_finds_the_biggest_episode_the_cluster_covers():
    cluster = [uuid.uuid4() for _ in range(5)]
    small, big = uuid.uuid4(), uuid.uuid4()
    rows = [
        (small, [str(cluster[0]), str(cluster[1])]),
        (big, [str(c) for c in cluster[:4]]),
    ]

    found = await _largest_covered_episode(_db(rows), uuid.uuid4(), cluster)

    assert found == (4, big)


@pytest.mark.asyncio
async def test_an_episode_citing_outside_evidence_is_not_covered():
    """Containment, not overlap.

    An episode built partly from evidence this cluster does not contain is
    about different material — re-telling this cluster does not supersede it,
    so it must not suppress the synthesis either.
    """
    cluster = [uuid.uuid4() for _ in range(3)]
    outsider = uuid.uuid4()
    rows = [(uuid.uuid4(), [str(cluster[0]), str(outsider)])]

    assert await _largest_covered_episode(_db(rows), uuid.uuid4(), cluster) is None


@pytest.mark.asyncio
async def test_no_prior_episode_means_no_suppression():
    cluster = [uuid.uuid4()]
    assert await _largest_covered_episode(_db([]), uuid.uuid4(), cluster) is None


@pytest.mark.asyncio
async def test_empty_cluster_is_handled():
    assert await _largest_covered_episode(_db([]), uuid.uuid4(), []) is None


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
