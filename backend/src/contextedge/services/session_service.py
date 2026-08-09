from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.models.session import DecisionTraceEvent, ResolutionSession
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.memory_service import REASONING_MEMORY, SHORT_TERM_MEMORY

logger = structlog.get_logger()


def _enqueue_review_context_prefetch(tenant_id: uuid.UUID, session_id: uuid.UUID) -> None:
    """Fire-and-forget enqueue of the review-queue prefetch task.

    Imported lazily because Celery pulls in redis + broker config and we want
    session creation to stay unit-testable without those. A failure to enqueue
    is logged and swallowed — the read-through cache on the endpoint still
    works, the first request just pays the live-compute cost.
    """
    try:
        from contextedge.workers.review_queue_tasks import prefetch_review_context

        prefetch_review_context.delay(str(tenant_id), str(session_id))
    except Exception:
        logger.warning(
            "review_queue.prefetch_enqueue_failed",
            tenant_id=str(tenant_id),
            session_id=str(session_id),
        )


async def create_resolution_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    initiated_by: uuid.UUID | None,
    symptoms: list[str],
    entities: list[str],
    external_case_ids: list[str],
    domain_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> ResolutionSession:
    session = ResolutionSession(
        tenant_id=tenant_id,
        initiated_by=initiated_by,
        domain_id=domain_id,
        symptoms=symptoms,
        entities=entities,
        external_case_ids=external_case_ids,
        notes=notes,
        status="open",
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    from contextedge.services.case_outcome_service import record_case_transition

    await record_case_transition(
        db,
        tenant_id,
        session.id,
        from_status=None,
        to_status="open",
        transitioned_by=str(initiated_by) if initiated_by else None,
    )
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="resolution_session",
        entity_id=session.id,
        session_id=session.id,
        actor_id=initiated_by,
        event_type="session.created",
        payload={
            "memory_class": SHORT_TERM_MEMORY,
            "domain_id": str(domain_id) if domain_id else None,
            "symptoms": symptoms,
            "entities": entities,
            "external_case_ids": external_case_ids,
            "notes": notes,
        },
    )
    _enqueue_review_context_prefetch(tenant_id, session.id)
    return session


async def get_resolution_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    include_decisions: bool = False,
) -> ResolutionSession | None:
    load_options = [selectinload(ResolutionSession.trace_events)]
    if include_decisions:
        load_options.append(selectinload(ResolutionSession.decisions))
    result = await db.execute(
        select(ResolutionSession)
        .where(
            ResolutionSession.id == session_id,
            ResolutionSession.tenant_id == tenant_id,
        )
        .options(*load_options)
    )
    return result.scalar_one_or_none()


async def list_resolution_sessions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    domain_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ResolutionSession]:
    stmt = (
        select(ResolutionSession)
        .where(ResolutionSession.tenant_id == tenant_id)
        .options(selectinload(ResolutionSession.trace_events))
        .order_by(ResolutionSession.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(ResolutionSession.status == status)
    if domain_id is not None:
        stmt = stmt.where(ResolutionSession.domain_id == domain_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def append_trace_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    event_type: str,
    inputs: dict | None = None,
    outputs: dict | None = None,
    reasoning: str | None = None,
    confidence: float | None = None,
) -> DecisionTraceEvent | None:
    session = await get_resolution_session(db, tenant_id=tenant_id, session_id=session_id)
    if session is None:
        return None

    event = DecisionTraceEvent(
        tenant_id=tenant_id,
        session_id=session_id,
        event_type=event_type,
        inputs=inputs or {},
        outputs=outputs or {},
        reasoning=reasoning,
        confidence=confidence,
    )
    db.add(event)
    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision_trace_event",
        entity_id=event.id,
        session_id=session_id,
        event_type=f"decision_trace.{event_type}",
        payload={
            "memory_class": REASONING_MEMORY,
            "inputs": event.inputs,
            "outputs": event.outputs,
            "reasoning": reasoning,
            "confidence": confidence,
        },
    )
    await db.refresh(event)
    return event


async def close_resolution_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    outcome: dict | None = None,
    closed_by: str | None = None,
) -> ResolutionSession | None:
    """``outcome`` (optional): asserts what the close MEANS —
    ``{"outcome_status": ..., "resolution_summary": ...,
    "confirmed_root_cause": ..., "successful_action": ...,
    "failed_actions": [...], "user_confirmed": ..., "fix_results":
    [...]}``. A close without it records the transition only: an
    unstated outcome is unknown, not "resolved"."""
    from contextedge.services.case_outcome_service import (
        record_case_outcome,
        record_case_transition,
    )

    session = await get_resolution_session(db, tenant_id=tenant_id, session_id=session_id)
    if session is None:
        return None

    previous_status = session.status
    session.status = "closed"
    session.closed_at = datetime.now(UTC)
    await db.flush()
    await record_case_transition(
        db,
        tenant_id,
        session.id,
        from_status=previous_status,
        to_status="closed",
        reason=(outcome or {}).get("resolution_summary"),
        transitioned_by=closed_by,
    )
    if outcome and outcome.get("outcome_status"):
        await record_case_outcome(
            db,
            tenant_id,
            session,
            outcome_status=outcome["outcome_status"],
            resolution_summary=outcome.get("resolution_summary"),
            confirmed_root_cause=outcome.get("confirmed_root_cause"),
            successful_action=outcome.get("successful_action"),
            failed_actions=outcome.get("failed_actions"),
            user_confirmed=outcome.get("user_confirmed"),
            closed_by=closed_by,
            fix_results=outcome.get("fix_results"),
        )
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="resolution_session",
        entity_id=session.id,
        session_id=session.id,
        event_type="session.closed",
        payload={
            "memory_class": SHORT_TERM_MEMORY,
            "closed_at": session.closed_at.isoformat(),
        },
    )
    from contextedge.services.review_queue_service import invalidate_review_context
    await invalidate_review_context(tenant_id, session.id)
    await db.refresh(session)
    return session
