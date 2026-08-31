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
        return await run_backfill_job(
            db, sid, oid, tid, window_days=window_days,
            celery_task_id=getattr(self.request, "id", None),
        )

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


@celery_app.task(
    name="sync.refresh_official_knowledge",
)
def refresh_official_knowledge(tenant_id: str = "all"):
    """Weekly: new/changed Zoho KB articles plus official version catalog."""
    from contextedge.services.official_kb_catalog import run_official_knowledge_refresh
    from contextedge.services.sync_ingestion_queue import queue_normalize_raw_objects
    from contextedge.workers.chunk_tasks import chunk_evidence_task

    async def work(db):
        return await run_official_knowledge_refresh(
            db,
            tenant_id,
            enqueue_article_sync=lambda source_id, object_id, tid: run_incremental_sync.delay(
                source_id, object_id, tid
            ),
        )

    summary = run_async(work)
    for tid, raw_ids in summary.get("normalize") or []:
        queue_normalize_raw_objects(
            [uuid.UUID(item) for item in raw_ids],
            uuid.UUID(tid),
        )
    for tid, evidence_ids in summary.get("rechunk") or []:
        for evidence_id in evidence_ids:
            chunk_evidence_task.delay(evidence_id, tid)
    return {
        "sources": summary.get("sources"),
        "article_syncs_queued": summary.get("article_syncs_queued"),
        "latest_official_release": summary.get("latest_official_release"),
        "catalog": summary.get("catalog"),
        "errors": summary.get("errors"),
    }
