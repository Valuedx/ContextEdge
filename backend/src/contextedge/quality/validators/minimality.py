"""Stage J (minimality) — utility-based padding detection."""

from __future__ import annotations

from contextedge.quality.claim_match import (
    contract_subject_corpus,
    normalize_action,
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
    CATEGORY_NO_UTILITY_STEP,
    CATEGORY_REDUNDANT_STEP,
    DIM_MINIMALITY,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "minimality"


def _step_text(step: dict) -> str:
    for key in ("text", "title", "action", "instruction"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    contract = context.contract or {}
    obligations = []
    for key in (
        "required_actions",
        "required_validations",
        "preconditions",
        "rollback_obligations",
    ):
        obligations.extend(str(x) for x in (contract.get(key) or []) if x)
    subject_corpus = contract_subject_corpus(contract)

    seen: dict[str, str] = {}
    for index, step in enumerate(context.steps, start=1):
        ref = str(step.get("step_id") or index)
        text = _step_text(step)
        if not text:
            continue
        norm = normalize_action(text)
        if norm in seen:
            findings.append(
                Finding(
                    category=CATEGORY_REDUNDANT_STEP,
                    dimension=DIM_MINIMALITY,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Step {ref} duplicates step {seen[norm]!r} — removal would not "
                        "reduce coverage."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                )
            )
        else:
            seen[norm] = ref

        if step.get("step_classification") == "best_practice":
            reason = str(step.get("reason") or "").strip()
            if not reason:
                findings.append(
                    Finding(
                        category=CATEGORY_NO_UTILITY_STEP,
                        dimension=DIM_MINIMALITY,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"Step {ref} is best-practice inference without a stated reason."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                        claim=text[:400],
                    )
                )
            elif subject_corpus:
                links_issue = max(
                    overlap_ratio(reason, subject_corpus),
                    overlap_ratio(reason, text),
                )
                if links_issue < 0.1:
                    findings.append(
                        Finding(
                            category=CATEGORY_NO_UTILITY_STEP,
                            dimension=DIM_MINIMALITY,
                            severity=SEVERITY_MAJOR,
                            explanation=(
                                f"Step {ref} reason does not connect to this issue's "
                                "contract subject or step action."
                            ),
                            validator=VALIDATOR,
                            target_kind="step",
                            target_ref=ref,
                            claim=text[:400],
                        )
                    )

        if obligations and step.get("step_classification") != "human_authored":
            if not any(overlap_ratio(text, ob) >= 0.2 for ob in obligations):
                if step.get("step_classification") == "best_practice":
                    findings.append(
                        Finding(
                            category=CATEGORY_NO_UTILITY_STEP,
                            dimension=DIM_MINIMALITY,
                            severity=SEVERITY_MINOR,
                            explanation=(
                                f"Step {ref} is not supported by any contract obligation."
                            ),
                            validator=VALIDATOR,
                            target_kind="step",
                            target_ref=ref,
                        )
                    )

    return result_from_findings(findings, (DIM_MINIMALITY,))


register_validator(VALIDATOR, (DIM_MINIMALITY,), validate, stage="J")
