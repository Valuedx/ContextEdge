"""Validator registry and the finding/result shapes every validator returns.

A validator is a pure function of a ``ValidationContext``. It returns findings;
it does not decide admissibility, does not write to the database, and does not
raise — a validator that raises is caught by the orchestrator and recorded as
``error``, because an evaluator that crashed must never read as a pass.

Validators are registered with the dimensions they can decide. That mapping is
what keeps the three top-level decisions independent: the subject validator can
only ever set subject dimensions, so no amount of step quality can make a
misleading title acceptable, and no amount of title quality can rescue wrong
steps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from contextedge.quality.states import (
    CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
    SEVERITY_INFO,
    STATE_PASS,
    state_for_findings,
)

# Bumped whenever any validator's behaviour changes. Stamped on every
# assessment so a result can be attributed to the code that produced it, and
# so retiring a defective bundle can invalidate exactly its assessments.
VALIDATOR_BUNDLE_VERSION = "qa-2026.09.01-p4"


@dataclass(frozen=True)
class Finding:
    """One defect. ``target_ref`` is a step_id or a field name, never a line
    number — steps get reordered and a reviewer needs to find the thing."""

    category: str
    dimension: str
    severity: str
    explanation: str
    validator: str
    target_kind: str = "playbook"
    target_ref: str | None = None
    claim: str | None = None
    supporting_spans: list = field(default_factory=list)
    contradicting_spans: list = field(default_factory=list)
    confidence: float | None = None
    remediation_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "dimension": self.dimension,
            "severity": self.severity,
            "explanation": self.explanation,
            "validator": self.validator,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "claim": self.claim,
            "supporting_spans": list(self.supporting_spans),
            "contradicting_spans": list(self.contradicting_spans),
            "confidence": self.confidence,
            "remediation_category": self.remediation_category,
        }


@dataclass
class ValidationContext:
    """Everything a validator is allowed to see.

    Deliberately not the ORM objects: a validator that can reach the session
    can reach the lifecycle state, and one that can see the lifecycle state
    will eventually be tempted to judge an approved playbook more leniently.
    """

    content: dict[str, Any]
    content_hash: str
    playbook_id: str
    tenant_id: str
    contract: dict[str, Any] | None = None
    knowledge: list[Any] = field(default_factory=list)
    policy_rules: list[Any] = field(default_factory=list)
    ontology_terms: list[Any] = field(default_factory=list)

    @property
    def steps(self) -> list[dict[str, Any]]:
        return [s for s in (self.content.get("steps") or []) if isinstance(s, dict)]


@dataclass
class ValidatorResult:
    """What one validator concluded, per dimension it owns."""

    dimension_states: dict[str, str]
    findings: list[Finding]


ValidatorFn = Callable[[ValidationContext], ValidatorResult]


@dataclass(frozen=True)
class RegisteredValidator:
    name: str
    dimensions: tuple[str, ...]
    fn: ValidatorFn
    # Stage in the cascade. Lower stages run first and are cheap; the
    # orchestrator may stop early on a structural failure rather than paying
    # for semantic evaluation of something that is not well-formed.
    stage: str = "A"


_REGISTRY: dict[str, RegisteredValidator] = {}


def register_validator(
    name: str,
    dimensions: Iterable[str],
    fn: ValidatorFn,
    *,
    stage: str = "A",
) -> None:
    if name in _REGISTRY:
        raise ValueError(f"validator {name!r} is already registered")
    _REGISTRY[name] = RegisteredValidator(
        name=name, dimensions=tuple(dimensions), fn=fn, stage=stage
    )


def registered_validators() -> list[RegisteredValidator]:
    """Registered validators, cheapest stage first."""
    return sorted(_REGISTRY.values(), key=lambda v: (v.stage, v.name))


def clear_registry() -> None:
    """Test seam. Production never calls this."""
    _REGISTRY.clear()


def result_from_findings(
    findings: list[Finding],
    dimensions: Iterable[str],
    *,
    default: str = STATE_PASS,
) -> ValidatorResult:
    """Build a result by grouping findings under the dimensions they name.

    A dimension the validator owns but produced no findings for gets
    ``default`` — which callers set to ``inconclusive`` when the validator
    cannot actually decide that dimension yet.
    """
    by_dimension: dict[str, list[dict]] = {dimension: [] for dimension in dimensions}
    for finding in findings:
        by_dimension.setdefault(finding.dimension, []).append(finding.as_dict())
    states = {
        dimension: state_for_findings(items, default=default)
        for dimension, items in by_dimension.items()
    }
    return ValidatorResult(dimension_states=states, findings=findings)


def not_implemented(
    validator: str, dimensions: Iterable[str], reason: str
) -> ValidatorResult:
    """A validator that cannot decide yet.

    Returns ``inconclusive`` with an explicit finding, never ``pass``. This is
    the single most important line in the package: a quality system whose
    unbuilt validators default to clean reports a corpus as healthy in exact
    proportion to how little of it has been checked.
    """
    dims = tuple(dimensions)
    findings = [
        Finding(
            category=CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
            dimension=dimension,
            severity=SEVERITY_INFO,
            explanation=reason,
            validator=validator,
        )
        for dimension in dims
    ]
    return result_from_findings(findings, dims, default=STATE_PASS)
