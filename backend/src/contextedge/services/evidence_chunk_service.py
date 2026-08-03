"""Persistence-layer service for ``EvidenceChunk`` rows.

Sits between the chunker protocol (pure functions over text) and the
ORM. Callers from the normalize worker and the backfill task both go
through :func:`write_chunks` so behaviour stays in one place — chunk
hashing, idempotent re-runs, the ``chunked_at`` / ``chunk_count``
stamp on the parent, and the per-chunk structured log line.

Why a service module rather than methods on the model: persistence
spans two tables (write children + stamp parent) plus structured
logging plus the embed-handoff. Keeping it functional means the
normalize worker doesn't grow another responsibility.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceChunk, EvidenceItem
from contextedge.services.chunkers import ChunkSpec, get_chunker

logger = structlog.get_logger()


def _hash_chunk_text(text: str) -> str:
    """SHA-256 of the chunk text.

    Mirrors ``evidence_content_hash_from_payload`` so chunk-level dedup
    uses the same algorithm. Encoded with ``errors='replace'`` for the
    same reason: hashing must never raise on weird upstream bytes.
    """
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


async def write_chunks(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
    source_type: str | None,
) -> list[EvidenceChunk]:
    """Chunk ``evidence`` and persist the rows.

    The flow:

    1. Resolve the chunker via ``get_chunker(source_type, evidence_type)``.
    2. Call ``chunker.chunk(...)`` — pure, deterministic.
    3. Drop any existing chunks at the same ``chunker_version`` (re-run
       safety; rare in practice but the backfill worker depends on it).
    4. Insert the new rows.
    5. Stamp ``evidence.chunked_at`` + ``evidence.chunk_count``.

    Embeddings are *not* generated here — that's the embed-batch
    worker's job. Chunks land with ``embedding = NULL`` and the embed
    task fills them in batches of 32. This split lets the inline
    normalize path return quickly even for large items, and lets the
    embed budget be enforced per-tenant via the existing budget gate.

    Returns the persisted rows in chunk-index order.
    """
    chunker = get_chunker(source_type, evidence.evidence_type)
    specs: list[ChunkSpec] = chunker.chunk(
        title=evidence.title,
        body=evidence.body_text,
        payload=payload,
    )

    # Drop any prior rows at this chunker_version. We do not delete other
    # versions — keeping them lets a re-chunk experiment run alongside
    # the production chunker until the maintenance task GCs the old
    # version.
    await db.execute(
        delete(EvidenceChunk).where(
            EvidenceChunk.evidence_id == evidence.id,
            EvidenceChunk.chunker_version == chunker.version,
        )
    )

    rows: list[EvidenceChunk] = []
    for idx, spec in enumerate(specs):
        # Source authority defaults from the chunker's own knowledge of
        # its source family unless the spec already set it.
        meta = dict(spec.metadata)
        meta.setdefault("source_authority", _default_authority(source_type))

        row = EvidenceChunk(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            evidence_id=evidence.id,
            chunk_index=idx,
            chunk_kind=spec.chunk_kind,
            text=spec.text,
            char_offset_start=spec.char_offset_start,
            char_offset_end=spec.char_offset_end,
            parent_section=spec.parent_section,
            content_hash=_hash_chunk_text(spec.text),
            chunk_metadata=meta,
            chunker_version=chunker.version,
        )
        db.add(row)
        rows.append(row)

    # Stamp the parent. ``utcnow()`` is intentionally tz-aware via
    # ``timezone.utc`` to match the rest of the codebase that stores
    # ``DateTime(timezone=True)`` columns.
    evidence.chunked_at = datetime.now(UTC)
    evidence.chunk_count = len(rows)
    await db.flush()

    logger.info(
        "evidence.chunked",
        tenant_id=str(tenant_id),
        evidence_id=str(evidence.id),
        chunker=chunker.name,
        chunker_version=chunker.version,
        chunk_count=len(rows),
        source_type=source_type,
    )

    return rows


def _default_authority(source_type: str | None) -> str:
    """Default ``source_authority`` tag from the source type.

    The reranker uses this as a feature when scoring chunks. Admins
    can override per-source later via a settings table; this is the
    day-1 floor.

    Mapping rationale:

    - ``runbook``  — internal SOPs, post-mortems uploaded as such
    - ``ticket``   — ITSM systems with formal lifecycle (Jira, ServiceNow)
    - ``email``    — ground-truth-ish but lower than ITSM
    - ``chat``     — high noise, low authority
    - ``gist``     — wikis, pasted snippets, ad-hoc docs
    """
    if source_type in {"jira_sm", "servicenow"}:
        return "ticket"
    if source_type == "gmail":
        return "email"
    if source_type == "teams":
        return "chat"
    return "gist"


async def chunk_ids_pending_embedding(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Return chunk IDs that still need embeddings, oldest first.

    Used by ``embed_chunks_batch_task`` to drive batched embedding.
    Tenant-scoped; legal-hold filtering is the caller's responsibility
    (parent-level — chunks inherit the parent's posture).
    """
    rows = await db.execute(
        select(EvidenceChunk.id)
        .where(
            EvidenceChunk.tenant_id == tenant_id,
            EvidenceChunk.evidence_id == evidence_id,
            EvidenceChunk.embedding.is_(None),
        )
        .order_by(EvidenceChunk.chunk_index.asc())
    )
    return [r[0] for r in rows.all()]


async def stamp_chunk_embeddings(
    db: AsyncSession,
    *,
    chunks: Iterable[EvidenceChunk],
    embeddings: list[list[float]],
) -> int:
    """Write a batch of embeddings onto chunks.

    Caller is responsible for enforcing tenant scope on the input
    iterable. ``embeddings`` must be aligned with ``chunks`` in order.
    Returns the count written.
    """
    written = 0
    for ch, emb in zip(chunks, embeddings):
        ch.embedding = emb
        written += 1
    await db.flush()
    return written
