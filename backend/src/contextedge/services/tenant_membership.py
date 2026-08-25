"""Validate that referenced workspaces and domains belong to the caller tenant."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.tenant import Domain, Workspace


async def assert_workspace_in_tenant(
    db: AsyncSession, tenant_id: UUID, workspace_id: UUID | None
) -> None:
    if workspace_id is None:
        return
    found = (
        await db.execute(
            select(Workspace.id).where(
                Workspace.id == workspace_id, Workspace.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace not found in this tenant",
        )


async def assert_domains_in_tenant(
    db: AsyncSession, tenant_id: UUID, domain_ids: list[UUID] | None
) -> None:
    if not domain_ids:
        return
    unique_ids = list(dict.fromkeys(domain_ids))
    result = await db.execute(
        select(Domain.id).where(Domain.id.in_(unique_ids), Domain.tenant_id == tenant_id)
    )
    found = {row[0] for row in result.all()}
    if len(found) != len(unique_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more domains are not in this tenant",
        )
