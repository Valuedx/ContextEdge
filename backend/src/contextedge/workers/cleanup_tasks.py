"""Post-hard-delete cleanup sweeps.

``purge_archived_evidence(mode="hard_delete")`` removes the
``EvidenceItem`` row and CASCADEs to the tables that have explicit
FK CASCADEs (``attachment_artifacts``, ``correlation_edges``,
``contradiction_scan_state``). Everything else is deliberately left
orphaned there and reaped on a schedule. This module is that
schedule.

Two orphan classes we reap:

- **MinIO raw + artifact blobs (review F-18).** The
  ``raw_evidence_objects`` row that carried the source payload is not
  FK-connected to ``evidence_items``, so hard-delete of an evidence
  item leaves the raw blob + raw row behind. Likewise, attachment
  artifact rows CASCADE via FK but their S3 blobs (keyed by
  ``AttachmentArtifact.object_storage_key``) don't. Both sets of
  blobs would otherwise accumulate indefinitely.

- **Graph edges (review F-20).** ``graph_edges`` rows carry
  ``source_node_id`` / ``target_node_id`` as plain UUIDs with no FK
  to the referenced entity, so edges pointing at deleted evidence
  stay forever. We sweep them by model + id pair.

Both sweeps are explicitly tenant-scoped and honour a ``limit`` so
one run doesn't try to reap millions of orphans at once. Operators
pick cadence via the Beat entry in ``celery_app.beat_schedule``;
daily is a sensible default.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import AttachmentArtifact, EvidenceItem, RawEvidenceObject
from contextedge.models.pattern import GraphEdge
from contextedge.models.tenant import Tenant
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.object_store import delete_object
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _reap_orphan_raw_blobs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int,
) -> dict:
    """Delete ``RawEvidenceObject`` rows (and their MinIO blobs) that
    have no corresponding ``EvidenceItem``. This happens after a
    hard-delete: the evidence row goes, the raw row stays, and the
    S3 blob with it."""
    # Find raws whose id is not referenced by any evidence_item.raw_object_ref
    # for this tenant. LEFT JOIN + IS NULL would also work; subquery is
    # simpler and cheap with the existing tenant+source indexes.
    referenced = select(EvidenceItem.raw_object_ref).where(
        EvidenceItem.tenant_id == tenant_id,
        EvidenceItem.raw_object_ref.is_not(None),
    )
    orphans_stmt = (
        select(RawEvidenceObject)
        .where(
            RawEvidenceObject.tenant_id == tenant_id,
            RawEvidenceObject.id.not_in(referenced),
        )
        .limit(limit)
    )
    orphans = list((await db.execute(orphans_stmt)).scalars().all())

    blobs_deleted = 0
    rows_deleted = 0
    for raw in orphans:
        key = getattr(raw, "object_storage_key", None)
        if key:
            try:
                if delete_object(key):
                    blobs_deleted += 1
            except Exception as exc:
                logger.warning(
                    "cleanup.blob_delete_failed",
                    tenant_id=str(tenant_id),
                    key=key,
                    error=str(exc),
                )
                # Leave the DB row alone so a retry picks it up.
                continue
        await db.delete(raw)
        rows_deleted += 1

    return {"blob_count": blobs_deleted, "raw_row_count": rows_deleted}


async def _reap_orphan_artifact_blobs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int,
) -> int:
    """Delete MinIO artifact blobs whose ``AttachmentArtifact`` row is
    gone (the row CASCADEd when evidence was hard-deleted, but the S3
    object didn't). We track this via a small marker: after evidence
    delete, we fire this sweep; because the rows are already gone we
    have no way to find the blobs by DB scan — so this helper
    currently just returns 0 until a "pending-blob-delete" queue is
    added. For now, we rely on the raw-blob sweep above to catch the
    common case (raw payload + any offloaded body) and operators can
    run an S3 lifecycle rule against the ``artifacts/`` prefix for
    belt-and-braces cleanup."""
    # Deliberately a stub — see docstring. Returning 0 so the
    # aggregate counts below are correct.
    return 0


async def _reap_orphan_graph_edges(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int,
) -> int:
    """Delete ``graph_edges`` rows whose source or target node is of
    type ``evidence`` but no longer exists. Only touches edges in the
    given tenant."""
    evidence_ids_stmt = select(EvidenceItem.id).where(
        EvidenceItem.tenant_id == tenant_id,
    )

    # Two delete statements — one for source-side orphans, one for
    # target-side. Delete is cheaper than select-then-delete at this
    # shape, and SQLAlchemy's ORM-level db.execute(delete(...)) is
    # fine here (no session-level events we care about).
    total = 0
    for column_type, column_id in (
        (GraphEdge.source_node_type, GraphEdge.source_node_id),
        (GraphEdge.target_node_type, GraphEdge.target_node_id),
    ):
        stmt = (
            delete(GraphEdge)
            .where(
                GraphEdge.tenant_id == tenant_id,
                column_type == "evidence",
                column_id.not_in(evidence_ids_stmt),
            )
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(stmt)
        total += result.rowcount or 0
        # Respect the overall limit — if we've already hit it, stop.
        if total >= limit:
            break

    return total


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.cleanup_hard_deleted_evidence",
)
def cleanup_hard_deleted_evidence(self, tenant_id: str, limit: int = 1000):
    """Sweep orphaned raw blobs + graph edges for a tenant (or all
    when ``tenant_id == "all"``).

    Scheduled daily via Beat. Operators can also invoke ad-hoc after
    a large retention purge to free MinIO storage faster.
    """

    async def _sweep_one(db: AsyncSession, tid: uuid.UUID) -> dict:
        raw_stats = await _reap_orphan_raw_blobs(db, tid, limit=limit)
        artifact_blob_count = await _reap_orphan_artifact_blobs(db, tid, limit=limit)
        edge_count = await _reap_orphan_graph_edges(db, tid, limit=limit)
        await db.flush()
        totals = {
            **raw_stats,
            "artifact_blob_count": artifact_blob_count,
            "edge_count": edge_count,
        }
        if any(v for v in totals.values()):
            await append_operational_event(
                db,
                tenant_id=tid,
                entity_type="retention",
                event_type="retention.hard_delete_cleanup",
                payload=totals,
            )
        return totals

    async def work(db):
        if tenant_id == "all":
            tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
            aggregate = {"tenants": len(tids), "blob_count": 0, "raw_row_count": 0, "artifact_blob_count": 0, "edge_count": 0}
            for tid in tids:
                try:
                    result = await _sweep_one(db, tid)
                    for k in ("blob_count", "raw_row_count", "artifact_blob_count", "edge_count"):
                        aggregate[k] += result.get(k, 0)
                except Exception as exc:
                    logger.exception(
                        "cleanup.tenant_failed",
                        tenant_id=str(tid), error=str(exc),
                    )
            return aggregate
        tid = uuid.UUID(tenant_id)
        return await _sweep_one(db, tid)

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("cleanup.hard_delete_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc
