"""Stale approval expiration (backlog E6 safety slice).

An approval request nobody decides is not a neutral state: the run sits
half-executed behind an eternal gate, and an approval granted against
week-old context is worse than a fresh denial. Pending requests older
than the window expire — the step stays blocked (expiry NEVER
approves), the requester re-raises with current context, and the
operational event makes the silence visible.

The rest of the execution-engine depth (tool registry, rollback
execution, cancellation, resume) remains Release-2 scope per the plan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import ApprovalRequest

logger = structlog.get_logger()

APPROVAL_EXPIRY_HOURS = 72
EXPIRY_SWEEP_LIMIT = 200


async def expire_stale_approvals(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    max_age_hours: int = APPROVAL_EXPIRY_HOURS,
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    stale = (
        (
            await db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.created_at < cutoff,
                )
                .limit(EXPIRY_SWEEP_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    expired = 0
    for request in stale:
        request.status = "expired"
        request.decided_at = datetime.now(UTC)
        expired += 1
        from contextedge.services.event_log_service import append_operational_event

        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="approval_request",
            entity_id=request.id,
            event_type="execution.approval_expired",
            payload={
                "execution_run_id": str(request.execution_run_id),
                "requested_action": request.requested_action,
                "age_hours": max_age_hours,
            },
        )
    if expired:
        logger.warning(
            "execution.approvals_expired",
            tenant_id=str(tenant_id),
            expired=expired,
            max_age_hours=max_age_hours,
        )
    return {"expired": expired}
