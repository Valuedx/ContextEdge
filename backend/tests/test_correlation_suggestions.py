"""Gated semantic correlation suggestions (P3): similarity floor +
non-semantic corroborator gate + reviewer accept/reject."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from contextedge.models.correlation_suggestion import CorrelationSuggestion
from contextedge.models.episode import CorrelationEdge
from contextedge.services.correlation_suggestion_service import (
    SIMILARITY_FLOOR,
    _pair_key,
    accept_suggestion,
    reject_suggestion,
    suggest_semantic_correlations,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _scalars_all(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def _rows_all(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def _suggestion_db(
    *,
    query_chunk_embeddings,
    ann_rows,
    seed_identities,
    shared_identity_rows,
    seed_cases,
    shared_case_rows,
    existing_edges,
    added,
    seed_visible=True,
    identity_degrees=None,
):
    """SQL-dispatching fake for the generator: routes by SELECT column."""

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SET LOCAL hnsw.ef_search"):
            return result
        if text.startswith("SELECT evidence_items.id"):
            result.scalar_one_or_none.return_value = (
                uuid4() if seed_visible else None
            )
            return result
        if text.startswith("SELECT evidence_chunks.embedding"):
            return _scalars_all(query_chunk_embeddings)
        if text.startswith("SELECT evidence_chunks.evidence_id"):
            return _rows_all(ann_rows)
        if text.startswith("SELECT correlation_edges.source_evidence_id"):
            return _rows_all(existing_edges)
        if text.startswith("SELECT evidence_identity_links.identity_id") and "count(" in text:
            degrees = identity_degrees
            if degrees is None:
                degrees = [(i, 3) for i in seed_identities]
            return _rows_all(degrees)
        if text.startswith("SELECT evidence_identity_links.identity_id"):
            return _scalars_all(seed_identities)
        if text.startswith("SELECT evidence_identity_links.evidence_id"):
            return _rows_all(shared_identity_rows)
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            return _scalars_all(seed_cases)
        if text.startswith("SELECT evidence_case_memberships.evidence_id"):
            return _rows_all(shared_case_rows)
        raise AssertionError(f"unrouted statement: {text[:80]}")

    return SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )


def test_pair_key_is_order_independent():
    a, b = uuid4(), uuid4()
    assert _pair_key(a, b) == _pair_key(b, a)
    low, high = _pair_key(a, b)
    assert low.bytes < high.bytes


@pytest.mark.asyncio
async def test_corroborated_candidate_becomes_pending_suggestion():
    tenant_id = uuid4()
    seed_id = uuid4()
    other_id = uuid4()
    identity_id = uuid4()
    added = []
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(other_id, 0.2)],  # similarity 0.8, above floor
        seed_identities=[identity_id],
        shared_identity_rows=[(other_id, identity_id)],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=added,
    )

    counts = await suggest_semantic_correlations(db, tenant_id, seed_id)

    assert counts == {"suggested": 1, "candidates": 1, "uncorroborated": 0}
    (suggestion,) = [a for a in added if isinstance(a, CorrelationSuggestion)]
    assert suggestion.status == "pending"
    assert suggestion.similarity == pytest.approx(0.8)
    assert suggestion.corroborators == [f"shared_identity:{identity_id}"]
    assert (suggestion.evidence_id_low, suggestion.evidence_id_high) == _pair_key(
        seed_id, other_id
    )


@pytest.mark.asyncio
async def test_similar_but_uncorroborated_is_never_suggested():
    """The core P3 gate: wording similarity alone must not reach a reviewer."""
    tenant_id = uuid4()
    seed_id = uuid4()
    other_id = uuid4()
    added = []
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(other_id, 0.05)],  # similarity 0.95 — very alike
        seed_identities=[],
        shared_identity_rows=[],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=added,
    )

    counts = await suggest_semantic_correlations(db, tenant_id, seed_id)

    assert counts["suggested"] == 0
    assert counts["uncorroborated"] == 1
    assert added == []


@pytest.mark.asyncio
async def test_below_floor_candidates_are_dropped():
    tenant_id = uuid4()
    seed_id = uuid4()
    other_id = uuid4()
    added = []
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(other_id, 1.0 - (SIMILARITY_FLOOR - 0.05))],
        seed_identities=[uuid4()],
        shared_identity_rows=[],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=added,
    )

    counts = await suggest_semantic_correlations(db, tenant_id, seed_id)

    assert counts == {"suggested": 0, "candidates": 0, "uncorroborated": 0}
    assert added == []


@pytest.mark.asyncio
async def test_already_correlated_pair_is_skipped():
    tenant_id = uuid4()
    seed_id = uuid4()
    other_id = uuid4()
    identity_id = uuid4()
    added = []
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(other_id, 0.1)],
        seed_identities=[identity_id],
        shared_identity_rows=[(other_id, identity_id)],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[(seed_id, other_id)],
        added=added,
    )

    counts = await suggest_semantic_correlations(db, tenant_id, seed_id)

    assert counts["suggested"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_no_embedded_chunks_is_noop():
    tenant_id = uuid4()
    db = _suggestion_db(
        query_chunk_embeddings=[],
        ann_rows=[],
        seed_identities=[],
        shared_identity_rows=[],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=[],
    )
    counts = await suggest_semantic_correlations(db, tenant_id, uuid4())
    assert counts == {"suggested": 0, "candidates": 0, "uncorroborated": 0}


@pytest.mark.asyncio
async def test_accept_creates_edge_and_marks_reviewed():
    tenant_id = uuid4()
    low, high = _pair_key(uuid4(), uuid4())
    suggestion = CorrelationSuggestion(
        tenant_id=tenant_id,
        evidence_id_low=low,
        evidence_id_high=high,
        similarity=0.81,
        corroborators=["shared_case:x"],
    )
    added = []
    db = SimpleNamespace(add=added.append, flush=AsyncMock())

    edge = await accept_suggestion(db, tenant_id, suggestion, "reviewer@acme.com")

    assert isinstance(edge, CorrelationEdge)
    assert edge.correlation_type == "semantic_suggestion"
    assert edge.confidence == 0.6
    assert edge.created_by == "reviewer@acme.com"
    assert "0.81" in edge.explanation
    assert suggestion.status == "accepted"
    assert suggestion.reviewed_by == "reviewer@acme.com"
    assert suggestion.reviewed_at is not None


@pytest.mark.asyncio
async def test_reject_is_recorded_without_edge():
    tenant_id = uuid4()
    low, high = _pair_key(uuid4(), uuid4())
    suggestion = CorrelationSuggestion(
        tenant_id=tenant_id,
        evidence_id_low=low,
        evidence_id_high=high,
        similarity=0.75,
        corroborators=["shared_identity:y"],
    )
    added = []
    db = SimpleNamespace(add=added.append, flush=AsyncMock())

    await reject_suggestion(db, tenant_id, suggestion, "reviewer@acme.com")

    assert suggestion.status == "rejected"
    assert suggestion.reviewed_at is not None
    assert added == []  # rejection never creates an edge


@pytest.mark.asyncio
async def test_hub_identity_does_not_corroborate():
    """P2's hub rule applies to the corroborator gate too."""
    tenant_id = uuid4()
    seed_id = uuid4()
    other_id = uuid4()
    identity_id = uuid4()
    added = []
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(other_id, 0.1)],
        seed_identities=[identity_id],
        shared_identity_rows=[(other_id, identity_id)],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=added,
        identity_degrees=[(identity_id, 500)],  # hub
    )

    counts = await suggest_semantic_correlations(db, tenant_id, seed_id)

    assert counts["suggested"] == 0
    assert counts["uncorroborated"] == 1
    assert added == []


@pytest.mark.asyncio
async def test_invisible_seed_generates_nothing():
    """A legal-hold seed must not surface through the suggestion queue."""
    tenant_id = uuid4()
    db = _suggestion_db(
        query_chunk_embeddings=[[0.1] * 4],
        ann_rows=[(uuid4(), 0.1)],
        seed_identities=[uuid4()],
        shared_identity_rows=[],
        seed_cases=[],
        shared_case_rows=[],
        existing_edges=[],
        added=[],
        seed_visible=False,
    )
    counts = await suggest_semantic_correlations(db, tenant_id, uuid4())
    assert counts == {"suggested": 0, "candidates": 0, "uncorroborated": 0}
