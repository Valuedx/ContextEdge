"""Validator bundle.

Importing this package registers every validator. The import order is the
cascade order and is load-bearing only in that structural runs first; the
registry sorts by stage regardless.
"""

from contextedge.quality.validators import (  # noqa: F401
    artifact_suitability,
    coherence,
    completeness,
    duplicate,
    grounding,
    lexical_support,
    minimality,
    # Registers nothing today — every former stub now has its own module — but
    # the package must still expose it. ``tests/test_playbook_quality_foundation``
    # re-registers the bundle by reloading ``bundle.pending`` after a
    # registry-clearing test, and without this import that attribute does not
    # exist: the reload raises inside a ``finally``, the registry stays empty,
    # and every later registry test fails for a reason that has nothing to do
    # with what it was testing.
    pending,
    safety_policy,
    step_quality,
    structural,
    subject,
)

__all__ = [
    "artifact_suitability",
    "coherence",
    "completeness",
    "duplicate",
    "grounding",
    "lexical_support",
    "minimality",
    "pending",
    "safety_policy",
    "step_quality",
    "structural",
    "subject",
]
