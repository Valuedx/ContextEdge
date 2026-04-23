"""Embedding generation for evidence items and decision traces.

All public helpers accept the same ``tenant_id`` + ``db`` instrumentation
kwargs as ``llm_complete`` so embedding cost lands in ``/admin/cost``
and the pre-call tenant budget gate fires (review C-04 / F-03). Callers
that don't have those handy can still pass ``None`` — the underlying
``generate_embedding`` treats them as "no instrumentation" and the
call still works, it just doesn't show up in the dashboard.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.provider import generate_embedding, generate_embeddings_batch


async def embed_evidence(
    title: str | None,
    body: str | None,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[float]:
    """Generate embedding for an evidence item by combining title and body."""
    text_parts = []
    if title:
        text_parts.append(title)
    if body:
        text_parts.append(body[:8000])
    text = "\n\n".join(text_parts) if text_parts else ""
    if not text:
        return [0.0] * 3072
    return await generate_embedding(text, tenant_id=tenant_id, db=db)


async def embed_decision(
    decision_type: str | None,
    rationale_summary: str | None,
    compact_trace: str | None = None,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[float]:
    """Generate embedding for a Decision's reasoning.

    Combines `decision_type`, `rationale_summary`, and `compact_trace` so
    similar-decision retrieval can find matches on the full reasoning
    surface, not just the context_snapshot keys that JSONB containment
    operates on. Returns a zero vector when all inputs are empty so
    `Decision.embedding.is_not(None)` gates still work.
    """
    parts: list[str] = []
    if decision_type:
        parts.append(decision_type)
    if compact_trace:
        parts.append(compact_trace[:2000])
    if rationale_summary:
        parts.append(rationale_summary[:6000])
    text = "\n\n".join(parts) if parts else ""
    if not text:
        return [0.0] * 3072
    return await generate_embedding(text, tenant_id=tenant_id, db=db)


async def embed_evidence_batch(items: list[tuple[str | None, str | None]]) -> list[list[float]]:
    """Batch embed multiple evidence items."""
    texts = []
    for title, body in items:
        parts = []
        if title:
            parts.append(title)
        if body:
            parts.append(body[:8000])
        texts.append("\n\n".join(parts) if parts else "")

    non_empty = [(i, t) for i, t in enumerate(texts) if t]
    if not non_empty:
        return [[0.0] * 3072 for _ in items]

    embeddings_result = await generate_embeddings_batch([t for _, t in non_empty])

    result = [[0.0] * 3072 for _ in items]
    for (original_idx, _), emb in zip(non_empty, embeddings_result):
        result[original_idx] = emb
    return result
