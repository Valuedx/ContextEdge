"""CMDB topology cache warming.

Dispatched by the correlate task when a ticket references a CI whose
cached topology is stale (TTL in ``cmdb_topology_service``). Keeps the
HTTP round-trips OFF the correlation path: correlation only surfaces the
candidate; this task does the two ServiceNow calls and the write-through.
Re-checks freshness before fetching so a burst of tickets on one CI
warms it once in the common sequential case (concurrent workers may
double-fetch; the write-through is idempotent so that costs API calls,
never correctness).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.models.entity import Entity
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _warm(db, tenant_id: uuid.UUID, source_id: uuid.UUID, sys_id: str) -> dict:
    from contextedge.services.cmdb_topology_service import (
        cache_neighborhood,
        entity_is_stale,
        fetch_ci_neighborhood,
        load_servicenow_connector,
    )

    entity = (
        await db.execute(
            select(Entity)
            .where(
                Entity.tenant_id == tenant_id,
                Entity.external_system == "servicenow",
                Entity.external_id == sys_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if entity is not None and not entity_is_stale(entity):
        return {"status": "fresh"}

    connector = await load_servicenow_connector(db, tenant_id, source_id)
    neighborhood = await fetch_ci_neighborhood(connector, sys_id)
    counts = await cache_neighborhood(db, tenant_id, neighborhood)
    logger.info(
        "cmdb_topology.warmed",
        tenant_id=str(tenant_id),
        sys_id=sys_id,
        **counts,
    )
    return {"status": "warmed", **counts}


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.warm_cmdb_topology",
)
def warm_cmdb_topology(self, tenant_id: str, source_id: str, sys_id: str):
    tid = uuid.UUID(tenant_id)
    sid = uuid.UUID(source_id)

    try:
        return run_async(lambda db: _warm(db, tid, sid, sys_id))
    except Exception as exc:
        # Warming is opportunistic — one retry, then give up quietly; the
        # next ticket touching the CI (or the MAF tool) will try again.
        logger.warning(
            "cmdb_topology.warm_failed",
            tenant_id=tenant_id,
            sys_id=sys_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
