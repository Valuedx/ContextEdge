"""First-class decision trace service.

Creates, retrieves, and manages structured Decision objects with options,
outcomes, and graph edges — the institutional reasoning memory layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.graph.builder import (
    link_decision_chain,
    link_decision_evidence,
    link_decision_episode,
    link_decision_option,
    link_decision_outcome,
    link_decision_pattern,
    link_decision_policy,
)
from contextedge.models.decision import Decision, DecisionOption, DecisionOutcome
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.memory_service import REASONING_MEMORY
from contextedge.services.session_service import append_trace_event

_DECISION_EAGER = [
    selectinload(Decision.options),
    selectinload(Decision.outcomes),
]

_REF_TYPE_TO_LINKER = {
    "evidence": link_decision_evidence,
    "episode": link_decision_episode,
    "pattern": link_decision_pattern,
}


async def create_decision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    agent_step: str,
    rationale_summary: str,
    actor_type: str = "ai",
    actor_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    domain_id: uuid.UUID | None = None,
    parent_decision_id: uuid.UUID | None = None,
    context_snapshot: dict | None = None,
    evidence_refs: list[dict] | None = None,
    options: list[dict] | None = None,
    confidence: float | None = None,
    uncertainty_notes: str | None = None,
    compact_trace: str | None = None,
    explanation: str | None = None,
    approval_required: bool = False,
    policy_refs: list[str] | None = None,
    human_override: bool = False,
    status: str = "pending",
) -> Decision:
    evidence_refs = evidence_refs or []
    options = options or []
    policy_refs = policy_refs or []

    evidence_summary = [
        {"ref_type": r.get("ref_type", ""), "ref_id": r.get("ref_id", ""), "description": r.get("description", "")}
        for r in evidence_refs
    ]

    decision = Decision(
        tenant_id=tenant_id,
        domain_id=domain_id,
        session_id=session_id,
        parent_decision_id=parent_decision_id,
        decision_type=decision_type,
        agent_step=agent_step,
        actor_type=actor_type,
        actor_id=actor_id,
        context_snapshot=context_snapshot or {},
        evidence_summary=evidence_summary,
        rationale_summary=rationale_summary,
        confidence=confidence,
        uncertainty_notes=uncertainty_notes,
        compact_trace=compact_trace,
        explanation=explanation,
        approval_required=approval_required,
        policy_refs=policy_refs,
        human_override=human_override,
        status=status,
    )
    db.add(decision)
    await db.flush()

    option_objs: list[DecisionOption] = []
    for opt in options:
        obj = DecisionOption(
            decision_id=decision.id,
            tenant_id=tenant_id,
            action=opt["action"],
            suitability=opt.get("suitability"),
            risk_level=opt.get("risk_level"),
            preconditions=opt.get("preconditions", []),
            rejection_reason=opt.get("rejection_reason"),
            selected=opt.get("selected", False),
        )
        db.add(obj)
        option_objs.append(obj)
    if option_objs:
        await db.flush()

    for ref in evidence_refs:
        linker = _REF_TYPE_TO_LINKER.get(ref.get("ref_type", ""))
        if linker and ref.get("ref_id"):
            try:
                ref_uuid = uuid.UUID(ref["ref_id"])
            except (ValueError, AttributeError):
                continue
            await linker(db, tenant_id, decision.id, ref_uuid, domain_id)

    for obj in option_objs:
        await link_decision_option(
            db, tenant_id, decision.id, obj.id, obj.selected, domain_id,
        )

    for pr in policy_refs:
        try:
            policy_uuid = uuid.UUID(pr)
        except (ValueError, AttributeError):
            continue
        await link_decision_policy(db, tenant_id, decision.id, policy_uuid, domain_id)

    if parent_decision_id:
        await link_decision_chain(db, tenant_id, parent_decision_id, decision.id, domain_id)

    if session_id:
        await append_trace_event(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=f"decision.{decision_type}",
            inputs=context_snapshot or {},
            outputs={"decision_id": str(decision.id)},
            reasoning=compact_trace or rationale_summary,
            confidence=confidence,
        )

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision",
        entity_id=decision.id,
        session_id=session_id,
        event_type="decision.created",
        payload={
            "memory_class": REASONING_MEMORY,
            "decision_type": decision_type,
            "agent_step": agent_step,
            "actor_type": actor_type,
            "confidence": confidence,
            "options_count": len(option_objs),
            "evidence_refs_count": len(evidence_refs),
        },
    )

    await db.refresh(decision)
    return decision


async def record_outcome(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    action_executed: str,
    execution_result: str,
    result_details: dict | None = None,
    follow_up_needed: bool = False,
    follow_up_decision_id: uuid.UUID | None = None,
    feedback_received: str | None = None,
    feedback_by: uuid.UUID | None = None,
) -> DecisionOutcome | None:
    decision = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if decision is None:
        return None

    outcome = DecisionOutcome(
        decision_id=decision_id,
        tenant_id=tenant_id,
        action_executed=action_executed,
        execution_result=execution_result,
        result_details=result_details or {},
        follow_up_needed=follow_up_needed,
        follow_up_decision_id=follow_up_decision_id,
        feedback_received=feedback_received,
        feedback_by=feedback_by,
    )
    db.add(outcome)
    await db.flush()

    await link_decision_outcome(
        db, tenant_id, decision_id, outcome.id, decision.domain_id,
    )

    if follow_up_decision_id:
        await link_decision_chain(
            db, tenant_id, decision_id, follow_up_decision_id, decision.domain_id,
        )

    if decision.status == "pending":
        decision.status = "completed"
        await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision",
        entity_id=decision_id,
        session_id=decision.session_id,
        event_type="decision.outcome_recorded",
        payload={
            "memory_class": REASONING_MEMORY,
            "action_executed": action_executed,
            "execution_result": execution_result,
            "follow_up_needed": follow_up_needed,
        },
    )

    await db.refresh(outcome)
    return outcome


async def get_decision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> Decision | None:
    result = await db.execute(
        select(Decision)
        .where(Decision.id == decision_id, Decision.tenant_id == tenant_id)
        .options(*_DECISION_EAGER)
    )
    return result.scalar_one_or_none()


async def list_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    decision_type: str | None = None,
    agent_step: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Decision]:
    stmt = (
        select(Decision)
        .where(Decision.tenant_id == tenant_id)
        .options(*_DECISION_EAGER)
        .order_by(Decision.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if session_id is not None:
        stmt = stmt.where(Decision.session_id == session_id)
    if decision_type is not None:
        stmt = stmt.where(Decision.decision_type == decision_type)
    if agent_step is not None:
        stmt = stmt.where(Decision.agent_step == agent_step)
    if status is not None:
        stmt = stmt.where(Decision.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_decision_chain(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    max_depth: int = 20,
) -> list[Decision]:
    """Walk the parent chain upward, then collect children downward."""
    chain: list[Decision] = []
    visited: set[uuid.UUID] = set()

    root = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if root is None:
        return chain

    current: Decision | None = root
    ancestors: list[Decision] = []
    while current and current.parent_decision_id and len(ancestors) < max_depth:
        if current.parent_decision_id in visited:
            break
        visited.add(current.parent_decision_id)
        current = await get_decision(db, tenant_id=tenant_id, decision_id=current.parent_decision_id)
        if current:
            ancestors.append(current)

    ancestors.reverse()
    chain.extend(ancestors)

    if root.id not in visited:
        chain.append(root)
        visited.add(root.id)

    queue = [root.id]
    while queue and len(chain) < max_depth:
        parent_id = queue.pop(0)
        children_stmt = (
            select(Decision)
            .where(
                Decision.tenant_id == tenant_id,
                Decision.parent_decision_id == parent_id,
            )
            .options(*_DECISION_EAGER)
            .order_by(Decision.created_at)
        )
        children_result = await db.execute(children_stmt)
        for child in children_result.scalars().all():
            if child.id not in visited:
                visited.add(child.id)
                chain.append(child)
                queue.append(child.id)

    return chain


async def find_similar_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_snapshot: dict | None = None,
    limit: int = 10,
) -> list[Decision]:
    """Find decisions with matching type and overlapping context keys.

    Uses JSONB containment (@>) when context_snapshot keys are provided,
    falling back to type-only matching otherwise.
    """
    stmt = (
        select(Decision)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.decision_type == decision_type,
        )
        .options(*_DECISION_EAGER)
        .order_by(Decision.created_at.desc())
        .limit(limit)
    )

    if context_snapshot:
        match_keys = {}
        for key in ("workflow", "environment", "impacted_dependency"):
            if key in context_snapshot:
                match_keys[key] = context_snapshot[key]
        if match_keys:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

            stmt = stmt.where(
                Decision.context_snapshot.op("@>")(cast(match_keys, JSONB_TYPE))
            )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_decision_effectiveness(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_filters: dict | None = None,
) -> dict:
    """Aggregate outcome stats for a decision type/context.

    Returns counts of each execution_result value and a total, enabling
    queries like "is restart usually effective for this failure type?"
    """
    stmt = (
        select(
            DecisionOutcome.execution_result,
            sa_func.count().label("cnt"),
        )
        .join(Decision, DecisionOutcome.decision_id == Decision.id)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.decision_type == decision_type,
        )
        .group_by(DecisionOutcome.execution_result)
    )

    if context_filters:
        match_keys = {}
        for key in ("workflow", "environment", "impacted_dependency"):
            if key in context_filters:
                match_keys[key] = context_filters[key]
        if match_keys:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

            stmt = stmt.where(
                Decision.context_snapshot.op("@>")(cast(match_keys, JSONB_TYPE))
            )

    result = await db.execute(stmt)
    rows = result.all()

    stats: dict[str, int] = {}
    total = 0
    for row in rows:
        stats[row[0]] = row[1]
        total += row[1]

    return {
        "decision_type": decision_type,
        "context_filters": context_filters or {},
        "total": total,
        "outcomes": stats,
    }
