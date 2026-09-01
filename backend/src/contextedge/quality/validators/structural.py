"""Stage A — deterministic structure. No model calls, no I/O.

This is the only fully-implemented validator in the Phase 1 bundle, and it is
deliberately the cheap one: every check here is decidable from the content
alone, runs in microseconds, and is never wrong for a reason a reviewer would
have to argue about.

Several of these checks already exist elsewhere in the codebase and fire at
different moments — ``sanitize_branching_logic`` repairs branch defects at
generation, ``transition_playbook`` rejects a stepless version at review entry,
``validate_steps`` bounds a draft edit. Running them again here is not
redundancy: those are enforcement points on three different paths, and none of
them records what it found. This one produces findings against a content hash,
which is what makes "was this ever checked, and what did it say?" answerable.
"""

from __future__ import annotations

import json

from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    register_validator,
    result_from_findings,
)
from contextedge.quality.states import (
    CATEGORY_DUPLICATE_STEP_IDENTITY,
    CATEGORY_EMPTY_PROCEDURE,
    CATEGORY_INVALID_STRUCTURE,
    CATEGORY_MISSING_REQUIRED_FIELD,
    CATEGORY_MISSING_VERIFICATION,
    CATEGORY_OVERSIZED_ARTIFACT,
    CATEGORY_UNREACHABLE_STEP,
    CATEGORY_UNRESOLVABLE_BRANCH,
    DIM_STEP_COMPLETENESS,
    DIM_STEP_ORDERING,
    DIM_STRUCTURE,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

VALIDATOR = "structural"

# Mirrors services/playbook_editing.py so the two cannot disagree about what
# an oversized artifact is.
MAX_STEPS = 100
MAX_STEPS_BYTES = 512 * 1024
MAX_INSTRUCTION_CHARS = 4_000

_INSTRUCTION_KEYS = ("text", "title", "description", "action", "instruction")

# A step that verifies something. Deliberately checked on the declared type and
# on the presence of an expected outcome, not on the wording — "verify" in the
# prose of a remediation step does not make it a verification step.
_VERIFICATION_TYPES = frozenset({"verification", "check", "validate"})


def _instruction(step: dict) -> str:
    for key in _INSTRUCTION_KEYS:
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _branch_findings(content: dict, orders: set[int]) -> list[Finding]:
    """Branch targets that resolve to nothing, and steps nothing reaches.

    Same traversal as ``ai/generators/playbook_generator._unreachable_orders``:
    several decision points can share one anchor, so a step one point jumps
    over is often reached by its sibling's branch. Judging points one at a time
    reports correct playbooks as broken.
    """
    findings: list[Finding] = []
    branching = content.get("branching_logic")
    if not isinstance(branching, dict):
        return findings
    points = branching.get("decision_points")
    if not isinstance(points, list):
        return findings

    valid_points: list[dict] = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            findings.append(
                Finding(
                    category=CATEGORY_INVALID_STRUCTURE,
                    dimension=DIM_STRUCTURE,
                    severity=SEVERITY_MINOR,
                    explanation=f"decision_points[{index}] is not an object.",
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref=f"branching_logic.decision_points[{index}]",
                )
            )
            continue
        anchor = point.get("after_step")
        targets = [point.get("if_true_goto"), point.get("if_false_goto")]
        unknown = [t for t in targets if t is not None and t not in orders]
        if anchor not in orders:
            findings.append(
                Finding(
                    category=CATEGORY_UNRESOLVABLE_BRANCH,
                    dimension=DIM_STEP_ORDERING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Decision point {index} is anchored on step order {anchor!r}, "
                        "which is not a step in this playbook."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref=f"branching_logic.decision_points[{index}].after_step",
                )
            )
            continue
        if unknown:
            findings.append(
                Finding(
                    category=CATEGORY_UNRESOLVABLE_BRANCH,
                    dimension=DIM_STEP_ORDERING,
                    severity=SEVERITY_MAJOR,
                    explanation=(
                        f"Decision point {index} branches to step order(s) {unknown!r}, "
                        "which do not exist. An operator following this path stops here."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref=f"branching_logic.decision_points[{index}]",
                )
            )
            continue
        if point.get("if_true_goto") is not None and (
            point.get("if_true_goto") == point.get("if_false_goto")
        ):
            findings.append(
                Finding(
                    category=CATEGORY_INVALID_STRUCTURE,
                    dimension=DIM_STEP_ORDERING,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Decision point {index} sends both outcomes to the same step, so "
                        "the condition decides nothing but reads as a real choice."
                    ),
                    validator=VALIDATOR,
                    target_kind="field",
                    target_ref=f"branching_logic.decision_points[{index}]",
                )
            )
            continue
        valid_points.append(point)

    stranded = _unreachable(orders, valid_points)
    for order in sorted(stranded):
        findings.append(
            Finding(
                category=CATEGORY_UNREACHABLE_STEP,
                dimension=DIM_STEP_ORDERING,
                severity=SEVERITY_MAJOR,
                explanation=(
                    f"No execution path reaches step {order}. The step is printed and read "
                    "but never performed."
                ),
                validator=VALIDATOR,
                target_kind="step",
                target_ref=str(order),
            )
        )
    return findings


def _unreachable(orders: set[int], points: list[dict]) -> set[int]:
    if not orders:
        return set()
    by_anchor: dict[int, set[int]] = {}
    for point in points:
        anchor = point.get("after_step")
        if anchor not in orders:
            continue
        targets = {
            t for t in (point.get("if_true_goto"), point.get("if_false_goto")) if t in orders
        }
        by_anchor.setdefault(anchor, set()).update(targets)

    start = min(orders)
    reached, worklist = {start}, [start]
    while worklist:
        step = worklist.pop()
        successors = by_anchor.get(step)
        if not successors:
            successors = {step + 1} if (step + 1) in orders else set()
        for nxt in successors:
            if nxt not in reached:
                reached.add(nxt)
                worklist.append(nxt)
    return orders - reached


def validate(context: ValidationContext) -> ValidatorResult:
    content = context.content
    findings: list[Finding] = []

    title = content.get("title")
    if not isinstance(title, str) or not title.strip():
        findings.append(
            Finding(
                category=CATEGORY_MISSING_REQUIRED_FIELD,
                dimension=DIM_STRUCTURE,
                severity=SEVERITY_CRITICAL,
                explanation="The playbook has no title.",
                validator=VALIDATOR,
                target_kind="field",
                target_ref="title",
            )
        )

    steps = context.steps
    if not steps:
        findings.append(
            Finding(
                category=CATEGORY_EMPTY_PROCEDURE,
                dimension=DIM_STRUCTURE,
                severity=SEVERITY_CRITICAL,
                explanation=(
                    "The playbook has no steps. There is nothing to review and nothing to "
                    "execute; a truncated generation response produces exactly this shape."
                ),
                validator=VALIDATOR,
                target_kind="field",
                target_ref="steps",
            )
        )
        # Everything below is about steps. Reporting twenty consequential
        # findings for one missing array buries the one that matters.
        return result_from_findings(
            findings, (DIM_STRUCTURE, DIM_STEP_ORDERING, DIM_STEP_COMPLETENESS)
        )

    if len(steps) > MAX_STEPS:
        findings.append(
            Finding(
                category=CATEGORY_OVERSIZED_ARTIFACT,
                dimension=DIM_STRUCTURE,
                severity=SEVERITY_MAJOR,
                explanation=f"{len(steps)} steps exceeds the {MAX_STEPS}-step limit.",
                validator=VALIDATOR,
                target_kind="field",
                target_ref="steps",
            )
        )

    encoded = len(json.dumps(steps, default=str).encode("utf-8"))
    if encoded > MAX_STEPS_BYTES:
        findings.append(
            Finding(
                category=CATEGORY_OVERSIZED_ARTIFACT,
                dimension=DIM_STRUCTURE,
                severity=SEVERITY_MAJOR,
                explanation=f"steps payload is {encoded} bytes, over the {MAX_STEPS_BYTES} limit.",
                validator=VALIDATOR,
                target_kind="field",
                target_ref="steps",
            )
        )

    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    orders: set[int] = set()

    for index, step in enumerate(steps, start=1):
        ref = str(step.get("step_id") or index)

        step_id = step.get("step_id")
        if isinstance(step_id, str) and step_id.strip():
            if step_id in seen_ids:
                findings.append(
                    Finding(
                        category=CATEGORY_DUPLICATE_STEP_IDENTITY,
                        dimension=DIM_STRUCTURE,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"step_id {step_id!r} appears more than once. Edits and findings "
                            "addressed to it are ambiguous."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                    )
                )
            seen_ids.add(step_id)

        order = step.get("order")
        if isinstance(order, int) and not isinstance(order, bool):
            if order in seen_orders:
                findings.append(
                    Finding(
                        category=CATEGORY_DUPLICATE_STEP_IDENTITY,
                        dimension=DIM_STEP_ORDERING,
                        severity=SEVERITY_MAJOR,
                        explanation=(
                            f"Two steps both claim order {order}. The execution sequence is "
                            "undefined."
                        ),
                        validator=VALIDATOR,
                        target_kind="step",
                        target_ref=ref,
                    )
                )
            seen_orders.add(order)
            orders.add(order)

        instruction = _instruction(step)
        if not instruction:
            findings.append(
                Finding(
                    category=CATEGORY_MISSING_REQUIRED_FIELD,
                    dimension=DIM_STRUCTURE,
                    severity=SEVERITY_CRITICAL,
                    explanation=f"Step {ref} has no instruction text.",
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                )
            )
        elif len(instruction) > MAX_INSTRUCTION_CHARS:
            findings.append(
                Finding(
                    category=CATEGORY_OVERSIZED_ARTIFACT,
                    dimension=DIM_STRUCTURE,
                    severity=SEVERITY_MINOR,
                    explanation=(
                        f"Step {ref} instruction is {len(instruction)} characters, over the "
                        f"{MAX_INSTRUCTION_CHARS} limit."
                    ),
                    validator=VALIDATOR,
                    target_kind="step",
                    target_ref=ref,
                )
            )

        # A citation that resolves to nothing is worse than none: it survives
        # review precisely because it looks like provenance. The generator
        # drops minted labels at creation, but nothing has ever checked a
        # hand-authored or edited step.
        refs = step.get("source_refs")
        if isinstance(refs, list):
            for entry in refs:
                if isinstance(entry, str):
                    findings.append(
                        Finding(
                            category=CATEGORY_INVALID_STRUCTURE,
                            dimension=DIM_STRUCTURE,
                            severity=SEVERITY_MINOR,
                            explanation=(
                                f"Step {ref} cites the bare label {entry!r}. Prompt labels "
                                "resolve to nothing once stored; a citation needs an id."
                            ),
                            validator=VALIDATOR,
                            target_kind="step",
                            target_ref=ref,
                        )
                    )
                elif isinstance(entry, dict) and not entry.get("id"):
                    findings.append(
                        Finding(
                            category=CATEGORY_INVALID_STRUCTURE,
                            dimension=DIM_STRUCTURE,
                            severity=SEVERITY_MINOR,
                            explanation=f"Step {ref} has a source_ref with no id.",
                            validator=VALIDATOR,
                            target_kind="step",
                            target_ref=ref,
                        )
                    )

    findings.extend(_branch_findings(content, orders))

    # Completeness signals, reported but not blocking. These are the two
    # shapes the corpus audit found most often (154 of 420 playbooks had a
    # verification step; 17 had an escalation step), and they are a useful
    # calibration anchor for the semantic completeness validator that
    # replaces them — not a substitute for it.
    has_verification = any(
        (step.get("type") in _VERIFICATION_TYPES) or step.get("verification")
        for step in steps
    )
    if not has_verification:
        findings.append(
            Finding(
                category=CATEGORY_MISSING_VERIFICATION,
                dimension=DIM_STEP_COMPLETENESS,
                severity=SEVERITY_MINOR,
                explanation=(
                    "No step verifies that the remediation worked. An operator finishes "
                    "without knowing whether the issue is resolved."
                ),
                validator=VALIDATOR,
                target_kind="playbook",
                remediation_category="add_verification",
            )
        )

    # Two steps, no verification, no escalation: on the reviewed corpus this
    # shape is overwhelmingly an informational note wearing a procedure's
    # clothing. Recorded at info severity as a suitability *signal* — the
    # artifact-type decision itself is Phase 2's, and this must not
    # pre-empt it.
    #
    # All three conditions, not two. A two-step playbook that routes to a
    # human is doing real procedural work even with nothing to verify, and
    # flagging it dilutes a signal whose only value is its precision.
    has_escalation = any(step.get("type") == "escalation" for step in steps)
    if len(steps) <= 2 and not has_verification and not has_escalation:
        findings.append(
            Finding(
                category=CATEGORY_INVALID_STRUCTURE,
                dimension=DIM_STRUCTURE,
                severity=SEVERITY_INFO,
                explanation=(
                    f"{len(steps)} steps, with no verification and no escalation. On the "
                    "reviewed corpus this shape is usually an informational or planning "
                    "note rather than a procedure; the artifact-suitability gate should "
                    "look at it."
                ),
                validator=VALIDATOR,
                target_kind="playbook",
                remediation_category="reclassify_artifact",
            )
        )

    return result_from_findings(
        findings, (DIM_STRUCTURE, DIM_STEP_ORDERING, DIM_STEP_COMPLETENESS)
    )


register_validator(
    VALIDATOR,
    (DIM_STRUCTURE, DIM_STEP_ORDERING, DIM_STEP_COMPLETENESS),
    validate,
    stage="A",
)
