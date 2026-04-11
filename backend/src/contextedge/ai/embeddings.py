"""Embedding generation for evidence items."""

from contextedge.ai.provider import generate_embedding, generate_embeddings_batch


async def embed_evidence(title: str | None, body: str | None) -> list[float]:
    """Generate embedding for an evidence item by combining title and body."""
    text_parts = []
    if title:
        text_parts.append(title)
    if body:
        text_parts.append(body[:8000])
    text = "\n\n".join(text_parts) if text_parts else ""
    if not text:
        return [0.0] * 3072
    return await generate_embedding(text)


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
