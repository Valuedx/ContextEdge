from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.audit import AuditLog
from contextedge.schemas.audit import AuditLogResponse

router = APIRouter()


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    db: DbSession,
    user: AuthUser,
    action: str | None = None,
    resource_type: str | None = None,
    actor_id: UUID | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("tenant_admin")
    q = select(AuditLog).where(AuditLog.tenant_id == user.tenant_id)
    if action:
        q = q.where(AuditLog.action == action)
    if resource_type:
        q = q.where(AuditLog.resource_type == resource_type)
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if from_date:
        q = q.where(AuditLog.timestamp >= from_date)
    if to_date:
        q = q.where(AuditLog.timestamp <= to_date)
    q = q.order_by(AuditLog.timestamp.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()
