import uuid

from sqlalchemy import select

from contextedge.services.sync_worker_service import (
    run_backfill_job,
    run_incremental_job,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app


@celery_app.task(
    name="sync.trigger_scheduled_syncs",
)
def trigger_scheduled_syncs():
    """Find all approved objects and trigger incremental sync for them."""
    async def work(db):
        from contextedge.models.source import SourceObject
        q = select(SourceObject).where(SourceObject.approved_for_sync.is_(True))
        result = await db.execute(q)
        objects = result.scalars().all()

        for obj in objects:
            run_incremental_sync.delay(
                str(obj.source_id),
                str(obj.id),
                str(obj.tenant_id)
            )
        return len(objects)

    return run_async(work)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="sync.run_backfill",
)
def run_backfill(
    self,
    source_id: str,
    source_object_id: str,
    tenant_id: str,
    window_days: int = 90,
):
    sid = uuid.UUID(source_id)
    oid = uuid.UUID(source_object_id)
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await run_backfill_job(db, sid, oid, tid, window_days=window_days)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    name="sync.run_incremental_sync",
)
def run_incremental_sync(self, source_id: str, source_object_id: str, tenant_id: str):
    sid = uuid.UUID(source_id)
    oid = uuid.UUID(source_object_id)
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await run_incremental_job(db, sid, oid, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
