"""Semantic correlation suggestion generation.

Dispatched by the chunk-embedding task once an evidence item's chunks
have embeddings (suggestions are ANN over stored chunk vectors, so
running earlier would see nothing). Generation is opportunistic — a
failure is logged and retried once; the next embedding pass for related
evidence gives another chance.
"""

from __future__ import annotations

import uuid

import structlog

from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.generate_correlation_suggestions",
)
def generate_correlation_suggestions(self, evidence_id: str, tenant_id: str):
    from contextedge.services.correlation_suggestion_service import (
        suggest_semantic_correlations,
    )

    eid = uuid.UUID(evidence_id)
    tid = uuid.UUID(tenant_id)
    try:
        return run_async(lambda db: suggest_semantic_correlations(db, tid, eid))
    except Exception as exc:
        logger.warning(
            "correlation_suggestions.generation_failed",
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
