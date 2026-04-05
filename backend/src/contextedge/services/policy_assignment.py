"""Validate tenant policy IDs when attaching to other resources."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.policy import TenantPolicy


async def assert_policy_assignment(
    db: AsyncSession,
    tenant_id: UUID,
    policy_id: UUID | None,
    expected_policy_type: str,
) -> None:
    """Raise 400 if policy_id is set but not a matching tenant policy row."""
    if policy_id is None:
        return
    r = await db.execute(
        select(TenantPolicy.id).where(
            TenantPolicy.id == policy_id,
            TenantPolicy.tenant_id == tenant_id,
            TenantPolicy.policy_type == expected_policy_type,
        )
    )
    if r.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Policy {policy_id} is not a valid {expected_policy_type} "
                "policy for this tenant"
            ),
        )
