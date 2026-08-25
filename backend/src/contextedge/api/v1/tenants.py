from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import Domain, RoleBinding, Tenant, User, Workspace
from contextedge.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate
from contextedge.tenant_rls import bind_session_tenant

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_exact_role("platform_super_admin")
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
    user.require_exact_role("platform_super_admin")
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tenant slug already exists")

    tenant = Tenant(
        **body.model_dump(exclude={"admin_username", "admin_display_name", "admin_password"})
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)
    # RLS is bound to the super-admin home tenant; child rows for the new
    # tenant must be written in that tenant's session context.
    await bind_session_tenant(db, tenant.id, bypass=False)

    workspace = Workspace(
        tenant_id=tenant.id,
        name="Default Workspace",
        description="Created with the tenant",
        config={},
        is_active=True,
    )
    db.add(workspace)
    await db.flush()
    db.add(
        Domain(
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            name="Default Domain",
            description="Created with the tenant",
            is_active=True,
        )
    )

    if body.admin_username:
        if not body.admin_password:
            raise HTTPException(
                status_code=400,
                detail="admin_password is required when creating a tenant admin",
            )
        existing = await db.execute(
            select(User).where(
                User.username == body.admin_username, User.tenant_id == tenant.id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="User with this username already exists")
        admin = User(
            tenant_id=tenant.id,
            username=body.admin_username,
            display_name=body.admin_display_name or body.admin_username,
            password_hash=pwd_context.hash(body.admin_password),
            status="active",
        )
        db.add(admin)
        await db.flush()
        db.add(
            RoleBinding(
                tenant_id=tenant.id,
                user_id=admin.id,
                role="tenant_admin",
                scope_type="tenant",
            )
        )

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
    if user.tenant_id != tenant_id and not user.has_exact_role("platform_super_admin"):
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
    if tenant_id != user.tenant_id and not user.has_exact_role("platform_super_admin"):
        raise HTTPException(status_code=403, detail="Access denied")
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
