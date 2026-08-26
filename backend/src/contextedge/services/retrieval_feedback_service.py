"""Durable runtime matches and joinable retrieval feedback."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import RetrievalFeedback, RuntimeMatchRecord

logger = structlog.get_logger()

_VALIDATED = frozenset(
    {"confirmed", "correct_match", "selected", "validated", "helpful"}
)
_PARTIAL = frozenset(
    {"partial", "partially_confirmed", "partial_match", "somewhat_helpful"}
)
_INVALIDATED = frozenset(
    {"wrong_match", "step_ineffective", "expired_workaround", "rejected"}
)


def _edge_type_for_feedback(feedback_type: str) -> str | None:
    if feedback_type in _VALIDATED:
        return "validated_fix"
    if feedback_type in _PARTIAL:
        return "partially_validated_fix"
    if feedback_type in _INVALIDATED:
        return "invalidated_fix"
    return None


def _session_id_from_frame(query_frame: dict[str, Any] | None) -> uuid.UUID | None:
    if not isinstance(query_frame, dict):
        return None
    raw = query_frame.get("session_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def persist_runtime_match(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    match_id: str,
    query_frame: dict[str, Any],
    ranked_results: list[dict[str, Any]],
    filters_applied: dict[str, Any],
    calibrated_confidence: float | None,
) -> RuntimeMatchRecord:
    record = RuntimeMatchRecord(
        tenant_id=tenant_id,
        match_id=match_id,
        query_frame=query_frame,
        ranked_results=ranked_results,
        filters_applied=filters_applied,
        calibrated_confidence=calibrated_confidence,
    )
    db.add(record)
    await db.flush()
    return record


async def _write_feedback_edges(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    match_id: str | None,
    playbook_id: uuid.UUID | None,
    playbook_version_id: uuid.UUID | None,
    feedback_type: str,
    feedback_id: uuid.UUID,
) -> None:
    edge_type = _edge_type_for_feedback(feedback_type)
    if edge_type is None or playbook_id is None:
        return
    from contextedge.graph.builder import ensure_edge

    match_record = None
    if match_id:
        match_record = (
            await db.execute(
                select(RuntimeMatchRecord).where(
                    RuntimeMatchRecord.tenant_id == tenant_id,
                    RuntimeMatchRecord.match_id == match_id,
                )
            )
        ).scalar_one_or_none()
    session_id = _session_id_from_frame(
        match_record.query_frame if match_record is not None else None
    )
    if session_id is not None:
        source_type, source_id = "session", session_id
    elif match_record is not None:
        source_type, source_id = "runtime_match", match_record.id
    else:
        source_type, source_id = "retrieval_feedback", feedback_id
    metadata = {
        "feedback_type": feedback_type,
        "feedback_id": str(feedback_id),
    }
    if playbook_version_id is not None:
        metadata["playbook_version_id"] = str(playbook_version_id)
    if match_id:
        metadata["match_id"] = match_id
    await ensure_edge(
        db,
        tenant_id,
        source_type,
        source_id,
        "playbook",
        playbook_id,
        edge_type,
        metadata=metadata,
        confidence=1.0 if edge_type == "validated_fix" else 0.7,
    )


async def record_feedback(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    match_id: str | None,
    playbook_id: uuid.UUID | None,
    playbook_version_id: uuid.UUID | None,
    feedback_type: str,
    details: dict[str, Any] | None,
    submitted_by: uuid.UUID | None,
) -> RetrievalFeedback:
    feedback = RetrievalFeedback(
        tenant_id=tenant_id,
        match_id=match_id,
        playbook_id=playbook_id,
        playbook_version_id=playbook_version_id,
        feedback_type=feedback_type,
        details=details,
        submitted_by=submitted_by,
    )
    db.add(feedback)
    await db.flush()
    try:
        await _write_feedback_edges(
            db,
            tenant_id=tenant_id,
            match_id=match_id,
            playbook_id=playbook_id,
            playbook_version_id=playbook_version_id,
            feedback_type=feedback_type,
            feedback_id=feedback.id,
        )
    except Exception:
        logger.warning(
            "retrieval_feedback.edge_write_failed",
            tenant_id=str(tenant_id),
            feedback_type=feedback_type,
            exc_info=True,
        )
    return feedback
