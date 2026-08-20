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
        from sqlalchemy import select

        from contextedge.models.evidence import EvidenceItem

        result = await correlate_evidence_item(db, tid, eid)

        domain_id = None
        if result and result.get("status") == "ok" and result.get("correlations_created", 0) > 0:
            # Fetch domain_id from evidence if not present in result
            res = await db.execute(select(EvidenceItem.domain_id).where(EvidenceItem.id == eid))
            domain_id = res.scalar_one_or_none()

        return result, domain_id

    try:
        result, domain_id = run_async(work)
        if result and result.get("status") == "ok" and result.get("correlations_created", 0) > 0:
            from contextedge.workers.extraction_tasks import (
                RECONSTRUCT_DEBOUNCE_SECONDS,
                reconstruct_episode_task,
            )

            # Debounced: fires after the window; _reconstruct re-checks
            # settlement so only the last task of a quiet period spends
            # the LLM call.
            reconstruct_episode_task.apply_async(
                args=[evidence_id, tenant_id],
                kwargs={"domain_id": str(domain_id) if domain_id else None},
                countdown=RECONSTRUCT_DEBOUNCE_SECONDS,
            )
            logger.info(
                "correlation.episode_reconstruction_enqueued",
                evidence_id=evidence_id,
                correlations_created=result["correlations_created"],
            )
        # Stale-CI topology warming, dispatched only after run_async has
        # committed so the warm task sees the entity row.
        snow_refs = (result or {}).get("servicenow_references") or {}
        if snow_refs.get("warm_candidates"):
            from contextedge.workers.cmdb_tasks import warm_cmdb_topology

            for candidate in snow_refs["warm_candidates"]:
                warm_cmdb_topology.delay(
                    tenant_id, candidate["source_id"], candidate["sys_id"]
                )
        return result
    except Exception as exc:
        logger.exception("correlation.failed", evidence_id=evidence_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="correlation.correlate_situations",
)
def correlate_situations_task(self, tenant_id: str, lookback_days: int = 30):
    """Assemble recent incident evidence into operational situations (H3).

    Tenant-wide rather than per-evidence: a situation is a claim about a SET
    of signals, and a per-evidence trigger would have to re-derive the set on
    every arrival anyway. Idempotent, so a schedule can run it as often as it
    likes -- a group that has not changed writes nothing.
    """
    from datetime import timedelta

    from contextedge.services.situation_correlation_service import (
        correlate_situations,
    )

    tid = uuid.UUID(tenant_id)

    async def work(db):
        result = await correlate_situations(
            db, tid, lookback=timedelta(days=lookback_days)
        )
        await db.commit()
        return result.as_dict()

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
