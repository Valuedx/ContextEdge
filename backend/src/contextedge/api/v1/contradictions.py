from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.pattern import Contradiction
from contextedge.schemas.review import (
    ContradictionResponse,
    ContradictionStatusUpdate,
)
from contextedge.services.event_log_service import append_operational_event

router = APIRouter()


@router.get("", response_model=list[ContradictionResponse])
async def list_contradictions(
    db: DbSession,
    user: AuthUser,
    resolution_status: str | None = None,
    contradiction_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("knowledge_manager")
    stmt = select(Contradiction).where(Contradiction.tenant_id == user.tenant_id)
    if resolution_status is not None:
        stmt = stmt.where(Contradiction.resolution_status == resolution_status)
    if contradiction_type is not None:
        stmt = stmt.where(Contradiction.contradiction_type == contradiction_type)
    stmt = stmt.order_by(Contradiction.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/{contradiction_id}/status", response_model=ContradictionResponse)
async def update_contradiction_status(
    contradiction_id: UUID,
    body: ContradictionStatusUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    contradiction = (
        await db.execute(
            select(Contradiction).where(
                Contradiction.id == contradiction_id,
                Contradiction.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not contradiction:
        raise HTTPException(status_code=404, detail="Contradiction not found")

    contradiction.resolution_status = body.resolution_status
    if body.description is not None:
        contradiction.description = body.description
    contradiction.resolved_by = None if body.resolution_status == "unresolved" else user.user_id
    await db.flush()
    await db.refresh(contradiction)

    await append_operational_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        entity_type="contradiction",
        entity_id=contradiction.id,
        event_type="contradiction.status_updated",
        payload={"resolution_status": body.resolution_status},
    )
    return contradiction
