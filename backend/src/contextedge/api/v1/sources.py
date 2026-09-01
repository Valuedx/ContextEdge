from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
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
    SourceTypeResponse,
    SourceUpdate,
    SyncRunResponse,
)
from contextedge.services.evidence_normalization import evidence_content_hash_from_payload
from contextedge.services.evidence_typing import UPLOADABLE_EVIDENCE_TYPES
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.services.tenant_membership import (
    assert_domains_in_tenant,
    assert_workspace_in_tenant,
)
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
    user.require_role("domain_admin")
    q = select(Source).where(Source.tenant_id == user.tenant_id)
    if source_type:
        q = q.where(Source.source_type == source_type)
    q = q.limit(limit).offset(offset).order_by(Source.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


# MUST stay above `GET /{source_id}`: that route parses its path segment
# as a UUID, so a later-registered `/types` would never be reached — the
# request would 422 on "types" instead.
@router.get("/types", response_model=list[SourceTypeResponse])
async def list_source_types(user: AuthUser):
    """Selectable source types and whether each one can actually sync.

    The source picker renders from this instead of a hardcoded list. The
    two had drifted apart in both directions — the UI offered three types
    with no connector behind them, and hid two that worked — which is a
    drift that a client-side list makes invisible until a user hits it.
    """
    user.require_role("domain_admin")
    from contextedge.connectors.registry import source_type_catalog

    return [
        SourceTypeResponse(
            source_type=info.source_type,
            label=info.label,
            connector_available=info.connector_available,
            status=info.status,
            description=info.description,
        )
        for info in source_type_catalog()
    ]


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(body: SourceCreate, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")

    await assert_workspace_in_tenant(db, user.tenant_id, body.workspace_id)
    await assert_domains_in_tenant(db, user.tenant_id, body.domain_ids)

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
            tenant_id=source.tenant_id,
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
    user.require_role("domain_admin")
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
        await assert_domains_in_tenant(db, user.tenant_id, update_data["domain_ids"])
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
    # `ingest_priority` is not a column — it rides in metadata_extra, so the
    # queue order is per source object without a migration for a preference.
    priority = update_data.pop("ingest_priority", None)
    if priority is not None:
        so.metadata_extra = {**(so.metadata_extra or {}), "ingest_priority": priority}
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


class SyncControlRequest(BaseModel):
    """Pause, resume or cancel. `source_object_id` targets one module; omit
    it to act on every running sync for the source."""

    action: str = Field(..., description="pause | resume | cancel")
    source_object_id: UUID | None = None


@router.post("/{source_id}/sync/control")
async def control_sync(
    source_id: UUID, body: SyncControlRequest, db: DbSession, user: AuthUser
):
    """Signal the running sync. The job acts on it inside its own loops.

    Nothing is killed: a backfill holds records in memory for the length of a
    page walk, and terminating the worker would throw away what it has
    already paid Zoho for. The cooperative stop persists them and checkpoints,
    so `resume` continues instead of restarting.
    """
    user.require_role("domain_admin")
    action = body.action.strip().lower()
    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="action must be pause, resume or cancel",
        )

    from contextedge.services.sync_control_service import active_run, signal_run

    objects = (
        await db.execute(
            select(SourceObject).where(
                SourceObject.source_id == source_id,
                SourceObject.tenant_id == user.tenant_id,
                *([SourceObject.id == body.source_object_id] if body.source_object_id else []),
            )
        )
    ).scalars().all()
    if not objects:
        raise HTTPException(status_code=404, detail="No matching source object")

    affected: list[dict] = []
    for so in objects:
        run = await active_run(db, tenant_id=user.tenant_id, source_object_id=so.id)
        if action == "resume":
            # Resume is about the NEXT run: the paused one has already ended
            # and persisted what it had. Clearing the gate lets the scheduler
            # (or an operator) start one that picks up from the checkpoint.
            so.metadata_extra = {**(so.metadata_extra or {}), "sync_paused": False}
            affected.append({"object": so.external_id, "resumed": True})
            continue
        so.metadata_extra = {
            **(so.metadata_extra or {}),
            "sync_paused": action == "pause",
        }
        signalled = None
        if run is not None:
            signalled = await signal_run(
                db, tenant_id=user.tenant_id, run_id=run.id, action=action
            )
        affected.append({
            "object": so.external_id,
            "action": action,
            "running_run_id": str(run.id) if run else None,
            "signalled": bool(signalled and signalled.control == action),
        })

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=f"sync.{action}",
        resource_type="source",
        resource_id=str(source_id),
        details={"objects": affected},
    )
    await db.commit()
    return {"status": action, "objects": affected}


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

    # Batch-level content kind, validated against the known set so a typo
    # cannot silently miss KNOWLEDGE_EVIDENCE_TYPES — a "kb-article"
    # upload that lands as unrecognized text is precisely the failure the
    # evidence-typing work exists to end. An unknown value is rejected
    # rather than ignored: the uploader stated an intent, and quietly
    # discarding it would file their SOPs as chat messages.
    batch_evidence_type = (body.evidence_type or "").strip() or None
    if batch_evidence_type and batch_evidence_type not in UPLOADABLE_EVIDENCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown evidence_type '{batch_evidence_type}'. "
                f"Expected one of: {', '.join(sorted(UPLOADABLE_EVIDENCE_TYPES))}"
            ),
        )

    created_ids = []
    for file in body.files:
        payload = {
            "filename": file.filename,
            "content": file.content,
            "content_type": file.content_type,
            # Stamped so derive_evidence_type resolves local uploads to
            # "document" rather than "message" when nothing is declared.
            "_connector_source_type": "local_file",
            "_connector_object_type": "upload",
            **file.metadata,
        }
        # Per-file metadata wins over the batch declaration; the batch
        # declaration wins over the source default.
        declared = file.metadata.get("evidence_type") or batch_evidence_type
        if declared:
            payload["evidence_type"] = declared

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
        from contextedge.services.quality_staleness_hooks import signal_stale_for_evidence

        await signal_stale_for_evidence(
            db,
            user.tenant_id,
            list(evidence_ids),
            origin="source_deleted",
        )

        from contextedge.models.episode import CorrelationEdge
        from contextedge.models.evidence import AttachmentArtifact, EvidenceChunk
        from contextedge.models.knowledge_case import CaseLink
        from contextedge.models.situation import SituationEvidenceLink, SituationOccurrence
        from contextedge.models.session import SessionAction
        from contextedge.models.knowledge_supersession import KnowledgeSupersession
        from contextedge.models.correlation_suggestion import CorrelationSuggestion
        from contextedge.models.claim import ClaimEvidenceLink, ClaimContradiction
        from contextedge.models.playbook import PlaybookEvidenceLink

        # 2. Delete Dependent Links & Edges referencing evidence
        await db.execute(
            delete(CaseLink).where(CaseLink.source_evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(SituationEvidenceLink).where(SituationEvidenceLink.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(SituationOccurrence).where(SituationOccurrence.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(SessionAction).where(SessionAction.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(KnowledgeSupersession).where(
                or_(
                    KnowledgeSupersession.old_evidence_id.in_(evidence_ids),
                    KnowledgeSupersession.new_evidence_id.in_(evidence_ids)
                )
            )
        )
        await db.execute(
            delete(CorrelationSuggestion).where(
                or_(
                    CorrelationSuggestion.source_evidence_id.in_(evidence_ids),
                    CorrelationSuggestion.target_evidence_id.in_(evidence_ids)
                )
            )
        )
        await db.execute(
            delete(ClaimEvidenceLink).where(ClaimEvidenceLink.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(ClaimContradiction).where(ClaimContradiction.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(PlaybookEvidenceLink).where(PlaybookEvidenceLink.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(CorrelationEdge).where(
                or_(
                    CorrelationEdge.source_evidence_id.in_(evidence_ids),
                    CorrelationEdge.target_evidence_id.in_(evidence_ids)
                )
            )
        )
        await db.execute(
            delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(EvidenceChunk).where(EvidenceChunk.evidence_id.in_(evidence_ids))
        )

        # 3. Delete Evidence Items
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
