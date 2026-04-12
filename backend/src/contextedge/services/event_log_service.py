from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.middleware.request_context import (
    current_causation_id,
    current_correlation_id,
    current_request_context,
)
from contextedge.models.events import OperationalEvent


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _as_entity_id(value: uuid.UUID | str | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


async def append_operational_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    event_type: str,
    entity_id: uuid.UUID | str | None = None,
    session_id: uuid.UUID | str | None = None,
    actor_id: uuid.UUID | str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    correlation_id: uuid.UUID | str | None = None,
    causation_id: uuid.UUID | str | None = None,
) -> OperationalEvent:
    request_ctx = current_request_context()
    event = OperationalEvent(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=_as_entity_id(entity_id),
        session_id=_as_uuid(session_id),
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=_as_uuid(correlation_id) or current_correlation_id(),
        causation_id=_as_uuid(causation_id) or current_causation_id(),
        actor_id=_as_uuid(actor_id) or _as_uuid(request_ctx.get("user_id")),
        payload=payload or {},
    )
    db.add(event)
    await db.flush()
    return event


async def list_operational_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    session_id: uuid.UUID | str | None = None,
    correlation_id: uuid.UUID | str | None = None,
    limit: int = 100,
) -> list[OperationalEvent]:
    stmt = select(OperationalEvent).where(OperationalEvent.tenant_id == tenant_id)
    if entity_type is not None:
        stmt = stmt.where(OperationalEvent.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(OperationalEvent.entity_id == _as_entity_id(entity_id))
    if session_id is not None:
        stmt = stmt.where(OperationalEvent.session_id == _as_uuid(session_id))
    if correlation_id is not None:
        stmt = stmt.where(OperationalEvent.correlation_id == _as_uuid(correlation_id))
    stmt = stmt.order_by(OperationalEvent.recorded_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
