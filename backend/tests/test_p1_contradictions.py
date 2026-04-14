from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.models.events import OperationalEvent
from contextedge.models.pattern import Contradiction, GraphEdge
from contextedge.services.contradiction_service import scan_contradictions


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


@pytest.mark.asyncio
async def test_contradiction_detected():
    tenant_id = uuid4()
    domain_id = uuid4()
    playbook_id = uuid4()
    evidence_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=domain_id,
        stable_key="pb-123",
        title="Restart Service",
    )
    version = SimpleNamespace(
        playbook_id=playbook_id,
        semantic_version="1.0.0",
        published_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        steps=[{"text": "restart service x"}],
    )
    evidence = SimpleNamespace(
        id=evidence_id,
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_type="kb_article",
        body_text="Never restart service x during incident response.",
        title="KB Article",
    )
    added = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarsResult([evidence]),
                _ScalarOneOrNoneResult(version),
                _ScalarOneOrNoneResult(None),
                _ScalarOneOrNoneResult(None),
            ]
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.contradiction_service.llm_complete_json",
            AsyncMock(return_value={"contradiction": True, "reason": "KB explicitly says never restart"}),
        ),
        patch(
            "contextedge.services.contradiction_service.send_notification",
            AsyncMock(),
        ) as notify_mock,
    ):
        result = await scan_contradictions(db, tenant_id, domain_id=domain_id)

    assert result["contradictions_created"] == 1
    assert any(isinstance(obj, Contradiction) for obj in added)
    assert any(isinstance(obj, GraphEdge) for obj in added)
    assert any(isinstance(obj, OperationalEvent) for obj in added)
    notify_mock.assert_awaited_once()

    graph_edges = [obj for obj in added if isinstance(obj, GraphEdge)]
    assert len(graph_edges) >= 1
    for ge in graph_edges:
        assert ge.domain_id == domain_id


@pytest.mark.asyncio
async def test_no_false_positive_contradiction():
    tenant_id = uuid4()
    domain_id = uuid4()
    playbook = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=domain_id,
        stable_key="pb-456",
        title="Restart Service",
    )
    version = SimpleNamespace(
        playbook_id=playbook.id,
        semantic_version="1.0.0",
        published_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        steps=[{"text": "restart service x"}],
    )
    evidence = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_type="kb_article",
        body_text="Restart service x after verifying the maintenance window.",
        title="KB Article",
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarsResult([evidence]),
                _ScalarOneOrNoneResult(version),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
    )

    with patch(
        "contextedge.services.contradiction_service.llm_complete_json",
        AsyncMock(return_value={"contradiction": False, "reason": "Same recommendation"}),
    ):
        result = await scan_contradictions(db, tenant_id, domain_id=domain_id)

    assert result["contradictions_created"] == 0
    assert result["contradictions_updated"] == 0
    db.add.assert_not_called()
