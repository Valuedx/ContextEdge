from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from contextedge.deps import AuthUser, DbSession
from contextedge.services import copilot_audit_service as audit
from contextedge.services.copilot_audit_service import CopilotConversationAccessError

router = APIRouter()


class CopilotUsageEventIn(BaseModel):
    event_type: str = "chat"
    action: str | None = None
    mode: str = "ticket"
    conversation_id: str | None = None
    ticket_id: str | None = None
    ticket_number: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_estimated: bool = False
    llm_calls: int = 0
    latency_ms: int | None = None
    cached: bool = False
    ok: bool = True
    error_code: str | None = None
    user_id: str | None = None


class CopilotMessageIn(BaseModel):
    role: str
    content: str = ""
    action: str | None = None
    citations: Any | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CopilotConversationIn(BaseModel):
    id: str | None = None
    conversation_id: str | None = None
    mode: str = "ticket"
    ticket_id: str | None = None
    ticket_number: str | None = None
    title: str | None = None
    user_id: str | None = None
    messages: list[CopilotMessageIn] = Field(default_factory=list)


class CopilotIngestRequest(BaseModel):
    events: list[CopilotUsageEventIn] = Field(default_factory=list)
    conversation: CopilotConversationIn | None = None
    user_id: str | None = None


def _target_user_id(user: AuthUser, requested: str | None) -> UUID:
    if user.principal_type == "service_account":
        if not requested:
            raise HTTPException(status_code=400, detail="user_id is required for service ingest")
        try:
            return UUID(requested)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid user_id") from exc
    return user.user_id


@router.post("/events")
async def ingest_copilot_events(
    body: CopilotIngestRequest,
    db: DbSession,
    user: AuthUser,
) -> dict[str, Any]:
    target = _target_user_id(user, body.user_id or (body.conversation.user_id if body.conversation else None))
    conversation_id = None
    if body.conversation is not None:
        try:
            conversation = await audit.append_conversation(
                db,
                tenant_id=user.tenant_id,
                user_id=target,
                payload=body.conversation.model_dump(),
            )
        except CopilotConversationAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        conversation_id = str(conversation.id)
    event_ids: list[str] = []
    for item in body.events:
        payload = item.model_dump()
        if conversation_id and not payload.get("conversation_id"):
            payload["conversation_id"] = conversation_id
        event = await audit.ingest_usage_event(
            db,
            tenant_id=user.tenant_id,
            user_id=_target_user_id(user, item.user_id) if item.user_id else target,
            payload=payload,
        )
        event_ids.append(str(event.id))
    return {"conversation_id": conversation_id, "event_ids": event_ids}


@router.get("/usage/summary")
async def copilot_usage_summary(
    db: DbSession,
    user: AuthUser,
    days: int = Query(30, ge=1, le=366),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    group_by: Literal["user", "day"] = Query("user"),
) -> dict[str, Any]:
    user.require_role("tenant_admin")
    until = to_ts or datetime.now(UTC)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if from_ts is not None:
        since = from_ts if from_ts.tzinfo else from_ts.replace(tzinfo=UTC)
    else:
        since = until - timedelta(days=days)
    rows = await audit.usage_summary(
        db,
        tenant_id=user.tenant_id,
        since=since,
        until=until,
        group_by=group_by,
    )
    return {
        "from": since.isoformat(),
        "to": until.isoformat(),
        "group_by": group_by,
        "rows": rows,
    }


@router.get("/conversations")
async def list_copilot_conversations(
    db: DbSession,
    user: AuthUser,
    ticket_id: str | None = None,
    q: str | None = None,
    user_id: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    admin = user.has_role("tenant_admin")
    owner_id = None
    if user_id:
        if not admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
        try:
            owner_id = UUID(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid user_id") from exc
    rows = await audit.list_conversations(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        admin=admin,
        ticket_id=ticket_id,
        query=q,
        owner_id=owner_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "mode": row.mode,
                "ticket_id": row.ticket_id,
                "ticket_number": row.ticket_number,
                "title": row.title,
                "message_count": row.message_count,
                "total_tokens": row.total_tokens,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                "user_id": str(row.user_id),
            }
            for row in rows
        ]
    }


@router.get("/conversations/{conversation_id}")
async def get_copilot_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: AuthUser,
) -> dict[str, Any]:
    admin = user.has_role("tenant_admin")
    conversation = await audit.get_conversation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        admin=admin,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await audit.list_messages(db, conversation_id=conversation.id)
    return {
        "id": str(conversation.id),
        "mode": conversation.mode,
        "ticket_id": conversation.ticket_id,
        "ticket_number": conversation.ticket_number,
        "title": conversation.title,
        "message_count": conversation.message_count,
        "total_tokens": conversation.total_tokens,
        "started_at": conversation.started_at.isoformat() if conversation.started_at else None,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "user_id": str(conversation.user_id),
        "messages": [
            {
                "seq": item.seq,
                "role": item.role,
                "content": item.content or "",
                "action": item.action,
                "citations": item.citations or [],
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_copilot_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: AuthUser,
) -> dict[str, bool]:
    deleted = await audit.soft_delete_conversation(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        conversation_id=conversation_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}
