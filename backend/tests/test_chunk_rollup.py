"""Chunk search-side rollup: MMR diversity + one-hit-per-parent grouping."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.search.chunk_rollup import (
    ChunkCandidate,
    mmr_order,
    rollup_best_chunk_per_evidence,
)


def _candidate(distance, embedding, evidence_id=None, **kw):
    return ChunkCandidate(
        chunk_id=kw.get("chunk_id", uuid4()),
        evidence_id=evidence_id or uuid4(),
        distance=distance,
        embedding=embedding,
        parent_section=kw.get("parent_section"),
        chunk_kind=kw.get("chunk_kind", "message"),
        snippet=kw.get("snippet", "RADIUS authentication timeout"),
    )


# --- MMR --------------------------------------------------------------------


def test_mmr_demotes_near_duplicates():
    """A slightly-worse near-duplicate of the best hit must rank BELOW a
    clearly distinct candidate — the whole point of chunk-level MMR."""
    best = _candidate(0.10, [1.0, 0.0, 0.0])
    duplicate = _candidate(0.12, [1.0, 0.0, 0.0])  # same direction as best
    distinct = _candidate(0.30, [0.0, 1.0, 0.0])   # orthogonal

    ordered = mmr_order([best, duplicate, distinct], select_n=3)
    assert ordered[0] is best
    assert ordered[1] is distinct  # diversity beats the near-duplicate
    assert ordered[2] is duplicate


def test_mmr_pure_relevance_when_no_duplication():
    a = _candidate(0.10, [1.0, 0.0, 0.0])
    b = _candidate(0.20, [0.0, 1.0, 0.0])
    c = _candidate(0.30, [0.0, 0.0, 1.0])
    assert mmr_order([c, a, b], select_n=3) == [a, b, c]


def test_mmr_caps_selection_and_handles_edges():
    candidates = [_candidate(0.1 * i, [float(i), 1.0]) for i in range(1, 6)]
    assert len(mmr_order(candidates, select_n=2)) == 2
    assert mmr_order([], select_n=5) == []
    assert mmr_order(candidates[:1], select_n=5) == candidates[:1]


def test_mmr_degrades_to_relevance_order_on_missing_embeddings():
    a = _candidate(0.30, None)
    b = _candidate(0.10, [1.0, 0.0])
    assert mmr_order([a, b], select_n=2) == [b, a]


def test_relevance_clamps_distance_range():
    assert _candidate(-0.5, None).relevance == 1.0
    assert _candidate(3.0, None).relevance == 0.0


# --- rollup -----------------------------------------------------------------


def test_rollup_keeps_closest_chunk_per_evidence():
    evidence = uuid4()
    far = _candidate(0.40, None, evidence_id=evidence)
    near = _candidate(0.15, None, evidence_id=evidence)
    other = _candidate(0.25, None)

    rolled = rollup_best_chunk_per_evidence([far, near, other])
    assert [c.distance for c in rolled] == [0.15, 0.25]
    assert rolled[0] is near  # the closest chunk represents the parent


# --- search flow ------------------------------------------------------------


def _chunk_row(chunk_id, evidence_id, distance, embedding, snippet="users cannot log in"):
    return (chunk_id, evidence_id, distance, embedding, "Timeline > 14:32", "message", snippet)


def _rows_result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


@pytest.mark.asyncio
async def test_search_rolls_up_chunks_and_merges_parent_pass():
    from contextedge.search.vector_search import search_evidence_semantic

    tenant_id = uuid4()
    chunked_item = SimpleNamespace(id=uuid4())
    unchunked_item = SimpleNamespace(id=uuid4())

    chunk_a = _chunk_row(uuid4(), chunked_item.id, 0.10, [1.0, 0.0])
    chunk_b = _chunk_row(uuid4(), chunked_item.id, 0.12, [1.0, 0.0])  # same parent

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _rows_result([chunk_a, chunk_b]),          # chunk ANN
                _scalars_result([chunked_item]),           # parent fetch for rolled
                _rows_result(                              # parent-embedding pass
                    [(chunked_item, 0.11), (unchunked_item, 0.20)]
                ),
            ]
        )
    )

    with patch("contextedge.search.vector_search.tune_ann_recall", AsyncMock()):
        results = await search_evidence_semantic(
            db, tenant_id, "vpn login failures", limit=10, query_embedding=[1.0, 0.0]
        )

    assert [(row[0], row[1]) for row in results] == [
        (chunked_item, 0.10),   # rolled-up: best chunk represents the parent
        (unchunked_item, 0.20),  # parent-pass fallback for unchunked evidence
    ]
    best_chunk = results[0][2]
    assert best_chunk["parent_section"] == "Timeline > 14:32"
    assert best_chunk["snippet"] == "users cannot log in"
    assert results[1][2] is None  # no chunk context on the parent-pass hit


@pytest.mark.asyncio
async def test_chunk_query_carries_visibility_predicates():
    from contextedge.search.vector_search import search_evidence_semantic

    captured: list[str] = []

    async def execute(stmt):
        captured.append(str(stmt))
        if len(captured) == 1:
            return _rows_result([])
        return _rows_result([])

    db = SimpleNamespace(execute=execute)
    with patch("contextedge.search.vector_search.tune_ann_recall", AsyncMock()):
        await search_evidence_semantic(
            db,
            uuid4(),
            "q",
            limit=5,
            query_embedding=[0.1],
            exclude_policy_ids=[uuid4()],
        )

    chunk_sql, parent_sql = captured[0], captured[1]
    for sql in (chunk_sql, parent_sql):
        assert "sensitivity_label" in sql  # legal-hold exclusion (bound param)
        assert "redaction_status" in sql
        assert "access_policy_id" in sql
    assert "evidence_chunks" in chunk_sql


@pytest.mark.asyncio
async def test_playbook_variant_keeps_ranker_row_shape():
    from contextedge.search.hybrid_ranker import _semantic_corpus_score
    from contextedge.search.vector_search import search_evidence_semantic_for_playbook

    tenant_id = uuid4()
    item = SimpleNamespace(id=uuid4())
    chunk = _chunk_row(uuid4(), item.id, 0.30, [1.0, 0.0])

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _rows_result([chunk]),
                _scalars_result([item]),
                _rows_result([]),
            ]
        )
    )
    with patch("contextedge.search.vector_search.tune_ann_recall", AsyncMock()):
        rows = await search_evidence_semantic_for_playbook(
            db,
            tenant_id,
            uuid4(),
            uuid4(),
            "q",
            query_embedding=[1.0, 0.0],
        )

    score, count = _semantic_corpus_score(rows)
    assert count == 1
    assert score == pytest.approx(1.0 - 0.30 / 2.0)
