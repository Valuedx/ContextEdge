import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.middleware.request_context import current_request_context
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
    request_ctx = current_request_context()
    merged_details = dict(details or {})
    if request_ctx.get("request_id") is not None:
        merged_details.setdefault("request_id", str(request_ctx["request_id"]))
    if request_ctx.get("correlation_id") is not None:
        merged_details.setdefault("correlation_id", str(request_ctx["correlation_id"]))
    if request_ctx.get("causation_id") is not None:
        merged_details.setdefault("causation_id", str(request_ctx["causation_id"]))

    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id or request_ctx.get("user_id"),
        actor_email=actor_email or request_ctx.get("user_email"),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=merged_details or None,
        ip_address=ip_address,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(entry)
    await db.flush()
    return entry
