from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.tenant import Workspace
from contextedge.schemas.tenant import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate

router = APIRouter()


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    result = await db.execute(
        select(Workspace)
        .where(Workspace.tenant_id == user.tenant_id)
        .limit(limit)
        .offset(offset)
        .order_by(Workspace.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(body: WorkspaceCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    ws = Workspace(tenant_id=user.tenant_id, **body.model_dump())
    db.add(ws)
    await db.flush()
    await db.refresh(ws)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="workspace.created",
        resource_type="workspace",
        resource_id=str(ws.id),
    )
    return ws


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(workspace_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == user.tenant_id,
        )
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    body: WorkspaceUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == user.tenant_id,
        )
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ws, field, value)
    await db.flush()
    await db.refresh(ws)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="workspace.updated",
        resource_type="workspace",
        resource_id=str(ws.id),
        details=update_data,
    )
    return ws
