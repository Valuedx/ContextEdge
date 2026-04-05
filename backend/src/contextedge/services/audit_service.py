"""Audit and compliance service for report generation and access review."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.audit import AuditLog


async def generate_access_report(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    from_date: datetime,
    to_date: datetime,
) -> dict:
    """Generate an access review report for compliance."""
    total_result = await db.execute(
        select(func.count()).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.timestamp >= from_date,
            AuditLog.timestamp <= to_date,
        )
    )
    total = total_result.scalar() or 0

    by_action = await db.execute(
        select(AuditLog.action, func.count().label("count"))
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.timestamp >= from_date,
            AuditLog.timestamp <= to_date,
        )
        .group_by(AuditLog.action)
        .order_by(func.count().desc())
    )

    by_actor = await db.execute(
        select(AuditLog.actor_email, func.count().label("count"))
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.timestamp >= from_date,
            AuditLog.timestamp <= to_date,
            AuditLog.actor_email.is_not(None),
        )
        .group_by(AuditLog.actor_email)
        .order_by(func.count().desc())
        .limit(20)
    )

    return {
        "tenant_id": str(tenant_id),
        "period": {"from": from_date.isoformat(), "to": to_date.isoformat()},
        "total_events": total,
        "by_action": [{"action": a, "count": c} for a, c in by_action.all()],
        "by_actor": [{"actor": a, "count": c} for a, c in by_actor.all()],
    }
