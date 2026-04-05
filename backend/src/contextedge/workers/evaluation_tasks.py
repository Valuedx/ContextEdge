import uuid

import structlog

from contextedge.services.drift_service import check_playbook_drift
from contextedge.services.evaluation_service import execute_evaluation_run
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def run_evaluation(self, evaluation_run_id: str, tenant_id: str):
    """Run evaluation replay against historical dataset."""

    async def work(db):
        return await execute_evaluation_run(
            db,
            uuid.UUID(evaluation_run_id),
            uuid.UUID(tenant_id),
        )

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("evaluation.run_failed", run_id=evaluation_run_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300)
def detect_drift(self, tenant_id: str):
    """Check approved playbooks for drift, staleness, and contradictions.

    Celery Beat passes the literal string ``all`` to scan every tenant.
    """
    from sqlalchemy import select

    from contextedge.models.tenant import Tenant

    async def work(db):
        if tenant_id == "all":
            r = await db.execute(select(Tenant.id))
            tids = [row[0] for row in r.all()]
            merged: list[dict] = []
            total_expired = 0
            for tid in tids:
                pack = await check_playbook_drift(db, tid)
                merged.extend(pack["alerts"])
                total_expired += int(pack["expired_transition_count"])
            return {
                "tenants": len(tids),
                "alerts": merged,
                "alert_count": len(merged),
                "expired_transition_count": total_expired,
            }
        tid = uuid.UUID(tenant_id)
        pack = await check_playbook_drift(db, tid)
        return {
            "tenants": 1,
            "alerts": pack["alerts"],
            "alert_count": pack["alert_count"],
            "expired_transition_count": pack["expired_transition_count"],
        }

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("drift.check_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc
