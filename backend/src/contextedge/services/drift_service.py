"""Drift and freshness monitoring for playbooks."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import RetrievalFeedback
from contextedge.models.playbook import Playbook


async def check_playbook_drift(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[dict]:
    """Check all approved playbooks for drift indicators."""
    result = await db.execute(
        select(Playbook).where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
        )
    )
    playbooks = result.scalars().all()
    now = datetime.now(timezone.utc)
    alerts = []

    for pb in playbooks:
        issues = []

        if pb.expiry_at and pb.expiry_at <= now:
            pb.lifecycle_state = "expired"
            issues.append("expired")

        if pb.last_validated_at:
            days_since = (now - pb.last_validated_at).days
            if days_since > 90:
                issues.append(f"not_validated_in_{days_since}_days")

        negative_feedback = await db.execute(
            select(func.count()).where(
                RetrievalFeedback.playbook_id == pb.id,
                RetrievalFeedback.feedback_type.in_(["wrong_match", "step_ineffective", "expired_workaround"]),
                RetrievalFeedback.created_at >= now - timedelta(days=30),
            )
        )
        neg_count = negative_feedback.scalar() or 0
        if neg_count >= 3:
            issues.append(f"high_negative_feedback_{neg_count}")

        if issues:
            alerts.append({
                "playbook_id": str(pb.id),
                "title": pb.title,
                "issues": issues,
                "severity": "high" if "expired" in issues else "medium",
            })

    await db.flush()
    return alerts
