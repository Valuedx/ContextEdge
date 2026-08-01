"""Post-action verification sweep.

Every beat tick, re-check recently completed execution runs against
operational reality (execution_verification_service). The queue is the
partial index from migration 0036: completed runs with
``verification_status IS NULL``. Runs whose per-playbook recheck delay
has not elapsed return ``not_due`` and stay queued; everything else gets
a persisted verdict, so a run is swept at most a handful of times.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from contextedge.models.execution import ExecutionRun
from contextedge.models.tenant import Tenant
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()

SWEEP_LIMIT_PER_TENANT = 50


async def _sweep(db, tenant_id: str, limit: int) -> dict:
    from contextedge.services.execution_verification_service import (
        MIN_RECHECK_FLOOR_SEC,
        VERIFIABLE_OUTCOMES,
        verify_execution_run,
    )

    if tenant_id == "all":
        tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
    else:
        tids = [uuid.UUID(tenant_id)]

    now = datetime.now(UTC)
    floor_cutoff = now - timedelta(seconds=MIN_RECHECK_FLOOR_SEC)
    totals = {"tenants": len(tids), "verified": 0, "failed": 0, "unverifiable": 0, "not_due": 0, "skipped": 0}
    for tid in tids:
        runs = (
            (
                await db.execute(
                    select(ExecutionRun)
                    .where(
                        ExecutionRun.tenant_id == tid,
                        ExecutionRun.status == "completed",
                        ExecutionRun.outcome.in_(VERIFIABLE_OUTCOMES),
                        ExecutionRun.verification_status.is_(None),
                        ExecutionRun.completed_at.is_not(None),
                        ExecutionRun.completed_at <= floor_cutoff,
                    )
                    .order_by(ExecutionRun.completed_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for run in runs:
            try:
                result = await verify_execution_run(db, tid, run, now=now)
                totals[result["status"]] = totals.get(result["status"], 0) + 1
            except Exception as exc:
                # One broken run must not stall the whole queue; it stays
                # NULL and is retried next sweep.
                logger.warning(
                    "execution.verification_failed_to_run",
                    tenant_id=str(tid),
                    execution_run_id=str(run.id),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        await db.commit()
    logger.info("execution.verification_sweep_done", **totals)
    return totals


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.verify_executions",
)
def verify_executions(self, tenant_id: str = "all", limit: int = SWEEP_LIMIT_PER_TENANT):
    try:
        return run_async(lambda db: _sweep(db, tenant_id, limit))
    except Exception as exc:
        logger.exception(
            "execution.verification_sweep_failed", tenant_id=tenant_id, error=str(exc)
        )
        raise self.retry(exc=exc) from exc
