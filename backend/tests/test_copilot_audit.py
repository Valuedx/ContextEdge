from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1.copilot import (
    CopilotConversationIn,
    CopilotIngestRequest,
    CopilotUsageEventIn,
    copilot_usage_summary,
    delete_copilot_conversation,
    get_copilot_conversation,
    ingest_copilot_events,
    list_copilot_conversations,
)
from contextedge.models.copilot import CopilotConversation
from contextedge.services.copilot_audit_service import (
    CopilotConversationAccessError,
    append_conversation,
    get_conversation,
    soft_delete_conversation,
)
from .conftest import make_user


class FakeSession:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.added = []

    async def get(self, model, key):
        return self.rows.get(key)

    def add(self, obj):
        self.added.append(obj)
        self.rows[getattr(obj, "id", id(obj))] = obj

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_ingest_forces_jwt_user_id():
    user = make_user(roles=["analyst"])
    requested = uuid4()
    captured = {}

    async def fake_event(db, *, tenant_id, user_id, payload):
        captured["user_id"] = user_id
        return SimpleNamespace(id=uuid4())

    with patch("contextedge.api.v1.copilot.audit.ingest_usage_event", fake_event):
        result = await ingest_copilot_events(
            CopilotIngestRequest(
                user_id=str(requested),
                events=[CopilotUsageEventIn(user_id=str(requested), event_type="chat")],
            ),
            db=SimpleNamespace(),
            user=user,
        )
    assert captured["user_id"] == user.user_id
    assert result["event_ids"]


@pytest.mark.asyncio
async def test_service_ingest_requires_user_id():
    user = make_user(roles=["tenant_admin"], principal_type="service_account")
    with pytest.raises(HTTPException) as exc:
        await ingest_copilot_events(CopilotIngestRequest(events=[CopilotUsageEventIn()]), SimpleNamespace(), user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_usage_summary_refuses_non_admin():
    user = make_user(roles=["analyst"])
    with pytest.raises(HTTPException) as exc:
        await copilot_usage_summary(
            db=SimpleNamespace(),
            user=user,
            days=30,
            from_ts=None,
            to_ts=None,
            group_by="user",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_usage_summary_admin_ok():
    user = make_user(roles=["tenant_admin"])
    rows = [{"user_id": str(user.user_id), "username": "ops", "login_count": 2, "chat_count": 3, "total_tokens": 40}]

    async def fake_summary(*args, **kwargs):
        assert kwargs["group_by"] == "user"
        return rows

    with patch("contextedge.api.v1.copilot.audit.usage_summary", fake_summary):
        result = await copilot_usage_summary(
            db=SimpleNamespace(),
            user=user,
            days=30,
            from_ts=None,
            to_ts=None,
            group_by="user",
        )
    assert result["group_by"] == "user"
    assert result["rows"] == rows


@pytest.mark.asyncio
async def test_list_conversations_is_scoped_to_caller():
    user = make_user(roles=["analyst"])
    captured = {}

    async def fake_list(*args, **kwargs):
        captured.update(kwargs)
        return []

    with patch("contextedge.api.v1.copilot.audit.list_conversations", fake_list):
        result = await list_copilot_conversations(
            db=SimpleNamespace(),
            user=user,
            limit=50,
            offset=0,
        )
    assert captured["user_id"] == user.user_id
    assert captured["admin"] is False
    assert result["items"] == []


@pytest.mark.asyncio
async def test_list_conversations_user_filter_requires_admin():
    user = make_user(roles=["analyst"])
    with pytest.raises(HTTPException) as exc:
        await list_copilot_conversations(db=SimpleNamespace(), user=user, user_id=str(uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_conversation_hides_other_user():
    user = make_user(roles=["analyst"])

    async def fake_get(*args, **kwargs):
        return None

    with patch("contextedge.api.v1.copilot.audit.get_conversation", fake_get):
        with pytest.raises(HTTPException) as exc:
            await get_copilot_conversation(uuid4(), SimpleNamespace(), user)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_soft_delete_disappears_from_get():
    user = make_user(roles=["analyst"])
    conversation_id = uuid4()

    async def fake_delete(*args, **kwargs):
        return True

    async def fake_get(*args, **kwargs):
        return None

    with patch("contextedge.api.v1.copilot.audit.soft_delete_conversation", fake_delete):
        result = await delete_copilot_conversation(conversation_id, SimpleNamespace(), user)
    assert result == {"ok": True}
    with patch("contextedge.api.v1.copilot.audit.get_conversation", fake_get):
        with pytest.raises(HTTPException) as exc:
            await get_copilot_conversation(conversation_id, SimpleNamespace(), user)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_append_conversation_rejects_other_user():
    owner = uuid4()
    tenant = uuid4()
    conversation_id = uuid4()
    existing = CopilotConversation(
        id=conversation_id,
        tenant_id=tenant,
        user_id=owner,
        mode="ticket",
        title="Secret",
    )
    db = FakeSession({conversation_id: existing})
    with pytest.raises(CopilotConversationAccessError):
        await append_conversation(
            db,
            tenant_id=tenant,
            user_id=uuid4(),
            payload={"id": str(conversation_id), "messages": [{"role": "user", "content": "hi"}]},
        )


@pytest.mark.asyncio
async def test_append_conversation_then_soft_delete_hides_row():
    tenant = uuid4()
    user_id = uuid4()
    db = FakeSession()
    conversation = await append_conversation(
        db,
        tenant_id=tenant,
        user_id=user_id,
        payload={
            "title": "Suggestion",
            "mode": "ticket",
            "ticket_id": "123",
            "messages": [
                {"role": "user", "content": "Suggestion"},
                {"role": "assistant", "content": "Restart the agent."},
            ],
        },
    )
    assert conversation.message_count == 2
    assert conversation.title == "Suggestion"
    found = await get_conversation(
        db, tenant_id=tenant, user_id=user_id, admin=False, conversation_id=conversation.id
    )
    assert found is not None
    other = await get_conversation(
        db, tenant_id=tenant, user_id=uuid4(), admin=False, conversation_id=conversation.id
    )
    assert other is None
    deleted = await soft_delete_conversation(
        db, tenant_id=tenant, user_id=user_id, conversation_id=conversation.id
    )
    assert deleted is True
    hidden = await get_conversation(
        db, tenant_id=tenant, user_id=user_id, admin=False, conversation_id=conversation.id
    )
    assert hidden is None
    assert conversation.deleted_at is not None


@pytest.mark.asyncio
async def test_ingest_conversation_idor_is_forbidden():
    user = make_user(roles=["analyst"])

    async def boom(*args, **kwargs):
        raise CopilotConversationAccessError("conversation belongs to another user")

    with patch("contextedge.api.v1.copilot.audit.append_conversation", boom):
        with pytest.raises(HTTPException) as exc:
            await ingest_copilot_events(
                CopilotIngestRequest(conversation=CopilotConversationIn(id=str(uuid4()), messages=[])),
                SimpleNamespace(),
                user,
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_other_user_conversation():
    admin = make_user(roles=["tenant_admin"])
    conversation = SimpleNamespace(
        id=uuid4(),
        mode="ticket",
        ticket_id="t1",
        ticket_number="48213",
        title="Suggestion",
        message_count=2,
        total_tokens=20,
        started_at=datetime.now(UTC),
        last_message_at=datetime.now(UTC),
        user_id=uuid4(),
    )

    async def fake_get(*args, **kwargs):
        assert kwargs["admin"] is True
        return conversation

    async def fake_messages(*args, **kwargs):
        return [
            SimpleNamespace(
                seq=1,
                role="user",
                content="Suggestion",
                action="solution",
                citations=None,
                created_at=datetime.now(UTC),
            )
        ]

    with (
        patch("contextedge.api.v1.copilot.audit.get_conversation", fake_get),
        patch("contextedge.api.v1.copilot.audit.list_messages", fake_messages),
    ):
        result = await get_copilot_conversation(conversation.id, SimpleNamespace(), admin)
    assert result["title"] == "Suggestion"
    assert result["messages"][0]["content"] == "Suggestion"


@pytest.mark.asyncio
async def test_list_conversations_search_strips_leading_hash():
    user = make_user(roles=["analyst"])
    captured = {}

    async def fake_list(*args, **kwargs):
        captured.update(kwargs)
        return [
            SimpleNamespace(
                id=uuid4(),
                mode="ticket",
                ticket_id="t1",
                ticket_number="48213",
                title="Sync agent stuck",
                message_count=2,
                total_tokens=10,
                started_at=datetime.now(UTC),
                last_message_at=datetime.now(UTC),
                user_id=user.user_id,
            )
        ]

    with patch("contextedge.api.v1.copilot.audit.list_conversations", fake_list):
        result = await list_copilot_conversations(
            db=SimpleNamespace(),
            user=user,
            q="#48213",
        )
    assert captured["query"] == "#48213"
    assert len(result["items"]) == 1
    assert result["items"][0]["ticket_number"] == "48213"


@pytest.mark.asyncio
async def test_cross_tenant_isolation_on_summary():
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = make_user(roles=["tenant_admin"], tenant_id=tenant_a)
    captured = {}

    async def fake_summary(db, *, tenant_id, since, until, group_by):
        captured["tenant_id"] = tenant_id
        return []

    with patch("contextedge.api.v1.copilot.audit.usage_summary", fake_summary):
        await copilot_usage_summary(
            db=SimpleNamespace(),
            user=user_a,
            days=30,
            from_ts=None,
            to_ts=None,
            group_by="user",
        )
    assert captured["tenant_id"] == tenant_a
    assert captured["tenant_id"] != tenant_b
