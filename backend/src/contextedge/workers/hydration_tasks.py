import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import Thread
from contextedge.models.source import Source, SourceObject
from contextedge.services.source_service import decrypt_credentials
from contextedge.models.source import SourceCredential
from contextedge.connectors.registry import get_connector
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app


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
    if thr:
        thr.hydration_status = "complete"
        thr.message_count = len(hydrated.messages)
        thr.participant_count = hydrated.participant_count
    await db.flush()
    return {"thread_ref": thread_id, "messages": len(hydrated.messages)}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def hydrate_thread(self, thread_id: str, source_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _hydrate(db, thread_id, source_id, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
