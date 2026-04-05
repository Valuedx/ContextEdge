"""Celery-facing sync/backfill/incremental orchestration (async)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.connectors.base import Checkpoint, DateRange
from contextedge.connectors.registry import get_connector
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.source import (
    Source,
    SourceCredential,
    SourceObject,
    SyncCheckpoint,
)
from contextedge.services.evidence_normalization import evidence_content_hash_from_payload
from contextedge.services.ingestion_persistence import persist_ingestion_events
from contextedge.services.source_service import (
    create_sync_run,
    decrypt_credentials,
    discover_source_objects,
)
from contextedge.services.sync_ingestion_queue import queue_normalize_raw_objects


async def run_discovery_job(db: AsyncSession, source_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    source = (
        await db.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id),
        )
    ).scalar_one_or_none()
    if not source:
        return {"error": "source_not_found"}

    run = await create_sync_run(db, source.id, tenant_id, "discovery", None)
    try:
        created = await discover_source_objects(db, source)
        run.status = "completed"
        run.items_processed = len(created)
    except Exception as exc:
        run.status = "failed"
        run.errors = {"message": str(exc)}
    finally:
        run.completed_at = datetime.now(UTC)
    await db.flush()
    return {"run_id": str(run.id), "status": run.status}


async def _load_connector(db: AsyncSession, source: Source):
    cred = (
        await db.execute(
            select(SourceCredential).where(
                SourceCredential.source_id == source.id,
                SourceCredential.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not cred:
        raise ValueError("no_active_credentials")
    decrypted = await decrypt_credentials(cred.encrypted_credentials)
    return get_connector(source.source_type, source.config, decrypted)


async def _pending_normalize_raw_ids(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            select(RawEvidenceObject.id, RawEvidenceObject.raw_payload)
            .where(
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.source_object_id == source_object_id,
            )
            .order_by(RawEvidenceObject.stored_at.asc())
        )
    ).all()
    if not rows:
        return []

    hash_by_raw_id: dict[uuid.UUID, str] = {
        raw_id: evidence_content_hash_from_payload(payload) for raw_id, payload in rows
    }
    hashes = list(set(hash_by_raw_id.values()))
    existing_hashes: set[str] = set()
    if hashes:
        existing_hashes = set(
            (
                await db.execute(
                    select(EvidenceItem.content_hash).where(
                        EvidenceItem.tenant_id == tenant_id,
                        EvidenceItem.content_hash.in_(hashes),
                    )
                )
            ).scalars()
        )

    return [
        raw_id
        for raw_id, content_hash in hash_by_raw_id.items()
        if content_hash not in existing_hashes
    ]


async def _commit_and_queue_normalization(
    db: AsyncSession,
    *,
    run,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
) -> None:
    await db.commit()
    pending_raw_ids = await _pending_normalize_raw_ids(
        db,
        tenant_id=tenant_id,
        source_object_id=source_object_id,
    )
    if not pending_raw_ids:
        return

    try:
        queue_normalize_raw_objects(pending_raw_ids, tenant_id)
    except Exception as exc:
        existing_errors = dict(run.errors) if isinstance(run.errors, dict) else {}
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        existing_errors["handoff"] = {
            "message": "normalize_enqueue_failed",
            "detail": str(exc),
            "pending_raw_count": len(pending_raw_ids),
        }
        run.errors = existing_errors
        await db.flush()
        await db.commit()
        raise


async def run_backfill_job(
    db: AsyncSession,
    source_id: uuid.UUID,
    source_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    window_days: int = 90,
) -> dict:
    r = await db.execute(
        select(SourceObject).where(
            SourceObject.id == source_object_id,
            SourceObject.source_id == source_id,
            SourceObject.tenant_id == tenant_id,
        )
    )
    so = r.scalar_one_or_none()
    if not so:
        return {"error": "source_object_not_found"}

    if not so.approved_for_backfill:
        return {"error": "source_object_not_approved_for_backfill"}

    source = await db.get(Source, so.source_id)
    if not source:
        return {"error": "source_not_found"}

    run = await create_sync_run(db, source.id, tenant_id, "backfill", so.id)
    connector = await _load_connector(db, source)

    ck_row = (
        await db.execute(
            select(SyncCheckpoint)
            .where(SyncCheckpoint.source_object_id == so.id)
            .order_by(SyncCheckpoint.captured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ck = Checkpoint(data=ck_row.checkpoint_data, captured_at=ck_row.captured_at) if ck_row else None

    end = datetime.now(UTC)
    start = end - timedelta(days=window_days)
    window = DateRange(start=start, end=end)

    try:
        result = await connector.backfill(so.external_id, so.object_type, window, ck)
        events = list(result.events or [])
        raw_created, raw_deduped, _new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source.id,
            source_object_id=so.id,
            events=events,
        )
        run.items_processed = len(events) if events else result.items_processed
        run.status = "completed"
        run.errors = (
            {"ingestion": {"raw_objects_created": raw_created, "raw_objects_deduped": raw_deduped}}
            if (raw_created or raw_deduped)
            else None
        )
        if result.new_checkpoint:
            db.add(
                SyncCheckpoint(
                    source_object_id=so.id,
                    checkpoint_data=result.new_checkpoint.data,
                )
            )
        so.last_checkpoint_at = datetime.now(UTC)
        so.last_successful_sync_at = datetime.now(UTC)
    except Exception as exc:
        run.status = "failed"
        run.errors = {"message": str(exc)}
    finally:
        run.completed_at = datetime.now(UTC)
    await db.flush()
    if run.status == "completed":
        await _commit_and_queue_normalization(
            db,
            run=run,
            tenant_id=tenant_id,
            source_object_id=so.id,
        )
    return {"run_id": str(run.id), "status": run.status}


async def run_incremental_job(
    db: AsyncSession,
    source_id: uuid.UUID,
    source_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    r = await db.execute(
        select(SourceObject).where(
            SourceObject.id == source_object_id,
            SourceObject.source_id == source_id,
            SourceObject.tenant_id == tenant_id,
        )
    )
    so = r.scalar_one_or_none()
    if not so:
        return {"error": "source_object_not_found"}

    if not so.approved_for_sync:
        return {"error": "source_object_not_approved_for_sync"}

    source = await db.get(Source, so.source_id)
    if not source:
        return {"error": "source_not_found"}

    run = await create_sync_run(db, source.id, tenant_id, "incremental", so.id)
    connector = await _load_connector(db, source)

    ck_row = (
        await db.execute(
            select(SyncCheckpoint)
            .where(SyncCheckpoint.source_object_id == so.id)
            .order_by(SyncCheckpoint.captured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not ck_row:
        run.status = "failed"
        run.errors = {"message": "no_checkpoint_for_incremental"}
        run.completed_at = datetime.now(UTC)
        await db.flush()
        return {"run_id": str(run.id), "status": run.status}

    ck = Checkpoint(data=ck_row.checkpoint_data, captured_at=ck_row.captured_at)

    try:
        result = await connector.fetch_changes(so.external_id, so.object_type, ck)
        events = list(result.events or [])
        raw_created, raw_deduped, _new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source.id,
            source_object_id=so.id,
            events=events,
        )
        run.items_processed = len(events) if events else result.items_processed
        run.status = "completed"
        run.errors = (
            {"ingestion": {"raw_objects_created": raw_created, "raw_objects_deduped": raw_deduped}}
            if (raw_created or raw_deduped)
            else None
        )
        db.add(
            SyncCheckpoint(
                source_object_id=so.id,
                checkpoint_data=result.new_checkpoint.data,
            )
        )
        so.last_checkpoint_at = datetime.now(UTC)
        so.last_successful_sync_at = datetime.now(UTC)
    except Exception as exc:
        run.status = "failed"
        run.errors = {"message": str(exc)}
    finally:
        run.completed_at = datetime.now(UTC)
    await db.flush()
    if run.status == "completed":
        await _commit_and_queue_normalization(
            db,
            run=run,
            tenant_id=tenant_id,
            source_object_id=so.id,
        )
    return {"run_id": str(run.id), "status": run.status}
