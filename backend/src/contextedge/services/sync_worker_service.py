"""Celery-facing sync/backfill/incremental orchestration (async)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
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
from contextedge.services.ingest_priority import (
    _ingest_priority,
    order_raw_ids_by_priority,
)
from contextedge.services.ingestion_persistence import persist_ingestion_events
from contextedge.services.source_service import (
    create_sync_run,
    decrypt_credentials,
    discover_source_objects,
)
from contextedge.services.sync_ingestion_queue import (
    NormalizeEnqueueError,
    queue_normalize_raw_objects,
)

logger = structlog.get_logger()


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


def _pending_handoff_raw_ids_from_errors(errors: object) -> list[uuid.UUID]:
    return _coerce_pending_raw_ids(_handoff_value_from_errors(errors).get("pending_raw_ids"))


def _coerce_pending_raw_ids(values: object) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []

    raw_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        try:
            raw_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if raw_id in seen:
            continue
        seen.add(raw_id)
        raw_ids.append(raw_id)
    return raw_ids


def _handoff_value_from_errors(errors: object) -> dict:
    if not isinstance(errors, dict):
        return {}

    handoff = errors.get("handoff")
    if not isinstance(handoff, dict):
        return {}
    return dict(handoff)


def _pending_raw_ids_from_source_object(source_object: SourceObject) -> list[uuid.UUID]:
    metadata = (
        source_object.metadata_extra if isinstance(source_object.metadata_extra, dict) else {}
    )
    return _coerce_pending_raw_ids(metadata.get("pending_normalize_raw_ids"))


def _set_pending_raw_ids_on_source_object(
    source_object: SourceObject,
    *,
    raw_ids: list[uuid.UUID],
    updated_at: datetime | None = None,
) -> None:
    metadata = (
        dict(source_object.metadata_extra)
        if isinstance(source_object.metadata_extra, dict)
        else {}
    )
    if raw_ids:
        metadata["pending_normalize_raw_ids"] = [str(raw_id) for raw_id in raw_ids]
        metadata["pending_normalize_updated_at"] = (updated_at or datetime.now(UTC)).isoformat()
    else:
        metadata.pop("pending_normalize_raw_ids", None)
        metadata.pop("pending_normalize_updated_at", None)

    source_object.metadata_extra = metadata or None


def _merge_raw_ids(*groups: list[uuid.UUID]) -> list[uuid.UUID]:
    merged: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for group in groups:
        for raw_id in group:
            if raw_id in seen:
                continue
            seen.add(raw_id)
            merged.append(raw_id)
    return merged


async def _lock_source_object(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
) -> SourceObject:
    source_object = (
        await db.execute(
            select(SourceObject)
            .where(
                SourceObject.id == source_object_id,
                SourceObject.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not source_object:
        raise ValueError("source_object_not_found")
    return source_object


async def _filter_already_normalized_raw_ids(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    if not raw_ids:
        return []

    raw_rows = (
        await db.execute(
            select(RawEvidenceObject.id, RawEvidenceObject.raw_payload).where(
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.id.in_(raw_ids),
            )
        )
    ).all()
    if not raw_rows:
        return []

    raw_hash_by_id = {
        raw_id: evidence_content_hash_from_payload(raw_payload)
        for raw_id, raw_payload in raw_rows
    }
    normalized_candidate_ids = list(raw_hash_by_id)
    normalized_raw_ids = {
        raw_id
        for raw_id in (
            await db.execute(
                select(EvidenceItem.raw_object_ref).where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.raw_object_ref.in_(normalized_candidate_ids),
                )
            )
        ).scalars().all()
        if raw_id is not None
    }
    normalized_hashes: set[str] = set()
    candidate_hashes = {content_hash for content_hash in raw_hash_by_id.values() if content_hash}
    if candidate_hashes:
        normalized_hashes = set(
            (
                await db.execute(
                    select(EvidenceItem.content_hash).where(
                        EvidenceItem.tenant_id == tenant_id,
                        EvidenceItem.content_hash.in_(candidate_hashes),
                    )
                )
            ).scalars().all()
        )

    return [
        raw_id
        for raw_id in raw_ids
        if raw_id in raw_hash_by_id
        and raw_id not in normalized_raw_ids
        and raw_hash_by_id[raw_id] not in normalized_hashes
    ]


async def _reconcile_pending_raw_ids_on_source_object(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
    add_raw_ids: list[uuid.UUID] | None = None,
    remove_raw_ids: list[uuid.UUID] | None = None,
    updated_at: datetime | None = None,
) -> list[uuid.UUID]:
    source_object = await _lock_source_object(
        db,
        tenant_id=tenant_id,
        source_object_id=source_object_id,
    )
    pending_raw_ids = _merge_raw_ids(
        _pending_raw_ids_from_source_object(source_object),
        add_raw_ids or [],
    )
    if remove_raw_ids:
        remove_set = set(remove_raw_ids)
        pending_raw_ids = [raw_id for raw_id in pending_raw_ids if raw_id not in remove_set]

    pending_raw_ids = await _filter_already_normalized_raw_ids(
        db,
        tenant_id=tenant_id,
        raw_ids=pending_raw_ids,
    )
    _set_pending_raw_ids_on_source_object(
        source_object,
        raw_ids=pending_raw_ids,
        updated_at=updated_at,
    )
    await db.flush()
    await db.commit()
    return pending_raw_ids


async def _claim_pending_raw_ids_for_handoff(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
    new_raw_ids: list[uuid.UUID],
) -> tuple[list[uuid.UUID], list[uuid.UUID], str]:
    source_object = await _lock_source_object(
        db,
        tenant_id=tenant_id,
        source_object_id=source_object_id,
    )
    recovered_raw_ids = _pending_raw_ids_from_source_object(source_object)
    pending_raw_ids = await _filter_already_normalized_raw_ids(
        db,
        tenant_id=tenant_id,
        raw_ids=_merge_raw_ids(recovered_raw_ids, new_raw_ids),
    )
    if recovered_raw_ids:
        _set_pending_raw_ids_on_source_object(source_object, raw_ids=[])
        await db.flush()
    await db.commit()
    # The priority travels with the claim: the source object is already
    # locked and loaded here, and re-fetching it downstream would be a second
    # query for a value this function is holding.
    return pending_raw_ids, recovered_raw_ids, _ingest_priority(source_object)


async def _commit_and_queue_normalization(
    db: AsyncSession,
    *,
    run,
    tenant_id: uuid.UUID,
    source_object_id: uuid.UUID,
    new_raw_ids: list[uuid.UUID],
) -> None:
    pending_raw_ids, _recovered_raw_ids, priority = await _claim_pending_raw_ids_for_handoff(
        db,
        tenant_id=tenant_id,
        source_object_id=source_object_id,
        new_raw_ids=new_raw_ids,
    )
    if not pending_raw_ids:
        return

    pending_raw_ids = await order_raw_ids_by_priority(
        db, tenant_id=tenant_id, raw_ids=pending_raw_ids, priority=priority,
    )

    try:
        queue_normalize_raw_objects(pending_raw_ids, tenant_id)
    except NormalizeEnqueueError as exc:
        unqueued_raw_ids = exc.pending_raw_ids
        unqueued_raw_id_set = set(unqueued_raw_ids)
        queued_raw_ids = [
            raw_id for raw_id in pending_raw_ids if raw_id not in unqueued_raw_id_set
        ]
        existing_errors = dict(run.errors) if isinstance(run.errors, dict) else {}
        handoff = _handoff_value_from_errors(existing_errors)
        handoff.update(
            {
                "message": "normalize_enqueue_failed",
                "detail": str(exc),
                "pending_raw_count": len(unqueued_raw_ids),
                "pending_raw_ids": [str(raw_id) for raw_id in unqueued_raw_ids],
                "attempted_raw_count": len(pending_raw_ids),
            }
        )
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        existing_errors["handoff"] = handoff
        run.errors = existing_errors
        await _reconcile_pending_raw_ids_on_source_object(
            db,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            add_raw_ids=unqueued_raw_ids,
            remove_raw_ids=queued_raw_ids,
            updated_at=datetime.now(UTC),
        )
        raise
    except Exception as exc:
        existing_errors = dict(run.errors) if isinstance(run.errors, dict) else {}
        handoff = _handoff_value_from_errors(existing_errors)
        handoff.update(
            {
                "message": "normalize_enqueue_failed",
                "detail": str(exc),
                "pending_raw_count": len(pending_raw_ids),
                "pending_raw_ids": [str(raw_id) for raw_id in pending_raw_ids],
            }
        )
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        existing_errors["handoff"] = handoff
        run.errors = existing_errors
        await _reconcile_pending_raw_ids_on_source_object(
            db,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            add_raw_ids=pending_raw_ids,
            updated_at=datetime.now(UTC),
        )
        raise


async def acquire_sync_lock(
    db: AsyncSession, source_object_id: uuid.UUID
) -> bool:
    """Single-flight per source object (backlog E4): a transaction-
    scoped Postgres advisory lock. A second worker starting a sync for
    the same object gets ``False`` and skips instead of racing —
    overlapping backfills/retries previously interleaved checkpoint
    writes. Transaction-scoped means the lock releases automatically at
    commit/rollback; a crashed worker cannot leak it."""
    from sqlalchemy import text as sa_text

    result = await db.execute(
        sa_text(
            "SELECT pg_try_advisory_xact_lock(hashtext(:key))"
        ).bindparams(key=f"sync:{source_object_id}")
    )
    return bool(result.scalar_one())


async def run_backfill_job(
    db: AsyncSession,
    source_id: uuid.UUID,
    source_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    window_days: int = 90,
) -> dict:
    if not await acquire_sync_lock(db, source_object_id):
        logger.info(
            "sync.skipped_locked",
            source_object_id=str(source_object_id),
            mode="backfill",
        )
        return {"status": "skipped_locked"}
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
        raw_created, raw_deduped, new_raw_ids = await persist_ingestion_events(
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
            new_raw_ids=new_raw_ids,
        )
    return {"run_id": str(run.id), "status": run.status}


async def run_incremental_job(
    db: AsyncSession,
    source_id: uuid.UUID,
    source_object_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    if not await acquire_sync_lock(db, source_object_id):
        logger.info(
            "sync.skipped_locked",
            source_object_id=str(source_object_id),
            mode="incremental",
        )
        return {"status": "skipped_locked"}
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

    ck = Checkpoint(data=ck_row.checkpoint_data, captured_at=ck_row.captured_at) if ck_row else None

    if ck is None:
        # An incremental sync means "changes since the last cursor", and there
        # is no cursor until a backfill establishes one. `fetch_changes` takes
        # a non-optional Checkpoint (see connectors/base.py), so every
        # connector dereferences it — passing None crashed the run with
        # "'NoneType' object has no attribute 'data'". Approving an object for
        # sync before its first backfill is the ordinary way to hit this.
        #
        # Skipping is the honest answer rather than treating it as a first
        # full pull: that would quietly ingest the source's entire history —
        # and pay to extract it — on a schedule nobody associated with a
        # backfill.
        run.items_processed = 0
        run.status = "completed"
        run.errors = {
            "skipped": "no checkpoint yet — run a backfill for this object first"
        }
        run.completed_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "sync.incremental_skipped_no_checkpoint",
            source_object_id=str(so.id),
            source_id=str(source.id),
        )
        return {"run_id": str(run.id), "status": "skipped_no_checkpoint"}

    try:
        result = await connector.fetch_changes(so.external_id, so.object_type, ck)
        events = list(result.events or [])
        raw_created, raw_deduped, new_raw_ids = await persist_ingestion_events(
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
            new_raw_ids=new_raw_ids,
        )
    return {"run_id": str(run.id), "status": run.status}
