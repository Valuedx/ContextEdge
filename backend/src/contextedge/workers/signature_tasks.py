"""Issue-signature extraction task (backlog B3).

Dispatched by the episode-approval endpoint. One LLM call per approved
episode; idempotent (an already-linked episode is a no-op), so a
double-fired approval or a retry costs nothing.
"""

from __future__ import annotations

import uuid

import structlog

from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="evaluation.extract_issue_signature",
)
def extract_issue_signature_task(self, episode_id: str, tenant_id: str):
    from contextedge.services.issue_signature_service import extract_issue_signature

    eid = uuid.UUID(episode_id)
    tid = uuid.UUID(tenant_id)
    try:
        return run_async(lambda db: extract_issue_signature(db, tid, eid))
    except Exception as exc:
        logger.warning(
            "issue_signature.extraction_failed",
            tenant_id=tenant_id,
            episode_id=episode_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
