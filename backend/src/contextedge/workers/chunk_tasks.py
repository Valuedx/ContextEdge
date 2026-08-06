"""Celery tasks for evidence chunking + chunk embedding.

Two tasks land here:

- :func:`chunk_evidence_task` — async path for the normalize worker
  when an evidence record exceeds the inline-chunking size budget
  (large attachments, long Teams threads, multi-comment tickets that
  exceed the threshold). Mirrors the dispatch shape used by
  ``extract_attachment_artifact``.
- :func:`embed_chunks_batch_task` — fans out chunk embeddings in
  batches of 32, respecting the per-tenant LLM budget gate that
  ``llm_complete`` already enforces. Triggered after chunks land.

Inline chunking (small bodies) happens directly inside
``_normalize``; see ``codewiki/CHUNKING_DESIGN.md`` for the threshold
rationale. The async path exists so worker latency on the critical
ingest path doesn't degrade for the long-tail-large items.

All bodies in this module are *stubs*. The wiring + signatures match
the rest of the worker tree so the implementation can be filled in
without changing call sites.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embeddings_batch
from contextedge.models.evidence import EvidenceChunk, EvidenceItem, RawEvidenceObject
from contextedge.services.artifact_extraction_service import load_raw_payload
from contextedge.services.chunkers import get_chunker
from contextedge.services.evidence_chunk_service import (
    chunk_ids_pending_embedding,
    stamp_chunk_embeddings,
    write_chunks,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app
from contextedge.workers.suggestion_tasks import generate_correlation_suggestions

logger = structlog.get_logger()


# How many chunk embeddings to send to the embedding provider per
# batch. Aligned with ``embed_evidence_batch`` patterns in
# ``ai/embeddings.py``. Tunable per provider; 32 is a safe floor.
EMBED_BATCH_SIZE = 32


async def _chunk_evidence(
    db: AsyncSession,
    evidence_id: str,
    tenant_id: uuid.UUID,
) -> dict:
    """Chunk a single evidence and enqueue embedding for the new chunks.

    Idempotent: if the parent has already been chunked at the current
    chunker's ``version``, the task returns without rewriting rows.
    Re-running with a bumped chunker version writes the new generation
    alongside the old (unique key includes ``chunker_version``); the
    GC task is responsible for retiring the old generation later.
    """
    eid = uuid.UUID(evidence_id)
    ev = await db.get(EvidenceItem, eid)
    if not ev or ev.tenant_id != tenant_id:
        return {"error": "evidence_not_found"}

    # Resolve chunker first so the idempotency check uses the same
    # version that ``write_chunks`` will persist.
    chunker = get_chunker(ev.source_type, ev.evidence_type)

    if ev.chunked_at is not None:
        # Check whether existing chunks already match this chunker's
        # version. If yes, no-op. If no, the next run will write the
        # new version.
        existing_q = await db.execute(
            select(EvidenceChunk.id)
            .where(
                EvidenceChunk.evidence_id == ev.id,
                EvidenceChunk.chunker_version == chunker.version,
            )
            .limit(1)
        )
        if existing_q.scalar_one_or_none() is not None:
            return {
                "evidence_id": evidence_id,
                "skipped": "already_chunked_at_version",
                "chunker_version": chunker.version,
            }

    # Reload the raw payload — chunkers need the full structured
    # payload to extract per-source metadata, not just body_text.
    payload: dict = {}
    if ev.raw_object_ref:
        raw = await db.get(RawEvidenceObject, ev.raw_object_ref)
        if raw is not None:
            try:
                payload = await load_raw_payload(raw) or {}
            except ValueError:
                # Offloaded raw without a storage key (legacy data).
                # Chunk against body_text only.
                payload = {}

    chunks = await write_chunks(
        db,
        tenant_id=tenant_id,
        evidence=ev,
        payload=payload,
        source_type=ev.source_type,
    )

    # Hand off embedding to the batched task. We do *not* embed
    # inline so the chunk task latency stays bounded; the batched
    # task fans out groups of EMBED_BATCH_SIZE per LLM call.
    if chunks:
        embed_chunks_batch_task.delay(
            [str(c.id) for c in chunks],
            str(tenant_id),
        )

    return {
        "evidence_id": evidence_id,
        "chunk_count": len(chunks),
        "chunker": chunker.name,
        "chunker_version": chunker.version,
    }


async def _embed_chunks_batch(
    db: AsyncSession,
    chunk_ids: list[str],
    tenant_id: uuid.UUID,
) -> dict:
    """Embed chunks in batches of ``EMBED_BATCH_SIZE``.

    Idempotent: skips chunks whose embedding is already populated.
    Per-tenant LLM budget enforcement happens inside
    ``generate_embeddings_batch``; this task only filters and dispatches.
    """
    if not chunk_ids:
        return {"written": 0, "skipped": 0}

    ids = [uuid.UUID(c) for c in chunk_ids]
    rows_q = await db.execute(
        select(EvidenceChunk)
        .where(
            EvidenceChunk.tenant_id == tenant_id,
            EvidenceChunk.id.in_(ids),
        )
        .order_by(EvidenceChunk.chunk_index.asc())
    )
    chunks = list(rows_q.scalars().all())
    pending = [c for c in chunks if c.embedding is None]
    skipped = len(chunks) - len(pending)

    written = 0
    embedded_evidence_ids: set[uuid.UUID] = set()
    for batch_start in range(0, len(pending), EMBED_BATCH_SIZE):
        batch = pending[batch_start : batch_start + EMBED_BATCH_SIZE]
        texts = [c.text for c in batch]
        try:
            # tenant_id + db make the budget gate and cost attribution real —
            # without them this docstring's claim was false: the batch path
            # skipped the tenant cap entirely and recorded spend as unknown.
            embeddings = await generate_embeddings_batch(
                texts, tenant_id=tenant_id, db=db
            )
        except Exception as exc:
            logger.warning(
                "chunk_embedding_failed",
                tenant_id=str(tenant_id),
                batch_size=len(batch),
                error=str(exc),
            )
            # Don't raise — the next replay will pick up the same
            # ``embedding IS NULL`` rows and try again.
            break
        written += await stamp_chunk_embeddings(
            db, chunks=batch, embeddings=embeddings,
        )
        embedded_evidence_ids.update(c.evidence_id for c in batch)

    return {
        "written": written,
        "skipped": skipped,
        "embedded_evidence_ids": sorted(str(e) for e in embedded_evidence_ids),
    }


# Re-export so ``write_chunks`` callers can find the helper without
# pulling in the service module directly. Keeps the worker boundary
# coherent: persistence helpers live in ``services.``, dispatchers
# live in ``workers.``.
__all__ = [
    "EMBED_BATCH_SIZE",
    "chunk_evidence_task",
    "chunk_ids_pending_embedding",
    "embed_chunks_batch_task",
]


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.chunk_evidence",
)
def chunk_evidence_task(self, evidence_id: str, tenant_id: str):
    """Async chunking entrypoint.

    Routed to the ``extraction`` queue alongside ``normalize_evidence``
    and the attachment extractor. Failures retry up to 3× with a 60s
    backoff — same shape as ``normalize_evidence``.
    """
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _chunk_evidence(db, evidence_id, tid)

    try:
        res = run_async(work)
        # On success, the inner function enqueues
        # ``embed_chunks_batch_task`` for each batch. Nothing else to
        # fan out at this level.
        return res
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="extraction.embed_chunks_batch",
)
def embed_chunks_batch_task(self, chunk_ids: list[str], tenant_id: str):
    """Embed a batch of chunks.

    Called from ``chunk_evidence_task`` after chunks land. Idempotent
    via the ``embedding IS NULL`` filter inside ``_embed_chunks_batch``
    — replaying a task on the same chunk IDs is a no-op.
    """
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _embed_chunks_batch(db, chunk_ids, tid)

    try:
        result = run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc

    # Post-commit fan-out: with embeddings persisted, the evidence is
    # ANN-visible — generate gated semantic correlation suggestions.
    # Dispatching after run_async (which commits) means the suggestion
    # task can never race an uncommitted embedding.
    for eid in result.get("embedded_evidence_ids", []):
        generate_correlation_suggestions.delay(eid, tenant_id)
    return result
