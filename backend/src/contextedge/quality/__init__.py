"""Playbook quality assessment.

Phase 1 of docs/PLAYBOOK_QUALITY_PERMANENT_FIX_PLAN.md v4.0: the revision,
hashing, state and validator foundation. Nothing here blocks anything — the
enforcement decision is Phase 5 and is deliberately a separate change, made
after thresholds are calibrated on a locked holdout set.

Layout:

- ``states``       — the six-state model, dimensions, generic finding categories
- ``hashing``      — RFC 8785 canonical content hashing
- ``revision``     — the immutable snapshot spanning shell + version
- ``registry``     — validator registration, ``Finding``, ``ValidationContext``
- ``validators``   — the bundle (structural and grounding are real; the rest
                     are registered as explicitly inconclusive)
- ``orchestrator`` — runs the cascade, combines verdicts, never lets an error pass

Persistence and the mutation-path wiring live in
``services/playbook_quality_service.py``; this package stays free of the
session so the same assessment runs identically from a worker, an endpoint,
and a backfill script.
"""

from contextedge.quality import validators as _validators  # noqa: F401  (registers the bundle)
from contextedge.quality.orchestrator import AssessmentOutcome, assess, error_outcome
from contextedge.quality.registry import (
    VALIDATOR_BUNDLE_VERSION,
    Finding,
    ValidationContext,
)
from contextedge.quality.revision import build_content, compute_content_hash

__all__ = [
    "VALIDATOR_BUNDLE_VERSION",
    "AssessmentOutcome",
    "Finding",
    "ValidationContext",
    "assess",
    "build_content",
    "compute_content_hash",
    "error_outcome",
]
