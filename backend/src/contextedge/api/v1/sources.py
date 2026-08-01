from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.source import Source, SourceCredential, SourceObject, SyncRun
from contextedge.schemas.common import TaskDispatchResponse
from contextedge.schemas.source import (
    BackfillRequest,
    CredentialRotateRequest,
    LocalIngestRequest,
    SourceCreate,
    SourceCredentialResponse,
    SourceObjectApproval,
    SourceObjectResponse,
    SourceResponse,
    SourceUpdate,
    SyncRunResponse,
)
from contextedge.services.evidence_normalization import evidence_content_hash_from_payload
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.services.source_service import (
    discover_source_objects,
    encrypt_credentials,
    probe_source_configuration,
    rotate_source_credentials,
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
        # Perform validation but don't let network timeouts crash the entire request
        import asyncio
        try:
            valid, message = await asyncio.wait_for(
                validate_source_credentials(
                    body.source_type, body.config, body.credentials
                ),
                timeout=10.0
            )
            source.auth_status = "connected" if valid else "failed"
        except TimeoutError:
            source.auth_status = "failed"
        except Exception:
            source.auth_status = "failed"
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
    await db.commit()

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
    await db.commit()

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
    await db.commit()
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

    # Trigger immediate sync if newly approved
    if update_data.get("approved_for_sync") is True:
        from contextedge.workers.sync_tasks import run_incremental_sync
        run_incremental_sync.delay(str(source_id), str(object_id), str(user.tenant_id))

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


@router.post(
    "/{source_id}/backfill",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskDispatchResponse,
)
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
        details={
            "object_ids": [str(i) for i in body.source_object_ids],
            "window_days": body.window_days,
        },
    )

    from contextedge.workers.sync_tasks import run_backfill
    for obj_id in body.source_object_ids:
        run_backfill.delay(str(source_id), str(obj_id), str(user.tenant_id), body.window_days)

    return TaskDispatchResponse(
        status="backfill_queued",
        detail={"object_count": len(body.source_object_ids)},
    )


@router.post("/{source_id}/credentials/rotate", response_model=SourceCredentialResponse)
async def rotate_credentials(
    source_id: UUID,
    body: CredentialRotateRequest,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("domain_admin")
    source = (
        await db.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        credential = await rotate_source_credentials(
            db,
            source,
            credentials=body.credentials,
            auth_type=body.auth_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source.credentials_rotated",
        resource_type="source",
        resource_id=str(source.id),
    )
    return credential


@router.post(
    "/local-ingest",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskDispatchResponse,
)
async def local_ingest(body: LocalIngestRequest, db: DbSession, user: AuthUser):
    """Directly ingest local files from the frontend folder picker."""
    user.require_role("domain_admin")

    source = (
        await db.execute(
            select(Source).where(Source.id == body.source_id, Source.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Mark as connected since it's a local ingest gesture
    source.auth_status = "connected"
    source.discovery_status = "completed"

    from contextedge.models.evidence import RawEvidenceObject
    from contextedge.services.artifact_extraction_service import process_attachment_artifact
    from contextedge.workers.extraction_tasks import _normalize

    created_ids = []
    for file in body.files:
        payload = {
            "filename": file.filename,
            "content": file.content,
            "content_type": file.content_type,
            "evidence_type": file.metadata.get("evidence_type", "message"),
            **file.metadata,
        }

        # Generate hash and external ID
        c_hash = evidence_content_hash_from_payload(payload)
        ext_id = f"local_{source.id}_{file.filename}"

        raw = RawEvidenceObject(
            tenant_id=user.tenant_id,
            source_id=source.id,
            external_id=ext_id,
            content_hash=c_hash,
            raw_payload=payload,
        )
        db.add(raw)
        await db.flush()
        created_ids.append(raw.id)

    # Queue normalization for each file - Run synchronously for local feedback
    for rid in created_ids:
        # We call the internal async _normalize directly to bypass Celery worker lag on Windows
        norm_res = await _normalize(db, str(rid), user.tenant_id)

        if norm_res and norm_res.get("attachment_ids"):
            for artifact_id in norm_res["attachment_ids"]:
                await process_attachment_artifact(
                    db,
                    artifact_id=UUID(str(artifact_id)),
                    tenant_id=user.tenant_id,
                )

        if norm_res and "evidence_id" in norm_res:
            from contextedge.workers.extraction_tasks import _classify
            await _classify(db, norm_res["evidence_id"], user.tenant_id)

    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source.local_ingest",
        resource_type="source",
        resource_id=str(source.id),
        details={"file_count": len(body.files)},
    )

    return TaskDispatchResponse(
        status="ingested",
        detail={
            "count": len(created_ids),
            "raw_ids": [str(rid) for rid in created_ids],
        },
    )


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: UUID, db: DbSession, user: AuthUser):
    """Permanently delete a source and all its associated evidence/logs."""
    user.require_role("domain_admin")

    source = (
        await db.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    from sqlalchemy import delete, or_

    from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
    from contextedge.models.source import SourceObject, SyncRun

    # 1. Resolve Evidence IDs to delete dependencies
    evidence_ids_q = await db.execute(
        select(EvidenceItem.id).where(EvidenceItem.source_id == source_id)
    )
    evidence_ids = evidence_ids_q.scalars().all()

    if evidence_ids:
        from contextedge.models.episode import CorrelationEdge
        from contextedge.models.evidence import AttachmentArtifact

        # 2. Delete Correlation Edges
        await db.execute(
            delete(CorrelationEdge).where(
                or_(
                    CorrelationEdge.source_evidence_id.in_(evidence_ids),
                    CorrelationEdge.target_evidence_id.in_(evidence_ids)
                )
            )
        )

        # 3. Delete Attachment Artifacts
        await db.execute(
            delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(evidence_ids))
        )

        # 4. Delete Evidence Items
        await db.execute(
            delete(EvidenceItem).where(EvidenceItem.source_id == source_id)
        )

    # 5. Delete Threads
    from contextedge.models.evidence import Thread
    await db.execute(
        delete(Thread).where(Thread.source_id == source_id)
    )

    # 6. Delete Raw Evidence
    await db.execute(
        delete(RawEvidenceObject).where(RawEvidenceObject.source_id == source_id)
    )

    # 6. Delete Sync Runs
    await db.execute(
        delete(SyncRun).where(SyncRun.source_id == source_id)
    )

    # 7. Delete Source Objects
    await db.execute(
        delete(SourceObject).where(SourceObject.source_id == source_id)
    )

    # 8. Delete Source Credentials
    from contextedge.models.source import SourceCredential
    await db.execute(
        delete(SourceCredential).where(SourceCredential.source_id == source_id)
    )

    # Finally delete the source
    await db.delete(source)
    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="source.deleted",
        resource_type="source",
        resource_id=str(source_id),
        details={"display_name": source.display_name},
    )
    return None


@router.post("/{source_id}/probe-config")
async def probe_source_config(source_id: UUID, db: DbSession, user: AuthUser):
    """D4: verify a config-mapped connector's instance mapping — which
    configured endpoints respond and which mapped field names actually
    appear in sample payloads. Read-only against the upstream API."""
    user.require_role("tenant_admin")
    from sqlalchemy import select as sa_select

    from contextedge.models.source import Source

    source = (
        await db.execute(
            sa_select(Source).where(
                Source.id == source_id, Source.tenant_id == user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        return await probe_source_configuration(db, source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
