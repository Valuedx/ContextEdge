"""The clarification loop: ask for what is missing, then use the answer.

Design and rationale: ``docs/PLAYBOOK_CLARIFICATION_LOOP_PLAN.md``.

The loop's shape, and why each piece is where it is:

- ``states``       — round/question vocabulary and the mandatory-vs-optional rule
- ``gaps``         — quality defects re-read as answerable information gaps,
                     each with a ``gap_key`` that survives a re-wording so the
                     next round does not re-ask what was already answered
- ``kb_resolution``— the ordering the requirement insists on: the artifact
                     first, approved knowledge second, a person only third
- ``apply``        — folding answers back into the version's provenance, and
                     onto the contract, which is what makes the loop terminate

Everything here is a pure function of dicts. Persistence, retrieval and the
generation calls live in ``services/playbook_clarification_service.py``, so the
same gap detection runs identically from an endpoint, a test, and a backfill
script — the separation ``quality/`` already keeps from the session.

Nothing here blocks anything. A playbook with every question unanswered can be
approved exactly as it can today; the loop is advisory, like the assessment it
reads.
"""

from contextedge.quality.clarification.gaps import (
    ANSWERABLE_CATEGORIES,
    InformationGap,
    compute_gap_key,
    detect_gaps,
)
from contextedge.quality.clarification.kb_resolution import (
    GapResolution,
    ResolutionOutcome,
    resolve_gaps,
)
from contextedge.quality.clarification.states import (
    MANDATORY,
    OPTIONAL,
    enforce_obligation,
    mandatory_outstanding,
)

__all__ = [
    "ANSWERABLE_CATEGORIES",
    "MANDATORY",
    "OPTIONAL",
    "GapResolution",
    "InformationGap",
    "ResolutionOutcome",
    "compute_gap_key",
    "detect_gaps",
    "enforce_obligation",
    "mandatory_outstanding",
    "resolve_gaps",
]
