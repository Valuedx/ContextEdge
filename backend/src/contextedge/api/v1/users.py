from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import RoleBinding, User
from contextedge.schemas.tenant import (
    RoleBindingCreate,
    RoleBindingResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(User)
        .where(User.tenant_id == user.tenant_id)
        .limit(limit)
        .offset(offset)
        .order_by(User.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")

    existing = await db.execute(
        select(User).where(User.email == body.email, User.tenant_id == user.tenant_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User with this email already exists")

    data = body.model_dump(exclude={"password"})
    new_user = User(tenant_id=user.tenant_id, **data)
    if body.password:
        new_user.password_hash = pwd_context.hash(body.password)
    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="user.created",
        resource_type="user",
        resource_id=str(new_user.id),
    )
    return new_user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == user.tenant_id)
    )
    u = result.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: UUID, body: UserUpdate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == user.tenant_id)
    )
    u = result.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

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
    return u


@router.post("/{user_id}/roles", response_model=RoleBindingResponse, status_code=status.HTTP_201_CREATED)
async def assign_role(user_id: UUID, body: RoleBindingCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    rb = RoleBinding(
        tenant_id=user.tenant_id,
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
        tenant_id=user.tenant_id,
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
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.tenant_id == user.tenant_id,
        )
    )
    return result.scalars().all()


@router.delete("/{user_id}/roles/{role_binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(user_id: UUID, role_binding_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.id == role_binding_id,
            RoleBinding.user_id == user_id,
            RoleBinding.tenant_id == user.tenant_id,
        )
    )
    rb = result.scalar_one_or_none()
    if not rb:
        raise HTTPException(status_code=404, detail="Role binding not found")
    await db.delete(rb)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="role.removed",
        resource_type="role_binding",
        resource_id=str(role_binding_id),
        details={"target_user_id": str(user_id)},
    )
