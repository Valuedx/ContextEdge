"""Stage H — cross-content coherence (title / symptoms / steps / resolution)."""

from __future__ import annotations

from contextedge.quality.claim_match import overlap_ratio, tokens
from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_SUBJECT_MISMATCH,
    DIM_COHERENCE,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "cross_content_coherence"


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    contract = context.contract or {}
    title = str(context.content.get("title") or "")
    description = str(context.content.get("description") or "")
    steps = context.steps

    symptoms = [str(s) for s in (contract.get("observed_symptoms") or []) if s]
    causes = [str(c) for c in (contract.get("supported_cause_claims") or []) if c]

    if symptoms and steps:
        step_blob = " ".join(
            str(s.get("text") or s.get("title") or "") for s in steps
        )
        if symptoms and max(overlap_ratio(symptoms[0], step_blob), overlap_ratio(symptoms[0], title)) < 0.05:
            findings.append(
                Finding(
                    category=CATEGORY_SUBJECT_MISMATCH,
                    dimension=DIM_COHERENCE,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        "Observed symptoms from the contract are not reflected in "
                        "the title or steps."
                    ),
                    validator=VALIDATOR,
                    target_kind="playbook",
                )
            )

    if len(causes) >= 2:
        cause_tokens = [tokens(c) for c in causes]
        for i, left in enumerate(cause_tokens):
            for right in cause_tokens[i + 1 :]:
                if left and right and len(left & right) / min(len(left), len(right)) < 0.1:
                    findings.append(
                        Finding(
                            category=CATEGORY_SUBJECT_MISMATCH,
                            dimension=DIM_COHERENCE,
                            severity=SEVERITY_MAJOR,
                            explanation=(
                                "Contract records multiple incompatible cause claims "
                                "across sources — the playbook may combine unrelated incidents."
                            ),
                            validator=VALIDATOR,
                            target_kind="playbook",
                        )
                    )
                    break

    if description and steps:
        last_step = str(steps[-1].get("text") or steps[-1].get("title") or "")
        success = contract.get("success_criteria") or []
        resolution_hint = success[0] if success else last_step
        if resolution_hint and overlap_ratio(description, str(resolution_hint)) < 0.08:
            findings.append(
                Finding(
                    category=CATEGORY_SUBJECT_MISMATCH,
                    dimension=DIM_COHERENCE,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        "Description and resolution/success criteria tell different stories."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref="description",
                )
            )

    return result_from_findings(findings, (DIM_COHERENCE,))


register_validator(VALIDATOR, (DIM_COHERENCE,), validate, stage="H")
