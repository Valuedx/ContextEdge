from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import Domain
from contextedge.schemas.tenant import DomainCreate, DomainResponse, DomainUpdate
from contextedge.services.tenant_membership import assert_workspace_in_tenant

router = APIRouter()


@router.get("", response_model=list[DomainResponse])
async def list_domains(
    db: DbSession,
    user: AuthUser,
    workspace_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Domain).where(Domain.tenant_id == user.tenant_id)
    if workspace_id:
        q = q.where(Domain.workspace_id == workspace_id)
    q = q.limit(limit).offset(offset).order_by(Domain.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def create_domain(body: DomainCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    await assert_workspace_in_tenant(db, user.tenant_id, body.workspace_id)
    domain = Domain(tenant_id=user.tenant_id, **body.model_dump())
    db.add(domain)
    await db.flush()
    await db.refresh(domain)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="domain.created",
        resource_type="domain",
        resource_id=str(domain.id),
    )
    return domain


@router.get("/{domain_id}", response_model=DomainResponse)
async def get_domain(domain_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Domain).where(
            Domain.id == domain_id,
            Domain.tenant_id == user.tenant_id,
        )
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


@router.patch("/{domain_id}", response_model=DomainResponse)
async def update_domain(
    domain_id: UUID,
    body: DomainUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(Domain).where(
            Domain.id == domain_id,
            Domain.tenant_id == user.tenant_id,
        )
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(domain, field, value)
    await db.flush()
    await db.refresh(domain)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="domain.updated",
        resource_type="domain",
        resource_id=str(domain.id),
        details=update_data,
    )
    return domain
