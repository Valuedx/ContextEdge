import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.audit import AuditLog


async def log_audit_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(entry)
    await db.flush()
    return entry
