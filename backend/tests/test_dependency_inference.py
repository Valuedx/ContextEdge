"""Blueprint Â§1.5 dependency auto-construction: co-occurrence edges."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.services import dependency_inference_service as svc
from contextedge.graph.agent.profiles import MAF_RELATIONSHIP_TYPES, MAF_V1


def test_confidence_scales_with_cases_and_caps():
    assert svc.pair_confidence(3) == pytest.approx(0.3)
    assert svc.pair_confidence(5) == pytest.approx(0.5)
    assert svc.pair_confidence(50) == pytest.approx(svc.CONFIDENCE_CAP)


def test_edge_is_projectable_with_derivation_metadata():
    assert "co_fails_with" in MAF_RELATIONSHIP_TYPES
    assert "shared_cases" in MAF_V1.relationship_metadata["co_fails_with"]
    # Inferred relatedness must never outrank authored topology by weight.
    assert MAF_V1.relationship_factor("co_fails_with") <= MAF_V1.relationship_factor("depends_on")


@pytest.mark.asyncio
async def test_sweep_writes_one_symmetric_edge_per_pair():
    a, b = uuid.uuid4(), uuid.uuid4()
    db = MagicMock()
    pair_result = MagicMock()
    pair_result.all.return_value = [(a, b, 4)]
    monitor_result = MagicMock()
    monitor_result.all.return_value = []  # monitoring pass no-ops here
    db.execute = AsyncMock(side_effect=[pair_result, monitor_result])
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
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await svc.infer_co_failure_edges(db, uuid.uuid4())
    assert counts["pairs"] == 0 and counts["edges"] == 0
    edge.assert_not_awaited()
