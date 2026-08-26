import uuid

import structlog
from sqlalchemy import select

from contextedge.models.tenant import Tenant
from contextedge.services.copilot_audit_service import purge_expired_message_bodies
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="evaluation.purge_copilot_message_bodies")
def purge_copilot_message_bodies(tenant_id: str = "all") -> dict:
    """Strip customer message bodies older than the retention window."""

    async def work(db):
        if tenant_id == "all":
            ids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
        else:
            ids = [uuid.UUID(tenant_id)]
        purged = 0
        for tid in ids:
            purged += await purge_expired_message_bodies(db, tid)
        return {"tenants": len(ids), "purged_messages": purged}

    try:
        return run_async(work)
    except Exception:
        logger.exception("copilot.retention_failed", tenant_id=tenant_id)
        raise
