"""Stage J — duplicate step graph within one artifact."""

from __future__ import annotations

from contextedge.quality.claim_match import overlap_ratio
from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_REDUNDANT_STEP,
    DIM_DUPLICATE_STATUS,
    SEVERITY_MINOR,
)

VALIDATOR = "duplicate_status"


def _step_text(step: dict) -> str:
    for key in ("text", "title", "action", "instruction"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    texts = [_step_text(s) for s in context.steps if _step_text(s)]

    for i, left in enumerate(texts):
        for j in range(i + 1, len(texts)):
            if overlap_ratio(left, texts[j]) >= 0.85:
                findings.append(
                    Finding(
                        category=CATEGORY_REDUNDANT_STEP,
                        dimension=DIM_DUPLICATE_STATUS,
                        severity=SEVERITY_MINOR,
                        explanation=(
                            f"Steps {i + 1} and {j + 1} are near-duplicates — one may be redundant."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=str(i + 1),
                    )
                )

    return result_from_findings(findings, (DIM_DUPLICATE_STATUS,))


register_validator(VALIDATOR, (DIM_DUPLICATE_STATUS,), validate, stage="J")
