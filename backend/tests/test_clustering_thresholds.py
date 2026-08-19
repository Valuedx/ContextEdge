"""Clustering picks the NEAREST existing pattern, not an arbitrary one.

`LIMIT 1` without `ORDER BY` returns whatever the planner hands back. On a
corpus where every episode has some pattern member within the prefilter
distance — measured, because 0.35 was near the 10th percentile of the gap
between two random episodes — that meant handing the LLM validator a
near-random pattern and asking "is this a match?". It said no 88% of the
time and each rejection minted a single-episode "pattern".
"""

from __future__ import annotations

import inspect

from contextedge.workers import pattern_tasks
from contextedge.workers.pattern_tasks import (
    CLUSTER_GROUP_MAX_DISTANCE,
    PATTERN_MATCH_MAX_DISTANCE,
)


def test_existing_pattern_match_is_ordered_by_distance():
    """The regression this module exists for. Asserted on source because
    the query runs inside a long DB-bound clustering pass; what matters is
    that the ordering is present at all."""
    source = inspect.getsource(pattern_tasks._cluster)
    match_block = source.split("matched_pattern_id")[0]

    assert "order_by(member_distance.asc())" in match_block, (
        "the existing-pattern lookup must take the nearest member; "
        "LIMIT 1 unordered returns an arbitrary pattern"
    )


def test_thresholds_are_named_not_inline_literals():
    """They were bare numbers in the middle of a query, which is how they
    went unexamined long enough to be wrong."""
    source = inspect.getsource(pattern_tasks._cluster)

    assert "PATTERN_MATCH_MAX_DISTANCE" in source
    assert "CLUSTER_GROUP_MAX_DISTANCE" in source
    assert "< 0.35" not in source
    assert "< 0.20" not in source


def test_group_threshold_is_stricter_than_the_match_prefilter():
    """Joining an established pattern is a weaker claim than founding a new
    cluster with a stranger, and the LLM validator backs the former up. If
    grouping were the looser of the two, unrelated episodes would fuse into
    new patterns with nothing checking them."""
    assert CLUSTER_GROUP_MAX_DISTANCE < PATTERN_MATCH_MAX_DISTANCE


def test_thresholds_sit_inside_the_measured_corpus_spread():
    """Guards against a future edit that reintroduces a value with no
    discriminating power. Random approved-episode pairs on this corpus span
    0.157 to 0.524 with median 0.409; a threshold at or above the median
    admits most of the corpus and clusters everything into one blob (0.40
    measured at mean cluster size 66)."""
    for threshold in (CLUSTER_GROUP_MAX_DISTANCE, PATTERN_MATCH_MAX_DISTANCE):
        assert 0.15 < threshold < 0.35
