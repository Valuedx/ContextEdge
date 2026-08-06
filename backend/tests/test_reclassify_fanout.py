"""A3: re-classification repairs what a stale verdict skipped.

The wrapper dispatches the retrieval fan-out (chunk + correlate +
baseline) only when _classify reports the item flipped to relevant and
was never chunked — and always AFTER run_async has committed, mirroring
normalize_evidence's post-commit dispatch pattern.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from contextedge.workers import evidence_baseline_tasks, extraction_tasks


def _run_classify_with(res: dict):
    with (
        patch.object(extraction_tasks, "run_async", return_value=res),
        patch.object(extraction_tasks.chunk_evidence_task, "delay") as chunk_mock,
        patch.object(extraction_tasks.correlate_evidence, "delay") as correlate_mock,
        patch.object(
            evidence_baseline_tasks.compute_evidence_baseline_task, "delay",
        ) as baseline_mock,
    ):
        out = extraction_tasks.classify_relevance_task.run(str(uuid4()), str(uuid4()))
    return out, chunk_mock, correlate_mock, baseline_mock


def test_flipped_unchunked_item_gets_fanout():
    res = {"evidence_id": "e1", "classification": "operational", "needs_fanout": True}
    out, chunk_mock, correlate_mock, baseline_mock = _run_classify_with(res)
    assert out == res
    chunk_mock.assert_called_once()
    correlate_mock.assert_called_once()
    baseline_mock.assert_called_once()


def test_not_relevant_item_gets_no_fanout():
    res = {"evidence_id": "e1", "classification": "not_relevant", "needs_fanout": False}
    _, chunk_mock, correlate_mock, baseline_mock = _run_classify_with(res)
    chunk_mock.assert_not_called()
    correlate_mock.assert_not_called()
    baseline_mock.assert_not_called()


def test_error_result_gets_no_fanout():
    """_classify returns {"error": ...} for missing/foreign evidence —
    no needs_fanout key must mean no dispatch, not a KeyError."""
    _, chunk_mock, correlate_mock, baseline_mock = _run_classify_with({"error": "evidence_not_found"})
    chunk_mock.assert_not_called()
    correlate_mock.assert_not_called()
    baseline_mock.assert_not_called()


def test_maintenance_sweep_dispatches_per_row():
    from contextedge.workers import maintenance_tasks

    ids = [str(uuid4()) for _ in range(3)]
    with (
        patch.object(maintenance_tasks, "run_async", return_value=ids),
        patch.object(maintenance_tasks.classify_relevance_task, "delay") as classify_mock,
    ):
        out = maintenance_tasks.reclassify_stale_evidence_task.run(str(uuid4()))
    assert out == {"dispatched": 3}
    assert classify_mock.call_count == 3
