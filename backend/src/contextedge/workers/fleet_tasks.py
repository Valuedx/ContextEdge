"""Fleet-group detection task (backlog B6).

Runs the deterministic detector per tenant (or all). Ad-hoc invocable
and safe on repeat: suggestions are idempotent per change reference and
rejection is permanent.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.models.tenant import Tenant
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _detect(db, tenant_id: str) -> dict:
    from contextedge.services.fleet_group_service import detect_fleet_groups

    if tenant_id == "all":
        tids = (await db.execute(select(Tenant.id))).scalars().all()
    else:
        tids = [uuid.UUID(tenant_id)]
    totals = {"tenants": len(tids), "groups_suggested": 0, "groups_updated": 0}
    for tid in tids:
        result = await detect_fleet_groups(db, tid)
        totals["groups_suggested"] += result["groups_suggested"]
        totals["groups_updated"] += result["groups_updated"]
    return totals


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.detect_fleet_groups",
)
def detect_fleet_groups_task(self, tenant_id: str = "all"):
    try:
        return run_async(lambda db: _detect(db, tenant_id))
    except Exception as exc:
        logger.warning(
            "fleet_group.detection_failed", tenant_id=tenant_id, error=str(exc)
        )
        raise self.retry(exc=exc) from exc
