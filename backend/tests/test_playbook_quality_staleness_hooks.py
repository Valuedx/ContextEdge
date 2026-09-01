"""Staleness hooks from contradiction and drift into quality assessments."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.contradiction_service import scan_contradictions
from contextedge.services.drift_service import check_playbook_drift


class _RowsResult:
    def __init__(self, values=None):
        self._values = values or []

    def all(self):
        return self._values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_drift_scan_signals_quality_stale_for_alerted_playbooks():
    tenant_id = uuid4()
    playbook_id = uuid4()
    alerts = [
        {
            "playbook_id": str(playbook_id),
            "pattern_id": None,
            "title": "Restart service",
            "issues": ["pattern_nodes_added_drift"],
            "severity": "medium",
        }
    ]

    with patch(
        "contextedge.services.drift_service.list_drift_alerts",
        AsyncMock(return_value=alerts),
    ), patch(
        "contextedge.services.drift_service.apply_expired_playbook_transitions",
        AsyncMock(return_value=0),
    ), patch(
        "contextedge.services.playbook_quality_service.signal_quality_stale",
        AsyncMock(return_value=1),
    ) as signal:
        result = await check_playbook_drift(SimpleNamespace(), tenant_id)

    signal.assert_awaited_once()
    kwargs = signal.await_args.kwargs
    assert kwargs["reason"] == "source_changed"
    assert kwargs["origin"] == "drift_scan"
    assert signal.await_args.args[2] == playbook_id
    assert result["quality_invalidated"] == 1


@pytest.mark.asyncio
async def test_drift_scan_does_not_signal_when_no_alerts():
    tenant_id = uuid4()
    with patch(
        "contextedge.services.drift_service.list_drift_alerts",
        AsyncMock(return_value=[]),
    ), patch(
        "contextedge.services.drift_service.apply_expired_playbook_transitions",
        AsyncMock(return_value=0),
    ), patch(
        "contextedge.services.playbook_quality_service.signal_quality_stale",
        AsyncMock(),
    ) as signal:
        result = await check_playbook_drift(SimpleNamespace(), tenant_id)

    signal.assert_not_awaited()
    assert result["quality_invalidated"] == 0


@pytest.mark.asyncio
async def test_contradiction_scan_signals_quality_stale_when_confirmed():
    tenant_id = uuid4()
    domain_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=domain_id,
        stable_key="pb-test",
        title="Restart Service",
    )
    version = SimpleNamespace(
        id=uuid4(),
        playbook_id=playbook_id,
        semantic_version="1.0.0",
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        steps=[{"text": "restart service x safely"}],
    )
    evidence = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_type="kb_article",
        body_text="never restart service x during incident response",
        title="KB",
        ingested_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _RowsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _RowsResult([]),
            ]
        ),
    )

    with patch(
        "contextedge.services.contradiction_service.generate_embedding",
        AsyncMock(return_value=[0.1, 0.2]),
    ), patch(
        "contextedge.services.contradiction_service._top_k_kb_candidates",
        AsyncMock(return_value=[evidence]),
    ), patch(
        "contextedge.services.contradiction_service._llm_confirms_contradiction",
        AsyncMock(return_value=(True, "conflicts with KB")),
    ), patch(
        "contextedge.services.contradiction_service._get_or_create_contradiction",
        AsyncMock(return_value=(SimpleNamespace(
            id=uuid4(),
            source_a_ref="playbook:pb-test:1.0.0",
            source_b_ref="evidence:abc",
            description="conflicts with KB",
        ), True)),
    ), patch(
        "contextedge.services.contradiction_service.add_contradicts_edge",
        AsyncMock(),
    ), patch(
        "contextedge.services.contradiction_service.append_operational_event",
        AsyncMock(),
    ), patch(
        "contextedge.services.contradiction_service.send_notification",
        AsyncMock(),
    ), patch(
        "contextedge.services.contradiction_service._record_scan_state",
        AsyncMock(),
    ), patch(
        "contextedge.services.playbook_quality_service.signal_quality_stale",
        AsyncMock(return_value=1),
    ) as signal:
        await scan_contradictions(db, tenant_id, domain_id=domain_id, max_llm_calls=5)

    signal.assert_awaited_once()
    assert signal.await_args.kwargs["origin"] == "contradiction_scan"
    assert signal.await_args.kwargs["reason"] == "source_changed"
    assert signal.await_args.args[2] == playbook_id


@pytest.mark.asyncio
async def test_signal_quality_stale_is_failure_tolerant():
    from contextedge.services.playbook_quality_service import (
        STALE_SOURCE_CHANGED,
        signal_quality_stale,
    )

    with patch(
        "contextedge.services.playbook_quality_service.invalidate_assessments",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        count = await signal_quality_stale(
            SimpleNamespace(),
            uuid4(),
            uuid4(),
            reason=STALE_SOURCE_CHANGED,
            origin="test",
        )
    assert count == 0


@pytest.mark.asyncio
async def test_contradiction_scan_does_not_signal_when_llm_disagrees():
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=None,
        stable_key="pb-test",
        title="Restart Service",
    )
    version = SimpleNamespace(
        id=uuid4(),
        playbook_id=playbook_id,
        semantic_version="1.0.0",
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        steps=[{"text": "restart service x safely"}],
    )
    evidence = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=None,
        evidence_type="kb_article",
        body_text="restart service x when healthy",
        title="KB",
        ingested_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _RowsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _RowsResult([]),
            ]
        ),
    )

    with patch(
        "contextedge.services.contradiction_service.generate_embedding",
        AsyncMock(return_value=[0.1, 0.2]),
    ), patch(
        "contextedge.services.contradiction_service._top_k_kb_candidates",
        AsyncMock(return_value=[evidence]),
    ), patch(
        "contextedge.services.contradiction_service._llm_confirms_contradiction",
        AsyncMock(return_value=(False, None)),
    ), patch(
        "contextedge.services.contradiction_service._record_scan_state",
        AsyncMock(),
    ), patch(
        "contextedge.services.playbook_quality_service.signal_quality_stale",
        AsyncMock(),
    ) as signal:
        await scan_contradictions(db, tenant_id, max_llm_calls=5)

    signal.assert_not_awaited()

