from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import RoleBinding, Tenant, User
from contextedge.schemas.tenant import (
    PLATFORM_ASSIGNABLE_ROLES,
    TENANT_ASSIGNABLE_ROLES,
    RoleBindingCreate,
    RoleBindingResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _user_out(user: User, roles: list[str], tenant_name: str | None = None) -> UserResponse:
    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        sso_provider=user.sso_provider,
        roles=roles,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


async def _tenant_names(db, tenant_ids: list[UUID]) -> dict[UUID, str]:
    names: dict[UUID, str] = {}
    if not tenant_ids:
        return names
    result = await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
    for tenant in result.scalars():
        names[tenant.id] = tenant.name
    return names


async def _roles_by_user(db, user_ids: list[UUID]) -> dict[UUID, list[str]]:
    mapped: dict[UUID, list[str]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return mapped
    result = await db.execute(select(RoleBinding).where(RoleBinding.user_id.in_(user_ids)))
    for binding in result.scalars():
        mapped.setdefault(binding.user_id, []).append(binding.role)
    return mapped


def _allowed_roles(actor: AuthUser) -> set[str]:
    if actor.has_exact_role("platform_super_admin"):
        return PLATFORM_ASSIGNABLE_ROLES
    return TENANT_ASSIGNABLE_ROLES


def _assert_can_assign(actor: AuthUser, role: str) -> None:
    actor.require_role("tenant_admin")
    if role not in _allowed_roles(actor) or not actor.can_assign_role(role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You cannot assign role '{role}'",
        )


async def _is_platform_super_admin_account(db, user_id: UUID) -> bool:
    binding = (
        await db.execute(
            select(RoleBinding.id).where(
                RoleBinding.user_id == user_id,
                RoleBinding.role == "platform_super_admin",
            )
        )
    ).scalar_one_or_none()
    return binding is not None


async def _managed_user(db: DbSession, actor: AuthUser, user_id: UUID) -> User:
    target = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == actor.tenant_id)
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not actor.has_exact_role("platform_super_admin") and await _is_platform_super_admin_account(
        db, target.id
    ):
        raise HTTPException(status_code=404, detail="User not found")
    return target


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant_id: UUID | None = None,
):
    user.require_role("tenant_admin")
    scope_id = user.tenant_id
    if tenant_id is not None:
        if user.has_exact_role("platform_super_admin"):
            scope_id = tenant_id
        elif tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=403,
                detail="Only the platform super admin can list another tenant",
            )
    stmt = select(User).where(User.tenant_id == scope_id)
    if not user.has_exact_role("platform_super_admin"):
        hidden = select(RoleBinding.user_id).where(
            RoleBinding.tenant_id == scope_id,
            RoleBinding.role == "platform_super_admin",
        )
        stmt = stmt.where(User.id.notin_(hidden))
    result = await db.execute(
        stmt.limit(limit).offset(offset).order_by(User.created_at.desc())
    )
    rows = list(result.scalars().all())
    roles = await _roles_by_user(db, [row.id for row in rows])
    names = await _tenant_names(db, list({row.tenant_id for row in rows}))
    return [_user_out(row, roles.get(row.id, []), names.get(row.tenant_id)) for row in rows]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    target_tenant_id = user.tenant_id
    if body.tenant_id is not None:
        if not user.has_exact_role("platform_super_admin"):
            raise HTTPException(
                status_code=403,
                detail="Only the platform super admin can create users in another tenant",
            )
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        target_tenant_id = tenant.id

    role = body.role or "analyst"
    _assert_can_assign(user, role)

    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User with this username already exists")

    data = body.model_dump(exclude={"password", "role", "tenant_id"})
    new_user = User(tenant_id=target_tenant_id, **data)
    if body.password:
        new_user.password_hash = pwd_context.hash(body.password)
    db.add(new_user)
    await db.flush()

    binding = RoleBinding(
        tenant_id=target_tenant_id,
        user_id=new_user.id,
        role=role,
        scope_type="tenant",
    )
    db.add(binding)
    await db.flush()
    await db.refresh(new_user)

    await log_audit_event(
        db,
        tenant_id=target_tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="user.created",
        resource_type="user",
        resource_id=str(new_user.id),
        details={"role": role},
    )
    target_tenant = (
        await db.execute(select(Tenant).where(Tenant.id == target_tenant_id))
    ).scalar_one_or_none()
    return _user_out(new_user, [role], target_tenant.name if target_tenant else None)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, db: DbSession, user: AuthUser):
    u = await _managed_user(db, user, user_id)
    roles = await _roles_by_user(db, [u.id])
    names = await _tenant_names(db, [u.tenant_id])
    return _user_out(u, roles.get(u.id, []), names.get(u.tenant_id))


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: UUID, body: UserUpdate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    u = await _managed_user(db, user, user_id)

    update_data = body.model_dump(exclude_unset=True, exclude={"password"})
    for field, value in update_data.items():
        setattr(u, field, value)
    if body.password:
        u.password_hash = pwd_context.hash(body.password)
    await db.flush()
    await db.refresh(u)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="user.updated",
        resource_type="user",
        resource_id=str(u.id),
    )
    roles = await _roles_by_user(db, [u.id])
    names = await _tenant_names(db, [u.tenant_id])
    return _user_out(u, roles.get(u.id, []), names.get(u.tenant_id))


@router.post(
    "/{user_id}/roles", response_model=RoleBindingResponse, status_code=status.HTTP_201_CREATED
)
async def assign_role(user_id: UUID, body: RoleBindingCreate, db: DbSession, user: AuthUser):
    _assert_can_assign(user, body.role)
    target = await _managed_user(db, user, user_id)

    duplicate = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.tenant_id == target.tenant_id,
            RoleBinding.role == body.role,
        )
    )
    if duplicate.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role already assigned")

    rb = RoleBinding(
        tenant_id=target.tenant_id,
        user_id=user_id,
        role=body.role,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    db.add(rb)
    await db.flush()
    await db.refresh(rb)

    await log_audit_event(
        db,
        tenant_id=target.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="role.assigned",
        resource_type="role_binding",
        resource_id=str(rb.id),
        details={"role": body.role, "target_user_id": str(user_id)},
    )
    return rb


@router.get("/{user_id}/roles", response_model=list[RoleBindingResponse])
async def list_user_roles(user_id: UUID, db: DbSession, user: AuthUser):
    target = await _managed_user(db, user, user_id)
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.tenant_id == target.tenant_id,
        )
    )
    return result.scalars().all()


@router.delete("/{user_id}/roles/{role_binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(user_id: UUID, role_binding_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    target = await _managed_user(db, user, user_id)
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.id == role_binding_id,
            RoleBinding.user_id == user_id,
            RoleBinding.tenant_id == target.tenant_id,
        )
    )
    rb = result.scalar_one_or_none()
    if not rb:
        raise HTTPException(status_code=404, detail="Role binding not found")
    if rb.role == "platform_super_admin" and not user.has_exact_role("platform_super_admin"):
        raise HTTPException(
            status_code=403,
            detail="Only the platform super admin can remove that role",
        )
    await db.delete(rb)

    await log_audit_event(
        db,
        tenant_id=target.tenant_id,
        resource_type="role_binding",
        resource_id=str(role_binding_id),
        details={"target_user_id": str(user_id)},
    )
