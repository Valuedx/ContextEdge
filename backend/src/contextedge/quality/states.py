"""The vocabulary of a quality decision: states, dimensions, severities,
generic finding categories, and the rule that combines them.

Two things here carry the weight of the whole design.

**Six states, not a boolean.** A boolean cannot distinguish "we checked and it
is fine" from "the evaluator crashed", "the evidence does not let us decide",
or "this was fine before the source article changed underneath it". Every one
of those three has previously been read as success somewhere in some system,
and each is a different kind of wrong. ``resolve_overall`` is written so that
error, inconclusive and stale can never produce ``pass``.

**No composite score.** ``resolve_overall`` takes the worst state across
dimensions; it does not average them. A correct title cannot pay for incorrect
steps, and correct steps cannot pay for a misleading title — a reviewer reading
one aggregated number has no way to see which half is broken. Scores are for
dashboards and prioritisation; the blocking decision stays per-dimension.
"""

from __future__ import annotations

# --- assessment states ------------------------------------------------------

STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_INCONCLUSIVE = "inconclusive"
STATE_ERROR = "error"
STATE_STALE = "stale"
STATE_OVERRIDDEN = "overridden"

ASSESSMENT_STATES: tuple[str, ...] = (
    STATE_PASS,
    STATE_FAIL,
    STATE_INCONCLUSIVE,
    STATE_ERROR,
    STATE_STALE,
    STATE_OVERRIDDEN,
)

# States that are NOT a pass. Written as an explicit set rather than
# "!= pass" so a future seventh state has to be classified deliberately
# instead of defaulting to acceptable.
NON_PASSING_STATES: frozenset[str] = frozenset(
    {STATE_FAIL, STATE_INCONCLUSIVE, STATE_ERROR, STATE_STALE}
)

# Severity order of states when combining dimensions. Higher wins.
# `overridden` sits above pass and below fail: an override is a human
# accepting a known defect, which is materially different from clean.
_STATE_RANK: dict[str, int] = {
    STATE_PASS: 0,
    STATE_OVERRIDDEN: 1,
    STATE_STALE: 2,
    STATE_INCONCLUSIVE: 3,
    STATE_ERROR: 4,
    STATE_FAIL: 5,
}


# --- dimensions (plan §5.1) -------------------------------------------------

DIM_ARTIFACT_SUITABILITY = "artifact_suitability"
DIM_SUBJECT_TRUTH = "subject_truth"
DIM_SUBJECT_SPECIFICITY = "subject_specificity"
DIM_STEP_ACCURACY = "step_accuracy"
DIM_STEP_COMPLETENESS = "step_completeness"
DIM_STEP_EXECUTABILITY = "step_executability"
DIM_STEP_ORDERING = "step_ordering"
DIM_STEP_CONSISTENCY = "step_consistency"
DIM_EVIDENCE_GROUNDING = "evidence_grounding"
DIM_SAFETY_POLICY = "safety_policy"
DIM_MINIMALITY = "minimality"
DIM_COHERENCE = "cross_content_coherence"
DIM_DUPLICATE_STATUS = "duplicate_status"
DIM_STRUCTURE = "structure"

DIMENSIONS: tuple[str, ...] = (
    DIM_STRUCTURE,
    DIM_ARTIFACT_SUITABILITY,
    DIM_SUBJECT_TRUTH,
    DIM_SUBJECT_SPECIFICITY,
    DIM_STEP_ACCURACY,
    DIM_STEP_COMPLETENESS,
    DIM_STEP_EXECUTABILITY,
    DIM_STEP_ORDERING,
    DIM_STEP_CONSISTENCY,
    DIM_EVIDENCE_GROUNDING,
    DIM_SAFETY_POLICY,
    DIM_MINIMALITY,
    DIM_COHERENCE,
    DIM_DUPLICATE_STATUS,
)

# The three independent decisions the plan requires (§1). Grouped for the
# reviewer UI so a panel can show "subject / steps / coherence" separately
# rather than one number.
SUBJECT_DIMENSIONS: frozenset[str] = frozenset(
    {DIM_SUBJECT_TRUTH, DIM_SUBJECT_SPECIFICITY, DIM_ARTIFACT_SUITABILITY}
)
STEP_DIMENSIONS: frozenset[str] = frozenset(
    {
        DIM_STEP_ACCURACY,
        DIM_STEP_COMPLETENESS,
        DIM_STEP_EXECUTABILITY,
        DIM_STEP_ORDERING,
        DIM_STEP_CONSISTENCY,
        DIM_EVIDENCE_GROUNDING,
        DIM_SAFETY_POLICY,
        DIM_MINIMALITY,
    }
)
COHERENCE_DIMENSIONS: frozenset[str] = frozenset({DIM_COHERENCE, DIM_DUPLICATE_STATUS})

# Structure is deliberately NOT a fourth peer group.
#
# The plan commits to three independent decisions, and structure is not a
# fourth opinion about the playbook — it is the precondition under which the
# other three mean anything. A playbook with no steps has no step quality to
# assess and no coherence to check; the structural validator already returns
# early in that case rather than emitting twenty consequential findings.
#
# Rendering it as a peer tab would invite a reviewer to read "subject: pass"
# off an artifact that has no procedure in it. It belongs above the three, as
# a gate: fix this, then the three verdicts are worth reading.
STRUCTURE_DIMENSIONS: frozenset[str] = frozenset({DIM_STRUCTURE})

# Every dimension belongs to exactly one of these, and the panel renders one
# heading per entry. Pinned by test: an unassigned dimension is invisible,
# which is how a critical empty-procedure finding could set the overall state
# to `fail` and then appear under no heading a reviewer opens.
#
# `structure` is first because it is a precondition rather than a peer — see
# `structure_state`.
PANEL_GROUPS: dict[str, frozenset[str]] = {
    "structure": STRUCTURE_DIMENSIONS,
    "subject": SUBJECT_DIMENSIONS,
    "steps": STEP_DIMENSIONS,
    "coherence": COHERENCE_DIMENSIONS,
}


# --- severities -------------------------------------------------------------

SEVERITY_CRITICAL = "critical"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"
SEVERITY_INFO = "info"

SEVERITIES: tuple[str, ...] = (
    SEVERITY_CRITICAL,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
    SEVERITY_INFO,
)

# Severities that make a dimension fail. `minor` and `info` are recorded for
# the reviewer and the metrics without failing the dimension.
BLOCKING_SEVERITIES: frozenset[str] = frozenset({SEVERITY_CRITICAL, SEVERITY_MAJOR})

TARGET_KINDS: tuple[str, ...] = ("playbook", "field", "step")


# --- policy decisions -------------------------------------------------------

POLICY_DECISIONS: tuple[str, ...] = (
    "allowed",
    "prohibited",
    # Not in the original plan. Added because the actual objection in the
    # AutomationEdge review is "we not suggest to change JAR" — a preference
    # with an alternative, not a prohibition. See models/playbook_quality.py.
    "discouraged",
    "requires_evidence",
    "requires_approval",
    "requires_conditions",
    "requires_rollback",
    "requires_role",
)


# --- generic finding categories ---------------------------------------------
#
# Failure semantics only. "wrong_jar_advice" would be a category derived from
# one review sheet; "policy_prohibited_action" survives the next corpus.

CATEGORY_MISSING_REQUIRED_FIELD = "missing_required_field"
CATEGORY_INVALID_STRUCTURE = "invalid_structure"
CATEGORY_DUPLICATE_STEP_IDENTITY = "duplicate_step_identity"
CATEGORY_UNRESOLVABLE_BRANCH = "unresolvable_branch"
CATEGORY_UNREACHABLE_STEP = "unreachable_step"
CATEGORY_EMPTY_PROCEDURE = "empty_procedure"
CATEGORY_OVERSIZED_ARTIFACT = "oversized_artifact"

CATEGORY_UNSUPPORTED_CLAIM = "unsupported_claim"
CATEGORY_CONTRADICTED_CLAIM = "contradicted_claim"
CATEGORY_UNSUPPORTED_SPECIFICITY = "unsupported_specificity"
CATEGORY_STALE_GROUNDING = "stale_grounding"
CATEGORY_CITATION_UNRESOLVABLE = "citation_unresolvable"

CATEGORY_SUBJECT_OVERBROAD = "subject_overbroad"
CATEGORY_SUBJECT_MULTIPLE = "subject_multiple_subjects"
CATEGORY_SUBJECT_MISMATCH = "subject_step_mismatch"
CATEGORY_TERMINOLOGY_NONCANONICAL = "terminology_noncanonical"

CATEGORY_MISSING_OBLIGATION = "missing_contract_obligation"
CATEGORY_MISSING_VERIFICATION = "missing_verification"
CATEGORY_MISSING_ROLLBACK = "missing_rollback"
CATEGORY_ORDERING_VIOLATION = "ordering_violation"
CATEGORY_INSUFFICIENT_DETAIL = "insufficient_detail"

CATEGORY_REDUNDANT_STEP = "redundant_step"
CATEGORY_NO_UTILITY_STEP = "no_utility_step"

CATEGORY_POLICY_PROHIBITED = "policy_prohibited_action"
CATEGORY_POLICY_DISCOURAGED = "policy_discouraged_action"
CATEGORY_POLICY_UNMET_CONDITION = "policy_unmet_condition"

CATEGORY_WRONG_ARTIFACT_TYPE = "wrong_artifact_type"
CATEGORY_DUPLICATE_ARTIFACT = "duplicate_artifact"
CATEGORY_EVIDENCE_INSUFFICIENT = "evidence_insufficient"

# Not a defect in the content — a defect in our ability to judge it. Recorded
# as a finding so an assessment can never look clean merely because a
# validator was not built yet.
CATEGORY_VALIDATOR_NOT_IMPLEMENTED = "validator_not_implemented"
CATEGORY_VALIDATOR_ERROR = "validator_error"


def _combine(dimension_states: dict[str, str], members: frozenset[str]) -> str | None:
    """Worst-wins across a subset. ``None`` when none of it was evaluated."""
    present = [dimension_states[d] for d in members if d in dimension_states]
    if not present:
        return None
    state = STATE_PASS
    for item in present:
        state = worse_state(state, item)
    return state


def group_states(dimension_states: dict[str, str]) -> dict[str, str | None]:
    """The three independent decisions, for the reviewer panel.

    Computed here rather than in the API layer so there is exactly one
    definition of "what is the subject verdict". Worst-wins within each group,
    same as overall — a group is only as good as its weakest dimension.

    ``None`` for a group none of whose dimensions were evaluated, which is not
    the same as a group that came back clean. The caller must render those
    differently; collapsing them is the mistake this whole module exists to
    prevent.

    Structure is not here on purpose — see ``structure_state``.
    """
    return {
        name: _combine(dimension_states, members)
        for name, members in PANEL_GROUPS.items()
        if name != "structure"
    }


def structure_state(dimension_states: dict[str, str]) -> str | None:
    """Whether the artifact is well-formed enough for the rest to mean anything.

    Served alongside ``group_states`` rather than inside it, because it is a
    precondition rather than a fourth opinion: an empty procedure, duplicate
    step identities or a branch pointing at a step that does not exist all
    make the subject and coherence verdicts moot rather than merely
    accompanying them.

    Render it above the three groups, not as a peer tab.
    """
    return _combine(dimension_states, STRUCTURE_DIMENSIONS)


def coverage(dimension_states: dict[str, str]) -> dict[str, int]:
    """How much of the artifact was actually judged.

    In the current validator bundle most dimensions come back
    ``inconclusive`` because their validators are not built yet, so the
    overall state is not a verdict — it is mostly a statement about our own
    coverage. A UI that paints that as a warning badge on every playbook will
    teach reviewers to ignore the badge before it ever means anything.

    Serving the counts lets the panel say "6 of 14 checks run" instead, and
    hold the verdict back until the number is worth showing.
    """
    decided = sum(
        1
        for state in dimension_states.values()
        if state in (STATE_PASS, STATE_FAIL, STATE_OVERRIDDEN)
    )
    return {
        "decided": decided,
        "undecided": len(dimension_states) - decided,
        "total": len(dimension_states),
    }


def worse_state(left: str, right: str) -> str:
    """The more serious of two states."""
    if _STATE_RANK.get(right, 0) > _STATE_RANK.get(left, 0):
        return right
    return left


def resolve_overall(dimension_states: dict[str, str]) -> str:
    """Combine per-dimension states into one overall state.

    Worst-wins, never averaged. An empty map is ``inconclusive``, not
    ``pass``: assessing nothing is not the same as finding nothing wrong.
    """
    if not dimension_states:
        return STATE_INCONCLUSIVE
    overall = STATE_PASS
    for state in dimension_states.values():
        overall = worse_state(overall, state)
    return overall


def state_for_findings(findings: list[dict], *, default: str = STATE_PASS) -> str:
    """State implied by a validator's findings.

    A blocking-severity finding fails the dimension. A validator that could
    not decide reports ``CATEGORY_VALIDATOR_NOT_IMPLEMENTED`` or
    ``CATEGORY_VALIDATOR_ERROR`` and gets ``inconclusive`` / ``error`` — which
    ``resolve_overall`` then refuses to let become a pass.
    """
    state = default
    for finding in findings:
        category = finding.get("category")
        if category == CATEGORY_VALIDATOR_ERROR:
            state = worse_state(state, STATE_ERROR)
        elif category == CATEGORY_VALIDATOR_NOT_IMPLEMENTED:
            state = worse_state(state, STATE_INCONCLUSIVE)
        elif finding.get("severity") in BLOCKING_SEVERITIES:
            state = worse_state(state, STATE_FAIL)
    return state
