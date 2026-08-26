"""Reciprocal Rank Fusion over playbook recall arms.

Rank-based fusion is scale-free: a compressed cosine band or a popularity
prior can no longer dominate the sum the way a linear weighted score did.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

RRF_K = 60
DEFAULT_ARM_WEIGHTS: dict[str, float] = {
    "r1_embedding": 1.0,
    "r2_lexical": 0.8,
    "r3_signature": 1.2,
    "r4_evidence": 0.6,
}


def rrf_scores(
    arm_ranks: Mapping[str, Sequence[uuid.UUID]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = RRF_K,
) -> dict[uuid.UUID, float]:
    """``rrf(pb) = Σ_arms weight / (K + rank)`` with rank 1-based."""
    w = dict(DEFAULT_ARM_WEIGHTS)
    if weights:
        w.update(weights)
    scores: dict[uuid.UUID, float] = {}
    for arm, ordered in arm_ranks.items():
        weight = float(w.get(arm, 1.0))
        for index, playbook_id in enumerate(ordered, start=1):
            scores[playbook_id] = scores.get(playbook_id, 0.0) + weight / (k + index)
    return scores


def rrf_max(weights: Mapping[str, float] | None = None, *, k: int = RRF_K) -> float:
    w = dict(DEFAULT_ARM_WEIGHTS)
    if weights:
        w.update(weights)
    return sum(float(v) / (k + 1) for v in w.values()) or 1.0
