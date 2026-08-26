"""Persist Copilot login, usage, and conversation rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.config import settings
from contextedge.models.copilot import (
    CopilotConversation,
    CopilotLoginEvent,
    CopilotMessage,
    CopilotUsageEvent,
)
from contextedge.models.tenant import User


class CopilotConversationAccessError(PermissionError):
    """Raised when ingest tries to append to another user's conversation."""


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _trim_title(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "Conversation"
    return text[:120]


async def record_login_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    username: str,
    client: str = "dashboard",
    extension_version: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    success: bool,
    failure_reason: str | None = None,
) -> None:
    if tenant_id is None:
        return
    db.add(
        CopilotLoginEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            username=(username or "")[:64],
            client=(client or "dashboard")[:40],
            extension_version=(extension_version or None) and extension_version[:32],
            ip_address=(ip_address or None) and ip_address[:45],
            user_agent=(user_agent or None) and user_agent[:512],
            success=success,
            failure_reason=(failure_reason or None) and failure_reason[:120],
        )
    )


async def ingest_usage_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
) -> CopilotUsageEvent:
    conversation_id = _as_uuid(payload.get("conversation_id"))
    event = CopilotUsageEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event_type=str(payload.get("event_type") or "chat")[:32],
        action=(str(payload.get("action")).strip()[:64] if payload.get("action") else None),
        mode=str(payload.get("mode") or "ticket")[:16],
        ticket_id=(str(payload.get("ticket_id")).strip()[:64] if payload.get("ticket_id") else None),
        ticket_number=(
            str(payload.get("ticket_number")).strip()[:64] if payload.get("ticket_number") else None
        ),
        provider=(str(payload.get("provider")).strip()[:64] if payload.get("provider") else None),
        model=(str(payload.get("model")).strip()[:120] if payload.get("model") else None),
        prompt_tokens=max(0, int(payload.get("prompt_tokens") or 0)),
        completion_tokens=max(0, int(payload.get("completion_tokens") or 0)),
        total_tokens=max(0, int(payload.get("total_tokens") or 0)),
        tokens_estimated=bool(payload.get("tokens_estimated")),
        llm_calls=max(0, int(payload.get("llm_calls") or 0)),
        latency_ms=int(payload["latency_ms"]) if payload.get("latency_ms") is not None else None,
        cached=bool(payload.get("cached")),
        ok=payload.get("ok", True) is not False,
        error_code=(str(payload.get("error_code")).strip()[:64] if payload.get("error_code") else None),
    )
    if event.total_tokens <= 0:
        event.total_tokens = event.prompt_tokens + event.completion_tokens
    db.add(event)
    return event


async def append_conversation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
) -> CopilotConversation:
    conversation_id = _as_uuid(payload.get("id") or payload.get("conversation_id"))
    conversation: CopilotConversation | None = None
    if conversation_id is not None:
        conversation = await db.get(CopilotConversation, conversation_id)
        if conversation is not None and (
            conversation.tenant_id != tenant_id or conversation.user_id != user_id
        ):
            raise CopilotConversationAccessError("conversation belongs to another user")
    if conversation is None:
        conversation = CopilotConversation(
            id=conversation_id or uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            mode=str(payload.get("mode") or "ticket")[:16],
            ticket_id=(str(payload.get("ticket_id")).strip()[:64] if payload.get("ticket_id") else None),
            ticket_number=(
                str(payload.get("ticket_number")).strip()[:64] if payload.get("ticket_number") else None
            ),
            title=_trim_title(payload.get("title")),
            message_count=0,
            total_tokens=0,
            started_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.flush()

    next_seq = conversation.message_count + 1
    added_tokens = 0
    for item in payload.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "")
        prompt_tokens = max(0, int(item.get("prompt_tokens") or 0))
        completion_tokens = max(0, int(item.get("completion_tokens") or 0))
        total_tokens = max(0, int(item.get("total_tokens") or 0)) or (
            prompt_tokens + completion_tokens
        )
        citations = item.get("citations")
        db.add(
            CopilotMessage(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                seq=next_seq,
                role=role,
                content=content[:20000] if content else "",
                action=(str(item.get("action")).strip()[:64] if item.get("action") else None),
                citations=citations if isinstance(citations, (dict, list)) else None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )
        added_tokens += total_tokens
        next_seq += 1
        if conversation.message_count == 0 and role == "user" and not payload.get("title"):
            conversation.title = _trim_title(content)

    conversation.message_count = next_seq - 1
    conversation.total_tokens += added_tokens
    conversation.last_message_at = datetime.now(UTC)
    if payload.get("ticket_id"):
        conversation.ticket_id = str(payload.get("ticket_id"))[:64]
    if payload.get("ticket_number"):
        conversation.ticket_number = str(payload.get("ticket_number"))[:64]
    if payload.get("mode"):
        conversation.mode = str(payload.get("mode"))[:16]
    return conversation


def _conversation_visible(admin: bool, user_id: uuid.UUID) -> list:
    clauses = [CopilotConversation.deleted_at.is_(None)]
    if not admin:
        clauses.append(CopilotConversation.user_id == user_id)
    return clauses


async def list_conversations(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: bool,
    ticket_id: str | None = None,
    query: str | None = None,
    owner_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CopilotConversation]:
    stmt: Select[tuple[CopilotConversation]] = select(CopilotConversation).where(
        CopilotConversation.tenant_id == tenant_id,
        *_conversation_visible(admin, user_id),
    )
    if owner_id is not None and admin:
        stmt = stmt.where(CopilotConversation.user_id == owner_id)
    if ticket_id:
        stmt = stmt.where(CopilotConversation.ticket_id == ticket_id)
    needle = (query or "").strip()
    if needle:
        cleaned = needle.lstrip("#").strip()
        patterns = [f"%{needle}%"]
        if cleaned and cleaned.casefold() != needle.casefold():
            patterns.append(f"%{cleaned}%")
        clauses = []
        for pattern in patterns:
            message_ids = select(CopilotMessage.conversation_id).where(
                CopilotMessage.tenant_id == tenant_id,
                CopilotMessage.content.ilike(pattern),
            )
            clauses.extend(
                [
                    CopilotConversation.title.ilike(pattern),
                    CopilotConversation.ticket_number.ilike(pattern),
                    CopilotConversation.ticket_id.ilike(pattern),
                    CopilotConversation.id.in_(message_ids),
                ]
            )
        stmt = stmt.where(or_(*clauses))
    stmt = (
        stmt.order_by(CopilotConversation.last_message_at.desc())
        .offset(max(0, offset))
        .limit(min(max(limit, 1), 100))
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_conversation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: bool,
    conversation_id: uuid.UUID,
) -> CopilotConversation | None:
    conversation = await db.get(CopilotConversation, conversation_id)
    if conversation is None or conversation.tenant_id != tenant_id:
        return None
    if conversation.deleted_at is not None:
        return None
    if not admin and conversation.user_id != user_id:
        return None
    return conversation


async def list_messages(
    db: AsyncSession, *, conversation_id: uuid.UUID
) -> list[CopilotMessage]:
    stmt = (
        select(CopilotMessage)
        .where(CopilotMessage.conversation_id == conversation_id)
        .order_by(CopilotMessage.seq.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def soft_delete_conversation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> bool:
    conversation = await get_conversation(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        admin=False,
        conversation_id=conversation_id,
    )
    if conversation is None:
        return False
    conversation.deleted_at = datetime.now(UTC)
    return True


async def usage_summary(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime,
    until: datetime,
    group_by: str = "user",
) -> list[dict[str, Any]]:
    if group_by == "day":
        return await _usage_summary_by_day(db, tenant_id=tenant_id, since=since, until=until)
    login_stmt = (
        select(
            CopilotLoginEvent.user_id,
            func.count().label("login_count"),
            func.max(CopilotLoginEvent.created_at).label("last_login"),
        )
        .where(
            CopilotLoginEvent.tenant_id == tenant_id,
            CopilotLoginEvent.success.is_(True),
            CopilotLoginEvent.created_at >= since,
            CopilotLoginEvent.created_at <= until,
        )
        .group_by(CopilotLoginEvent.user_id)
    )
    usage_stmt = (
        select(
            CopilotUsageEvent.user_id,
            func.count().label("chat_count"),
            func.coalesce(func.sum(CopilotUsageEvent.total_tokens), 0).label("total_tokens"),
        )
        .where(
            CopilotUsageEvent.tenant_id == tenant_id,
            CopilotUsageEvent.event_type == "chat",
            CopilotUsageEvent.created_at >= since,
            CopilotUsageEvent.created_at <= until,
        )
        .group_by(CopilotUsageEvent.user_id)
    )
    logins = {row.user_id: row for row in (await db.execute(login_stmt)).all()}
    usage = {row.user_id: row for row in (await db.execute(usage_stmt)).all()}
    user_ids = {key for key in logins if key} | {key for key in usage if key}
    if not user_ids:
        return []
    users = list(
        (
            await db.execute(select(User).where(User.tenant_id == tenant_id, User.id.in_(user_ids)))
        ).scalars().all()
    )
    by_id = {user.id: user for user in users}
    rows = []
    for user_id in user_ids:
        user = by_id.get(user_id)
        login = logins.get(user_id)
        used = usage.get(user_id)
        rows.append(
            {
                "user_id": str(user_id),
                "username": user.username if user else "",
                "login_count": int(login.login_count) if login else 0,
                "last_login": login.last_login.isoformat() if login and login.last_login else None,
                "chat_count": int(used.chat_count) if used else 0,
                "total_tokens": int(used.total_tokens) if used else 0,
            }
        )
    rows.sort(key=lambda item: (-item["total_tokens"], -item["chat_count"]))
    return rows


async def _usage_summary_by_day(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    since: datetime,
    until: datetime,
) -> list[dict[str, Any]]:
    login_day = func.date_trunc("day", CopilotLoginEvent.created_at)
    usage_day = func.date_trunc("day", CopilotUsageEvent.created_at)
    login_stmt = (
        select(
            login_day.label("day"),
            func.count().label("login_count"),
        )
        .where(
            CopilotLoginEvent.tenant_id == tenant_id,
            CopilotLoginEvent.success.is_(True),
            CopilotLoginEvent.created_at >= since,
            CopilotLoginEvent.created_at <= until,
        )
        .group_by(login_day)
    )
    usage_stmt = (
        select(
            usage_day.label("day"),
            func.count().label("chat_count"),
            func.coalesce(func.sum(CopilotUsageEvent.total_tokens), 0).label("total_tokens"),
        )
        .where(
            CopilotUsageEvent.tenant_id == tenant_id,
            CopilotUsageEvent.event_type == "chat",
            CopilotUsageEvent.created_at >= since,
            CopilotUsageEvent.created_at <= until,
        )
        .group_by(usage_day)
    )
    logins = {row.day: row for row in (await db.execute(login_stmt)).all()}
    usage = {row.day: row for row in (await db.execute(usage_stmt)).all()}
    days = set(logins) | set(usage)
    rows = []
    for day in days:
        login = logins.get(day)
        used = usage.get(day)
        rows.append(
            {
                "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "login_count": int(login.login_count) if login else 0,
                "chat_count": int(used.chat_count) if used else 0,
                "total_tokens": int(used.total_tokens) if used else 0,
            }
        )
    rows.sort(key=lambda item: item["day"], reverse=True)
    return rows


async def purge_expired_message_bodies(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    days = max(1, int(getattr(settings, "copilot_message_retention_days", 180) or 180))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await db.execute(
        update(CopilotMessage)
        .where(
            CopilotMessage.tenant_id == tenant_id,
            CopilotMessage.created_at < cutoff,
            CopilotMessage.content.is_not(None),
        )
        .values(content=None)
    )
    return int(result.rowcount or 0)
