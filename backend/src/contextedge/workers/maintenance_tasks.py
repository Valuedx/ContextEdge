"""One-shot / operator-dispatched maintenance sweeps (roadmap A3).

``maintenance.reclassify_stale_evidence`` re-runs relevance
classification over evidence that predates the current prompt +
salient-slicing pipeline. Staleness marker: ``body_summary IS NULL`` —
the v2 relevance prompt writes a summary on every relevant item, so a
row with text but no summary was last classified by v1 (or by v2 as
not_relevant, which re-verifies cheaply).

Each row goes through the existing ``extraction.classify_relevance``
task — one small dispatch loop, no second classification code path — so
re-classification lands in /admin/cost, respects the tenant budget
gate, and (via that task's fan-out) chunks/embeds/correlates items a
stale head-truncated verdict skipped.

Dispatch from a shell::

    celery_app.send_task("maintenance.reclassify_stale_evidence",
                         args=[str(tenant_id)])
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.models.evidence import EvidenceItem
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app
from contextedge.workers.extraction_tasks import classify_relevance_task

logger = structlog.get_logger()

# One sweep dispatch is bounded; re-run the task for the next batch.
# 500 covers the current corpus in one pass while keeping a runaway
# dispatch loop impossible.
MAX_BATCH = 500


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    name="maintenance.infer_ci_relatedness",
)
def infer_ci_relatedness_task(self, tenant_id: str):
    """Blueprint §1.5 dependency auto-construction: co-occurring CI
    pairs (>=3 shared canonical cases) gain symmetric co_fails_with
    edges. Idempotent; re-runs refresh confidence in place."""
    tid = uuid.UUID(tenant_id)

    async def work(db):
        from contextedge.services.dependency_inference_service import (
            infer_co_failure_edges,
        )

        return await infer_co_failure_edges(db, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    name="maintenance.reclassify_stale_evidence",
)
def reclassify_stale_evidence_task(
    self, tenant_id: str, limit: int = MAX_BATCH
):
    tid = uuid.UUID(tenant_id)
    limit = max(1, min(int(limit), MAX_BATCH))

    async def work(db):
        rows = (
            (
                await db.execute(
                    select(EvidenceItem.id)
                    .where(
                        EvidenceItem.tenant_id == tid,
                        EvidenceItem.body_summary.is_(None),
                        EvidenceItem.body_text.isnot(None),
                        # not_relevant rows keep a NULL summary BY DESIGN
                        # (the v2 prompt returns null for them), so they
                        # are the sweep's correct end state, not stale
                        # work. Without this exclusion every sweep pass
                        # re-classified the same ~290 rows forever —
                        # found live when "remaining" plateaued while
                        # classifications kept succeeding.
                        EvidenceItem.relevance_state.is_distinct_from(
                            "not_relevant"
                        ),
                    )
                    .order_by(EvidenceItem.ingested_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [str(r) for r in rows]

    try:
        evidence_ids = run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc

    for eid in evidence_ids:
        classify_relevance_task.delay(eid, tenant_id)
    logger.info(
        "maintenance.reclassify_dispatched",
        tenant_id=tenant_id,
        count=len(evidence_ids),
    )
    return {"dispatched": len(evidence_ids)}
