"""Stage D — subject and title validation."""

from __future__ import annotations

from contextedge.quality.claim_match import (
    contract_subject_corpus,
    overlap_ratio,
    tokens,
)
from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_SUBJECT_MISMATCH,
    CATEGORY_SUBJECT_OVERBROAD,
    CATEGORY_TERMINOLOGY_NONCANONICAL,
    DIM_SUBJECT_SPECIFICITY,
    DIM_SUBJECT_TRUTH,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "subject_truth"


def _step_text(step: dict) -> str:
    for key in ("text", "title", "action", "instruction", "description"):
        val = step.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _ontology_terms(context: ValidationContext) -> list[dict]:
    return [t for t in context.ontology_terms if isinstance(t, dict)]


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    title = str(context.content.get("title") or "").strip()
    description = str(context.content.get("description") or "").strip()
    contract = context.contract or {}

    if not title:
        return result_from_findings(findings, (DIM_SUBJECT_TRUTH, DIM_SUBJECT_SPECIFICITY))

    subject_corpus = contract_subject_corpus(contract)
    step_blob = " ".join(_step_text(s) for s in context.steps)

    if subject_corpus:
        title_overlap = overlap_ratio(title, subject_corpus)
        step_overlap = overlap_ratio(step_blob, subject_corpus) if step_blob else 0.0
        # Title is materially less specific than the steps relative to the contract.
        if step_blob and step_overlap >= 0.2 and title_overlap < step_overlap * 0.45:
            findings.append(
                Finding(
                    category=CATEGORY_SUBJECT_OVERBROAD,
                    dimension=DIM_SUBJECT_SPECIFICITY,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        "Title is less specific than the procedure steps relative to "
                        "the source-derived contract subject."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref="title",
                    claim=title,
                )
            )

    subject = contract.get("primary_subject") or contract.get("failure_mode")
    if subject and overlap_ratio(title, str(subject)) < 0.12:
        findings.append(
            Finding(
                category=CATEGORY_SUBJECT_MISMATCH,
                dimension=DIM_SUBJECT_TRUTH,
                severity=SEVERITY_MINOR,
                explanation=(
                    "Title does not reflect the contract's primary subject or failure mode."
                ),
                validator=VALIDATOR,
                target_kind="field",
                target_ref="title",
                claim=title,
            )
        )

    ont = _ontology_terms(context)
    if ont:
        title_toks = tokens(title + " " + description)
        step_toks = tokens(step_blob)
        canonical = {t["canonical_term"].lower() for t in ont}
        aliases = {
            a.lower()
            for t in ont
            for a in (t.get("aliases") or [])
            if isinstance(a, str)
        }
        known = canonical | aliases
        step_components = step_toks & known
        title_components = title_toks & known
        if step_components and not title_components:
            findings.append(
                Finding(
                    category=CATEGORY_SUBJECT_MISMATCH,
                    dimension=DIM_SUBJECT_SPECIFICITY,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        "Steps name ontology terms the title does not mention."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref="title",
                )
            )
        for term in ont:
            canon = term.get("canonical_term", "")
            for alias in term.get("aliases") or []:
                if isinstance(alias, str) and alias.lower() in step_blob.lower():
                    if canon.lower() not in (title + description).lower():
                        findings.append(
                            Finding(
                                category=CATEGORY_TERMINOLOGY_NONCANONICAL,
                                dimension=DIM_SUBJECT_SPECIFICITY,
                                severity=SEVERITY_MINOR,
                                explanation=(
                                    f"Steps use alias {alias!r}; prefer canonical "
                                    f"{canon!r} in the subject line."
                                ),
                                validator=VALIDATOR,
                                target_kind="field",
                                target_ref="title",
                            )
                        )
                        break

    return result_from_findings(findings, (DIM_SUBJECT_TRUTH, DIM_SUBJECT_SPECIFICITY))


register_validator(
    VALIDATOR,
    (DIM_SUBJECT_TRUTH, DIM_SUBJECT_SPECIFICITY),
    validate,
    stage="D",
)
