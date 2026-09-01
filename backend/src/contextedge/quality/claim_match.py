"""Shared token overlap helpers for contract-based validators.

No stopword filtering — overlap is computed on raw tokens so validators
judge against source-derived material, not a curated English vocabulary.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def overlap_ratio(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def normalize_action(text: str) -> str:
    return " ".join(sorted(tokens(text)))


def contract_subject_corpus(contract: dict[str, Any]) -> str:
    """Text derived from the quality contract for subject comparisons."""
    parts: list[str] = []
    for key in (
        "primary_subject",
        "failure_mode",
        "affected_component",
        "affected_capability",
        "defect_identity",
    ):
        val = contract.get(key)
        if val:
            parts.append(str(val))
    for key in (
        "observed_symptoms",
        "error_claims",
        "supported_cause_claims",
    ):
        parts.extend(str(x) for x in (contract.get(key) or []) if x)
    return " ".join(parts)


def has_executable_detail(step: dict[str, Any]) -> bool:
    """Whether a step names something an operator can run or observe."""
    for key in ("tool_ref", "action_name", "action_type", "parameters"):
        if step.get(key):
            return True
    for key in ("expected_outcome", "verification"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return True
        if isinstance(val, dict) and val:
            return True
    refs = step.get("source_refs")
    return isinstance(refs, list) and len(refs) > 0
