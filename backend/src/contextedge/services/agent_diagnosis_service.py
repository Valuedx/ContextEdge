"""Agent diagnoses flow back into the graph, and the next one inherits them.

Roadmap F1, which the roadmap calls the biggest structural omission — and it is
the only item in the whole plan that closes a loop rather than adding a lane.
Everything else makes the graph a better reference. This makes it learn from
being used.

Without it every diagnosis starts from zero. An agent works a signature, rules
out the connection-leak hypothesis on evidence, finds it was the pool size, and
none of that survives the session. The next agent facing the same signature
repeats the ruled-out hypothesis, at the same cost, with the same tools.

## What is written, and what makes it safe

The machinery already existed and nothing called it. `decision_trace_service`
creates decisions with options, records outcomes, and links the graph edges;
`DecisionOption` carries `selected`, `rejection_reason` and `rejection_code`,
which is exactly "hypotheses considered, which was chosen, and why the others
were not". This service is the agent-facing shape of that, so governance —
review, audit, supersession — applies to agent-authored records exactly as to
human ones.

**The self-training hazard is already closed, and this depends on it.** An
agent that reads its own unreviewed conclusions as evidence launders opinion
into fact and gets more confident with every lap. The projection already
refuses it: `hydrators` drops any decision with `actor_type='ai'` and
`status='pending'`. So a diagnosis written here is *inert* until a human
reviews it or an outcome is recorded against it.

That guard is upstream of this module and this module relies on it, which is
worth stating plainly: **do not add a retrieval path that ignores it.**
`prior_hypotheses` honours it explicitly rather than inheriting it by accident.

## Rejected hypotheses are the valuable half

A confirmed cause is useful once. A ruled-out hypothesis is useful every time
the signature recurs, and it is the part nobody writes down — the ticket
records the fix, never the four things checked first. Recording rejection with
a reason turns each investigation into a shorter next one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.models.decision import Decision, DecisionOption
from contextedge.services.decision_trace_service import create_decision, record_outcome

logger = structlog.get_logger()

DIAGNOSIS_DECISION_TYPE = "diagnose_issue"
DIAGNOSIS_AGENT_STEP = "diagnose"

# An agent-authored diagnosis lands here and goes no further on its own. The
# projection drops pending AI decisions, so this status IS the containment.
INITIAL_STATUS = "pending"

MAX_HYPOTHESES = 12
MAX_EVIDENCE_REFS = 20


@dataclass(frozen=True)
class Hypothesis:
    """One thing the agent considered.

    ``rejection_reason`` is required when not selected: a hypothesis dropped
    without a reason teaches the next agent nothing, and it is indistinguishable
    from one that was never seriously considered.
    """

    hypothesis: str
    selected: bool = False
    rejection_reason: str | None = None
    rejection_code: str | None = None
    confidence: float | None = None
    risk_level: str | None = None

    def as_option(self) -> dict[str, Any]:
        return {
            "action": self.hypothesis[:200],
            "selected": self.selected,
            "suitability": self.confidence,
            "risk_level": self.risk_level,
            "rejection_reason": None if self.selected else (self.rejection_reason or None),
            "rejection_code": None if self.selected else (self.rejection_code or None),
        }


async def record_agent_diagnosis(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    rationale: str,
    hypotheses: Sequence[Hypothesis],
    evidence_ids: Sequence[uuid.UUID] | None = None,
    situation_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    confidence: float | None = None,
    uncertainty_notes: str | None = None,
    domain_id: uuid.UUID | None = None,
) -> Decision:
    """Write one agent diagnosis, with everything it ruled out.

    Lands `pending` and AI-authored, so it is inert until reviewed or until an
    outcome is recorded. That is deliberate and load-bearing: see the module
    docstring.
    """
    trimmed = list(hypotheses)[:MAX_HYPOTHESES]
    selected = [h for h in trimmed if h.selected]

    decision = await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type=DIAGNOSIS_DECISION_TYPE,
        agent_step=DIAGNOSIS_AGENT_STEP,
        rationale_summary=rationale[:2000],
        actor_type="ai",
        actor_id=actor_id,
        session_id=session_id,
        domain_id=domain_id,
        context_snapshot=(
            {"situation_id": str(situation_id)} if situation_id else {}
        ),
        evidence_refs=[
            {"evidence_id": str(e)} for e in list(evidence_ids or [])[:MAX_EVIDENCE_REFS]
        ],
        options=[h.as_option() for h in trimmed],
        confidence=confidence,
        uncertainty_notes=uncertainty_notes,
        status=INITIAL_STATUS,
    )
    logger.info(
        "agent_diagnosis.recorded",
        tenant_id=str(tenant_id),
        decision_id=str(decision.id),
        hypotheses=len(trimmed),
        rejected=len(trimmed) - len(selected),
        situation_id=str(situation_id) if situation_id else None,
    )
    return decision


async def record_diagnosis_outcome(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    *,
    action_executed: str,
    execution_result: str,
    result_details: dict | None = None,
    follow_up_needed: bool = False,
) -> dict[str, Any]:
    """Close the loop on a diagnosis.

    An outcome is the governance event that makes a diagnosis usable: it moves
    the decision off `pending`, which is what lets the projection show it to
    the next agent. A diagnosis nobody ever confirmed or refuted stays inert
    forever, which is the correct default — an unverified conclusion should not
    become the next agent's premise merely by ageing.
    """
    decision = await db.get(Decision, decision_id)
    if decision is None or decision.tenant_id != tenant_id:
        return {"error": "decision not found"}

    outcome = await record_outcome(
        db,
        tenant_id=tenant_id,
        decision_id=decision_id,
        action_executed=action_executed[:200],
        execution_result=execution_result,
        result_details=result_details or {},
        follow_up_needed=follow_up_needed,
    )
    logger.info(
        "agent_diagnosis.outcome_recorded",
        tenant_id=str(tenant_id),
        decision_id=str(decision_id),
        execution_result=execution_result,
    )
    return {
        "decision_id": str(decision_id),
        "outcome_id": str(getattr(outcome, "id", "")) or None,
        "execution_result": execution_result,
        "status": decision.status,
    }


async def prior_hypotheses(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    situation_id: uuid.UUID | None = None,
    limit: int = 20,
    include_unreviewed: bool = False,
) -> dict[str, Any]:
    """What has already been tried for this, and what was ruled out.

    The payoff of F1. The next agent facing the same signature inherits "the
    connection-leak hypothesis was checked and disproven; it was the pool
    size", instead of paying to rediscover it.

    **Excludes unreviewed AI diagnoses by default**, matching the projection's
    rule. Reading back a pending AI conclusion as prior knowledge is exactly
    the laundering the projection refuses, and it would be easy to reintroduce
    here by accident — so it is refused here explicitly rather than assumed.
    ``include_unreviewed`` exists for human review surfaces, which are the one
    reader that should see them, and it is never set by an agent path.
    """
    conditions = [
        Decision.tenant_id == tenant_id,
        Decision.decision_type == DIAGNOSIS_DECISION_TYPE,
        Decision.status.not_in(("superseded", "reverted")),
    ]
    if not include_unreviewed:
        # An AI decision still pending has been neither reviewed nor
        # outcome-bearing. Human-authored decisions are exempt: a person
        # writing one IS the review.
        conditions.append(
            (Decision.actor_type != "ai") | (Decision.status != "pending")
        )

    rows = await db.execute(
        select(Decision)
        .options(selectinload(Decision.options))
        .where(*conditions)
        .order_by(Decision.created_at.desc())
        .limit(limit)
    )
    decisions = list(rows.scalars().unique().all())

    if situation_id is not None:
        wanted = str(situation_id)
        decisions = [
            d
            for d in decisions
            if (d.context_snapshot or {}).get("situation_id") == wanted
        ]

    ruled_out: list[dict[str, Any]] = []
    concluded: list[dict[str, Any]] = []
    for decision in decisions:
        for option in decision.options or []:
            entry = {
                "hypothesis": option.action,
                "decision_id": str(decision.id),
                "confidence": option.suitability,
                "recorded_at": (
                    decision.created_at.isoformat() if decision.created_at else None
                ),
            }
            if option.selected:
                entry["rationale"] = decision.rationale_summary
                concluded.append(entry)
            else:
                entry["rejection_reason"] = option.rejection_reason
                entry["rejection_code"] = option.rejection_code
                ruled_out.append(entry)

    return {
        "ruled_out": ruled_out,
        "concluded": concluded,
        "diagnoses_considered": len(decisions),
        "note": (
            "Unreviewed AI diagnoses are excluded: an agent's own pending "
            "conclusion is not evidence for the next agent."
            if not include_unreviewed
            else "Includes unreviewed AI diagnoses — review surfaces only."
        ),
    }


async def rejected_hypothesis_count(
    db: AsyncSession, tenant_id: uuid.UUID
) -> int:
    """How much ruled-out knowledge exists. Cheap enough for a coverage line."""
    rows = await db.execute(
        select(DecisionOption.id)
        .join(Decision, Decision.id == DecisionOption.decision_id)
        .where(
            Decision.tenant_id == tenant_id,
            DecisionOption.selected.is_(False),
            DecisionOption.rejection_reason.is_not(None),
        )
    )
    return len(list(rows.all()))
