"""Phase 1 foundation: hashing, revisions, states, and the validator cascade.

These are pure-function tests — no database, no session. The persistence layer
has its own integration tests; what is worth pinning down here is the logic
that everything else trusts:

- a title-only change produces a different content hash (the defect the whole
  design exists to close),
- an evaluator that fails cannot produce a pass,
- an unbuilt validator cannot produce a pass.

The metamorphic tests at the bottom are the plan's §18.2 invariants.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contextedge.quality import assess, build_content, error_outcome
from contextedge.quality.hashing import combine_hashes, content_hash, normalize
from contextedge.quality.registry import (
    Finding,
    ValidationContext,
    ValidatorResult,
    clear_registry,
    register_validator,
    registered_validators,
    result_from_findings,
)
from contextedge.quality.revision import (
    SHELL_QUALITY_FIELDS,
    compute_content_hash,
    summarize_change,
)
from contextedge.quality.states import (
    CATEGORY_EMPTY_PROCEDURE,
    CATEGORY_STALE_GROUNDING,
    CATEGORY_UNREACHABLE_STEP,
    CATEGORY_UNRESOLVABLE_BRANCH,
    CATEGORY_VALIDATOR_ERROR,
    CATEGORY_VALIDATOR_NOT_IMPLEMENTED,
    DIM_EVIDENCE_GROUNDING,
    DIM_STEP_ACCURACY,
    DIM_STRUCTURE,
    DIMENSIONS,
    STEP_DIMENSIONS,
    NON_PASSING_STATES,
    SEVERITY_CRITICAL,
    SEVERITY_MAJOR,
    STATE_ERROR,
    STATE_FAIL,
    STATE_INCONCLUSIVE,
    STATE_PASS,
    PANEL_GROUPS,
    coverage,
    group_states,
    resolve_overall,
    state_for_findings,
    structure_state,
    worse_state,
)
from contextedge.quality.validators import grounding, structural

# --------------------------------------------------------------------- helpers


def make_playbook(title="Restart the AutomationEdge Agent", description="d", **kwargs):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title=title,
        description=description,
        risk_tier=kwargs.get("risk_tier", "medium"),
        automation_mode=kwargs.get("automation_mode", "suggest_only"),
        domain_id=kwargs.get("domain_id"),
    )


def make_version(steps=None, **kwargs):
    return SimpleNamespace(
        id=uuid.uuid4(),
        semantic_version=kwargs.get("semantic_version", "0.1.0"),
        trigger_conditions=kwargs.get("trigger_conditions", {}),
        branching_logic=kwargs.get("branching_logic", {}),
        inputs=kwargs.get("inputs", []),
        outputs=kwargs.get("outputs", []),
        steps=steps if steps is not None else [_step(1)],
        rollback_notes=kwargs.get("rollback_notes"),
        evidence_refs=kwargs.get("evidence_refs"),
        conflicts=kwargs.get("conflicts"),
        generation_provenance=kwargs.get("generation_provenance"),
        playbook_confidence=kwargs.get("playbook_confidence"),
        execution_confidence_guidance=kwargs.get("execution_confidence_guidance"),
        verification_policy=kwargs.get("verification_policy"),
    )


def _step(order, **kwargs):
    step = {
        "step_id": kwargs.get("step_id", f"s{order}"),
        "order": order,
        "type": kwargs.get("type", "remediation"),
        "text": kwargs.get("text", f"Do thing {order}."),
    }
    step.update({k: v for k, v in kwargs.items() if k not in ("step_id", "type", "text")})
    return step


def context_for(playbook, version):
    content = build_content(playbook, version)
    return ValidationContext(
        content=content,
        content_hash=content_hash(content),
        playbook_id=str(playbook.id),
        tenant_id=str(playbook.tenant_id),
    )


# --------------------------------------------------------------------- hashing


def test_hash_is_stable_across_key_order():
    left = {"a": 1, "b": {"c": 2, "d": [3, 4]}}
    right = {"b": {"d": [3, 4], "c": 2}, "a": 1}
    assert content_hash(left) == content_hash(right)


def test_hash_is_order_sensitive_for_lists():
    # Step order is meaning, not presentation: a procedure that restarts
    # before it backs up is a different procedure.
    assert content_hash([1, 2]) != content_hash([2, 1])


def test_volatile_keys_are_excluded():
    base = {"text": "x"}
    noisy = {"text": "x", "updated_at": "2026-09-01T00:00:00Z", "revision": 7}
    assert content_hash(base) == content_hash(noisy)


def test_normalize_survives_non_json_types():
    value = normalize(
        {"id": uuid.uuid4(), "score": float("nan"), "deep": {"x": [1, {"y": 2}]}}
    )
    assert isinstance(value["id"], str)
    assert value["score"] is None
    content_hash(value)  # must not raise


def test_normalize_is_depth_limited():
    node: dict = {}
    cursor = node
    for _ in range(80):
        cursor["next"] = {}
        cursor = cursor["next"]
    assert "<max-depth>" in str(normalize(node))


def test_combine_hashes_distinguishes_none_from_empty():
    assert combine_hashes("a", None) != combine_hashes("a", "")


# -------------------------------------------------------------- content model


def test_title_only_change_changes_the_hash():
    """The defect the entire design exists to close.

    Title lives on the shell, steps live on the version. Any hash computed
    from the version alone is identical across this change, which is how an
    approved playbook keeps a passing verdict about a title it no longer has.
    """
    version = make_version()
    before = compute_content_hash(make_playbook(title="Old title"), version)
    after = compute_content_hash(make_playbook(title="New title"), version)
    assert before != after


def test_description_only_change_changes_the_hash():
    version = make_version()
    before = compute_content_hash(make_playbook(description="a"), version)
    after = compute_content_hash(make_playbook(description="b"), version)
    assert before != after


def test_step_edit_changes_the_hash():
    playbook = make_playbook()
    before = compute_content_hash(playbook, make_version([_step(1, text="Restart it.")]))
    after = compute_content_hash(playbook, make_version([_step(1, text="Reinstall it.")]))
    assert before != after


def test_editor_bookkeeping_does_not_change_the_hash():
    """Re-saving a draft unchanged must not mint a revision.

    If it did, a few no-op saves would bury the edit that mattered under
    identical history entries, and each one would invalidate a good
    assessment for nothing.
    """
    playbook = make_playbook()
    plain = make_version([_step(1)])
    noisy = make_version([_step(1, index=0, edited_at="2026-09-01T00:00:00Z")])
    assert compute_content_hash(playbook, plain) == compute_content_hash(playbook, noisy)


def test_human_edited_flag_does_change_the_hash():
    # It is a claim about the grounding, not bookkeeping.
    playbook = make_playbook()
    before = compute_content_hash(playbook, make_version([_step(1)]))
    after = compute_content_hash(playbook, make_version([_step(1, human_edited=True)]))
    assert before != after


def test_content_keys_are_pinned():
    """A drift guard, and the reason it exists.

    The first cut of the PATCH hook reassessed on title and description only,
    while ``build_content`` also hashed ``risk_tier`` and ``automation_mode`` —
    so patching a risk tier moved the content and left the old verdict
    attached to it. The hook now reads ``SHELL_QUALITY_FIELDS`` instead of
    repeating the names, and this test makes adding a field to the snapshot a
    conscious act: it fails until whoever added it decides which half the
    field belongs to.
    """
    content = build_content(make_playbook(), make_version())
    assert set(content) == SHELL_QUALITY_FIELDS | {
        "semantic_version",
        "trigger_conditions",
        "branching_logic",
        "inputs",
        "outputs",
        "steps",
        "rollback_notes",
        "evidence_refs",
        "conflicts",
        "generation_provenance",
        "playbook_confidence",
        "execution_confidence_guidance",
        "verification_policy",
    }


@pytest.mark.parametrize("field", sorted(SHELL_QUALITY_FIELDS))
def test_every_shell_quality_field_changes_the_hash(field):
    """Soundness: nothing is listed that does not actually matter.

    A field in this set that did not affect the hash would make the PATCH hook
    reassess for no reason, minting revisions and burying real edits.
    """
    version = make_version()
    before = make_playbook()
    after = make_playbook()
    setattr(after, field, "changed-value" if field != "domain_id" else uuid.uuid4())
    assert compute_content_hash(before, version) != compute_content_hash(after, version)


@pytest.mark.parametrize(
    "field,value",
    [
        ("playbook_confidence", 0.91),
        ("execution_confidence_guidance", "Run this only during a maintenance window."),
        ("verification_policy", {"require_screenshot": True}),
    ],
)
def test_operator_facing_version_fields_change_the_hash(field, value):
    playbook = make_playbook()
    before = make_version()
    after = make_version(**{field: value})
    assert compute_content_hash(playbook, before) != compute_content_hash(playbook, after)


def test_build_content_tolerates_a_versionless_shell():
    content = build_content(make_playbook(), None)
    assert content["steps"] == []
    assert content["title"]


def test_summarize_change_names_the_fields():
    left = build_content(make_playbook(title="A"), make_version())
    right = build_content(make_playbook(title="B"), make_version())
    assert summarize_change(left, right) == ["title"]
    assert summarize_change(None, right) == ["*"]


# ---------------------------------------------------------------------- states


def test_worst_state_wins_no_averaging():
    """Nine clean dimensions and one failure is a failure.

    A composite score would round this to healthy, which is exactly how a
    strong title comes to pay for incorrect steps.
    """
    states = {f"d{i}": STATE_PASS for i in range(9)}
    states["d9"] = STATE_FAIL
    assert resolve_overall(states) == STATE_FAIL


@pytest.mark.parametrize("state", [STATE_ERROR, STATE_INCONCLUSIVE, "stale"])
def test_non_pass_states_never_resolve_to_pass(state):
    assert resolve_overall({"a": STATE_PASS, "b": state}) == state
    assert state in NON_PASSING_STATES


def test_empty_dimension_map_is_inconclusive_not_pass():
    assert resolve_overall({}) == STATE_INCONCLUSIVE


def test_worse_state_is_symmetric():
    assert worse_state(STATE_PASS, STATE_FAIL) == worse_state(STATE_FAIL, STATE_PASS)


def test_minor_findings_do_not_fail_a_dimension():
    findings = [{"category": "x", "severity": "minor"}, {"category": "x", "severity": "info"}]
    assert state_for_findings(findings) == STATE_PASS


def test_blocking_severity_fails_a_dimension():
    assert state_for_findings([{"category": "x", "severity": SEVERITY_MAJOR}]) == STATE_FAIL


def test_not_implemented_category_is_inconclusive():
    findings = [{"category": CATEGORY_VALIDATOR_NOT_IMPLEMENTED, "severity": "info"}]
    assert state_for_findings(findings) == STATE_INCONCLUSIVE


def test_error_category_is_error():
    findings = [{"category": CATEGORY_VALIDATOR_ERROR, "severity": "major"}]
    assert state_for_findings(findings) == STATE_ERROR


def test_every_dimension_belongs_to_exactly_one_panel_group():
    """The guard for the bug this pins.

    ``structure`` was in ``DIMENSIONS`` but in none of the three groups, so a
    critical empty-procedure finding set the overall state to ``fail`` and then
    appeared under no heading a reviewer opens. Any new dimension that is not
    assigned is invisible in the same way, so assignment is now mandatory
    rather than remembered.
    """
    assigned: set[str] = set()
    for name, members in PANEL_GROUPS.items():
        overlap = assigned & members
        assert not overlap, f"{name} shares a dimension with another group: {sorted(overlap)}"
        assigned |= members
    assert assigned == set(DIMENSIONS), (
        f"unassigned: {sorted(set(DIMENSIONS) - assigned)}; "
        f"unknown: {sorted(assigned - set(DIMENSIONS))}"
    )


def test_structure_is_reported_outside_the_three_groups():
    """A precondition, not a peer.

    Structure failing must be visible on its own, and must not be silently
    folded into the step verdict where it would read as a step-quality problem.
    """
    states = {d: STATE_PASS for d in DIMENSIONS}
    states[DIM_STRUCTURE] = STATE_FAIL
    assert structure_state(states) == STATE_FAIL
    groups = group_states(states)
    assert groups == {"subject": STATE_PASS, "steps": STATE_PASS, "coherence": STATE_PASS}


def test_structure_is_none_when_not_evaluated():
    assert structure_state({DIM_STEP_ACCURACY: STATE_PASS}) is None


def test_an_empty_procedure_is_visible_as_a_structure_failure():
    """End-to-end for the case that motivated the partition guard."""
    outcome = assess(context_for(make_playbook(), make_version(steps=[])))
    assert structure_state(outcome.dimension_states) == STATE_FAIL
    assert CATEGORY_EMPTY_PROCEDURE in {f.category for f in outcome.findings}


def test_blocking_findings_includes_major_not_only_critical():
    """Regression: the property disagreed with the constant it is named after.

    ``state_for_findings`` fails a dimension on critical *or* major, so a list
    called `blocking_findings` that omitted major would have shown a reviewer
    a shorter list than the verdict was actually based on.
    """
    from contextedge.quality.orchestrator import AssessmentOutcome

    outcome = AssessmentOutcome(
        overall_state=STATE_FAIL,
        dimension_states={DIM_STRUCTURE: STATE_FAIL},
        findings=[
            Finding("c", DIM_STRUCTURE, SEVERITY_CRITICAL, "e", "v"),
            Finding("c", DIM_STRUCTURE, SEVERITY_MAJOR, "e", "v"),
            Finding("c", DIM_STRUCTURE, "minor", "e", "v"),
            Finding("c", DIM_STRUCTURE, "info", "e", "v"),
        ],
    )
    severities = {f.severity for f in outcome.blocking_findings}
    assert severities == {SEVERITY_CRITICAL, SEVERITY_MAJOR}


def test_blocking_findings_agrees_with_the_dimension_verdict():
    """The invariant behind the fix, rather than a restatement of the list.

    Anything that fails a dimension must appear in `blocking_findings`, and
    nothing that does not fail one may.
    """
    for severity in ("critical", "major", "minor", "info"):
        finding = Finding("c", DIM_STRUCTURE, severity, "e", "v")
        from contextedge.quality.orchestrator import AssessmentOutcome

        outcome = AssessmentOutcome(
            overall_state=STATE_FAIL, dimension_states={}, findings=[finding]
        )
        fails_dimension = state_for_findings([finding.as_dict()]) == STATE_FAIL
        assert bool(outcome.blocking_findings) is fails_dimension, severity


def test_group_states_are_independent():
    """The three decisions must not bleed into one another.

    A failing step dimension may not darken the subject verdict, and vice
    versa. This is the §1 requirement expressed as a test rather than as an
    intention.
    """
    states = {d: STATE_PASS for d in DIMENSIONS}
    states[DIM_STEP_ACCURACY] = STATE_FAIL
    groups = group_states(states)
    assert groups["steps"] == STATE_FAIL
    assert groups["subject"] == STATE_PASS
    assert groups["coherence"] == STATE_PASS


def test_group_is_none_when_nothing_in_it_was_evaluated():
    """Not evaluated is not the same as clean, at group level too."""
    groups = group_states({DIM_STRUCTURE: STATE_PASS})
    assert groups["subject"] is None
    assert groups["steps"] is None
    assert groups["coherence"] is None


def test_group_state_is_worst_wins_within_the_group():
    states = {d: STATE_PASS for d in STEP_DIMENSIONS}
    states[DIM_STEP_ACCURACY] = STATE_INCONCLUSIVE
    assert group_states(states)["steps"] == STATE_INCONCLUSIVE


def test_coverage_counts_only_decided_dimensions():
    """`inconclusive` and `error` are not decisions.

    This is what lets the panel say "6 of 14 checks run" instead of painting
    a warning badge on every playbook in the corpus.
    """
    states = {
        "a": STATE_PASS,
        "b": STATE_FAIL,
        "c": STATE_INCONCLUSIVE,
        "d": STATE_ERROR,
        "e": "stale",
    }
    assert coverage(states) == {"decided": 2, "undecided": 3, "total": 5}


def test_coverage_of_the_current_bundle_runs_real_validators():
    """Every dimension should be claimed; most should reach a pass/fail verdict."""
    outcome = assess(context_for(make_playbook(), make_version()))
    numbers = coverage(outcome.dimension_states)
    assert numbers["total"] == len(DIMENSIONS)
    assert numbers["decided"] > numbers["undecided"]


# ------------------------------------------------------------ structural stage


def test_empty_procedure_is_critical():
    result = structural.validate(context_for(make_playbook(), make_version(steps=[])))
    categories = {f.category for f in result.findings}
    assert CATEGORY_EMPTY_PROCEDURE in categories
    assert result.dimension_states[DIM_STRUCTURE] == STATE_FAIL


def test_empty_procedure_does_not_cascade_into_noise():
    """One missing array should produce one finding, not twenty consequences."""
    result = structural.validate(context_for(make_playbook(), make_version(steps=[])))
    assert len(result.findings) == 1


def test_missing_title_is_critical():
    playbook = make_playbook(title="")
    result = structural.validate(context_for(playbook, make_version()))
    assert any(f.severity == SEVERITY_CRITICAL and f.target_ref == "title" for f in result.findings)


def test_step_without_instruction_is_critical():
    version = make_version([{"step_id": "s1", "order": 1, "type": "remediation"}])
    result = structural.validate(context_for(make_playbook(), version))
    assert any(f.severity == SEVERITY_CRITICAL for f in result.findings)


def test_duplicate_step_ids_are_reported():
    version = make_version([_step(1, step_id="dup"), _step(2, step_id="dup")])
    result = structural.validate(context_for(make_playbook(), version))
    assert any("dup" in (f.explanation or "") for f in result.findings)


def test_branch_to_a_nonexistent_step_is_major():
    version = make_version(
        [_step(1), _step(2)],
        branching_logic={"decision_points": [{"after_step": 1, "if_true_goto": 9}]},
    )
    result = structural.validate(context_for(make_playbook(), version))
    assert CATEGORY_UNRESOLVABLE_BRANCH in {f.category for f in result.findings}


def test_unreachable_step_is_reported():
    # 1 branches to 3 or 4; nothing ever reaches 2.
    version = make_version(
        [_step(1), _step(2), _step(3), _step(4)],
        branching_logic={
            "decision_points": [{"after_step": 1, "if_true_goto": 3, "if_false_goto": 4}]
        },
    )
    result = structural.validate(context_for(make_playbook(), version))
    stranded = [f for f in result.findings if f.category == CATEGORY_UNREACHABLE_STEP]
    assert [f.target_ref for f in stranded] == ["2"]


def test_sibling_branches_do_not_report_a_false_unreachable():
    """Two decision points sharing an anchor is the switch shape.

    Judging points one at a time reports correct playbooks as broken; this is
    the regression that guards the traversal.
    """
    version = make_version(
        [_step(1), _step(2), _step(3)],
        branching_logic={
            "decision_points": [
                {"after_step": 1, "if_true_goto": 2, "if_false_goto": 3},
                {"after_step": 1, "if_true_goto": 3, "if_false_goto": 2},
            ]
        },
    )
    result = structural.validate(context_for(make_playbook(), version))
    assert not [f for f in result.findings if f.category == CATEGORY_UNREACHABLE_STEP]


def test_bare_prompt_label_citation_is_flagged():
    version = make_version([_step(1, source_refs=["kb-1"])])
    result = structural.validate(context_for(make_playbook(), version))
    assert any("kb-1" in (f.explanation or "") for f in result.findings)


def test_missing_verification_is_minor_not_blocking():
    version = make_version([_step(1), _step(2), _step(3)])
    result = structural.validate(context_for(make_playbook(), version))
    verification = [f for f in result.findings if f.category == "missing_verification"]
    assert verification and verification[0].severity == "minor"


def test_two_step_no_verification_shape_is_info_only():
    """The 'this is probably not a procedure' signal must not pre-empt the
    artifact-suitability decision it exists to feed."""
    version = make_version([_step(1), _step(2)])
    result = structural.validate(context_for(make_playbook(), version))
    signals = [f for f in result.findings if f.remediation_category == "reclassify_artifact"]
    assert signals and signals[0].severity == "info"


def test_two_step_with_escalation_is_not_flagged_as_a_non_procedure():
    """All three conditions, not two.

    A two-step playbook that routes to a human is doing real procedural work
    even with nothing to verify. The signal's only value is its precision, so
    flagging this would dilute it.
    """
    version = make_version([_step(1), _step(2, type="escalation")])
    result = structural.validate(context_for(make_playbook(), version))
    assert not [f for f in result.findings if f.remediation_category == "reclassify_artifact"]


def test_three_steps_no_verification_is_not_the_suitability_signal():
    version = make_version([_step(1), _step(2), _step(3)])
    result = structural.validate(context_for(make_playbook(), version))
    assert not [f for f in result.findings if f.remediation_category == "reclassify_artifact"]


def test_a_well_formed_playbook_passes_structure():
    version = make_version(
        [_step(1), _step(2, type="verification", text="Confirm the agent shows Running.")]
    )
    result = structural.validate(context_for(make_playbook(), version))
    assert result.dimension_states[DIM_STRUCTURE] == STATE_PASS


# -------------------------------------------------------------- grounding stage


def test_grounded_step_without_citations_is_major():
    version = make_version([_step(1, grounding_status="grounded", source_refs=[])])
    result = grounding.validate(context_for(make_playbook(), version))
    assert any(f.severity == SEVERITY_MAJOR for f in result.findings)


def test_edited_grounded_step_is_stale_grounding():
    """The live hole in services/playbook_editing.py.

    PROTECTED_KEYS keeps source_refs across a merge, so a rewritten grounded
    step keeps the citations of the sentence it replaced and still reads as
    evidenced.
    """
    version = make_version(
        [
            _step(
                1,
                grounding_status="grounded",
                source_refs=[{"id": "abc", "kind": "knowledge"}],
                human_edited=True,
                text="Replace the GUI automation JAR with version 4.5.",
            )
        ]
    )
    result = grounding.validate(context_for(make_playbook(), version))
    stale = [f for f in result.findings if f.category == CATEGORY_STALE_GROUNDING]
    assert stale and stale[0].severity == SEVERITY_MAJOR
    assert stale[0].claim.startswith("Replace the GUI")


def test_unedited_grounded_step_is_not_stale():
    version = make_version(
        [_step(1, grounding_status="grounded", source_refs=[{"id": "abc"}])]
    )
    result = grounding.validate(context_for(make_playbook(), version))
    assert not [
        f
        for f in result.findings
        if f.category == CATEGORY_STALE_GROUNDING and f.severity == SEVERITY_MAJOR
    ]


def test_grounding_never_reports_pass():
    """It proves the claims are self-consistent, not that a source supports
    them. Reporting `pass` here would be §4.2's mistake in a new table."""
    version = make_version([_step(1, grounding_status="non_grounded", source_refs=[])])
    result = grounding.validate(context_for(make_playbook(), version))
    assert result.dimension_states[DIM_EVIDENCE_GROUNDING] == STATE_INCONCLUSIVE


# ---------------------------------------------------------------- orchestrator


def test_assessment_covers_every_dimension():
    from contextedge.quality.states import DIMENSIONS

    outcome = assess(context_for(make_playbook(), make_version()))
    assert set(outcome.dimension_states) >= set(DIMENSIONS)


def test_unbuilt_validators_prevent_a_pass():
    """Phase 1 cannot pass anything, and that is the correct behaviour.

    A quality system whose unbuilt validators default to clean reports a
    corpus as healthy in exact proportion to how little of it was checked.
    """
    outcome = assess(context_for(make_playbook(), make_version()))
    assert outcome.overall_state != STATE_PASS
    assert outcome.overall_state in NON_PASSING_STATES


def test_a_broken_validator_cannot_produce_a_pass():
    clear_registry()
    try:

        def boom(context):
            raise RuntimeError("evaluator exploded")

        register_validator("boom", (DIM_STRUCTURE,), boom)
        outcome = assess(context_for(make_playbook(), make_version()))
        assert outcome.dimension_states[DIM_STRUCTURE] == STATE_ERROR
        assert outcome.overall_state == STATE_ERROR
        assert CATEGORY_VALIDATOR_ERROR in {f.category for f in outcome.findings}
    finally:
        clear_registry()
        _reregister_bundle()


def test_a_broken_validator_does_not_stop_the_others():
    clear_registry()
    try:

        def boom(context):
            raise RuntimeError("nope")

        def fine(context):
            return result_from_findings([], (DIM_EVIDENCE_GROUNDING,))

        register_validator("boom", (DIM_STRUCTURE,), boom)
        register_validator("fine", (DIM_EVIDENCE_GROUNDING,), fine)
        outcome = assess(context_for(make_playbook(), make_version()))
        assert outcome.dimension_states[DIM_STRUCTURE] == STATE_ERROR
        assert outcome.dimension_states[DIM_EVIDENCE_GROUNDING] == STATE_PASS
    finally:
        clear_registry()
        _reregister_bundle()


def test_validator_error_severity_is_major_not_critical():
    """Our evaluator broke; that is not evidence the playbook is dangerous.
    The `error` state already prevents a pass without sending reviewers to
    hunt a defect in content that may be fine."""
    clear_registry()
    try:
        register_validator(
            "boom", (DIM_STRUCTURE,), lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))
        )
        outcome = assess(context_for(make_playbook(), make_version()))
        errors = [f for f in outcome.findings if f.category == CATEGORY_VALIDATOR_ERROR]
        assert errors and all(f.severity == SEVERITY_MAJOR for f in errors)
    finally:
        clear_registry()
        _reregister_bundle()


def test_error_outcome_is_error_across_the_board():
    outcome = error_outcome("could not load version")
    assert outcome.overall_state == STATE_ERROR
    assert set(outcome.dimension_states.values()) == {STATE_ERROR}


def test_registry_refuses_duplicate_names():
    clear_registry()
    try:
        register_validator("dup", (DIM_STRUCTURE,), lambda ctx: result_from_findings([], ()))
        with pytest.raises(ValueError):
            register_validator("dup", (DIM_STRUCTURE,), lambda ctx: result_from_findings([], ()))
    finally:
        clear_registry()
        _reregister_bundle()


# ----------------------------------------------------- metamorphic (plan §18.2)


def test_changing_only_the_title_cannot_change_step_findings():
    """The independence invariant, tested rather than asserted."""
    version = make_version([_step(1, grounding_status="grounded", source_refs=[])])
    before = grounding.validate(context_for(make_playbook(title="A"), version))
    after = grounding.validate(context_for(make_playbook(title="B"), version))
    assert [f.as_dict() for f in before.findings] == [f.as_dict() for f in after.findings]


def test_changing_only_steps_does_not_change_structural_title_findings():
    playbook = make_playbook(title="")
    left = structural.validate(context_for(playbook, make_version([_step(1)])))
    right = structural.validate(context_for(playbook, make_version([_step(1), _step(2)])))
    title_findings = lambda r: [f.as_dict() for f in r.findings if f.target_ref == "title"]  # noqa: E731
    assert title_findings(left) == title_findings(right)


def test_reordering_a_dependency_pair_changes_the_hash():
    playbook = make_playbook()
    forward = make_version([_step(1, text="Back up the config."), _step(2, text="Restart.")])
    reversed_ = make_version([_step(1, text="Restart."), _step(2, text="Back up the config.")])
    assert compute_content_hash(playbook, forward) != compute_content_hash(playbook, reversed_)


def test_adding_a_step_changes_the_hash():
    playbook = make_playbook()
    before = compute_content_hash(playbook, make_version([_step(1)]))
    after = compute_content_hash(playbook, make_version([_step(1), _step(2)]))
    assert before != after


def _reregister_bundle() -> None:
    """Re-import the bundle after a registry-clearing test."""
    import importlib

    from contextedge.quality import validators as bundle

    for name in bundle.__all__:
        importlib.reload(importlib.import_module(f"contextedge.quality.validators.{name}"))
    importlib.reload(bundle)


def test_bundle_is_registered():
    assert {v.name for v in registered_validators()} >= {
        "structural",
        "grounding_integrity",
        "artifact_suitability",
        "subject_truth",
        "step_quality",
        "contract_completeness",
        "cross_content_coherence",
        "safety_policy",
        "minimality",
        "duplicate_status",
    }


def test_finding_round_trips_to_a_dict():
    finding = Finding(
        category="x",
        dimension=DIM_STRUCTURE,
        severity="minor",
        explanation="e",
        validator="v",
    )
    payload = finding.as_dict()
    assert payload["supporting_spans"] == [] and payload["target_kind"] == "playbook"


def test_validator_result_groups_by_dimension():
    findings = [
        Finding("c", DIM_STRUCTURE, SEVERITY_MAJOR, "e", "v"),
        Finding("c", DIM_EVIDENCE_GROUNDING, "info", "e", "v"),
    ]
    result: ValidatorResult = result_from_findings(
        findings, (DIM_STRUCTURE, DIM_EVIDENCE_GROUNDING)
    )
    assert result.dimension_states[DIM_STRUCTURE] == STATE_FAIL
    assert result.dimension_states[DIM_EVIDENCE_GROUNDING] == STATE_PASS
