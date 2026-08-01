from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.models.episode import CorrelationEdge
from contextedge.models.events import OperationalEvent
from contextedge.services.correlation_service import (
    correlate_evidence_item,
    extract_case_link_candidates,
)
from contextedge.workers import extraction_tasks


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def test_extract_case_link_candidates_includes_external_and_thread():
    raw = SimpleNamespace(external_id="INC-100", raw_payload={"_thread_id": "THREAD-1"})

    candidates = extract_case_link_candidates(
        source_type="servicenow",
        raw_object=raw,
        thread_external_id="THREAD-1",
    )

    assert candidates == [
        ("servicenow", "INC-100"),
        ("servicenow:thread", "THREAD-1"),
    ]


def test_normalize_chains_correlation_task():
    """After the classify-before-embed refactor, normalize runs classification
    inline and no longer dispatches classify_relevance_task. The post-normalize
    fan-out is trimmed to correlation + baseline."""
    from contextedge.workers import evidence_baseline_tasks

    evidence_id = str(uuid4())

    with (
        patch.object(extraction_tasks, "run_async", return_value={"evidence_id": evidence_id}),
        patch.object(extraction_tasks.classify_relevance_task, "delay") as classify_mock,
        patch.object(extraction_tasks.correlate_evidence, "delay") as correlate_mock,
        patch.object(
            evidence_baseline_tasks.compute_evidence_baseline_task, "delay",
        ) as baseline_mock,
    ):
        result = extraction_tasks.normalize_evidence.run(str(uuid4()), str(uuid4()))

    assert result == {"evidence_id": evidence_id}
    # classify_relevance_task is NOT part of the default fan-out anymore —
    # classification is done inline in _normalize before the expensive work.
    classify_mock.assert_not_called()
    correlate_mock.assert_called_once_with(evidence_id, ANY)
    baseline_mock.assert_called_once_with(evidence_id, ANY)


@pytest.mark.asyncio
async def test_correlation_links_matching_external_id():
    tenant_id = uuid4()
    evidence_id = uuid4()
    other_evidence_id = uuid4()
    raw_id = uuid4()
    source_id = uuid4()
    canonical_case_id = uuid4()
    evidence = SimpleNamespace(
        id=evidence_id,
        tenant_id=tenant_id,
        source_id=source_id,
        raw_object_ref=raw_id,
        thread_id=None,
        ingested_at=None,
    )
    source = SimpleNamespace(id=source_id, tenant_id=tenant_id, source_type="servicenow", config={})
    raw = SimpleNamespace(external_id="INC-100", raw_payload={})
    existing_link = SimpleNamespace(
        system="servicenow",
        external_id="INC-100",
        canonical_case_id=canonical_case_id,
        evidence_id=other_evidence_id,
        confidence=0.8,
        last_seen=None,
    )
    added = []

    def add(obj):
        added.append(obj)

    db = SimpleNamespace(
        get=AsyncMock(side_effect=[evidence, source, raw]),
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([existing_link]),
                _ScalarOneOrNoneResult(None),
            ]
        ),
        add=add,
        flush=AsyncMock(),
    )

    with patch(
        "contextedge.services.correlation_service.get_identity_ids_for_evidence",
        AsyncMock(return_value=set()),
    ):
        result = await correlate_evidence_item(db, tenant_id, evidence_id)

    assert result["canonical_case_id"] == str(canonical_case_id)
    assert result["correlations_created"] == 1
    assert any(isinstance(obj, CorrelationEdge) for obj in added)
    assert any(isinstance(obj, OperationalEvent) for obj in added)
