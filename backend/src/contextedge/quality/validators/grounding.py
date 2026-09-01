"""Stage A — grounding integrity. Deterministic, no model calls.

This validator does *not* decide whether a source supports a claim; that is
Stage C and it is not built yet. What it decides is narrower and entirely
mechanical: whether the artifact's own grounding claims are internally
coherent, and whether any of them are known to be stale.

Two of these are live defects in the current code path rather than
hypotheticals.

**Edited grounded steps keep their citations.** ``services/playbook_editing.py``
lists ``source_refs``, ``grounding_status`` and ``evidence_quality`` in
``PROTECTED_KEYS``, so a client patch can never strip them. That is right — a
typed round-trip must not silently drop provenance. The side effect is that a
reviewer can rewrite a grounded step's instruction and the step keeps the
citations of the sentence it replaced, still reading as evidenced. The module
does set ``human_edited``, so the fact is recorded; nothing acts on it. This
does.

**Claimed grounding without citations.** ``classify_step_grounding`` forces the
tags at generation, so a generated step cannot be inconsistent. A hand-authored
step, an imported one, or one from a migration never went through it.
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
    CATEGORY_CITATION_UNRESOLVABLE,
    CATEGORY_STALE_GROUNDING,
    DIM_EVIDENCE_GROUNDING,
    SEVERITY_INFO,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    STATE_INCONCLUSIVE,
)

VALIDATOR = "grounding_integrity"


def validate(context: ValidationContext) -> ValidatorResult:
    findings: list[Finding] = []
    steps = context.steps

    for index, step in enumerate(steps, start=1):
        ref = str(step.get("step_id") or index)
        refs = step.get("source_refs") or []
        status = step.get("grounding_status")
        classification = step.get("step_classification")

        if status == "grounded" and not refs:
            findings.append(
                Finding(
                    category=CATEGORY_CITATION_UNRESOLVABLE,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Step {ref} is tagged grounded but cites nothing. The tag asserts "
                        "evidence a reviewer cannot open."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    remediation_category="recite_or_reclassify",
                )
            )

        if refs and status in (None, "non_grounded") and classification != "human_authored":
            findings.append(
                Finding(
                    category=CATEGORY_CITATION_UNRESOLVABLE,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Step {ref} carries citations but is tagged {status!r}. One of the "
                        "two is wrong and a filter on grounding_status will miss this step."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                )
            )

        if step.get("human_edited") and status == "grounded" and refs:
            findings.append(
                Finding(
                    category=CATEGORY_STALE_GROUNDING,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Step {ref} was edited by hand but kept the citations of the text it "
                        "replaced. The sources support the previous wording; whether they "
                        "support this one has not been established."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                    claim=str(step.get("text") or step.get("title") or "")[:500],
                    remediation_category="re_entail_or_reclassify",
                )
            )

    # Corpus-level observation, not a defect. Reported so the reviewer sees the
    # ratio the plan's minimality work will act on, and so the metric exists
    # before the validator that uses it.
    if steps:
        best_practice = sum(
            1 for step in steps if step.get("step_classification") == "best_practice"
        )
        if best_practice:
            findings.append(
                Finding(
                    category=CATEGORY_STALE_GROUNDING,
                    dimension=DIM_EVIDENCE_GROUNDING,
                    severity=SEVERITY_INFO,
                    explanation=(
                        f"{best_practice} of {len(steps)} steps are best-practice inference "
                        "rather than evidence. Reviewers rejected playbooks for exactly "
                        "these steps ('no need to check agent state', 'no need to check "
                        "log4j file'); the minimality validator decides, this only counts."
                    ),
                    validator=VALIDATOR,
                    target_kind="playbook",
                )
            )

    # Deliberately inconclusive, never pass. This validator proves the
    # grounding claims are self-consistent; it does not and cannot prove a
    # cited passage actually supports the step, which is the thing the
    # dimension is named after. Reporting `pass` here would be the exact
    # mistake the plan calls out in §4.2 — treating citation presence as
    # grounding — dressed up in a new table.
    return result_from_findings(findings, (DIM_EVIDENCE_GROUNDING,), default=STATE_INCONCLUSIVE)


register_validator(VALIDATOR, (DIM_EVIDENCE_GROUNDING,), validate, stage="A")
