"""Playbook maintenance workers.

``backfill_playbook_embeddings`` embeds playbooks created before migration
0035 (their ``embedding`` is NULL, so they are invisible to the semantic
seed layer and only reachable via FTS). Ad-hoc, not on Beat — run it once
per tenant after upgrading, or "all":

    celery call evaluation.backfill_playbook_embeddings --args '["all"]'
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.models.playbook import Playbook
from contextedge.models.tenant import Tenant
from contextedge.services.playbook_embedding import embed_playbook
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.backfill_playbook_embeddings",
)
def backfill_playbook_embeddings(self, tenant_id: str = "all", limit: int = 200):
    """Embed up to *limit* un-embedded playbooks per tenant. Idempotent —
    already-embedded rows are skipped; failed embeds stay NULL and are
    retried on the next invocation."""

    async def work(db):
        if tenant_id == "all":
            tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
        else:
            tids = [uuid.UUID(tenant_id)]

        totals = {"tenants": len(tids), "embedded": 0, "failed": 0}
        for tid in tids:
            rows = (
                await db.execute(
                    select(Playbook)
                    .where(
                        Playbook.tenant_id == tid,
                        Playbook.embedding.is_(None),
                    )
                    .order_by(Playbook.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            for playbook in rows:
                ok = await embed_playbook(db, playbook)
                totals["embedded" if ok else "failed"] += 1
            await db.commit()
        logger.info("playbook.embedding_backfill_done", **totals)
        return totals

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("playbook.embedding_backfill_failed", error=str(exc))
        raise self.retry(exc=exc) from exc
