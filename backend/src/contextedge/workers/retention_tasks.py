"""Scheduled retention: archive daily, purge weekly.

``apply_retention_policy`` and ``purge_archived_evidence`` were
production-ready services with no Beat wiring, so tenant retention
defaults had no effect until an operator invoked them by hand. These
tasks close that gap with the documented typical cadence (archive daily,
purge weekly). The purge mode defaults to the conservative
``soft_purge`` via ``settings.retention_purge_mode`` — operators who
want row removal set ``RETENTION_PURGE_MODE=hard_delete``.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.config import settings
from contextedge.models.tenant import Tenant
from contextedge.services.retention_service import (
    apply_retention_policy,
    purge_archived_evidence,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _tenant_ids(db, tenant_id: str) -> list[uuid.UUID]:
    if tenant_id == "all":
        return [row[0] for row in (await db.execute(select(Tenant.id))).all()]
    return [uuid.UUID(tenant_id)]


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.apply_retention_archive",
)
def apply_retention_archive(self, tenant_id: str = "all"):
    """Archive evidence past its memory-class retention window."""

    async def work(db):
        totals = {"tenants": 0, "archived": 0}
        for tid in await _tenant_ids(db, tenant_id):
            try:
                archived = await apply_retention_policy(db, tid)
                await db.commit()
                totals["tenants"] += 1
                totals["archived"] += archived
            except Exception as exc:
                await db.rollback()
                logger.exception(
                    "retention.archive_tenant_failed", tenant_id=str(tid), error=str(exc)
                )
        return totals

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("retention.archive_failed", error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.purge_archived",
)
def purge_archived(self, tenant_id: str = "all", limit: int = 1000):
    """Purge (default: soft-scrub) evidence archived past the grace window."""
    mode = settings.retention_purge_mode

    async def work(db):
        totals = {"tenants": 0, "processed": 0, "mode": mode}
        for tid in await _tenant_ids(db, tenant_id):
            try:
                result = await purge_archived_evidence(
                    db, tenant_id=tid, mode=mode, limit=limit
                )
                await db.commit()
                totals["tenants"] += 1
                totals["processed"] += result["processed_count"]
            except Exception as exc:
                await db.rollback()
                logger.exception(
                    "retention.purge_tenant_failed", tenant_id=str(tid), error=str(exc)
                )
        return totals

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("retention.purge_failed", error=str(exc))
        raise self.retry(exc=exc) from exc
