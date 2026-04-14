import uuid

import structlog

from contextedge.services.correlation_service import correlate_evidence_item
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="extraction.correlate_evidence",
)
def correlate_evidence(self, evidence_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)
    eid = uuid.UUID(evidence_id)

    async def work(db):
        return await correlate_evidence_item(db, tid, eid)

    try:
        result = run_async(work)
        if result and result.get("status") == "ok" and result.get("correlations_created", 0) > 0:
            from contextedge.workers.extraction_tasks import reconstruct_episode_task

            cluster_ids = [evidence_id]
            canonical_case_id = result.get("canonical_case_id")
            if canonical_case_id:
                cluster_ids.append(canonical_case_id)
            reconstruct_episode_task.delay(evidence_id, tenant_id)
            logger.info(
                "correlation.episode_reconstruction_enqueued",
                evidence_id=evidence_id,
                correlations_created=result["correlations_created"],
            )
        return result
    except Exception as exc:
        logger.exception("correlation.failed", evidence_id=evidence_id, error=str(exc))
        raise self.retry(exc=exc) from exc
