"""Blueprint §1.5 dependency auto-construction: co-occurrence edges."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.graph.agent.profiles import MAF_RELATIONSHIP_TYPES, MAF_V1
from contextedge.services import dependency_inference_service as svc


def test_confidence_scales_with_cases_and_caps():
    assert svc.pair_confidence(3) == pytest.approx(0.3)
    assert svc.pair_confidence(5) == pytest.approx(0.5)
    assert svc.pair_confidence(50) == pytest.approx(svc.CONFIDENCE_CAP)


def test_edge_is_projectable_with_derivation_metadata():
    assert "co_fails_with" in MAF_RELATIONSHIP_TYPES
    assert "shared_cases" in MAF_V1.relationship_metadata["co_fails_with"]
    # Inferred relatedness must never outrank authored topology by weight.
    assert MAF_V1.relationship_factor("co_fails_with") <= MAF_V1.relationship_factor("depends_on")


def _rows_result(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


def _scalars_result(values):
    r = MagicMock()
    r.scalars.return_value.all.return_value = list(values)
    return r


def _co_edge(a, b, confidence, shared, origin="co_occurrence"):
    return SimpleNamespace(
        source_node_id=a,
        target_node_id=b,
        confidence=confidence,
        metadata_extra={"origin": origin, "shared_cases": shared, "symmetric": True},
        valid_to=None,
    )


def _sweep_db(pair_rows, existing_edges=(), monitor_rows=(), stale_entities=()):
    """Execute order: pair SQL, existing co_fails_with edges, monitor
    SQL, stale-attribute entities."""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _rows_result(pair_rows),
            _scalars_result(existing_edges),
            _rows_result(monitor_rows),
            _scalars_result(stale_entities),
        ]
    )
    db.get = AsyncMock(return_value=None)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_sweep_writes_one_symmetric_edge_per_pair():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = _sweep_db([(a, b, 4)])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["pairs"] == 1 and counts["edges"] == 1
    kwargs = edge.await_args.kwargs
    assert kwargs["edge_type"] == "co_fails_with"
    assert kwargs["confidence"] == pytest.approx(0.4)
    assert kwargs["metadata"]["shared_cases"] == 4
    assert kwargs["metadata"]["origin"] == "co_occurrence"


@pytest.mark.asyncio
async def test_no_pairs_means_no_edges():
    db = _sweep_db([])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["pairs"] == 0 and counts["edges"] == 0
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_grown_pair_refreshes_confidence_on_the_existing_edge():
    """ensure_edge returns existing edges untouched — the sweep itself
    must push new counts onto the edge or confidence freezes forever."""
    a, b = uuid.uuid4(), uuid.uuid4()
    existing = _co_edge(a, b, confidence=0.4, shared=4)
    db = _sweep_db([(a, b, 6)], existing_edges=[existing])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    edge.assert_not_awaited()  # refreshed in place, not re-ensured
    assert counts["refreshed"] == 1 and counts["edges"] == 0
    assert existing.confidence == pytest.approx(0.6)
    assert existing.metadata_extra["shared_cases"] == 6


@pytest.mark.asyncio
async def test_unchanged_pair_touches_nothing():
    a, b = uuid.uuid4(), uuid.uuid4()
    existing = _co_edge(a, b, confidence=svc.pair_confidence(4), shared=4)
    db = _sweep_db([(a, b, 4)], existing_edges=[existing])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()):
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["refreshed"] == 0 and counts["expired"] == 0


@pytest.mark.asyncio
async def test_dropped_pair_expires_the_edge_but_not_foreign_edges():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    ours = _co_edge(a, b, confidence=0.3, shared=3)
    foreign = _co_edge(c, d, confidence=0.9, shared=0, origin="authored")
    db = _sweep_db([], existing_edges=[ours, foreign])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()):
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["expired"] == 1
    assert ours.valid_to is not None  # closed, never deleted
    assert foreign.valid_to is None  # not ours to expire


@pytest.mark.asyncio
async def test_truncated_pair_query_skips_expiry():
    """With LIMIT hit, an absent pair may just be past the cutoff —
    expiring it would flap the edge on every sweep."""
    rows = [(uuid.uuid4(), uuid.uuid4(), 3) for _ in range(svc.MAX_PAIRS_PER_RUN)]
    a, b = uuid.uuid4(), uuid.uuid4()
    survivor = _co_edge(a, b, confidence=0.3, shared=3)
    db = _sweep_db(rows, existing_edges=[survivor])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()):
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["expired"] == 0
    assert survivor.valid_to is None


@pytest.mark.asyncio
async def test_monitoring_sources_reconcile_not_union():
    ci = uuid.uuid4()
    tenant_id = uuid.uuid4()
    entity = SimpleNamespace(
        id=ci,
        tenant_id=tenant_id,
        attributes={"monitoring_sources": ["em_alert", "splunk_log"]},
    )
    db = _sweep_db([], monitor_rows=[(ci, ["em_alert"])])
    db.get = AsyncMock(return_value=entity)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()):
        counts = await svc.infer_co_failure_edges(db, tenant_id)
    # splunk_log no longer observed -> dropped, not kept forever.
    assert entity.attributes["monitoring_sources"] == ["em_alert"]
    assert counts["monitored_cis"] == 1


@pytest.mark.asyncio
async def test_unobserved_ci_has_coverage_cleared():
    stale = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        attributes={"monitoring_sources": ["splunk_log"], "criticality": "high"},
    )
    db = _sweep_db([], stale_entities=[stale])
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()):
        counts = await svc.infer_co_failure_edges(db, stale.tenant_id)
    assert "monitoring_sources" not in stale.attributes
    assert stale.attributes["criticality"] == "high"  # other facts untouched
    assert counts["monitored_cis"] == 1
