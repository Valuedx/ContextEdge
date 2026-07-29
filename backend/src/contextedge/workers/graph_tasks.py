"""Scheduled graph-relationship reconciliation.

Migration ``0031`` backfilled graph edges for claims, fix patterns, case
outcomes, error signatures, and execution/approval paths, but rows created
*after* the migration only get edges for the relationship types the runtime
services write inline (``executes`` / ``has_execution`` /
``requires_approval`` and the decision links). Everything else — claims,
fix patterns, case outcomes — would silently stop appearing in agent
projections without a periodic reconcile. This module is that schedule:
``GraphRelationshipMaterializer.reconcile_tenant`` is idempotent
(``ensure_edge`` is ON CONFLICT-safe), so re-running is cheap and safe.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.graph.agent.materializer import GraphRelationshipMaterializer
from contextedge.models.tenant import Tenant
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.reconcile_graph_relationships",
)
def reconcile_graph_relationships(self, tenant_id: str, batch_size: int = 500):
    """Materialize relational rows into graph edges for a tenant (or all
    tenants when ``tenant_id == "all"``). Scheduled via Beat; also safe to
    invoke ad-hoc after bulk imports."""

    async def work(db):
        materializer = GraphRelationshipMaterializer(db)
        if tenant_id == "all":
            tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
            aggregate = {"tenants": len(tids), "relationships_seen": 0}
            for tid in tids:
                try:
                    result = await materializer.reconcile_tenant(tid, batch_size=batch_size)
                    aggregate["relationships_seen"] += result.relationships_seen
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.exception(
                        "graph.reconcile_tenant_failed",
                        tenant_id=str(tid),
                        error=str(exc),
                    )
            return aggregate
        tid = uuid.UUID(tenant_id)
        result = await materializer.reconcile_tenant(tid, batch_size=batch_size)
        logger.info(
            "graph.reconcile_completed",
            tenant_id=tenant_id,
            relationships_seen=result.relationships_seen,
        )
        return {"relationships_seen": result.relationships_seen}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("graph.reconcile_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc
