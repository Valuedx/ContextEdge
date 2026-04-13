from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.episode import CorrelationEdge
from contextedge.schemas.review import (
    CorrelationDecisionRequest,
    CorrelationEdgeCreate,
    CorrelationEdgeResponse,
    CorrelationEdgeUpdate,
)
from contextedge.services.correlation_service import create_correlation

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


@router.post("/{correlation_id}/decision")
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
        return {"status": "rejected", "correlation_id": str(correlation_id)}

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
            return {
                "status": "split",
                "correlation_id": str(correlation_id),
                "replacement_ids": created_ids,
            }

    await db.flush()
    return {
        "status": "accepted" if body.decision == "accept" else body.decision,
        "correlation_id": str(correlation_id),
        "replacement_ids": created_ids,
    }


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
