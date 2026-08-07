from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.pattern import NegativeKnowledgeItem
from contextedge.schemas.review import (
    NegativeKnowledgeCreate,
    NegativeKnowledgeResponse,
    NegativeKnowledgeUpdate,
)

router = APIRouter()


@router.get("", response_model=list[NegativeKnowledgeResponse])
async def list_negative_knowledge(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = None,
    item_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(NegativeKnowledgeItem).where(NegativeKnowledgeItem.tenant_id == user.tenant_id)
    if domain_id is not None:
        stmt = stmt.where(NegativeKnowledgeItem.domain_id == domain_id)
    if item_status is not None:
        stmt = stmt.where(NegativeKnowledgeItem.status == item_status)
    stmt = stmt.order_by(NegativeKnowledgeItem.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=NegativeKnowledgeResponse, status_code=status.HTTP_201_CREATED)
async def create_negative_knowledge(
    body: NegativeKnowledgeCreate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    item = NegativeKnowledgeItem(
        tenant_id=user.tenant_id,
        domain_id=body.domain_id,
        step_text=body.step_text,
        failure_reason=body.failure_reason,
        status=body.status,
        evidence_refs=body.evidence_refs,
        created_by=user.email,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


@router.patch("/{item_id}", response_model=NegativeKnowledgeResponse)
async def update_negative_knowledge(
    item_id: UUID,
    body: NegativeKnowledgeUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    item = (
        await db.execute(
            select(NegativeKnowledgeItem).where(
                NegativeKnowledgeItem.id == item_id,
                NegativeKnowledgeItem.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Negative knowledge item not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.flush()
    await db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_negative_knowledge(
    item_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    item = (
        await db.execute(
            select(NegativeKnowledgeItem).where(
                NegativeKnowledgeItem.id == item_id,
                NegativeKnowledgeItem.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Negative knowledge item not found")
    await db.delete(item)
    return None
