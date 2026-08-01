"""Chunk-level MMR + parent rollup for semantic search.

Implements CHUNKING_DESIGN.md §6: vector search hits ``evidence_chunks``
(high recall — a 40-message thread matches on the one message that
matters), then results roll up to the parent evidence for the card/
ranking surface. Two failure modes are handled between those steps:

- **Same-parent crowding** — five chunks of one long document filling
  top-K. Fixed by the rollup itself: one result per evidence, scored by
  its closest chunk.
- **Near-duplicate crowding across parents** — five evidence rows of the
  SAME thread (each a message re-stating the outage) crowding out four
  distinct threads. The rollup alone cannot fix this; MMR at the chunk
  level demotes candidates that are nearly identical to already-selected
  ones before grouping.

Pure functions over fetched candidates (numpy for the pairwise math) —
the SQL side lives in ``vector_search.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np

# λ balances relevance against diversity in the greedy MMR objective:
# λ·relevance − (1−λ)·max-similarity-to-selected. 0.7 favors relevance
# — diversity only breaks near-ties, it never buries a clearly better hit.
MMR_LAMBDA = 0.7


@dataclass(slots=True)
class ChunkCandidate:
    chunk_id: uuid.UUID
    evidence_id: uuid.UUID
    distance: float
    embedding: object  # numpy array / list / None (defensive)
    parent_section: str | None = None
    chunk_kind: str | None = None
    snippet: str | None = None

    @property
    def relevance(self) -> float:
        # Cosine distance lives in [0, 2] → [0, 1] relevance.
        return 1.0 - min(max(self.distance, 0.0), 2.0) / 2.0

    def context(self) -> dict:
        """Best-chunk breadcrumb for consumers rendering the hit."""
        return {
            "chunk_id": str(self.chunk_id),
            "parent_section": self.parent_section,
            "chunk_kind": self.chunk_kind,
            "snippet": self.snippet,
        }


def _normalized_matrix(candidates: list[ChunkCandidate]) -> np.ndarray | None:
    """Row-normalized embedding matrix, or None when any candidate lacks
    an embedding (MMR then degrades to pure relevance ordering rather
    than treating missing vectors as orthogonal-to-everything)."""
    vectors = []
    for candidate in candidates:
        if candidate.embedding is None:
            return None
        vectors.append(np.asarray(candidate.embedding, dtype=np.float32))
    matrix = np.stack(vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def mmr_order(
    candidates: list[ChunkCandidate],
    select_n: int,
    lambda_: float = MMR_LAMBDA,
) -> list[ChunkCandidate]:
    """Greedy maximal-marginal-relevance selection over chunk candidates."""
    if select_n <= 0 or not candidates:
        return []
    if len(candidates) == 1:
        return list(candidates)

    matrix = _normalized_matrix(candidates)
    if matrix is None:
        return sorted(candidates, key=lambda c: c.distance)[:select_n]

    similarity = matrix @ matrix.T  # bounded: oversample cap × oversample cap
    relevance = np.array([c.relevance for c in candidates])

    selected: list[int] = []
    remaining = set(range(len(candidates)))
    while remaining and len(selected) < select_n:
        best_index, best_score = None, None
        for index in remaining:
            max_sim = float(similarity[index, selected].max()) if selected else 0.0
            score = lambda_ * float(relevance[index]) - (1.0 - lambda_) * max_sim
            if best_score is None or score > best_score:
                best_index, best_score = index, score
        selected.append(best_index)
        remaining.discard(best_index)
    return [candidates[index] for index in selected]


def rollup_best_chunk_per_evidence(
    candidates: list[ChunkCandidate],
) -> list[ChunkCandidate]:
    """One candidate per evidence — its closest chunk — ordered by that
    parent score (min distance). Input order is irrelevant."""
    best: dict[uuid.UUID, ChunkCandidate] = {}
    for candidate in candidates:
        current = best.get(candidate.evidence_id)
        if current is None or candidate.distance < current.distance:
            best[candidate.evidence_id] = candidate
    return sorted(best.values(), key=lambda c: (c.distance, str(c.chunk_id)))
