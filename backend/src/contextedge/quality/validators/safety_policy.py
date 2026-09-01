"""Stage I — safety and policy against the active policy pack."""

from __future__ import annotations

from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_POLICY_DISCOURAGED,
    CATEGORY_POLICY_PROHIBITED,
    DIM_SAFETY_POLICY,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    STATE_INCONCLUSIVE,
)
from contextedge.quality.policy_match import rule_matches_action

VALIDATOR = "safety_policy"


def _step_text(step: dict) -> str:
    for key in ("text", "title", "action", "instruction"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def validate(context: ValidationContext) -> ValidatorResult:
    rules = [r for r in context.policy_rules if isinstance(r, dict)]
    if not rules:
        return ValidatorResult(
            dimension_states={DIM_SAFETY_POLICY: STATE_INCONCLUSIVE},
            findings=[
                Finding(
                    category=CATEGORY_POLICY_DISCOURAGED,
                    dimension=DIM_SAFETY_POLICY,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        "No active policy pack — policy-prohibited actions cannot be detected."
                    ),
                    validator=VALIDATOR,
                )
            ],
        )

    findings: list[Finding] = []
    for index, step in enumerate(context.steps, start=1):
        ref = str(step.get("step_id") or index)
        text = _step_text(step)
        if not text:
            continue
        for rule in rules:
            if not rule_matches_action(rule, text):
                continue
            decision = rule.get("decision", "")
            alt = rule.get("alternative_action")
            if decision == "prohibited":
                findings.append(
                    Finding(
                        category=CATEGORY_POLICY_PROHIBITED,
                        dimension=DIM_SAFETY_POLICY,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"Step {ref} matches a prohibited policy action: "
                            f"{rule.get('normalized_action')!r}."
                            + (f" Prefer: {alt}" if alt else "")
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                        claim=text[:400],
                        remediation_category="remove_or_override",
                    )
                )
            elif decision == "discouraged":
                findings.append(
                    Finding(
                        category=CATEGORY_POLICY_DISCOURAGED,
                        dimension=DIM_SAFETY_POLICY,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"Step {ref} matches a discouraged policy action: "
                            f"{rule.get('normalized_action')!r}."
                            + (f" Prefer: {alt}" if alt else "")
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                        claim=text[:400],
                        remediation_category="justify_or_replace",
                    )
                )

    return result_from_findings(findings, (DIM_SAFETY_POLICY,))


register_validator(VALIDATOR, (DIM_SAFETY_POLICY,), validate, stage="I")
