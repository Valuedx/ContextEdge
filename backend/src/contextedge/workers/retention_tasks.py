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
from contextedge.models.policy import TenantPolicy
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


async def _tenant_retention_days(db, tid: uuid.UUID) -> int:
    """Resolve the tenant's base retention window: the most recent active
    retention policy's ``config.retention_days``, else the settings
    default."""
    row = (
        await db.execute(
            select(TenantPolicy.config)
            .where(
                TenantPolicy.tenant_id == tid,
                TenantPolicy.policy_type == "retention",
                TenantPolicy.is_active.is_(True),
            )
            .order_by(TenantPolicy.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    configured = (row or {}).get("retention_days")
    # bool is an int subclass: a config typo of `true` would silently mean
    # a 1-day retention window — reject it.
    if isinstance(configured, bool):
        return settings.retention_default_days
    try:
        days = int(configured)
        if days > 0:
            return days
    except (TypeError, ValueError):
        pass
    return settings.retention_default_days


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
                retention_days = await _tenant_retention_days(db, tid)
                archived = await apply_retention_policy(db, tid, retention_days)
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
