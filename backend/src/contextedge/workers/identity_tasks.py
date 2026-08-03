"""Identity maintenance workers.

``rebuild_identity_snapshots`` repairs the cached JSONB identity snapshots
after a merge. The normalized tables (``evidence_identity_links``, graph
edges) are re-pointed synchronously inside
``merge_canonical_identities``; the derived JSONB caches —
``evidence_items.canonical_entity_refs`` and ``episodes.entity_refs`` —
are repaired here so they never permanently drift from the link tables
(the "normalized tables = source of truth, JSONB = derived cache" rule).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

from contextedge.models.episode import CanonicalIdentity, Episode
from contextedge.models.evidence import EvidenceItem
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


def _rewrite_identity_refs(
    refs: dict | None, duplicate_id: str, primary: CanonicalIdentity
) -> dict | None:
    """Replace duplicate identity references with the primary, deduplicating.

    Returns the rewritten dict, or None when nothing referenced the
    duplicate (no write needed).
    """
    if not refs:
        return None
    identities = refs.get("identities")
    if not isinstance(identities, list):
        return None
    changed = False
    rewritten: list[dict] = []
    seen_ids: set[str] = set()
    for item in identities:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue
        canonical_id = str(item.get("canonical_id") or "")
        if canonical_id == duplicate_id:
            item = {
                **item,
                "canonical_id": str(primary.id),
                "canonical_name": primary.canonical_name,
            }
            canonical_id = str(primary.id)
            changed = True
        if canonical_id and canonical_id in seen_ids:
            changed = True  # dropped a duplicate entry
            continue
        if canonical_id:
            seen_ids.add(canonical_id)
        rewritten.append(item)
    if not changed:
        return None
    return {**refs, "identities": rewritten}


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.rebuild_identity_snapshots",
)
def rebuild_identity_snapshots(self, tenant_id: str, primary_id: str, duplicate_id: str):
    """Rewrite cached JSONB identity refs from *duplicate* to *primary*."""

    async def work(db):
        tid = uuid.UUID(tenant_id)
        primary = await db.get(CanonicalIdentity, uuid.UUID(primary_id))
        if primary is None or primary.tenant_id != tid:
            return {"status": "skipped", "reason": "primary_not_found"}

        containment = cast(
            {"identities": [{"canonical_id": duplicate_id}]}, JSONB_TYPE
        )

        evidence_count = 0
        evidence_rows = await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tid,
                EvidenceItem.canonical_entity_refs.op("@>")(containment),
            )
        )
        for item in evidence_rows.scalars().all():
            rewritten = _rewrite_identity_refs(
                item.canonical_entity_refs, duplicate_id, primary
            )
            if rewritten is not None:
                item.canonical_entity_refs = rewritten
                evidence_count += 1

        episode_count = 0
        episode_rows = await db.execute(
            select(Episode).where(
                Episode.tenant_id == tid,
                Episode.entity_refs.op("@>")(containment),
            )
        )
        for episode in episode_rows.scalars().all():
            rewritten = _rewrite_identity_refs(
                episode.entity_refs, duplicate_id, primary
            )
            if rewritten is not None:
                episode.entity_refs = rewritten
                episode_count += 1

        await db.flush()
        logger.info(
            "identity.snapshots_rebuilt",
            tenant_id=tenant_id,
            primary_identity_id=primary_id,
            duplicate_identity_id=duplicate_id,
            evidence_count=evidence_count,
            episode_count=episode_count,
        )
        return {
            "status": "ok",
            "evidence_count": evidence_count,
            "episode_count": episode_count,
        }

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception(
            "identity.snapshot_rebuild_failed",
            tenant_id=tenant_id,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=600,
    name="identity.reconcile_identities",
)
def reconcile_identities_task(self, tenant_id: str = "all"):
    """Propose merges across a tenant's unresolved identities.

    Scheduled rather than triggered because the duplicates it finds are
    created one at a time by the hot path and only become visible as a
    SET. Per-mention resolution compares an incoming name against
    candidates sharing a substring with it, so an acronym and its
    expansion never meet; this pass reads the whole type at once.

    Proposes only. The merge itself waits for a human on the identities
    page, because a wrong merge destroys the distinction between two real
    systems and leaves no trace that it did.
    """
    from contextedge.models.tenant import Tenant
    from contextedge.services.identity_reconciliation_service import (
        reconcile_identities,
    )

    async def work(db):
        if tenant_id == "all":
            tenant_ids = list(
                (await db.execute(select(Tenant.id))).scalars().all()
            )
        else:
            tenant_ids = [uuid.UUID(tenant_id)]

        total = 0
        for tid in tenant_ids:
            try:
                proposals = await reconcile_identities(db, tid)
                total += len(proposals)
                await db.flush()
            except Exception as exc:  # noqa: BLE001
                # One tenant's failure must not stop the fan-out; the
                # next beat retries it anyway.
                logger.warning(
                    "identity.reconcile_tenant_failed",
                    tenant_id=str(tid),
                    error_type=type(exc).__name__,
                )
        return {"status": "ok", "tenants": len(tenant_ids), "proposals": total}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("identity.reconcile_failed", error=str(exc))
        raise self.retry(exc=exc) from exc
