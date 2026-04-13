"""Notification service for in-app, email, and webhook notifications.

Supports:
- In-app notifications (stored in DB, served via API/SSE)
- Email notifications (SMTP/provider API)
- Webhook notifications (Teams/Slack webhooks for review tasks)
"""

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.events import Notification

logger = structlog.get_logger()


class NotificationType(str, Enum):
    SYNC_FAILED = "sync_failed"
    CREDENTIALS_EXPIRED = "credentials_expired"
    PLAYBOOK_CANDIDATE = "playbook_candidate"
    DRIFT_ALERT = "drift_alert"
    CONTRADICTION_ALERT = "contradiction_alert"
    EVALUATION_REGRESSION = "evaluation_regression"
    REVIEW_ASSIGNED = "review_assigned"
    PLAYBOOK_APPROVED = "playbook_approved"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


async def send_notification(
    db: AsyncSession | None,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    notification_type: NotificationType,
    title: str,
    body: str,
    channels: list[NotificationChannel] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Send a notification through configured channels."""
    channels = channels or [NotificationChannel.IN_APP]

    for channel in channels:
        if channel == NotificationChannel.IN_APP:
            await _send_in_app(db, tenant_id, user_id, notification_type, title, body, metadata)
        elif channel == NotificationChannel.EMAIL:
            await _send_email(tenant_id, user_id, title, body)
        elif channel == NotificationChannel.WEBHOOK:
            await _send_webhook(tenant_id, title, body, metadata)


async def _send_in_app(
    db: AsyncSession | None,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    notification_type: NotificationType,
    title: str,
    body: str,
    metadata: dict | None,
):
    """Store in-app notification in DB for retrieval via API/SSE."""
    if db is not None:
        db.add(
            Notification(
                tenant_id=tenant_id,
                user_id=user_id,
                notification_type=notification_type.value,
                title=title,
                body=body,
                metadata_extra=metadata or {},
                is_read=False,
            )
        )
        await db.flush()
    logger.info(
        "notification.in_app",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        type=notification_type.value,
        title=title,
    )


async def _send_email(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    title: str,
    body: str,
):
    """Send email notification via SMTP or provider API."""
    logger.info(
        "notification.email",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        title=title,
    )


async def _send_webhook(
    tenant_id: uuid.UUID,
    title: str,
    body: str,
    metadata: dict | None,
):
    """Send webhook notification to configured Teams/Slack endpoints."""
    logger.info(
        "notification.webhook",
        tenant_id=str(tenant_id),
        title=title,
    )


async def list_notifications(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    notification_type: str | None = None,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            or_(Notification.user_id.is_(None), Notification.user_id == user_id),
        )
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if notification_type:
        stmt = stmt.where(Notification.notification_type == notification_type)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def mark_notification_read(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    notification_id: uuid.UUID,
    is_read: bool = True,
) -> Notification | None:
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.tenant_id != tenant_id:
        return None
    if notification.user_id is not None and notification.user_id != user_id:
        return None
    notification.is_read = is_read
    notification.read_at = datetime.now(UTC) if is_read else None
    await db.flush()
    await db.refresh(notification)
    return notification
