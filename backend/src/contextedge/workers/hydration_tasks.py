import uuid
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.connectors.base import IngestionEvent
from contextedge.connectors.registry import get_connector
from contextedge.models.evidence import Thread
from contextedge.models.source import Source, SourceCredential
from contextedge.services.ingestion_persistence import persist_ingestion_events
from contextedge.services.source_service import decrypt_credentials
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


def _parse_msg_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def _hydrate(db: AsyncSession, thread_id: str, source_id: str, tenant_id: uuid.UUID) -> dict:
    src = await db.get(Source, uuid.UUID(source_id))
    if not src or src.tenant_id != tenant_id:
        return {"error": "source_not_found"}

    cred = (
        await db.execute(
            select(SourceCredential).where(
                SourceCredential.source_id == src.id,
                SourceCredential.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not cred:
        return {"error": "no_credentials"}

    decrypted = await decrypt_credentials(cred.encrypted_credentials)
    connector = get_connector(src.source_type, src.config, decrypted)
    hydrated = await connector.hydrate_thread(thread_id)

    thr = (
        await db.execute(
            select(Thread).where(
                Thread.external_thread_id == thread_id,
                Thread.source_id == src.id,
                Thread.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()

    first_ts: datetime | None = None
    last_ts: datetime | None = None
    ingestion_events: list[IngestionEvent] = []

    for msg in hydrated.messages:
        msg_ts = _parse_msg_timestamp(msg.get("timestamp"))
        if msg_ts:
            if first_ts is None or msg_ts < first_ts:
                first_ts = msg_ts
            if last_ts is None or msg_ts > last_ts:
                last_ts = msg_ts

        ingestion_events.append(
            IngestionEvent(
                external_id=f"{thread_id}:msg:{msg.get('id', '')}",
                source_type=src.source_type,
                object_type="hydrated_message",
                content={
                    "body": msg.get("body", ""),
                    "from": msg.get("from", ""),
                    "type": msg.get("type", "message"),
                },
                thread_id=thread_id,
                timestamp=msg_ts,
                metadata={"hydrated_from_thread": thread_id},
            )
        )

    if thr:
        thr.hydration_status = "complete"
        thr.message_count = len(hydrated.messages)
        thr.participant_count = hydrated.participant_count
        if first_ts:
            thr.first_message_at = first_ts
        if last_ts:
            thr.last_message_at = last_ts
        if not thr.title and hydrated.messages:
            first_msg = hydrated.messages[0]
            title = first_msg.get("subject") or first_msg.get("body", "")[:200]
            if title:
                thr.title = title[:500]

    if ingestion_events:
        source_object_id = thr.source_object_id if thr else None
        created, skipped, new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=src.id,
            source_object_id=source_object_id,
            events=ingestion_events,
        )
        await db.flush()
    else:
        new_raw_ids = []

    await db.flush()
    return {
        "thread_ref": thread_id,
        "messages": len(hydrated.messages),
        "raw_objects_created": len(new_raw_ids),
        "_new_raw_ids": [str(rid) for rid in new_raw_ids],
    }


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def hydrate_thread(self, thread_id: str, source_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _hydrate(db, thread_id, source_id, tid)

    try:
        res = run_async(work)
        if res and res.get("raw_objects_created"):
            from contextedge.workers.extraction_tasks import normalize_evidence
            for raw_id in res.get("_new_raw_ids", []):
                normalize_evidence.delay(str(raw_id), tenant_id)
        return res
    except Exception as exc:
        raise self.retry(exc=exc) from exc
