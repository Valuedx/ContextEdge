"""Celery tasks for attachment artifact extraction."""

import uuid

from contextedge.services.artifact_extraction_service import process_attachment_artifact
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app
from contextedge.workers.correlation_tasks import correlate_evidence


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="artifact.extract_attachment",
)
def extract_attachment_artifact(self, artifact_id: str, tenant_id: str):
    artifact_uuid = uuid.UUID(artifact_id)
    tenant_uuid = uuid.UUID(tenant_id)

    async def work(db):
        return await process_attachment_artifact(
            db,
            artifact_id=artifact_uuid,
            tenant_id=tenant_uuid,
        )

    try:
        result = run_async(work)
        if result and result.get("evidence_id") and result.get("follow_up_ready"):
            from contextedge.workers.extraction_tasks import classify_relevance_task

            classify_relevance_task.delay(result["evidence_id"], tenant_id)
            correlate_evidence.delay(result["evidence_id"], tenant_id)
        return result
    except Exception as exc:
        raise self.retry(exc=exc) from exc
