from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import Tenant
from contextedge.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate

router = APIRouter()


@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("platform_super_admin")
    result = await db.execute(
        select(Tenant).limit(limit).offset(offset).order_by(Tenant.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("platform_super_admin")
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tenant slug already exists")

    tenant = Tenant(**body.model_dump())
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    await log_audit_event(
        db,
        tenant_id=tenant.id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="tenant.created",
        resource_type="tenant",
        resource_id=str(tenant.id),
    )
    return tenant


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if user.tenant_id != tenant_id and not user.has_role("platform_super_admin"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: UUID,
    body: TenantUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("tenant_admin")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tenant, field, value)
    await db.flush()
    await db.refresh(tenant)

    await log_audit_event(
        db,
        tenant_id=tenant.id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="tenant.updated",
        resource_type="tenant",
        resource_id=str(tenant.id),
        details=update_data,
    )
    return tenant
