"""Helpers for retrieval-time access policy filtering."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.policy import TenantPolicy

ADMIN_ROLES = {"platform_super_admin", "tenant_admin", "domain_admin"}


async def resolve_excluded_access_policy_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    caller_roles: list[str] | None,
) -> list[uuid.UUID] | None:
    roles = set(caller_roles or [])
    if roles & ADMIN_ROLES:
        return None
    if not hasattr(db, "execute"):
        return None

    result = await db.execute(
        select(TenantPolicy).where(
            TenantPolicy.tenant_id == tenant_id,
            TenantPolicy.policy_type == "access",
            TenantPolicy.is_active.is_(True),
        )
    )
    excluded = [
        policy.id
        for policy in result.scalars().all()
        if isinstance(getattr(policy, "config", None), dict)
        and getattr(policy, "config", {}).get("restricted") is True
    ]
    return excluded or None
