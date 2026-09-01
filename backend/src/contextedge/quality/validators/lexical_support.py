"""Stage C — lexical support for grounded steps (token overlap + polarity).

This is not semantic entailment: it scores token and bigram overlap against
contract source claims, with a polarity guard so forbidden actions are not
scored as perfect support. Embedding-based entailment will register under a
separate validator when the async layer lands.

Stage A still checks citation self-consistency; this module checks whether
grounded steps lexically align with source obligations in the quality contract.
"""

from __future__ import annotations

from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.semantic_match import (
    best_polarity_conflict,
    best_support_score,
    combined_entailment_score,
    contract_negative_claims,
    contract_source_claims,
    contradicts_negative,
)
from contextedge.quality.polarity import describe_conflict
from contextedge.quality.states import (
    CATEGORY_CONTRADICTED_CLAIM,
    CATEGORY_UNSUPPORTED_CLAIM,
    CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
    DIM_EVIDENCE_GROUNDING,
    SEVERITY_INFO,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    STATE_INCONCLUSIVE,
    STATE_PASS,
)

VALIDATOR = "lexical_support"

# Calibrated conservative: under-counting is inconclusive, over-counting is fail.
_ENTAILED = 0.30
_PARTIAL = 0.16
_CONTRADICTED = 0.32


def _instruction(step: dict) -> str:
    for key in ("text", "title", "action", "instruction", "description"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    contract = context.contract or {}
    sources = contract_source_claims(contract)
    negatives = contract_negative_claims(contract)

    grounded_steps = [
        (index, step)
        for index, step in enumerate(context.steps, start=1)
        if step.get("grounding_status") == "grounded" and (step.get("source_refs") or [])
    ]

    if not grounded_steps:
        findings.append(
            Finding(
                category=CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
                dimension=DIM_EVIDENCE_GROUNDING,
                severity=SEVERITY_INFO,
                explanation="No grounded cited steps to evaluate for lexical support.",
                validator=VALIDATOR,
            )
        )
        return result_from_findings(
            findings, (DIM_EVIDENCE_GROUNDING,), default=STATE_INCONCLUSIVE
        )

    if not sources:
        findings.append(
            Finding(
                category=CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
                dimension=DIM_EVIDENCE_GROUNDING,
                severity=SEVERITY_INFO,
                explanation=(
                    "Grounded steps present but no source claims in the quality "
                    "contract — lexical support cannot run."
                ),
                validator=VALIDATOR,
            )
        )
        return result_from_findings(
            findings, (DIM_EVIDENCE_GROUNDING,), default=STATE_INCONCLUSIVE
        )

    evaluated = 0
    for index, step in grounded_steps:
        ref = str(step.get("step_id") or index)
        text = _instruction(step)
        if not text:
            continue

        neg_score, neg_text = contradicts_negative(text, negatives)
        if neg_score >= _CONTRADICTED and neg_text:
            findings.append(
                Finding(
                    category=CATEGORY_CONTRADICTED_CLAIM,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Step {ref} aligns with a known-failed action from sources: "
                        f"{neg_text[:180]}"
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    claim=text[:400],
                    contradicting_spans=[neg_text[:300]],
                    confidence=neg_score,
                    remediation_category="remove_or_reclassify",
                )
            )
            evaluated += 1
            continue

        conflict_score, conflict_text = best_polarity_conflict(text, sources)
        if conflict_score >= _ENTAILED and conflict_text:
            findings.append(
                Finding(
                    category=CATEGORY_CONTRADICTED_CLAIM,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Step {ref} matches a source claim almost word for word but "
                        f"reverses it: {describe_conflict(text, conflict_text)}."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    claim=text[:400],
                    contradicting_spans=[conflict_text[:300]],
                    confidence=conflict_score,
                    remediation_category="resolve_polarity_conflict",
                )
            )
            evaluated += 1
            continue

        score, matched = best_support_score(text, sources)
        if matched:
            score = max(score, combined_entailment_score(text, matched))
        evaluated += 1

        if score >= _ENTAILED:
            continue

        if score >= _PARTIAL:
            findings.append(
                Finding(
                    category=CATEGORY_UNSUPPORTED_CLAIM,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Step {ref} is only partially supported by source claims "
                        f"(score {score:.2f}). Review whether the paraphrase is faithful."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    claim=text[:400],
                    supporting_spans=[matched[:300]] if matched else [],
                    confidence=score,
                    remediation_category="verify_entailment",
                )
            )
            continue

        findings.append(
            Finding(
                category=CATEGORY_UNSUPPORTED_CLAIM,
                dimension=DIM_EVIDENCE_GROUNDING,
                severity=SEVERITY_MAJOR,
                explanation=(
                    f"Step {ref} is tagged grounded but does not lexically align "
                    "with any contract source claim."
                ),
                validator=VALIDATOR,
                target_kind="step",
                target_ref=ref,
                claim=text[:400],
                confidence=score,
                remediation_category="recite_or_reclassify",
            )
        )

    if evaluated == 0:
        return result_from_findings(
            findings, (DIM_EVIDENCE_GROUNDING,), default=STATE_INCONCLUSIVE
        )

    default = STATE_PASS if not any(
        f.severity in ("critical", "major") for f in findings
    ) else STATE_INCONCLUSIVE
    return result_from_findings(findings, (DIM_EVIDENCE_GROUNDING,), default=default)


register_validator(
    VALIDATOR,
    (DIM_EVIDENCE_GROUNDING,),
    validate,
    stage="C",
)
