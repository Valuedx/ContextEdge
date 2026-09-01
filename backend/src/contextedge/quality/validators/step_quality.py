"""Stage E — per-step quality against contract claims."""

from __future__ import annotations

from contextedge.quality.claim_match import (
    contract_subject_corpus,
    has_executable_detail,
    overlap_ratio,
)
from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_INSUFFICIENT_DETAIL,
    CATEGORY_UNSUPPORTED_CLAIM,
    CATEGORY_UNSUPPORTED_SPECIFICITY,
    DIM_STEP_ACCURACY,
    DIM_STEP_CONSISTENCY,
    DIM_STEP_EXECUTABILITY,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "step_quality"


def _instruction(step: dict) -> str:
    for key in ("text", "title", "action", "instruction", "description"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _contract_claim_texts(contract: dict) -> list[str]:
    texts: list[str] = []
    for key in (
        "required_actions",
        "preconditions",
        "required_validations",
        "rollback_obligations",
        "optional_actions",
    ):
        for item in contract.get(key) or []:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
    for claim in contract.get("claims") or []:
        if isinstance(claim, dict) and claim.get("text"):
            texts.append(str(claim["text"]))
    return texts


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    contract = context.contract or {}
    claim_texts = _contract_claim_texts(contract)
    subject_corpus = contract_subject_corpus(contract)

    for index, step in enumerate(context.steps, start=1):
        ref = str(step.get("step_id") or index)
        text = _instruction(step)
        if not text:
            continue

        claim_overlap = (
            max((overlap_ratio(text, c) for c in claim_texts), default=0.0)
            if claim_texts
            else 0.0
        )
        subject_overlap = overlap_ratio(text, subject_corpus) if subject_corpus else 0.0

        if (
            step.get("step_classification") not in ("human_authored",)
            and not has_executable_detail(step)
            and claim_overlap < 0.12
            and subject_overlap < 0.12
        ):
            findings.append(
                Finding(
                    category=CATEGORY_UNSUPPORTED_SPECIFICITY,
                    dimension=DIM_STEP_ACCURACY,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Step {ref} neither cites sources nor aligns with the "
                        "contract's subject or obligations."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    claim=text[:400],
                )
            )

        if step.get("grounding_status") == "grounded" and claim_texts:
            if claim_overlap < 0.08:
                findings.append(
                    Finding(
                        category=CATEGORY_UNSUPPORTED_CLAIM,
                        dimension=DIM_STEP_ACCURACY,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"Step {ref} is tagged grounded but does not align with "
                            "any contract claim or source obligation."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                        claim=text[:400],
                    )
                )

        tool_ref = step.get("tool_ref")
        if tool_ref and not step.get("action_name") and not step.get("action_type"):
            findings.append(
                Finding(
                    category=CATEGORY_INSUFFICIENT_DETAIL,
                    dimension=DIM_STEP_EXECUTABILITY,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Step {ref} names tool {tool_ref!r} without an executable action binding."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                )
            )

        if step.get("type") == "escalation":
            escalation_overlap = overlap_ratio(text, subject_corpus) if subject_corpus else 0.0
            if escalation_overlap < 0.05 and not step.get("on_failure"):
                findings.append(
                    Finding(
                        category=CATEGORY_UNSUPPORTED_CLAIM,
                        dimension=DIM_STEP_CONSISTENCY,
                        severity=SEVERITY_MINOR,
                        explanation=(
                            f"Step {ref} is typed escalation but does not reference "
                            "the contract subject or a failure route."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                    )
                )

    return result_from_findings(
        findings,
        (DIM_STEP_ACCURACY, DIM_STEP_EXECUTABILITY, DIM_STEP_CONSISTENCY),
    )


register_validator(
    VALIDATOR,
    (DIM_STEP_ACCURACY, DIM_STEP_EXECUTABILITY, DIM_STEP_CONSISTENCY),
    validate,
    stage="E",
)
