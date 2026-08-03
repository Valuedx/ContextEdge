"""E6 safety slice: stale approvals expire, never auto-approve."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.approval_expiry_service import expire_stale_approvals


def _request(age_hours):
    return SimpleNamespace(
        id=uuid4(),
        execution_run_id=uuid4(),
        requested_action="restart production database",
        status="pending",
        decided_at=None,
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )


@pytest.mark.asyncio
async def test_stale_pending_requests_expire_with_event(monkeypatch):
    tenant_id = uuid4()
    stale = _request(100)
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )

    async def execute(stmt):
        result = Mock()
        result.scalars.return_value.all.return_value = [stale]
        return result

    out = await expire_stale_approvals(
        SimpleNamespace(execute=execute), tenant_id
    )

    assert out == {"expired": 1}
    assert stale.status == "expired"  # blocked, never approved
    assert stale.decided_at is not None
    assert events[0]["event_type"] == "execution.approval_expired"


@pytest.mark.asyncio
async def test_no_stale_requests_is_noop(monkeypatch):
    tenant_id = uuid4()
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )

    async def execute(stmt):
        result = Mock()
        result.scalars.return_value.all.return_value = []
        return result

    out = await expire_stale_approvals(SimpleNamespace(execute=execute), tenant_id)
    assert out == {"expired": 0}
    assert events == []
