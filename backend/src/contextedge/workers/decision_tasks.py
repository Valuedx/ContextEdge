"""Celery tasks for decision pattern mining and confidence calibration.

Both tasks accept the literal string ``"all"`` as ``tenant_id`` to fan
out across every tenant in the database — matches the pattern used by
``evaluation.detect_drift`` and ``evaluation.scan_contradictions_task``
so Celery Beat can schedule them with a single entry instead of one
per tenant. Explicit task names route them to the ``evaluation`` queue.
"""

import uuid

import structlog
from sqlalchemy import select, func as sa_func

from contextedge.models.decision import Decision, DecisionOutcome
from contextedge.models.tenant import Tenant
from contextedge.services.event_log_service import append_operational_event
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _list_tenant_ids(db) -> list[uuid.UUID]:
    result = await db.execute(select(Tenant.id))
    return [row[0] for row in result.all()]


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    name="evaluation.mine_decision_patterns",
)
def mine_decision_patterns(self, tenant_id: str):
    """Scan completed decisions with outcomes to surface recurring patterns.

    Groups decisions by (decision_type, execution_result) and identifies
    contexts where certain actions consistently fail or succeed,
    e.g. "restart is ineffective for network share failures".
    """

    async def _mine_for_tenant(db, tid: uuid.UUID) -> list[dict]:
        stmt = (
            select(
                Decision.decision_type,
                DecisionOutcome.execution_result,
                sa_func.count().label("cnt"),
            )
            .join(DecisionOutcome, DecisionOutcome.decision_id == Decision.id)
            .where(Decision.tenant_id == tid, Decision.status == "completed")
            .group_by(Decision.decision_type, DecisionOutcome.execution_result)
            .having(sa_func.count() >= 3)
        )
        result = await db.execute(stmt)
        rows = result.all()

        insights = []
        for decision_type, execution_result, count in rows:
            failure_rate = None
            if execution_result == "failure":
                success_stmt = (
                    select(sa_func.count())
                    .select_from(DecisionOutcome)
                    .join(Decision, DecisionOutcome.decision_id == Decision.id)
                    .where(
                        Decision.tenant_id == tid,
                        Decision.decision_type == decision_type,
                        DecisionOutcome.execution_result == "success",
                    )
                )
                success_count = (await db.execute(success_stmt)).scalar() or 0
                total = count + success_count
                failure_rate = round(count / total, 3) if total > 0 else None

            insights.append({
                "decision_type": decision_type,
                "execution_result": execution_result,
                "count": count,
                "failure_rate": failure_rate,
            })

        if insights:
            await append_operational_event(
                db,
                tenant_id=tid,
                entity_type="decision_analytics",
                event_type="decision.patterns_mined",
                payload={
                    "insight_count": len(insights),
                    "insights": insights[:20],
                },
            )
        return insights

    async def work(db):
        if tenant_id == "all":
            tids = await _list_tenant_ids(db)
            total = 0
            for tid in tids:
                try:
                    insights = await _mine_for_tenant(db, tid)
                    total += len(insights)
                except Exception as exc:
                    # Keep going — one broken tenant shouldn't kill the
                    # beat for the rest. The per-tenant failure is logged
                    # so it's still investigable.
                    logger.exception(
                        "decision.pattern_mining_tenant_failed",
                        tenant_id=str(tid), error=str(exc),
                    )
            return {"tenants": len(tids), "insight_count": total}

        tid = uuid.UUID(tenant_id)
        insights = await _mine_for_tenant(db, tid)
        return {"tenant_id": tenant_id, "insights": insights}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("decision.pattern_mining_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    name="evaluation.calibrate_decision_confidence",
)
def calibrate_decision_confidence(self, tenant_id: str):
    """Compare decision confidence predictions to actual outcomes.

    Produces calibration stats: how well does the confidence score
    predict success? Buckets decisions by confidence range and
    computes observed success rate per bucket. Emits
    ``decision.confidence_calibrated`` operational events that the
    admin dashboard can chart over time.
    """

    async def _calibrate_for_tenant(db, tid: uuid.UUID) -> list[dict]:
        stmt = (
            select(Decision.confidence, DecisionOutcome.execution_result)
            .join(DecisionOutcome, DecisionOutcome.decision_id == Decision.id)
            .where(
                Decision.tenant_id == tid,
                Decision.confidence.is_not(None),
            )
        )
        result = await db.execute(stmt)
        rows = result.all()

        buckets: dict[str, dict] = {}
        for confidence, execution_result in rows:
            if confidence is None:
                continue
            bucket_key = f"{int(confidence * 10) / 10:.1f}"
            if bucket_key not in buckets:
                buckets[bucket_key] = {"total": 0, "success": 0, "failure": 0}
            buckets[bucket_key]["total"] += 1
            if execution_result == "success":
                buckets[bucket_key]["success"] += 1
            elif execution_result == "failure":
                buckets[bucket_key]["failure"] += 1

        calibration = []
        for bucket_key in sorted(buckets.keys()):
            b = buckets[bucket_key]
            observed_success_rate = round(b["success"] / b["total"], 3) if b["total"] > 0 else None
            calibration.append({
                "predicted_confidence": float(bucket_key),
                "total": b["total"],
                "success": b["success"],
                "failure": b["failure"],
                "observed_success_rate": observed_success_rate,
            })

        if calibration:
            await append_operational_event(
                db,
                tenant_id=tid,
                entity_type="decision_analytics",
                event_type="decision.confidence_calibrated",
                payload={
                    "bucket_count": len(calibration),
                    "calibration": calibration,
                },
            )
        return calibration

    async def work(db):
        if tenant_id == "all":
            tids = await _list_tenant_ids(db)
            total_buckets = 0
            for tid in tids:
                try:
                    cal = await _calibrate_for_tenant(db, tid)
                    total_buckets += len(cal)
                except Exception as exc:
                    # One bad tenant shouldn't block the rest of the beat.
                    logger.exception(
                        "decision.calibration_tenant_failed",
                        tenant_id=str(tid), error=str(exc),
                    )
            return {"tenants": len(tids), "bucket_count": total_buckets}

        tid = uuid.UUID(tenant_id)
        calibration = await _calibrate_for_tenant(db, tid)
        return {"tenant_id": tenant_id, "calibration": calibration}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("decision.calibration_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc
