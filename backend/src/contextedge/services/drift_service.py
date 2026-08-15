"""Drift and freshness monitoring for playbooks."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import RetrievalFeedback
from contextedge.models.playbook import Playbook


async def list_drift_alerts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[dict]:
    """Read-only drift heuristics for HTTP GET. Does not mutate playbook lifecycle."""
    result = await db.execute(
        select(Playbook).where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
        )
    )
    playbooks = result.scalars().all()
    now = datetime.now(UTC)
    alerts = []

    for pb in playbooks:
        issues = []

        if pb.expiry_at and pb.expiry_at <= now:
            issues.append("past_expiry")

        if pb.last_validated_at:
            days_since = (now - pb.last_validated_at).days
            if days_since > 90:
                issues.append(f"not_validated_in_{days_since}_days")

        negative_feedback = await db.execute(
            select(func.count()).where(
                RetrievalFeedback.playbook_id == pb.id,
                RetrievalFeedback.feedback_type.in_(
                    ["wrong_match", "step_ineffective", "expired_workaround"]
                ),
                RetrievalFeedback.created_at >= now - timedelta(days=30),
            )
        )
        neg_count = negative_feedback.scalar() or 0
        if neg_count >= 3:
            issues.append(f"high_negative_feedback_{neg_count}")

        # Check if source pattern had new nodes/episodes added after playbook was generated
        if pb.pattern_id:
            from contextedge.models.pattern import Pattern

            pat_res = await db.execute(
                select(Pattern).where(Pattern.id == pb.pattern_id)
            )
            pat = pat_res.scalar_one_or_none()
            if (
                pat
                and pat.updated_at
                and pb.updated_at
                and (pat.updated_at - pb.updated_at).total_seconds() > 5
            ):
                issues.append("pattern_nodes_added_drift")

        if issues:
            alerts.append({
                "playbook_id": str(pb.id),
                "pattern_id": str(pb.pattern_id) if pb.pattern_id else None,
                "title": pb.title,
                "issues": issues,
                "severity": (
                    "high"
                    if "past_expiry" in issues
                    else ("medium" if "pattern_nodes_added_drift" in issues else "low")
                ),
            })

    return alerts


async def apply_expired_playbook_transitions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> int:
    """Mark approved playbooks past ``expiry_at`` as ``expired`` (Celery / batch)."""
    now = datetime.now(UTC)
    res = await db.execute(
        update(Playbook)
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
            Playbook.expiry_at.is_not(None),
            Playbook.expiry_at <= now,
        )
        .values(lifecycle_state="expired")
    )
    await db.flush()
    return res.rowcount or 0


async def check_playbook_drift(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """Scheduled drift pass: snapshot alerts while still ``approved``, then expire, then metrics.

    Alerts include playbooks that were past expiry (``past_expiry``) before the transition runs.
    """
    alerts = await list_drift_alerts(db, tenant_id)
    expired_count = await apply_expired_playbook_transitions(db, tenant_id)
    return {
        "alerts": alerts,
        "expired_transition_count": expired_count,
        "alert_count": len(alerts),
    }
