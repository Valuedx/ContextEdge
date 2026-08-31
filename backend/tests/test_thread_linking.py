"""Tests for Thread creation during normalization and connector thread_ref consistency."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.evidence_normalization import ensure_thread_for_evidence


def _scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_ensure_thread_creates_thread_when_missing():
    tenant_id = uuid4()
    source_id = uuid4()
    source_object_id = uuid4()
    evidence = SimpleNamespace(
        id=uuid4(),
        source_id=source_id,
        source_object_id=source_object_id,
        thread_id=None,
    )
    payload = {"_thread_id": "user@example.com:abc123", "subject": "Test Subject"}

    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_scalar_one_or_none_result(None)),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    thread_id = await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=evidence, payload=payload,
    )

    assert thread_id is not None
    assert evidence.thread_id is not None
    assert len(added) == 1
    thread_obj = added[0]
    assert thread_obj.external_thread_id == "user@example.com:abc123"
    assert thread_obj.tenant_id == tenant_id
    assert thread_obj.source_id == source_id
    assert thread_obj.hydration_status == "pending"
    assert thread_obj.title == "Test Subject"


@pytest.mark.asyncio
async def test_ensure_thread_reuses_existing_thread():
    tenant_id = uuid4()
    source_id = uuid4()
    existing_thread_id = uuid4()
    existing_thread = SimpleNamespace(id=existing_thread_id)
    evidence = SimpleNamespace(
        id=uuid4(),
        source_id=source_id,
        source_object_id=None,
        thread_id=None,
    )
    payload = {"_thread_id": "user@example.com:abc123"}

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_scalar_one_or_none_result(existing_thread)),
        flush=AsyncMock(),
    )

    thread_id = await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=evidence, payload=payload,
    )

    assert thread_id == existing_thread_id
    assert evidence.thread_id == existing_thread_id


@pytest.mark.asyncio
async def test_ensure_thread_returns_none_without_thread_id():
    tenant_id = uuid4()
    evidence = SimpleNamespace(
        id=uuid4(),
        source_id=uuid4(),
        source_object_id=None,
        thread_id=None,
    )

    db = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    result = await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=evidence, payload={"body": "no thread"},
    )

    assert result is None
    assert evidence.thread_id is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_thread_returns_none_for_empty_payload():
    tenant_id = uuid4()
    evidence = SimpleNamespace(
        id=uuid4(), source_id=uuid4(), source_object_id=None, thread_id=None,
    )
    db = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    result = await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=evidence, payload={},
    )

    assert result is None


def test_gmail_backfill_emits_compound_thread_id():
    """Verify Gmail backfill thread_id has email:threadId format."""
    from contextedge.connectors.base import IngestionEvent

    user_email = "shared@example.com"
    gmail_thread_id = "18abcdef12345678"
    ev = IngestionEvent(
        external_id=gmail_thread_id,
        source_type="gmail",
        object_type="email_thread",
        content={"subject": "test"},
        thread_id=f"{user_email}:{gmail_thread_id}",
    )
    assert ":" in ev.thread_id
    parts = ev.thread_id.split(":")
    assert parts[0] == user_email
    assert parts[1] == gmail_thread_id


def test_teams_backfill_emits_compound_thread_id():
    """Verify Teams thread_id has teamId:channelId:msgId format."""
    from contextedge.connectors.base import IngestionEvent

    team_id = "team-001"
    channel_id = "chan-002"
    msg_id = "msg-003"
    ev = IngestionEvent(
        external_id=msg_id,
        source_type="teams",
        object_type="channel_message",
        content={"body": "hello"},
        thread_id=f"{team_id}:{channel_id}:{msg_id}",
    )
    parts = ev.thread_id.split(":")
    assert len(parts) == 3
    assert parts == [team_id, channel_id, msg_id]


def test_servicenow_backfill_emits_compound_thread_id():
    """Verify ServiceNow thread_id has table:sys_id format."""
    from contextedge.connectors.base import IngestionEvent

    table = "incident"
    sys_id = "abc123def456"
    ev = IngestionEvent(
        external_id=sys_id,
        source_type="servicenow",
        object_type=table,
        content={"number": "INC0001234"},
        thread_id=f"{table}:{sys_id}",
    )
    parts = ev.thread_id.split(":")
    assert len(parts) == 2
    assert parts == [table, sys_id]


def test_jira_thread_id_is_issue_key():
    """Jira thread_id should be the issue key, matching hydrate_thread expectation."""
    from contextedge.connectors.base import IngestionEvent

    ev = IngestionEvent(
        external_id="PROJ-123",
        source_type="jira_sm",
        object_type="issue",
        content={"summary": "test"},
        thread_id="PROJ-123",
    )
    assert ev.thread_id == "PROJ-123"


def test_related_evidence_inherits_ticket_version_without_overwriting():
    from contextedge.services.evidence_normalization import (
        merge_inherited_ticket_facets,
    )

    ticket = {"version": "8.2.3", "ticket_number": "245390"}
    assert merge_inherited_ticket_facets({}, ticket) == ticket
    assert merge_inherited_ticket_facets({"version": "7*"}, ticket) == {
        "version": "7*",
        "ticket_number": "245390",
    }
    assert merge_inherited_ticket_facets({"version": "8.2.3"}, {}) == {"version": "8.2.3"}


@pytest.mark.asyncio
async def test_thread_message_inherits_version_from_the_ticket_on_its_thread():
    from contextedge.services.evidence_normalization import (
        _inherit_ticket_facets_from_thread,
    )

    tenant_id = uuid4()
    thread_id = uuid4()
    ticket = SimpleNamespace(
        evidence_type="ticket",
        thread_id=thread_id,
        source_facets={"version": "8.2.3", "ticket_number": "245390"},
    )
    message = SimpleNamespace(
        evidence_type="thread_message",
        thread_id=thread_id,
        source_facets={},
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_scalar_one_or_none_result(ticket)),
    )
    assert await _inherit_ticket_facets_from_thread(db, tenant_id, message) is True
    assert message.source_facets["version"] == "8.2.3"
    assert message.source_facets["ticket_number"] == "245390"


def test_normalize_calls_ticket_facet_sync_after_thread_link():
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._normalize)
    assert "sync_related_ticket_facets" in source
    assert source.index("ensure_thread_for_evidence") < source.index(
        "sync_related_ticket_facets"
    )
