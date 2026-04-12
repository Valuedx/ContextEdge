"""Persist connector events to raw storage. Caller queues normalize after commit."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import RawEvidenceObject
from contextedge.services.object_store import upload_raw

OFFLOAD_THRESHOLD_BYTES = 32_768


async def persist_ingestion_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    source_object_id: uuid.UUID | None,
    events: Sequence[object],
) -> tuple[int, int, list[uuid.UUID]]:
    """Insert ``RawEvidenceObject`` rows for connector events.

    Deduplicates on (tenant_id, source_id, external_id, content_hash).

    Callers must ``commit`` the session, then enqueue ``normalize_evidence`` for each id in
    ``new_raw_ids`` so workers see committed rows.

    Returns:
        ``(raw_rows_created, events_skipped_duplicate, new_raw_ids)``.
    """
    created = 0
    skipped = 0
    new_ids: list[uuid.UUID] = []

    for ev in events:
        payload = {
            **(getattr(ev, "content", None) or {}),
            "_connector_source_type": getattr(ev, "source_type", "") or "",
            "_connector_object_type": getattr(ev, "object_type", "") or "",
            "_connector_metadata": getattr(ev, "metadata", None) or {},
        }
        if ev.thread_id:
            payload["_thread_id"] = ev.thread_id
        if ev.timestamp:
            payload["_source_timestamp"] = ev.timestamp.isoformat()

        canonical = json.dumps(
            {"external_id": ev.external_id, "body": payload},
            sort_keys=True,
            default=str,
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        dup = (
            await db.execute(
                select(RawEvidenceObject.id).where(
                    RawEvidenceObject.tenant_id == tenant_id,
                    RawEvidenceObject.source_id == source_id,
                    RawEvidenceObject.external_id == ev.external_id,
                    RawEvidenceObject.content_hash == content_hash,
                )
            )
        ).scalar_one_or_none()
        if dup:
            skipped += 1
            continue

        raw = RawEvidenceObject(
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=source_object_id,
            external_id=ev.external_id[:500],
            raw_payload=payload,
            content_hash=content_hash,
        )
        db.add(raw)
        await db.flush()
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        if len(payload_bytes) > OFFLOAD_THRESHOLD_BYTES:
            raw.object_storage_key = upload_raw(str(tenant_id), str(raw.id), payload_bytes)
            raw.raw_payload = {"_offloaded": True, "size_bytes": len(payload_bytes)}
        created += 1
        new_ids.append(raw.id)

    return created, skipped, new_ids
