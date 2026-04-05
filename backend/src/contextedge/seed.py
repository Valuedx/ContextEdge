"""Seed script: creates a default tenant, admin user, and sample domain for development.

Apply the database schema first (e.g. ``alembic upgrade head`` or ``make migrate``); this
script does not call ``create_all`` so migrations remain the source of truth.
"""

import asyncio
import uuid

from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.database import async_session_factory
from contextedge.models.tenant import Domain, RoleBinding, Tenant, User, Workspace

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def seed():
    async with async_session_factory() as db:
        existing = await db.execute(select(Tenant).where(Tenant.slug == "default"))
        if existing.scalar_one_or_none():
            print("Seed data already exists, skipping.")
            return

        tenant = Tenant(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            name="Default Tenant",
            slug="default",
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

        admin = User(
            id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
            tenant_id=tenant.id,
            email="admin@contextedge.local",
            display_name="Platform Admin",
            password_hash=pwd_context.hash("admin123"),
            status="active",
        )
        db.add(admin)

        role = RoleBinding(
            tenant_id=tenant.id,
            user_id=admin.id,
            role="platform_super_admin",
            scope_type="tenant",
        )
        db.add(role)

        analyst = User(
            id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
            tenant_id=tenant.id,
            email="analyst@contextedge.local",
            display_name="Sample Analyst",
            password_hash=pwd_context.hash("analyst123"),
            status="active",
        )
        db.add(analyst)

        analyst_role = RoleBinding(
            tenant_id=tenant.id,
            user_id=analyst.id,
            role="analyst",
            scope_type="tenant",
        )
        db.add(analyst_role)

        await db.commit()
        print("Seed data created successfully.")
        print(f"  Tenant: {tenant.name} ({tenant.slug})")
        print("  Admin:  admin@contextedge.local / admin123")
        print("  Analyst: analyst@contextedge.local / analyst123")


if __name__ == "__main__":
    asyncio.run(seed())
