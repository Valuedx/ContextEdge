"""Stage B — artifact suitability (Phase 2).

Decides whether the content shape matches the routed artifact type from the
quality contract. Shape alone is not the routing signal — the contract's
``artifact_type`` was set at pre-generation from sources (§8.2).
"""

from __future__ import annotations

from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_WRONG_ARTIFACT_TYPE,
    DIM_ARTIFACT_SUITABILITY,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    STATE_INCONCLUSIVE,
    STATE_PASS,
)

_ARTIFACT_MIN_STEPS = {
    "procedural": 1,
    "diagnostic": 1,
    "defect_record": 1,
    "informational": 0,
    "limitation": 0,
    "planning": 0,
    "change": 1,
    "communication": 0,
}


def validate_artifact_suitability(context: ValidationContext) -> ValidatorResult:
    contract = context.contract
    if not contract:
        return ValidatorResult(
            dimension_states={DIM_ARTIFACT_SUITABILITY: STATE_INCONCLUSIVE},
            findings=[
                Finding(
                    category=CATEGORY_WRONG_ARTIFACT_TYPE,
                    dimension=DIM_ARTIFACT_SUITABILITY,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        "No quality contract captured at generation — "
                        "artifact suitability cannot be decided."
                    ),
                    validator="artifact_suitability",
                )
            ],
        )

    artifact_type = str(contract.get("artifact_type") or "procedural")
    steps = context.steps
    findings: list[Finding] = []

    min_steps = _ARTIFACT_MIN_STEPS.get(artifact_type, 1)
    if min_steps and len(steps) < min_steps:
        findings.append(
            Finding(
                category=CATEGORY_WRONG_ARTIFACT_TYPE,
                dimension=DIM_ARTIFACT_SUITABILITY,
                severity=SEVERITY_MAJOR,
                explanation=(
                    f"Contract routed artifact type '{artifact_type}' expects at "
                    f"least {min_steps} step(s); content has {len(steps)}."
                ),
                validator="artifact_suitability",
                target_ref="steps",
            )
        )

    if artifact_type in {"informational", "limitation", "communication"} and len(steps) > 2:
        findings.append(
            Finding(
                category=CATEGORY_WRONG_ARTIFACT_TYPE,
                dimension=DIM_ARTIFACT_SUITABILITY,
                severity=SEVERITY_MAJOR,
                explanation=(
                    f"Contract routed '{artifact_type}' but content has "
                    f"{len(steps)} procedural-looking steps — possible template mismatch."
                ),
                validator="artifact_suitability",
                target_ref="steps",
            )
        )

    if not findings:
        return ValidatorResult(
            dimension_states={DIM_ARTIFACT_SUITABILITY: STATE_PASS},
            findings=[],
        )
    return result_from_findings(findings, default_dimension=DIM_ARTIFACT_SUITABILITY)


register_validator(
    "artifact_suitability",
    (DIM_ARTIFACT_SUITABILITY,),
    validate_artifact_suitability,
    stage="B",
)
