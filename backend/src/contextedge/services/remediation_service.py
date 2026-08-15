"""Deriving a rollback plan, and handing over with the evidence (F11).

Both are produced by the same event — F9's verdict — and both exist because
the previous behaviour dropped information at exactly the moment it was most
needed. A failed verification said "failed" and stopped. A human received a
notification rather than what the system saw.

The plan is *derived, never executed here*. Running it is an ``ExecutionRun``
with ``rolls_back_run_id`` set, so it inherits the approval binding, the
attempt ledger and the verification that F6–F9 built. A rollback that executed
through a side door would be the one action in the system nobody verified.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import ExecutionRun, ExecutionStepRun
from contextedge.models.remediation import Escalation, RollbackPlan
from contextedge.models.skill import Skill

logger = structlog.get_logger()


async def _rollback_action_for(
    db: AsyncSession, tenant_id: uuid.UUID, step: ExecutionStepRun
) -> dict | None:
    """How this step would be undone, or None if it cannot be.

    Two sources, in order of strength: the bound skill's registered rollback
    skill (F6), then the step's free-text ``rollback_hint``. A hint is weaker
    — nobody can execute it automatically — but it is what a responder needs
    at 3am, so it counts as a way back rather than being discarded.
    """
    inputs = step.inputs if isinstance(step.inputs, dict) else {}
    tool_ref = inputs.get("tool_ref")
    if isinstance(tool_ref, str) and tool_ref.strip():
        from contextedge.services.skill_registry_service import (
            UnresolvedSkillReference,
            resolve_skill,
        )

        try:
            skill = await resolve_skill(db, tenant_id, tool_ref)
        except UnresolvedSkillReference:
            skill = None
        if skill is not None and skill.rollback_skill_id is not None:
            rollback_skill = await db.get(Skill, skill.rollback_skill_id)
            if rollback_skill is not None:
                return {
                    "step_index": step.step_index,
                    "reverses": step.step_title,
                    "method": "skill",
                    "tool_ref": f"{rollback_skill.skill_key}@{rollback_skill.version}",
                    "safety_class": rollback_skill.safety_class,
                }

    hint = inputs.get("rollback_hint")
    if isinstance(hint, str) and hint.strip():
        return {
            "step_index": step.step_index,
            "reverses": step.step_title,
            "method": "manual",
            "instruction": hint.strip()[:2000],
        }
    return None


async def derive_rollback_plan(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run: ExecutionRun,
    assessment_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> RollbackPlan:
    """Build the plan for undoing *run*, in reverse step order.

    Steps that ran are considered; steps that were skipped or never started
    are not, because there is nothing to undo. A plan with no actions is
    recorded as ``infeasible`` rather than not recorded — "we cannot undo
    this" is the most important thing a responder can learn early, and a
    missing row reads as "nobody checked".
    """
    steps = (
        (
            await db.execute(
                select(ExecutionStepRun)
                .where(
                    ExecutionStepRun.execution_run_id == run.id,
                    ExecutionStepRun.tenant_id == tenant_id,
                )
                .order_by(ExecutionStepRun.step_index.desc())
            )
        )
        .scalars()
        .all()
    )

    actions: list[dict] = []
    irreversible: list[dict] = []
    for step in steps:
        if step.status in ("skipped", "pending"):
            continue
        action = await _rollback_action_for(db, tenant_id, step)
        if action is None:
            irreversible.append(
                {"step_index": step.step_index, "step_title": step.step_title}
            )
        else:
            actions.append(action)

    plan = RollbackPlan(
        tenant_id=tenant_id,
        execution_run_id=run.id,
        verification_assessment_id=assessment_id,
        status="proposed" if actions else "infeasible",
        actions=actions,
        irreversible_steps=irreversible,
        reason=reason,
    )
    db.add(plan)
    await db.flush()
    logger.info(
        "rollback.plan_derived",
        tenant_id=str(tenant_id),
        execution_run_id=str(run.id),
        status=plan.status,
        actions=len(actions),
        irreversible=len(irreversible),
    )
    return plan


async def raise_escalation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    reason: str,
    escalated_by: str,
    case_id: uuid.UUID | None = None,
    execution_run_id: uuid.UUID | None = None,
    decision_id: uuid.UUID | None = None,
    priority: str = "normal",
    escalated_to: str | None = None,
    evidence_bundle: dict | None = None,
    recommended_next_actions: list | None = None,
) -> Escalation:
    """Hand over to a human with the bundle attached."""
    escalation = Escalation(
        tenant_id=tenant_id,
        case_id=case_id,
        execution_run_id=execution_run_id,
        decision_id=decision_id,
        reason=reason,
        priority=priority,
        status="open",
        escalated_by=escalated_by[:120],
        escalated_to=escalated_to,
        evidence_bundle=evidence_bundle or {},
        recommended_next_actions=recommended_next_actions or [],
    )
    db.add(escalation)
    await db.flush()
    logger.info(
        "escalation.raised",
        tenant_id=str(tenant_id),
        escalation_id=str(escalation.id),
        priority=priority,
        execution_run_id=str(execution_run_id) if execution_run_id else None,
    )
    return escalation


async def acknowledge_escalation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    escalation_id: uuid.UUID,
    acknowledged_by: uuid.UUID,
    now: datetime | None = None,
) -> Escalation | None:
    """Record that a human picked it up, and how long that took.

    The latency is stored rather than computed later so the number survives a
    subsequent edit of either timestamp — and so "how long do escalations sit?"
    is one query rather than a join and a date subtraction.
    """
    now = now or datetime.now(UTC)
    escalation = await db.get(Escalation, escalation_id)
    if escalation is None or escalation.tenant_id != tenant_id:
        return None
    if escalation.status != "open":
        return escalation

    escalation.status = "acknowledged"
    escalation.acknowledged_by = acknowledged_by
    escalation.acknowledged_at = now
    created = escalation.created_at
    if created is not None:
        created = created if created.tzinfo else created.replace(tzinfo=UTC)
        escalation.acknowledgement_latency_min = max(
            0, int((now - created).total_seconds() // 60)
        )
    await db.flush()
    return escalation
