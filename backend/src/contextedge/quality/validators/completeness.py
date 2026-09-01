"""Stage F — completeness against contract obligations."""

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
    CATEGORY_MISSING_OBLIGATION,
    CATEGORY_MISSING_ROLLBACK,
    CATEGORY_MISSING_VERIFICATION,
    DIM_STEP_COMPLETENESS,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "contract_completeness"


def _step_texts(steps: list[dict]) -> list[str]:
    out: list[str] = []
    for step in steps:
        for key in ("text", "title", "action", "instruction"):
            val = step.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
                break
    return out


def _covered(obligation: str, step_texts: list[str], *, threshold: float = 0.25) -> bool:
    return any(overlap_ratio(obligation, st) >= threshold for st in step_texts)


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    contract = context.contract
    if not contract:
        return result_from_findings(findings, (DIM_STEP_COMPLETENESS,))

    step_texts = _step_texts(context.steps)
    rollback_notes = str(context.content.get("rollback_notes") or "")

    for obligation in contract.get("required_actions") or []:
        if isinstance(obligation, str) and obligation.strip():
            if not _covered(obligation, step_texts):
                findings.append(
                    Finding(
                        category=CATEGORY_MISSING_OBLIGATION,
                        dimension=DIM_STEP_COMPLETENESS,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            "Contract required action is not reflected in any step."
                        ),
                        validator=VALIDATOR,
                        target_kind="playbook",
                        claim=obligation[:400],
                        remediation_category="add_step_or_conflict",
                    )
                )

    for obligation in contract.get("required_validations") or []:
        if isinstance(obligation, str) and obligation.strip():
            if not _covered(obligation, step_texts):
                findings.append(
                    Finding(
                        category=CATEGORY_MISSING_VERIFICATION,
                        dimension=DIM_STEP_COMPLETENESS,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            "Contract validation obligation is missing from steps."
                        ),
                        validator=VALIDATOR,
                        target_kind="playbook",
                        claim=obligation[:400],
                    )
                )

    for obligation in contract.get("rollback_obligations") or []:
        if isinstance(obligation, str) and obligation.strip():
            combined = step_texts + ([rollback_notes] if rollback_notes else [])
            if not _covered(obligation, combined, threshold=0.2):
                findings.append(
                    Finding(
                        category=CATEGORY_MISSING_ROLLBACK,
                        dimension=DIM_STEP_COMPLETENESS,
                        severity=SEVERITY_MINOR,
                        explanation=(
                            "Contract rollback obligation is not in steps or rollback_notes."
                        ),
                        validator=VALIDATOR,
                        target_kind="playbook",
                        claim=obligation[:400],
                    )
                )

    return result_from_findings(findings, (DIM_STEP_COMPLETENESS,))


register_validator(VALIDATOR, (DIM_STEP_COMPLETENESS,), validate, stage="F")
