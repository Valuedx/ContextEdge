"""Celery-facing sync/backfill/incremental orchestration (async)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.connectors.base import Checkpoint, DateRange
from contextedge.models.source import Source, SourceCredential, SourceObject, SyncCheckpoint, SyncRun
from contextedge.services.source_service import create_sync_run, decrypt_credentials, discover_source_objects
from contextedge.connectors.registry import get_connector


async def run_discovery_job(db: AsyncSession, source_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    source = (
        await db.execute(select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id))
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
        run.completed_at = datetime.now(timezone.utc)
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


async def run_backfill_job(
    db: AsyncSession,
    source_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    window_days: int = 90,
) -> dict:
    so = await db.get(SourceObject, source_object_id)
    if not so or so.tenant_id != tenant_id:
        return {"error": "source_object_not_found"}

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

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)
    window = DateRange(start=start, end=end)

    try:
        result = await connector.backfill(so.external_id, so.object_type, window, ck)
        run.items_processed = result.items_processed
        run.status = "completed"
        if result.new_checkpoint:
            db.add(
                SyncCheckpoint(
                    source_object_id=so.id,
                    checkpoint_data=result.new_checkpoint.data,
                )
            )
        so.last_checkpoint_at = datetime.now(timezone.utc)
        so.last_successful_sync_at = datetime.now(timezone.utc)
    except Exception as exc:
        run.status = "failed"
        run.errors = {"message": str(exc)}
    finally:
        run.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"run_id": str(run.id), "status": run.status}


async def run_incremental_job(db: AsyncSession, source_object_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    so = await db.get(SourceObject, source_object_id)
    if not so or so.tenant_id != tenant_id:
        return {"error": "source_object_not_found"}

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
        run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        return {"run_id": str(run.id), "status": run.status}

    ck = Checkpoint(data=ck_row.checkpoint_data, captured_at=ck_row.captured_at)

    try:
        result = await connector.fetch_changes(so.external_id, so.object_type, ck)
        run.items_processed = result.items_processed
        run.status = "completed"
        db.add(
            SyncCheckpoint(
                source_object_id=so.id,
                checkpoint_data=result.new_checkpoint.data,
            )
        )
        so.last_checkpoint_at = datetime.now(timezone.utc)
        so.last_successful_sync_at = datetime.now(timezone.utc)
    except Exception as exc:
        run.status = "failed"
        run.errors = {"message": str(exc)}
    finally:
        run.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return {"run_id": str(run.id), "status": run.status}
