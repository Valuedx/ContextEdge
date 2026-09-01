"""Clarification loop: gap detection, KB-first resolution, question generation,
and the property the whole feature rests on — that the loop terminates.

The tests that matter most here are not the shape tests. They are:

- ``test_answered_gap_does_not_produce_a_question_next_round`` — the loop is
  only useful if it converges. Without answer attestation the same defect
  produces the same ``gap_key`` forever and a reviewer is asked the same
  question until they stop using the feature.
- the polarity guards — a source that *forbids* what a gap asks about scores
  high on lexical overlap and would otherwise be handed to a reviewer as the
  answer.
- ``test_blocking_gap_stays_mandatory_whatever_the_model_says`` — a model that
  can mark its own blockers optional makes the badge decorative.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.quality.clarification.apply import (
    answers_payload,
    attest_answers_on_contract,
    merge_clarification_into_evidence_refs,
    skipped_payload,
    version_data_from_revision,
)
from contextedge.quality.clarification.gaps import (
    ANSWERABLE_CATEGORIES,
    InformationGap,
    compute_gap_key,
    detect_gaps,
    gaps_from_contract,
    gaps_from_gate,
    gaps_from_structure,
    normalize_claim,
)
from contextedge.quality.clarification.kb_resolution import (
    KB_UNRESOLVABLE_KINDS,
    resolve_from_context,
    resolve_from_knowledge,
    resolve_gaps,
)
from contextedge.quality.clarification.states import (
    KB_FAILED,
    KB_NO_RESULTS,
    MANDATORY,
    OPTIONAL,
    Q_ANSWERED,
    Q_OPEN,
    Q_SKIPPED,
    enforce_obligation,
    mandatory_outstanding,
    round_is_answerable,
)


def _finding(**over):
    base = {
        "id": "f1",
        "category": "missing_contract_obligation",
        "dimension": "step_completeness",
        "severity": "major",
        "target_kind": "playbook",
        "target_ref": None,
        "claim": "Restart the agent service after applying the patch",
        "explanation": "Contract required action is not reflected in any step.",
    }
    base.update(over)
    return base


def _question(**over):
    base = {
        "gap_key": "k1",
        "gap_kind": "missing_required_action",
        "question_text": "Which service must be restarted?",
        "obligation": OPTIONAL,
        "status": Q_OPEN,
        "answer_text": None,
        "answer_source": None,
        "answered_by": None,
        "answer_provenance": None,
        "target_kind": "playbook",
        "target_ref": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _section(text, ref="§2.1"):
    return SimpleNamespace(text=text, section_ref=ref, page=3, model_derived=False)


def _document(title, sections, evidence_id="e1"):
    return SimpleNamespace(
        evidence_id=evidence_id, title=title, sections=sections, evidence_type="sop"
    )


# --- gap identity -------------------------------------------------------------


def test_gap_key_survives_rewording_of_the_same_claim():
    """Round 2 recomputes gaps from rewritten content. If a re-worded claim
    minted a new key, the reviewer would be asked the same question again with
    their previous answer discarded."""
    a = compute_gap_key("missing_required_action", "playbook", None, "Restart the AE Server service.")
    b = compute_gap_key("missing_required_action", "playbook", None, "restart the ae server service")
    assert a == b


def test_gap_key_separates_genuinely_different_claims():
    a = compute_gap_key("missing_required_action", "playbook", None, "Restart the agent")
    b = compute_gap_key("missing_required_action", "playbook", None, "Restart the database")
    assert a != b


def test_gap_key_separates_the_same_claim_on_different_steps():
    a = compute_gap_key("insufficient_detail", "step", "s1", "Check the logs")
    b = compute_gap_key("insufficient_detail", "step", "s2", "Check the logs")
    assert a != b


def test_normalize_claim_is_bounded():
    assert len(normalize_claim("x " * 5000)) <= 240


# --- which findings become questions -----------------------------------------


@pytest.mark.parametrize(
    "category",
    [
        "validator_not_implemented",
        "validator_error",
        "invalid_structure",
        "duplicate_step_identity",
        "unreachable_step",
        "unresolvable_branch",
        "redundant_step",
        "no_utility_step",
        "oversized_artifact",
        "duplicate_artifact",
    ],
)
def test_findings_a_human_cannot_answer_produce_no_question(category):
    """The default has to be "do not bother a human". A question about our own
    unbuilt validator teaches reviewers the questions are noise, and after that
    they stop reading the ones that matter."""
    assert category not in ANSWERABLE_CATEGORIES
    gaps = detect_gaps(
        content={"steps": [{"step_id": "s1", "text": "do a thing"}]},
        contract=None,
        findings=[_finding(category=category)],
    )
    assert gaps == []


def test_answerable_finding_becomes_a_gap_with_its_claim():
    gaps = detect_gaps(
        content={"steps": [{"step_id": "s1", "text": "unrelated"}]},
        contract=None,
        findings=[_finding()],
    )
    assert len(gaps) == 1
    assert gaps[0].kind == "missing_required_action"
    assert gaps[0].origin == "finding"
    assert gaps[0].blocking is True
    assert gaps[0].source_finding_id == "f1"


def test_minor_finding_is_a_gap_but_not_blocking():
    gaps = detect_gaps(
        content={"steps": [{"step_id": "s1", "text": "unrelated"}]},
        contract=None,
        findings=[_finding(severity="minor", claim="Note the ticket number")],
    )
    assert len(gaps) == 1
    assert gaps[0].blocking is False


def test_gaps_are_deduplicated_and_blocking_comes_first():
    findings = [
        _finding(id="f1", severity="minor", claim="Low priority thing"),
        _finding(id="f2", severity="critical", claim="Dangerous thing"),
        # Same claim as f2 under a different finding id: one gap, not two.
        _finding(id="f3", severity="critical", claim="Dangerous thing"),
    ]
    gaps = detect_gaps(content={"steps": [{"text": "x"}]}, contract=None, findings=findings)
    assert len(gaps) == 2
    assert gaps[0].blocking is True
    assert gaps[0].claim == "Dangerous thing"


# --- gaps from the contract and the gate --------------------------------------


def test_unresolved_requirements_and_conflicts_become_blocking_gaps():
    contract = {
        "unresolved_requirements": ["No applicable KB documents — obligations unknown."],
        "source_conflicts": ["Episodes report different root causes: A; B"],
    }
    gaps = gaps_from_contract(contract)
    assert {g.kind for g in gaps} == {"unresolved_requirement", "source_conflict"}
    assert all(g.blocking for g in gaps)


def test_gate_outcome_becomes_a_gap_only_when_something_is_open():
    ready = {"quality_contract": {"gate": {"outcome": "ready_for_procedural_generation"}}}
    assert gaps_from_gate(ready) == []

    blocked = {
        "quality_contract": {
            "gate": {
                "outcome": "requires_conflict_adjudication",
                "reasons": ["Unresolved source conflicts at contract build."],
            }
        }
    }
    gaps = gaps_from_gate(blocked)
    assert len(gaps) == 1
    assert gaps[0].kind == "source_conflict"
    assert gaps[0].blocking is True


def test_missing_rollback_is_a_gap_only_when_the_contract_asks_for_one():
    """A playbook with no rollback notes and no rollback obligation is
    complete, not incomplete. Asking about it invents a requirement."""
    content = {"steps": [{"text": "x"}], "rollback_notes": None}
    assert gaps_from_structure(content, {"rollback_obligations": []}) == []

    gaps = gaps_from_structure(content, {"rollback_obligations": ["Restore the previous JAR"]})
    assert [g.target_ref for g in gaps] == ["rollback_notes"]
    assert gaps[0].blocking is True


def test_empty_procedure_is_always_a_critical_gap():
    gaps = gaps_from_structure({"steps": [], "title": "Fix the thing"}, None)
    assert [g.kind for g in gaps] == ["empty_procedure"]
    assert gaps[0].severity == "critical"


# --- obligation policy ---------------------------------------------------------


def test_blocking_gap_stays_mandatory_whatever_the_model_says():
    assert enforce_obligation(OPTIONAL, blocking=True) == MANDATORY
    assert enforce_obligation(None, blocking=True) == MANDATORY


def test_non_blocking_gap_defaults_to_optional_but_can_be_raised():
    assert enforce_obligation(None, blocking=False) == OPTIONAL
    assert enforce_obligation("nonsense", blocking=False) == OPTIONAL
    assert enforce_obligation(MANDATORY, blocking=False) == MANDATORY


def test_a_skipped_optional_question_settles_the_round_and_a_mandatory_one_does_not():
    optional_skipped = _question(gap_key="a", obligation=OPTIONAL, status=Q_SKIPPED)
    mandatory_open = _question(gap_key="b", obligation=MANDATORY, status=Q_OPEN)
    assert round_is_answerable([optional_skipped]) is True
    assert round_is_answerable([optional_skipped, mandatory_open]) is False
    assert [q.gap_key for q in mandatory_outstanding([optional_skipped, mandatory_open])] == ["b"]


# --- KB-first resolution -------------------------------------------------------


def test_context_resolution_finds_the_answer_already_in_a_step():
    gap = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Restart the agent service after applying the patch",
    )
    content = {
        "steps": [{"text": "After applying the patch, restart the agent service."}],
    }
    resolution = resolve_from_context(gap, content=content, contract=None)
    assert resolution.resolved
    assert resolution.answer_source == "context"


def test_context_resolution_refuses_a_step_that_declines_the_action():
    """A step saying "do not restart" matches "restart" on every token. Counting
    it as the answer is the same defect the Stage C polarity work fixed in the
    grounding validator."""
    gap = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Restart the agent service",
    )
    content = {"steps": [{"text": "Do not restart the agent service."}]}
    assert resolve_from_context(gap, content=content, contract=None).resolved is False


def test_an_unrelated_short_step_does_not_answer_a_gap():
    """Regression. ``overlap_ratio`` divides by the shorter token set, so
    "Apply the patch." (three tokens, one of them "the") scored 0.33 against a
    completely unrelated obligation — over the completeness validator's 0.25
    threshold this module first borrowed. The validator can afford that number
    because its failure mode is declining to raise a finding; here the failure
    mode is telling a reviewer their question is already settled and dropping
    it."""
    gap = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Escalate to the platform team if the restart fails twice",
    )
    content = {"steps": [{"text": "Apply the patch."}]}
    assert resolve_from_context(gap, content=content, contract=None).resolved is False


def test_a_paraphrase_that_shares_real_vocabulary_still_resolves():
    """The floor must not be so high that a genuine restatement is re-asked."""
    gap = InformationGap(
        kind="missing_verification",
        origin="finding",
        claim="Verify the agent service is listening on port 8443",
    )
    content = {"steps": [{"text": "Verify that the agent service listens on port 8443."}]}
    assert resolve_from_context(gap, content=content, contract=None).resolved is True


def test_an_attested_answer_resolves_by_gap_key_not_by_wording():
    """A reviewer answering "contact platform-ops after the second failed
    restart" has settled "escalate if the restart fails twice" while sharing
    almost no vocabulary with it. Matching those by overlap would fail, the
    question would be asked again, and the loop would not terminate."""
    gap = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Escalate to the platform team if the restart fails twice",
    )
    contract = attest_answers_on_contract(
        {},
        [
            {
                "gap_key": gap.gap_key,
                "question": "When should this be escalated?",
                "answer": "Contact platform-ops after the second failed restart.",
                "source": "human",
            }
        ],
        round_number=1,
    )
    resolution = resolve_from_context(gap, content={"steps": []}, contract=contract)
    assert resolution.resolved
    assert resolution.provenance["attested_in_round"] == 1


def test_a_field_gap_is_only_answered_by_that_field():
    """A rollback obligation described inside step 4 does not put anything in
    rollback_notes. Treating it as resolved leaves the field empty forever."""
    gap = InformationGap(
        kind="missing_rollback",
        origin="structure",
        claim="Restore the previous JAR",
        target_kind="field",
        target_ref="rollback_notes",
    )
    described_elsewhere = {
        "steps": [{"text": "If it fails, restore the previous JAR."}],
        "rollback_notes": None,
    }
    assert resolve_from_context(gap, content=described_elsewhere, contract=None).resolved is False

    filled = dict(described_elsewhere, rollback_notes="Restore the previous JAR from backup.")
    assert resolve_from_context(gap, content=filled, contract=None).resolved is True


def test_knowledge_resolution_carries_provenance():
    gap = InformationGap(
        kind="missing_verification",
        origin="finding",
        claim="Verify the service is listening on port 8443 after restart",
    )
    document = _document(
        "Agent restart SOP",
        [_section("After the restart, verify the service is listening on port 8443.")],
    )
    resolution = resolve_from_knowledge(gap, [document])
    assert resolution.resolved
    assert resolution.answer_source == "kb"
    assert resolution.provenance["evidence_id"] == "e1"
    assert resolution.provenance["section_ref"] == "§2.1"
    assert resolution.provenance["score"] > 0


def test_knowledge_resolution_refuses_a_section_that_forbids_the_action():
    """The guard that matters. Without it, the article saying "never restart the
    agent" is handed to the reviewer as the answer to "how do I restart it"."""
    gap = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Restart the agent service after applying the patch",
    )
    document = _document(
        "Agent SOP",
        [_section("After applying the patch, do not restart the agent service.")],
    )
    assert resolve_from_knowledge(gap, [document]).resolved is False


@pytest.mark.parametrize("kind", sorted(KB_UNRESOLVABLE_KINDS))
def test_gap_kinds_no_article_can_settle_are_never_kb_resolved(kind):
    """A conflict between two sources is not resolved by finding a third, and a
    scope decision about what this playbook is about is in no article."""
    gap = InformationGap(kind=kind, origin="contract", claim="Sources disagree about the fix")
    document = _document("Anything", [_section("Sources disagree about the fix")])
    assert resolve_from_knowledge(gap, [document]).resolved is False


def test_retrieval_failure_is_distinguishable_from_an_empty_knowledge_base():
    gap = InformationGap(kind="missing_verification", origin="finding", claim="Verify something")
    failed = resolve_gaps([gap], content={}, contract=None, documents=[], retrieval_failed=True)
    empty = resolve_gaps([gap], content={}, contract=None, documents=[], retrieval_failed=False)
    assert failed.kb_status == KB_FAILED
    assert empty.kb_status == KB_NO_RESULTS
    assert failed.unresolved and empty.unresolved


def test_resolution_counts_separate_kb_from_context():
    kb_gap = InformationGap(kind="missing_verification", origin="finding", claim="Check port 8443")
    ctx_gap = InformationGap(kind="missing_required_action", origin="finding", claim="Restart the agent")
    outcome = resolve_gaps(
        [kb_gap, ctx_gap],
        content={"steps": [{"text": "Restart the agent."}]},
        contract=None,
        documents=[_document("SOP", [_section("Check that port 8443 is listening.")])],
    )
    counts = outcome.counts()
    assert counts["resolved_from_context"] == 1
    assert counts["resolved_from_kb"] == 1
    assert counts["unresolved"] == 0


# --- termination ---------------------------------------------------------------


def test_answered_gap_does_not_produce_a_question_next_round():
    """The property the whole feature rests on.

    Round 1 raises a gap the KB cannot answer, so a person is asked. Their
    answer is attested onto the contract. Round 2 runs against content whose
    steps still do not lexically match the obligation — so the validator finding
    is unchanged and the gap is detected again with the identical key — and the
    reviewer must nonetheless not be asked again.
    """
    finding = _finding(claim="Escalate to the platform team if the restart fails twice")
    content = {"steps": [{"step_id": "s1", "text": "Apply the patch."}]}

    round_one = detect_gaps(content=content, contract={}, findings=[finding])
    assert len(round_one) == 1
    resolved_before = resolve_gaps(round_one, content=content, contract={}, documents=[])
    assert resolved_before.unresolved, "round 1 must actually need a person"

    answers = [
        {
            "gap_key": round_one[0].gap_key,
            "gap_kind": round_one[0].kind,
            "question": "When should this be escalated?",
            "answer": "Escalate to the platform team if the restart fails twice.",
            "source": "human",
        }
    ]
    contract_after = attest_answers_on_contract({}, answers, round_number=1)

    round_two = detect_gaps(content=content, contract=contract_after, findings=[finding])
    assert [g.gap_key for g in round_two] == [round_one[0].gap_key], "same defect, same key"

    resolved_after = resolve_gaps(
        round_two, content=content, contract=contract_after, documents=[]
    )
    assert resolved_after.unresolved == [], "an answered gap must not reach a person again"


def test_a_later_answer_supersedes_an_earlier_one_for_the_same_gap():
    first = [{"gap_key": "k", "question": "q", "answer": "old", "source": "human"}]
    second = [{"gap_key": "k", "question": "q", "answer": "new", "source": "human"}]
    snapshot = attest_answers_on_contract({}, first, round_number=1)
    snapshot = attest_answers_on_contract(snapshot, second, round_number=2)
    entries = snapshot["human_attested_answers"]
    assert len(entries) == 1
    assert entries[0]["answer"] == "new"
    assert entries[0]["round"] == 2


def test_attestation_does_not_become_a_new_contract_obligation():
    """Appending answers to required_actions was the first design and it is a
    trap: the completeness validator would then demand a step whose text
    overlaps the answer, and an answer phrased differently from the step it
    produced becomes a permanently unsatisfiable obligation."""
    snapshot = attest_answers_on_contract(
        {"required_actions": ["Apply the patch"]},
        [{"gap_key": "k", "question": "q", "answer": "Restart afterwards", "source": "human"}],
        round_number=1,
    )
    assert snapshot["required_actions"] == ["Apply the patch"]
    assert len(snapshot["human_attested_answers"]) == 1


# --- applying ------------------------------------------------------------------


def test_answer_payload_excludes_skips_and_records_source():
    questions = [
        _question(gap_key="a", status=Q_ANSWERED, answer_text="Yes", answer_source="human"),
        _question(gap_key="b", status=Q_SKIPPED),
        _question(gap_key="c", status="resolved_from_kb", answer_text="From the SOP", answer_source="kb"),
        _question(gap_key="d", status=Q_OPEN),
    ]
    answers = answers_payload(questions)
    assert [a["gap_key"] for a in answers] == ["a", "c"]
    assert {a["source"] for a in answers} == {"human", "kb"}
    assert [s["gap_key"] for s in skipped_payload(questions)] == ["b"]


def test_evidence_refs_carry_both_the_audit_trail_and_the_attestation():
    answers = [{"gap_key": "k", "question": "q", "answer": "a", "source": "human", "obligation": MANDATORY}]
    refs = merge_clarification_into_evidence_refs(
        {"quality_contract": {"hash": "h", "snapshot": {"required_actions": ["x"]}}},
        round_id="r1",
        round_number=2,
        answers=answers,
        skipped=[],
    )
    assert refs["clarification"]["latest_round"] == 2
    assert refs["clarification"]["rounds"][-1]["mandatory_answered"] == 1
    assert refs["quality_contract"]["hash"] == "h"
    assert len(refs["quality_contract"]["snapshot"]["human_attested_answers"]) == 1


def test_evidence_refs_create_a_contract_shell_for_a_playbook_generated_before_contracts():
    refs = merge_clarification_into_evidence_refs(
        None,
        round_id="r1",
        round_number=1,
        answers=[{"gap_key": "k", "question": "q", "answer": "a", "source": "human"}],
    )
    assert refs["quality_contract"]["snapshot"]["human_attested_answers"]


def test_a_revision_that_omits_a_field_does_not_clear_it():
    """A model that returns a playbook without verification_policy has almost
    certainly forgotten it rather than decided the procedure no longer needs
    verifying."""
    previous = SimpleNamespace(
        trigger_conditions={"a": 1},
        branching_logic={"decision_points": []},
        inputs=[{"name": "host"}],
        outputs=[],
        rollback_notes="Restore the JAR",
        conflicts=[{"note": "x"}],
        playbook_confidence=0.72,
        execution_confidence_guidance="Watch the logs",
        verification_policy={"required": True},
    )
    payload = version_data_from_revision(
        {"steps": [{"text": "new step"}]}, previous=previous, evidence_refs={}
    )
    assert payload["verification_policy"] == {"required": True}
    assert payload["rollback_notes"] == "Restore the JAR"
    assert payload["playbook_confidence"] == 0.72
    assert payload["steps"] == [{"text": "new step"}]


# --- question generation --------------------------------------------------------


def _gap(key_claim: str, blocking: bool = False) -> InformationGap:
    return InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim=key_claim,
        blocking=blocking,
        explanation="Contract required action is not reflected in any step.",
    )


@pytest.mark.asyncio
async def test_generated_questions_are_keyed_to_real_gaps_and_inventions_are_dropped():
    gaps = [_gap("Restart the agent")]
    payload = {
        "questions": [
            {
                "gap_key": gaps[0].gap_key,
                "question": "Which service must be restarted, and in what order?",
                "why_it_matters": "The procedure cannot be followed without the service name.",
                "obligation": "optional",
                "answer_kind": "text",
            },
            {
                "gap_key": "a-key-we-never-reported",
                "question": "Would you like to add a backup step?",
                "obligation": "mandatory",
            },
        ]
    }
    from contextedge.ai.generators import clarification_generator as gen

    with patch.object(gen, "llm_complete_json", AsyncMock(return_value=payload)):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert [q.gap_key for q in result.questions] == [gaps[0].gap_key]
    assert result.dropped_unknown_keys == 1
    assert "unknown gap" in (result.error or "")


@pytest.mark.asyncio
async def test_the_model_cannot_mark_a_blocking_gap_optional():
    gaps = [_gap("Restart the agent", blocking=True)]
    payload = {
        "questions": [
            {"gap_key": gaps[0].gap_key, "question": "Which service?", "obligation": "optional"}
        ]
    }
    from contextedge.ai.generators import clarification_generator as gen

    with patch.object(gen, "llm_complete_json", AsyncMock(return_value=payload)):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert result.questions[0].obligation == MANDATORY


@pytest.mark.asyncio
async def test_a_gap_the_model_ignored_still_gets_a_question():
    """Dropping it would be worse in the one case that matters: a blocking
    defect would vanish and the playbook would look ready."""
    gaps = [_gap("Restart the agent", blocking=True), _gap("Verify port 8443")]
    payload = {"questions": [{"gap_key": gaps[1].gap_key, "question": "Which port?"}]}
    from contextedge.ai.generators import clarification_generator as gen

    with patch.object(gen, "llm_complete_json", AsyncMock(return_value=payload)):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    by_key = {q.gap_key: q for q in result.questions}
    assert set(by_key) == {gaps[0].gap_key, gaps[1].gap_key}
    assert by_key[gaps[0].gap_key].is_fallback is True
    assert by_key[gaps[0].gap_key].obligation == MANDATORY
    assert result.fallback_count == 1


@pytest.mark.asyncio
async def test_generation_failure_is_recorded_rather_than_raised():
    gaps = [_gap("Restart the agent", blocking=True)]
    from contextedge.ai.generators import clarification_generator as gen

    with patch.object(gen, "llm_complete_json", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert result.error and "RuntimeError" in result.error
    # The gap still surfaces, so a blocking defect cannot disappear because the
    # model was unavailable.
    assert len(result.questions) == 1
    assert result.questions[0].is_fallback is True


@pytest.mark.asyncio
async def test_a_truncated_first_response_is_retried_before_falling_back():
    """Regression from the first live run.

    The response came back truncated mid-JSON, the provider raised, and the
    repair retry was skipped because it was guarded on the first call having
    succeeded. All three gaps fell back to raw validator text when a second
    ask would have produced real questions.
    """
    gaps = [_gap("Restart the agent", blocking=True), _gap("Verify port 8443")]
    good = {
        "questions": [
            {"gap_key": gaps[0].gap_key, "question": "Which service?"},
            {"gap_key": gaps[1].gap_key, "question": "Which port?"},
        ]
    }
    from contextedge.ai.generators import clarification_generator as gen

    call = AsyncMock(side_effect=[ValueError("LLM returned invalid JSON"), good])
    with patch.object(gen, "llm_complete_json", call):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert call.await_count == 2
    assert result.fallback_count == 0
    assert {q.question for q in result.questions} == {"Which service?", "Which port?"}


@pytest.mark.asyncio
async def test_the_retry_is_bounded_at_one_extra_call():
    gaps = [_gap("Restart the agent", blocking=True)]
    from contextedge.ai.generators import clarification_generator as gen

    call = AsyncMock(side_effect=ValueError("LLM returned invalid JSON"))
    with patch.object(gen, "llm_complete_json", call):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert call.await_count == 2
    assert result.fallback_count == 1


def test_the_clarification_task_has_its_own_output_ceiling():
    """Without an entry the task inherits the global cost backstop, and on a
    thinking model reasoning eats it before the answer is written — the exact
    truncation that produced no usable questions on the first live run."""
    from contextedge.config import settings

    assert settings.llm_task_output_tokens.get("clarification", 0) >= 8192


@pytest.mark.asyncio
async def test_a_choice_question_with_one_option_degrades_to_free_text():
    gaps = [_gap("Restart the agent")]
    payload = {
        "questions": [
            {
                "gap_key": gaps[0].gap_key,
                "question": "Which service?",
                "answer_kind": "choice",
                "choices": ["only one"],
            }
        ]
    }
    from contextedge.ai.generators import clarification_generator as gen

    with patch.object(gen, "llm_complete_json", AsyncMock(return_value=payload)):
        result = await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert result.questions[0].answer_kind == "text"
    assert result.questions[0].choices == []


def test_ontology_block_never_invents_a_product_name():
    from contextedge.ai.generators.clarification_generator import format_ontology_for_prompt

    rendered = format_ontology_for_prompt([])
    assert "none recorded" in rendered.lower()
    named = format_ontology_for_prompt(
        [{"canonical_term": "Widget Server", "term_kind": "product", "aliases": ["WS"]}]
    )
    assert "Widget Server" in named and "WS" in named


def test_human_attested_steps_survive_grounding_classification():
    """``classify_step_grounding`` forces every step with no source_refs to
    best_practice — correct for generation, wrong for a revision. A support
    decision must not be relabelled as the model's own suggestion."""
    from contextedge.ai.generators.clarification_generator import (
        _claimed_human_attested,
        _restore_human_attested,
    )
    from contextedge.ai.generators.playbook_generator import classify_step_grounding

    result = {
        "steps": [
            {"text": "Grounded step", "source_refs": [{"label": "kb-1", "kind": "knowledge", "id": "e1"}]},
            {"text": "Support told us to do this", "grounding_status": "human_attested"},
            {"text": "Model's own idea"},
        ]
    }
    claimed = _claimed_human_attested(result)
    classify_step_grounding(result)
    restored = _restore_human_attested(result, claimed, round_number=2)

    assert restored == 1
    assert result["steps"][0]["grounding_status"] == "grounded"
    assert result["steps"][1]["grounding_status"] == "human_attested"
    assert result["steps"][1]["attested_in_round"] == 2
    assert result["steps"][2]["step_classification"] == "best_practice"
    assert result["grounding"]["best_practice"] == 1
    assert result["grounding"]["human_attested"] == 1


def test_a_step_with_citations_cannot_claim_to_be_human_attested():
    """Mirrors the generator's rule that structure beats the model's claim:
    grounded is the stronger status and the model may not trade it away."""
    from contextedge.ai.generators.clarification_generator import (
        _claimed_human_attested,
        _restore_human_attested,
    )

    result = {
        "steps": [
            {
                "text": "Cited step",
                "grounding_status": "human_attested",
                "source_refs": [{"label": "kb-1", "kind": "knowledge", "id": "e1"}],
            }
        ]
    }
    claimed = _claimed_human_attested(result)
    assert _restore_human_attested(result, claimed, round_number=1) == 0


# --- old playbooks --------------------------------------------------------------


def test_a_playbook_with_no_assessment_and_no_contract_yields_no_gaps():
    """The pre-quality-pipeline corpus, and why the empty result is a trap.

    An old playbook has steps but no stored contract and, until something
    assesses it, no findings. Every gap source is then empty and the detector
    returns nothing — the identical result to a playbook examined closely and
    found clean. Only one of those is good news, so the service records the
    difference (``_Snapshot.has_inputs``) rather than reporting the second.
    """
    content = {"title": "Old playbook", "steps": [{"step_id": "s1", "text": "Do the thing."}]}
    assert detect_gaps(content=content, contract=None, findings=[], evidence_refs=None) == []


def test_the_same_old_playbook_yields_gaps_once_it_has_been_assessed():
    content = {"title": "Old playbook", "steps": [{"step_id": "s1", "text": "Do the thing."}]}
    gaps = detect_gaps(
        content=content,
        contract=None,
        findings=[_finding(claim="Verify the service came back up")],
        evidence_refs=None,
    )
    assert [g.kind for g in gaps] == ["missing_required_action"]


def test_snapshot_reports_whether_there_was_anything_to_read():
    from contextedge.services.playbook_clarification_service import _Snapshot

    nothing = _Snapshot(
        version=None, content={}, content_hash="a" * 64, contract=None,
        assessment=None, findings=[], assessment_matches=False,
    )
    assert nothing.has_inputs is False

    assessed = _Snapshot(
        version=None, content={}, content_hash="a" * 64, contract=None,
        assessment=object(), findings=[], assessment_matches=True,
    )
    assert assessed.has_inputs is True

    # An assessment that describes content the playbook has moved away from is
    # not something to read either — its findings are about text nobody can see.
    moved = _Snapshot(
        version=None, content={}, content_hash="a" * 64, contract=None,
        assessment=object(), findings=[], assessment_matches=False,
    )
    assert moved.has_inputs is False

    with_contract = _Snapshot(
        version=None, content={}, content_hash="a" * 64,
        contract={"required_actions": ["x"]},
        assessment=None, findings=[], assessment_matches=False,
    )
    assert with_contract.has_inputs is True


# --- regenerating the questions ---------------------------------------------------


def _row(**over):
    """A stored question row, as the regeneration path reads it back."""
    base = {
        "id": "row-1",
        "gap_key": None,  # filled from the reconstructed gap below
        "gap_kind": "missing_required_action",
        "gap_origin": "finding",
        "target_kind": "playbook",
        "target_ref": None,
        "claim": "Restart the ingest service after applying the patch",
        "severity": "major",
        "question_text": "Vague question?",
        "why_it_matters": None,
        "obligation": MANDATORY,
        "answer_kind": "text",
        "choices": [],
        "expected_format": None,
        "status": Q_OPEN,
        "answer_text": None,
        "answer_source": None,
        "source_finding_id": None,
    }
    base.update(over)
    row = SimpleNamespace(**base)
    if row.gap_key is None:
        from contextedge.services.playbook_clarification_service import _gap_from_question

        row.gap_key = _gap_from_question(row).gap_key
    return row


def test_a_reconstructed_gap_keeps_its_key_and_its_blocking_status():
    """The rewrite path rebuilds gaps from stored rows. If the key moved, the
    generator would drop every question as unknown; if `blocking` moved, a
    mandatory question could come back optional."""
    from contextedge.services.playbook_clarification_service import _gap_from_question

    original = InformationGap(
        kind="missing_required_action",
        origin="finding",
        claim="Restart the ingest service after applying the patch",
        severity="major",
        blocking=True,
    )
    row = _row(gap_key=original.gap_key)
    rebuilt = _gap_from_question(row)

    assert rebuilt.gap_key == original.gap_key
    assert rebuilt.blocking is True

    minor = _gap_from_question(_row(severity="minor", gap_key="x"))
    assert minor.blocking is False


@pytest.mark.asyncio
async def test_a_rewrite_shows_the_model_what_it_said_before():
    """At temperature 0 the same inputs give the same output. Without the
    rejected wording in the prompt, a rewrite is a re-roll the reviewer paid
    for and it returns the question they already refused."""
    gaps = [_gap("Restart the agent", blocking=True)]
    from contextedge.ai.generators import clarification_generator as gen

    captured: dict[str, str] = {}

    async def _capture(user, **kwargs):
        captured["user"] = user
        return {"questions": [{"gap_key": gaps[0].gap_key, "question": "Better?"}]}

    with patch.object(gen, "llm_complete_json", _capture):
        await gen.generate_questions(
            gaps,
            content={},
            contract_prompt="",
            guidance="Too vague — ask about ordering.",
            previous_questions={gaps[0].gap_key: "Vague question?"},
        )

    assert "THIS IS A REWRITE" in captured["user"]
    assert "Vague question?" in captured["user"]
    assert "Too vague — ask about ordering." in captured["user"]
    # The reviewer's note steers wording; it does not license invention.
    assert "rule 2 holds" in captured["user"]


@pytest.mark.asyncio
async def test_an_ordinary_round_carries_no_rewrite_block():
    """Prompt v1 must stay byte-identical on the normal path, or its version
    attribution stops meaning anything."""
    gaps = [_gap("Restart the agent")]
    from contextedge.ai.generators import clarification_generator as gen

    captured: dict[str, str] = {}

    async def _capture(user, **kwargs):
        captured["user"] = user
        return {"questions": [{"gap_key": gaps[0].gap_key, "question": "q"}]}

    with patch.object(gen, "llm_complete_json", _capture):
        await gen.generate_questions(gaps, content={}, contract_prompt="")

    assert "REWRITE" not in captured["user"]
    assert "REVIEWER FEEDBACK" not in captured["user"]


def test_the_rewrite_block_only_shows_wording_for_gaps_being_asked_about():
    """A stale entry for a gap that is no longer in the subset would tell the
    model not to repeat wording it was never going to use, and leaks another
    question's text into an unrelated prompt."""
    from contextedge.ai.generators.clarification_generator import _rewrite_block

    asked = _gap("Restart the agent")
    other = _gap("Something else entirely")
    block = _rewrite_block(
        None,
        {asked.gap_key: "asked before", other.gap_key: "unrelated question"},
        [asked],
    )
    assert "asked before" in block
    assert "unrelated question" not in block


def test_no_rewrite_block_without_guidance_or_history():
    from contextedge.ai.generators.clarification_generator import _rewrite_block

    assert _rewrite_block(None, None, []) == ""
    assert _rewrite_block("   ", {}, []) == ""
