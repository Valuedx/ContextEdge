"""Seed tenant structure. Users and passwords live in the database.

Optional first-time accounts can be created from environment variables
(never hardcoded passwords in application or UI code):

  SEED_SUPER_ADMIN_USERNAME / SEED_SUPER_ADMIN_PASSWORD
  SEED_TENANT_ADMIN_USERNAME / SEED_TENANT_ADMIN_PASSWORD
  SEED_ANALYST_USERNAME / SEED_ANALYST_PASSWORD

Apply the database schema first (e.g. ``alembic upgrade head`` or ``make migrate``);
this script does not call ``create_all`` so migrations remain the source of truth.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from passlib.context import CryptContext
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.database import async_session_factory
from contextedge.models.tenant import Domain, RoleBinding, Tenant, User, Workspace

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SEED_TENANT_NAME = "AutomationEdge"
SEED_TENANT_SLUG = "automationedge"
LEGACY_TENANT_SLUG = "default"

# ae = AutomationEdge. Assigned onto existing role rows; passwords are not changed.
BOOTSTRAP_USERNAMES = {
    "platform_super_admin": "superadmin-contextedge",
    "tenant_admin": "tenantadmin-ae",
    "analyst": "analyst-ae",
}


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


async def resolve_seed_tenant(db: AsyncSession) -> Tenant | None:
    for slug in (SEED_TENANT_SLUG, LEGACY_TENANT_SLUG):
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if tenant:
            return tenant
    return None


async def resolve_owner_user(db: AsyncSession, tenant_id: uuid.UUID) -> User | None:
    for role in ("platform_super_admin", "tenant_admin"):
        user = (
            await db.execute(
                select(User)
                .join(RoleBinding, RoleBinding.user_id == User.id)
                .where(
                    User.tenant_id == tenant_id,
                    RoleBinding.tenant_id == tenant_id,
                    RoleBinding.role == role,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if user:
            return user
    return (
        await db.execute(select(User).where(User.tenant_id == tenant_id).limit(1))
    ).scalar_one_or_none()


async def _ensure_user(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    username: str,
    display_name: str,
    password: str,
    roles: set[str],
) -> User:
    username = username.lower()
    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if user:
        user.display_name = display_name
        user.status = "active"
        user.tenant_id = tenant_id
    else:
        user = User(
            tenant_id=tenant_id,
            username=username,
            display_name=display_name,
            password_hash=pwd_context.hash(password),
            status="active",
        )
        db.add(user)
        await db.flush()
    await _set_roles(db, tenant_id, user.id, roles)
    return user


async def _set_roles(db: AsyncSession, tenant_id, user_id, wanted: set[str]) -> None:
    existing = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.tenant_id == tenant_id,
            )
        )
    ).scalars().all()
    have = {binding.role: binding for binding in existing}
    for role, binding in have.items():
        if role not in wanted:
            await db.delete(binding)
    for role in wanted:
        if role not in have:
            db.add(
                RoleBinding(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=role,
                    scope_type="tenant",
                )
            )


async def _assign_role_usernames(db: AsyncSession, tenant: Tenant) -> None:
    reserved = set(BOOTSTRAP_USERNAMES.values())
    for role, username in BOOTSTRAP_USERNAMES.items():
        taken = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        candidates = (
            await db.execute(
                select(User)
                .join(RoleBinding, RoleBinding.user_id == User.id)
                .where(
                    User.tenant_id == tenant.id,
                    RoleBinding.role == role,
                )
                .order_by(User.created_at.asc())
            )
        ).scalars().all()
        for user in candidates:
            if taken and taken.id != user.id:
                break
            if user.username in reserved and user.username != username:
                continue
            user.username = username
            break


async def _maybe_bootstrap_users_from_env(db: AsyncSession, tenant: Tenant) -> int:
    specs = (
        (
            "SEED_SUPER_ADMIN_USERNAME",
            "SEED_SUPER_ADMIN_PASSWORD",
            "SEED_SUPER_ADMIN_NAME",
            "Platform Super Admin",
            {"platform_super_admin"},
        ),
        (
            "SEED_TENANT_ADMIN_USERNAME",
            "SEED_TENANT_ADMIN_PASSWORD",
            "SEED_TENANT_ADMIN_NAME",
            "Tenant Admin",
            {"tenant_admin"},
        ),
        (
            "SEED_ANALYST_USERNAME",
            "SEED_ANALYST_PASSWORD",
            "SEED_ANALYST_NAME",
            "Analyst",
            {"analyst"},
        ),
    )
    created_or_updated = 0
    for username_key, password_key, name_key, default_name, roles in specs:
        username = _env(username_key)
        password = _env(password_key)
        if not username or not password:
            continue
        if "@" in username:
            raise ValueError(f"{username_key} must be a username without @")
        await _ensure_user(
            db,
            tenant_id=tenant.id,
            username=username,
            display_name=_env(name_key) or default_name,
            password=password,
            roles=roles,
        )
        created_or_updated += 1
    return created_or_updated


async def _align_tenant(db: AsyncSession, tenant: Tenant) -> None:
    tenant.name = SEED_TENANT_NAME
    tenant.slug = SEED_TENANT_SLUG


async def _print_accounts(db: AsyncSession, tenant: Tenant) -> None:
    print(f"  Tenant: {tenant.name} ({tenant.slug})")
    rows = (
        await db.execute(
            select(User, RoleBinding.role)
            .outerjoin(
                RoleBinding,
                and_(RoleBinding.user_id == User.id, RoleBinding.tenant_id == tenant.id),
            )
            .where(User.tenant_id == tenant.id)
            .order_by(User.username)
        )
    ).all()
    if not rows:
        print("  Users: none in the database. Create them in Settings, or set SEED_* env vars.")
        return
    by_username: dict[str, list[str]] = {}
    for user, role in rows:
        by_username.setdefault(user.username, [])
        if role:
            by_username[user.username].append(role)
    print("  Users in database (sign in with username; passwords stay hashed):")
    for username, roles in by_username.items():
        role_label = ", ".join(roles) if roles else "(no role)"
        print(f"    {username}  [{role_label}]")


async def seed():
    async with async_session_factory() as db:
        from contextedge.tenant_rls import bind_session_tenant

        await bind_session_tenant(db, None, bypass=True)
        tenant = await resolve_seed_tenant(db)
        if tenant is None:
            tenant = Tenant(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                name=SEED_TENANT_NAME,
                slug=SEED_TENANT_SLUG,
                config={},
            )
            db.add(tenant)
            workspace = Workspace(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                tenant_id=tenant.id,
                name="Default Workspace",
            )
            db.add(workspace)
            domain = Domain(
                id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
                tenant_id=tenant.id,
                workspace_id=workspace.id,
                name="General IT Operations",
                description="VPN, SSO, endpoint, desktop, and application support",
            )
            db.add(domain)
            await db.flush()
            print("Seed tenant created.")
        else:
            await _align_tenant(db, tenant)
            print("Seed tenant updated.")

        await _assign_role_usernames(db, tenant)
        bootstrapped = await _maybe_bootstrap_users_from_env(db, tenant)
        if bootstrapped:
            print(f"  Bootstrapped {bootstrapped} user(s) from environment variables.")
        await db.commit()
        await _print_accounts(db, tenant)


if __name__ == "__main__":
    asyncio.run(seed())
