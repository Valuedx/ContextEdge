"""Backfill ``evidence_type`` for evidence ingested before it was derived.

Every record ingested before ``services/evidence_typing`` landed carries
``evidence_type="message"``, because no connector but ``zoho_desk`` ever
set the field and the normalizer defaulted it. A ServiceNow KB article
and a Teams chat line are indistinguishable in those rows.

The information is not lost: ``RawEvidenceObject`` still holds the
connector's own ``_connector_source_type`` / ``_connector_object_type``
in its payload, which is exactly what the derivation reads. So the
backfill re-derives from the stored raw payload rather than guessing.

Ad-hoc, not on Beat — run it once per tenant after upgrading, or "all"::

    celery call extraction.backfill_evidence_types --args '["all"]'

Idempotent: only rows whose derived type *differs* from the stored one
are updated, so a second run is a no-op. Rows whose raw payload is
offloaded to object storage are skipped rather than fetched — the point
is a cheap metadata repair, not a re-read of every blob ever ingested.

**Re-chunking is deliberately NOT triggered.** ``source_authority`` is
stamped into chunk metadata at write time, so existing chunks keep the
authority they were written with. Fixing that is a re-chunk at a new
``chunker_version``, which is a much larger operation with an embedding
cost attached — it belongs in a decision the operator makes explicitly,
not as a side effect of a metadata backfill.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.tenant import Tenant
from contextedge.services.evidence_typing import derive_evidence_type
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()

DEFAULT_LIMIT = 5000


async def _backfill(db, tenant_id: str, limit: int) -> dict:
    if tenant_id == "all":
        tids = [row[0] for row in (await db.execute(select(Tenant.id))).all()]
    else:
        tids = [uuid.UUID(tenant_id)]

    totals = {"scanned": 0, "updated": 0, "skipped_no_raw": 0, "unchanged": 0}

    for tid in tids:
        rows = (
            await db.execute(
                select(EvidenceItem, RawEvidenceObject)
                .join(
                    RawEvidenceObject,
                    EvidenceItem.raw_object_ref == RawEvidenceObject.id,
                )
                .where(EvidenceItem.tenant_id == tid)
                .limit(limit)
            )
        ).all()

        for evidence, raw in rows:
            totals["scanned"] += 1
            payload = raw.raw_payload if isinstance(raw.raw_payload, dict) else None
            # An offloaded row stores {"_offloaded": true} inline and the
            # real payload in object storage. That stub is a dict, so
            # deriving from it would return "message" and OVERWRITE a
            # correct type with a wrong one — the opposite of the repair.
            # Skipped rather than fetched: this is a cheap metadata pass,
            # not a re-read of every blob ever ingested.
            if not payload or payload.get("_offloaded"):
                totals["skipped_no_raw"] += 1
                continue

            derived = derive_evidence_type(payload)
            if derived == evidence.evidence_type:
                totals["unchanged"] += 1
                continue

            evidence.evidence_type = derived
            totals["updated"] += 1

        await db.flush()

    logger.info("evidence.type_backfill_done", **totals)
    return totals


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.backfill_evidence_types",
)
def backfill_evidence_types(self, tenant_id: str = "all", limit: int = DEFAULT_LIMIT):
    try:
        # run_async supplies the session and owns commit/rollback — the
        # same contract every other worker uses. An earlier version here
        # opened its own session and passed a coroutine instead of a
        # callable, which failed at the first real invocation with
        # "'coroutine' object is not callable". It had unit tests; none
        # of them called the Celery task.
        return run_async(lambda db: _backfill(db, tenant_id, int(limit)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc
