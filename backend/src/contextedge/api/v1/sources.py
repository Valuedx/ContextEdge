from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.source import Source, SourceCredential, SourceObject, SyncRun
from contextedge.schemas.source import (
    BackfillRequest,
    SourceCreate,
    SourceObjectApproval,
    SourceObjectResponse,
    SourceResponse,
    SourceUpdate,
    SyncRunResponse,
)
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.services.source_service import (
    discover_source_objects,
    encrypt_credentials,
    validate_source_credentials,
)

router = APIRouter()


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    db: DbSession,
    user: AuthUser,
    source_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Source).where(Source.tenant_id == user.tenant_id)
    if source_type:
        q = q.where(Source.source_type == source_type)
    q = q.limit(limit).offset(offset).order_by(Source.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceCreate, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")

    await assert_policy_assignment(
        db, user.tenant_id, body.retention_policy_id, "retention"
    )
    await assert_policy_assignment(
        db, user.tenant_id, body.classification_policy_id, "classification"
    )

    source = Source(
        tenant_id=user.tenant_id,
        workspace_id=body.workspace_id,
        domain_ids=[str(d) for d in body.domain_ids],
        source_type=body.source_type,
        display_name=body.display_name,
        owner_user_id=user.user_id,
        purpose=body.purpose,
        auth_type=body.auth_type,
        sync_mode=body.sync_mode,
        config=body.config,
        retention_policy_id=body.retention_policy_id,
        classification_policy_id=body.classification_policy_id,
    )

    if body.credentials:
        valid, message = await validate_source_credentials(
            body.source_type, body.config, body.credentials
        )
        source.auth_status = "connected" if valid else "failed"
    else:
        source.auth_status = "pending"

    db.add(source)
    await db.flush()

    if body.credentials:
        encrypted = await encrypt_credentials(body.credentials)
        cred = SourceCredential(
            source_id=source.id,
            auth_type=body.auth_type,
            encrypted_credentials=encrypted,
            status="active",
        )
        db.add(cred)
        await db.flush()

    await db.refresh(source)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source.created",
        resource_type="source",
        resource_id=str(source.id),
    )
    return source


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(source_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(source_id: UUID, body: SourceUpdate, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")
    result = await db.execute(
        select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = body.model_dump(exclude_unset=True)
    if "domain_ids" in update_data and update_data["domain_ids"] is not None:
        update_data["domain_ids"] = [str(d) for d in update_data["domain_ids"]]
    if "retention_policy_id" in update_data:
        await assert_policy_assignment(
            db, user.tenant_id, update_data["retention_policy_id"], "retention"
        )
    if "classification_policy_id" in update_data:
        await assert_policy_assignment(
            db, user.tenant_id, update_data["classification_policy_id"], "classification"
        )
    for field, value in update_data.items():
        setattr(source, field, value)
    await db.flush()
    await db.refresh(source)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source.updated",
        resource_type="source",
        resource_id=str(source.id),
        details=update_data,
    )
    return source


@router.post("/{source_id}/discover", response_model=list[SourceObjectResponse])
async def trigger_discovery(source_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")
    result = await db.execute(
        select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    objects = await discover_source_objects(db, source)
    return objects


@router.get("/{source_id}/objects", response_model=list[SourceObjectResponse])
async def list_source_objects(
    source_id: UUID,
    db: DbSession,
    user: AuthUser,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    result = await db.execute(
        select(SourceObject)
        .where(SourceObject.source_id == source_id, SourceObject.tenant_id == user.tenant_id)
        .limit(limit)
        .offset(offset)
        .order_by(SourceObject.display_name)
    )
    return result.scalars().all()


@router.patch("/{source_id}/objects/{object_id}", response_model=SourceObjectResponse)
async def approve_source_object(
    source_id: UUID,
    object_id: UUID,
    body: SourceObjectApproval,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("tenant_admin")
    result = await db.execute(
        select(SourceObject).where(
            SourceObject.id == object_id,
            SourceObject.source_id == source_id,
            SourceObject.tenant_id == user.tenant_id,
        )
    )
    so = result.scalar_one_or_none()
    if not so:
        raise HTTPException(status_code=404, detail="Source object not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(so, field, value)
    await db.flush()
    await db.refresh(so)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source_object.approved",
        resource_type="source_object",
        resource_id=str(so.id),
        details=update_data,
    )
    return so


@router.get("/{source_id}/sync-runs", response_model=list[SyncRunResponse])
async def list_sync_runs(
    source_id: UUID,
    db: DbSession,
    user: AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    result = await db.execute(
        select(SyncRun)
        .where(SyncRun.source_id == source_id, SyncRun.tenant_id == user.tenant_id)
        .limit(limit)
        .offset(offset)
        .order_by(SyncRun.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{source_id}/backfill", status_code=status.HTTP_202_ACCEPTED)
async def trigger_backfill(source_id: UUID, body: BackfillRequest, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="backfill.requested",
        resource_type="source",
        resource_id=str(source_id),
        details={"object_ids": [str(i) for i in body.source_object_ids], "window_days": body.window_days},
    )

    from contextedge.workers.sync_tasks import run_backfill
    for obj_id in body.source_object_ids:
        run_backfill.delay(str(obj_id), str(user.tenant_id), body.window_days)

    return {"status": "backfill_queued", "object_count": len(body.source_object_ids)}
