"""Production writers for the outcome/fix flywheel.

``CaseOutcome`` / ``CaseStateTransition`` / ``CaseOutcomeFixPattern``
shipped as schema plus projection (materializer edges, hydrator facts,
maf.v1 node type) with ZERO writers — the "learn from the outcome" loop
existed only on the read side. These writers close it at the source of
truth for case lifecycle: the resolution session. Every status change
appends a transition row; a close that carries an outcome records it,
with MTTR derived from the session's own timeline; fix results link the
outcome to the fix patterns it validated or refuted, which is the raw
material for "resolved 8 of 9, failed on version >= 6.2" statistics.

Deliberately NOT here: inferring outcomes from ticket text. An outcome
row asserts "the case is actually resolved and this is what we
learned" — that is a caller's claim (human close, agent close with
user confirmation), never a regex's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from contextedge.models.case_outcome import (
    OUTCOME_STATUSES,
    CaseOutcome,
    CaseOutcomeFixPattern,
    CaseStateTransition,
)

logger = structlog.get_logger()

_FIX_RESULTS = ("successful", "failed", "partial")


async def get_case_history(db, tenant_id: uuid.UUID, case_id: uuid.UUID) -> dict:
    """Lifecycle history for one case: every state transition (oldest
    first — it reads as a timeline) plus recorded outcomes (newest
    first — the latest is the operative one; earlier rows are
    reopen-and-close history)."""
    from sqlalchemy import select

    transitions = (
        (
            await db.execute(
                select(CaseStateTransition)
                .where(
                    CaseStateTransition.tenant_id == tenant_id,
                    CaseStateTransition.case_id == case_id,
                )
                .order_by(CaseStateTransition.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    outcomes = (
        (
            await db.execute(
                select(CaseOutcome)
                .where(
                    CaseOutcome.tenant_id == tenant_id,
                    CaseOutcome.case_id == case_id,
                )
                .order_by(CaseOutcome.closed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "transitions": [
            {
                "from_status": t.from_status,
                "to_status": t.to_status,
                "reason": t.transition_reason,
                "transitioned_by": t.transitioned_by,
                "at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transitions
        ],
        "outcomes": [
            {
                "outcome_status": o.outcome_status,
                "resolution_summary": o.resolution_summary,
                "confirmed_root_cause": o.confirmed_root_cause,
                "successful_action": o.successful_action,
                "failed_actions": o.failed_actions,
                "user_confirmed": o.user_confirmed,
                "mttr_minutes": float(o.mttr_minutes) if o.mttr_minutes is not None else None,
                "closed_by": o.closed_by,
                "closed_at": o.closed_at.isoformat() if o.closed_at else None,
            }
            for o in outcomes
        ],
    }


async def record_case_transition(
    db,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    from_status: str | None,
    to_status: str,
    reason: str | None = None,
    transitioned_by: str | None = None,
) -> CaseStateTransition:
    """Append one lifecycle transition. Append-only by design — the
    history is the point; there is nothing to update."""
    transition = CaseStateTransition(
        tenant_id=tenant_id,
        case_id=case_id,
        from_status=from_status,
        to_status=to_status,
        transition_reason=reason,
        transitioned_by=(transitioned_by or None),
    )
    db.add(transition)
    await db.flush()
    return transition


async def record_case_outcome(
    db,
    tenant_id: uuid.UUID,
    session,
    *,
    outcome_status: str,
    resolution_summary: str | None = None,
    confirmed_root_cause: str | None = None,
    successful_action: str | None = None,
    failed_actions: list[str] | None = None,
    user_confirmed: bool | None = None,
    closed_by: str | None = None,
    fix_results: list[dict] | None = None,
) -> CaseOutcome:
    """One outcome row per close; a reopen closes again with a new row.

    ``fix_results``: ``[{"fix_pattern_id": UUID-ish, "result":
    "successful"|"failed"|"partial", "confidence": float|None}, ...]``
    — malformed entries are skipped with a log line rather than sinking
    the outcome they annotate.
    """
    if outcome_status not in OUTCOME_STATUSES:
        raise ValueError(
            f"outcome_status must be one of {OUTCOME_STATUSES}, got {outcome_status!r}"
        )

    closed_at = datetime.now(UTC)
    mttr_minutes = None
    started = getattr(session, "created_at", None)
    if started is not None:
        try:
            mttr_minutes = round((closed_at - started).total_seconds() / 60.0, 2)
        except TypeError:  # naive/aware mismatch from odd fixtures
            mttr_minutes = None

    outcome = CaseOutcome(
        tenant_id=tenant_id,
        case_id=session.id,
        outcome_status=outcome_status,
        resolution_summary=resolution_summary,
        confirmed_root_cause=confirmed_root_cause,
        successful_action=(successful_action or None),
        failed_actions=list(failed_actions or []),
        user_confirmed=user_confirmed,
        mttr_minutes=mttr_minutes,
        closed_by=(closed_by or None),
        closed_at=closed_at,
    )
    db.add(outcome)
    await db.flush()

    for item in fix_results or []:
        result = item.get("result")
        raw_id = item.get("fix_pattern_id")
        try:
            fix_pattern_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            fix_pattern_id = None
        if fix_pattern_id is None or result not in _FIX_RESULTS:
            logger.warning(
                "case_outcome.fix_result_skipped",
                case_id=str(session.id),
                fix_pattern_id=str(raw_id),
                result=result,
            )
            continue
        confidence = item.get("confidence")
        db.add(
            CaseOutcomeFixPattern(
                tenant_id=tenant_id,
                case_outcome_id=outcome.id,
                fix_pattern_id=fix_pattern_id,
                result=result,
                confidence=(
                    confidence
                    if isinstance(confidence, int | float)
                    and not isinstance(confidence, bool)
                    else None
                ),
            )
        )
    await db.flush()
    return outcome
