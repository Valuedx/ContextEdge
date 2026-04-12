import uuid

from contextedge.services.sync_worker_service import (
    run_backfill_job,
    run_discovery_job,
    run_incremental_job,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def discover_source(self, source_id: str, tenant_id: str):
    sid = uuid.UUID(source_id)
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await run_discovery_job(db, sid, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(bind=True, max_retries=3, default_retry_delay=120)
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


@celery_app.task(bind=True, max_retries=5, default_retry_delay=30)
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
