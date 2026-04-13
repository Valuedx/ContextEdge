from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.review import (
    NotificationReadUpdate,
    NotificationResponse,
)
from contextedge.services.notification_service import (
    list_notifications,
    mark_notification_read,
)

router = APIRouter()


@router.get("", response_model=list[NotificationResponse])
async def get_notifications(
    db: DbSession,
    user: AuthUser,
    notification_type: str | None = None,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await list_notifications(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        notification_type=notification_type,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def update_notification_read_state(
    notification_id: UUID,
    body: NotificationReadUpdate,
    db: DbSession,
    user: AuthUser,
):
    notification = await mark_notification_read(
        db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        notification_id=notification_id,
        is_read=body.is_read,
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification
