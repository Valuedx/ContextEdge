"""Celery task wrapping compute_evidence_baseline.

Runs after normalization / artifact extraction so every new `EvidenceItem`
gets a baseline_ref populated. Cheap (single indexed lookup scoped by
tenant + evidence_type + source_object_id + window) so running it inline
with the correlation fan-out is fine.
"""

from __future__ import annotations

import uuid

import structlog

from contextedge.services.evidence_baseline_service import compute_evidence_baseline
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="extraction.compute_evidence_baseline",
)
def compute_evidence_baseline_task(self, evidence_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)
    eid = uuid.UUID(evidence_id)

    async def work(db):
        return await compute_evidence_baseline(
            db, tenant_id=tid, evidence_id=eid,
        )

    try:
        result = run_async(work)
        if result is None:
            logger.info(
                "evidence.baseline_skipped",
                evidence_id=evidence_id,
                reason="no_source_object_id_or_missing",
            )
        return {"status": "ok", "baseline_ref": result}
    except Exception as exc:
        logger.exception(
            "evidence.baseline_failed",
            evidence_id=evidence_id,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
