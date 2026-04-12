from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.evidence import EvidenceItem, Thread
from contextedge.schemas.evidence import (
    EvidenceAccessPolicyUpdate,
    EvidenceItemDetail,
    EvidenceItemResponse,
    ThreadResponse,
)
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.search.access_control import resolve_excluded_access_policy_ids

router = APIRouter()


@router.get("", response_model=list[EvidenceItemResponse])
async def search_evidence(
    db: DbSession,
    user: AuthUser,
    query: str | None = None,
    source_id: UUID | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)

    if query and query.strip():
        from contextedge.search.pg_fts import search_evidence_fts

        fts_results = await search_evidence_fts(
            db,
            user.tenant_id,
            query.strip(),
            limit=limit,
            exclude_policy_ids=excluded_policy_ids,
        )
        return [item for item, _rank in fts_results]

    q = select(EvidenceItem).where(EvidenceItem.tenant_id == user.tenant_id)
    if excluded_policy_ids:
        q = q.where(
            or_(
                EvidenceItem.access_policy_id.is_(None),
                EvidenceItem.access_policy_id.notin_(excluded_policy_ids),
            )
        )
    if source_id:
        q = q.where(EvidenceItem.source_id == source_id)
    if relevance_state:
        q = q.where(EvidenceItem.relevance_state == relevance_state)
    if evidence_type:
        q = q.where(EvidenceItem.evidence_type == evidence_type)
    if domain_id:
        q = q.where(EvidenceItem.domain_id == domain_id)
    q = q.order_by(EvidenceItem.ingested_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{evidence_id}", response_model=EvidenceItemDetail)
async def get_evidence(evidence_id: UUID, db: DbSession, user: AuthUser):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item or (
        excluded_policy_ids and item.access_policy_id in set(excluded_policy_ids)
    ):
        raise HTTPException(status_code=404, detail="Evidence not found")
    return item


@router.patch(
    "/{evidence_id}/access-policy",
    response_model=EvidenceItemDetail,
)
async def update_access_policy(
    evidence_id: UUID,
    body: EvidenceAccessPolicyUpdate,
    db: DbSession,
    user: AuthUser,
):
    if not (
        user.has_role("domain_admin")
        or user.has_role("knowledge_manager")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Domain admin or knowledge manager role required",
        )
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")
    await assert_policy_assignment(
        db, user.tenant_id, body.access_policy_id, "access"
    )
    item.access_policy_id = body.access_policy_id
    await db.flush()
    await db.refresh(item)
    return item


@router.patch("/{evidence_id}/relevance")
async def update_relevance(
    evidence_id: UUID,
    relevance_state: str,
    db: DbSession,
    user: AuthUser,
):
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")
    item.relevance_state = relevance_state
    await db.flush()
    return {"status": "updated"}


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evidence(evidence_id: UUID, db: DbSession, user: AuthUser):
    """Permanently delete an evidence item."""
    user.require_role("domain_admin")

    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")

    await db.delete(item)
    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="evidence.deleted",
        resource_type="evidence",
        resource_id=str(evidence_id),
        details={"title": item.title},
    )
    return None
