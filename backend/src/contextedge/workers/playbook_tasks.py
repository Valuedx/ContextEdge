"""Playbook maintenance workers.

``backfill_playbook_embeddings`` embeds playbooks created before migration
0035 (their ``embedding`` is NULL) and, with ``refresh_stale=True``,
re-embeds approved playbooks from their newest **published** version so
fingerprints written from unpublished drafts or v1.0.0 content are
replaced. Ad-hoc, not on Beat:

    celery call evaluation.backfill_playbook_embeddings --args '["all"]'
    celery call evaluation.backfill_playbook_embeddings --args '["all", 200, true]'

Batched (``limit``) and resumable (``after_id``). Failed embeds stay in
the candidate set and are retried on the next invocation.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import exists, or_, select

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.tenant import Tenant
from contextedge.services.playbook_embedding import embed_playbook
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _backfill(
    db,
    tenant_id: str,
    limit: int,
    *,
    refresh_stale: bool = False,
    after_id: str | None = None,
) -> dict:
    """Embed up to *limit* playbooks per tenant.

    Default: NULL embeddings on non-terminal rows (legacy 0035 repair).
    ``refresh_stale``: also re-embed approved playbooks that have a
    published version, so unpublished-draft fingerprints are replaced.
    """
    if tenant_id == "all":
        tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
    else:
        tids = [uuid.UUID(tenant_id)]

    after_uuid = uuid.UUID(after_id) if after_id else None
    totals = {"tenants": len(tids), "embedded": 0, "failed": 0, "refresh_stale": refresh_stale}
    last_id = None
    for tid in tids:
        filters = [
            Playbook.tenant_id == tid,
            Playbook.lifecycle_state.notin_(("retired", "deprecated")),
        ]
        if after_uuid is not None:
            filters.append(Playbook.id > after_uuid)
        if refresh_stale:
            has_published = exists(
                select(PlaybookVersion.id).where(
                    PlaybookVersion.playbook_id == Playbook.id,
                    PlaybookVersion.published_at.is_not(None),
                )
            )
            filters.append(
                or_(
                    Playbook.embedding.is_(None),
                    has_published,
                )
            )
        else:
            filters.append(Playbook.embedding.is_(None))
        rows = (
            (
                await db.execute(
                    select(Playbook)
                    .where(*filters)
                    .order_by(Playbook.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for playbook in rows:
            last_id = playbook.id
            ok = await embed_playbook(db, playbook)
            totals["embedded" if ok else "failed"] += 1
        await db.commit()
    if last_id is not None:
        totals["after_id"] = str(last_id)
    logger.info("playbook.embedding_backfill_done", **totals)
    return totals


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.backfill_playbook_embeddings",
)
def backfill_playbook_embeddings(
    self,
    tenant_id: str = "all",
    limit: int = 200,
    refresh_stale: bool = False,
    after_id: str | None = None,
):
    try:
        return run_async(
            lambda db: _backfill(
                db,
                tenant_id,
                limit,
                refresh_stale=refresh_stale,
                after_id=after_id,
            )
        )
    except Exception as exc:
        logger.exception("playbook.embedding_backfill_failed", error=str(exc))
        raise self.retry(exc=exc) from exc
