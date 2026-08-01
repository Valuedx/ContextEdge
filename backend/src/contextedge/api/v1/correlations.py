from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.correlation_suggestion import CorrelationSuggestion
from contextedge.models.episode import CorrelationEdge
from contextedge.schemas.common import StatusResponse
from contextedge.schemas.review import (
    CorrelationDecisionRequest,
    CorrelationEdgeCreate,
    CorrelationEdgeResponse,
    CorrelationEdgeUpdate,
    CorrelationSuggestionResponse,
)
from contextedge.services.correlation_service import create_correlation
from contextedge.services.correlation_suggestion_service import (
    accept_suggestion,
    reject_suggestion,
)

router = APIRouter()


@router.get("", response_model=list[CorrelationEdgeResponse])
async def list_correlations(
    db: DbSession,
    user: AuthUser,
    evidence_id: UUID | None = None,
    correlation_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("knowledge_manager")
    stmt = select(CorrelationEdge).where(CorrelationEdge.tenant_id == user.tenant_id)
    if evidence_id is not None:
        stmt = stmt.where(
            or_(
                CorrelationEdge.source_evidence_id == evidence_id,
                CorrelationEdge.target_evidence_id == evidence_id,
            )
        )
    if correlation_type is not None:
        stmt = stmt.where(CorrelationEdge.correlation_type == correlation_type)
    stmt = stmt.order_by(CorrelationEdge.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=CorrelationEdgeResponse, status_code=status.HTTP_201_CREATED)
async def create_manual_correlation(
    body: CorrelationEdgeCreate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    return await create_correlation(
        db,
        user.tenant_id,
        body.source_evidence_id,
        body.target_evidence_id,
        body.correlation_type,
        body.confidence,
        explanation=body.explanation,
        created_by=user.email,
    )


@router.get("/suggestions/stats")
async def suggestion_stats(db: DbSession, user: AuthUser):
    """Reviewer-outcome aggregates per source pair and corroborator
    type (C1). The per-pair learned floors derive from these counts —
    visible here so a raised bar is never a mystery."""
    user.require_role("knowledge_manager")
    from contextedge.services.correlation_suggestion_service import (
        SIMILARITY_FLOOR,
        similarity_floor_for,
        suggestion_review_stats,
    )

    stats = await suggestion_review_stats(db, user.tenant_id)
    floors = {
        pair: similarity_floor_for(pair, stats["pairs"])
        for pair in stats["pairs"]
    }
    return {**stats, "base_floor": SIMILARITY_FLOOR, "effective_floors": floors}


@router.get("/suggestions", response_model=list[CorrelationSuggestionResponse])
async def list_suggestions(
    db: DbSession,
    user: AuthUser,
    status_filter: str = Query("pending", alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("knowledge_manager")
    stmt = (
        select(CorrelationSuggestion)
        .where(
            CorrelationSuggestion.tenant_id == user.tenant_id,
            CorrelationSuggestion.status == status_filter,
        )
        .order_by(CorrelationSuggestion.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().all()


async def _pending_suggestion(db, user, suggestion_id: UUID) -> CorrelationSuggestion:
    suggestion = (
        await db.execute(
            select(CorrelationSuggestion).where(
                CorrelationSuggestion.id == suggestion_id,
                CorrelationSuggestion.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"Suggestion already {suggestion.status}"
        )
    return suggestion


@router.post("/suggestions/{suggestion_id}/accept", response_model=CorrelationEdgeResponse)
async def accept_correlation_suggestion(
    suggestion_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    suggestion = await _pending_suggestion(db, user, suggestion_id)
    return await accept_suggestion(db, user.tenant_id, suggestion, user.email)


@router.post("/suggestions/{suggestion_id}/reject", response_model=StatusResponse)
async def reject_correlation_suggestion(
    suggestion_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    suggestion = await _pending_suggestion(db, user, suggestion_id)
    await reject_suggestion(db, user.tenant_id, suggestion, user.email)
    return StatusResponse(
        status="rejected", detail={"suggestion_id": str(suggestion_id)}
    )


@router.patch("/{correlation_id}", response_model=CorrelationEdgeResponse)
async def update_correlation(
    correlation_id: UUID,
    body: CorrelationEdgeUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    edge = (
        await db.execute(
            select(CorrelationEdge).where(
                CorrelationEdge.id == correlation_id,
                CorrelationEdge.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Correlation not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(edge, field, value)
    edge.created_by = user.email
    await db.flush()
    await db.refresh(edge)
    return edge


@router.post("/{correlation_id}/decision", response_model=StatusResponse)
async def decide_correlation(
    correlation_id: UUID,
    body: CorrelationDecisionRequest,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    edge = (
        await db.execute(
            select(CorrelationEdge).where(
                CorrelationEdge.id == correlation_id,
                CorrelationEdge.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Correlation not found")

    if body.decision == "reject":
        await db.delete(edge)
        return StatusResponse(
            status="rejected",
            detail={"correlation_id": str(correlation_id)},
        )

    if body.confidence is not None:
        edge.confidence = body.confidence
    if body.explanation is not None:
        edge.explanation = body.explanation
    edge.created_by = user.email

    created_ids: list[str] = []
    if body.decision in {"merge", "split"}:
        if body.decision == "split":
            await db.delete(edge)
        for replacement in body.replacement_edges:
            created = await create_correlation(
                db,
                user.tenant_id,
                replacement.source_evidence_id,
                replacement.target_evidence_id,
                replacement.correlation_type,
                replacement.confidence,
                explanation=replacement.explanation,
                created_by=user.email,
            )
            created_ids.append(str(created.id))
        if body.decision == "split":
            return StatusResponse(
                status="split",
                detail={
                    "correlation_id": str(correlation_id),
                    "replacement_ids": created_ids,
                },
            )

    await db.flush()
    return StatusResponse(
        status="accepted" if body.decision == "accept" else body.decision,
        detail={
            "correlation_id": str(correlation_id),
            "replacement_ids": created_ids,
        },
    )


@router.delete("/{correlation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_correlation(
    correlation_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    edge = (
        await db.execute(
            select(CorrelationEdge).where(
                CorrelationEdge.id == correlation_id,
                CorrelationEdge.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Correlation not found")
    await db.delete(edge)
    return None
