"""Generating clarification questions, and revising a playbook from the answers.

Prompt text lives in ``contextedge.ai.prompts.clarification`` (registry-
versioned, A/B-routable per tenant).

The one invariant worth stating up front: **the model chooses the wording, this
module chooses the set.** Questions are keyed by ``gap_key``; anything the model
returns under a key that was not supplied is dropped, and any supplied key the
model ignored is either recovered by a bounded retry or falls back to the
defect's own text. Without that, "AI-generated questions" degrades into an
interview about whatever the model found interesting, and a blocking defect
disappears because no question was ever asked about it.
"""

from __future__ import annotations

import json
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from contextedge.ai.prompts import get_prompt
from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY, generation_provenance
from contextedge.ai.provider import llm_complete_json
from contextedge.quality.clarification.gaps import InformationGap
from contextedge.quality.clarification.states import (
    ANSWER_KIND_CHOICE,
    ANSWER_KIND_TEXT,
    ANSWER_KINDS,
    MANDATORY,
    OPTIONAL,
    enforce_obligation,
)

logger = structlog.get_logger()

MAX_QUESTION_CHARS = 600
MAX_CHOICES = 8


@dataclass
class GeneratedQuestion:
    """One question, after validation against the gap it claims to be about."""

    gap_key: str
    question: str
    why_it_matters: str | None = None
    obligation: str = OPTIONAL
    answer_kind: str = ANSWER_KIND_TEXT
    choices: list[str] = field(default_factory=list)
    expected_format: str | None = None
    # True when the model gave us nothing usable for this gap and the defect's
    # own text is standing in. Surfaced so a panel can say so rather than
    # presenting a raw validator explanation as a composed question.
    is_fallback: bool = False


@dataclass
class QuestionGenerationResult:
    questions: list[GeneratedQuestion]
    prompt_name: str | None = None
    prompt_version: str | None = None
    model_provenance: dict[str, Any] | None = None
    error: str | None = None
    dropped_unknown_keys: int = 0
    fallback_count: int = 0


# --- rendering the inputs ----------------------------------------------------


def format_steps_for_prompt(content: dict[str, Any] | None, limit: int = 30) -> str:
    """Step titles only.

    The question generator needs to know what the procedure already covers so it
    does not ask about something on screen. It does not need the full text, and
    sending it would crowd out the gaps, which are the part of the prompt that
    actually determines the output.
    """
    steps = [s for s in ((content or {}).get("steps") or []) if isinstance(s, dict)]
    if not steps:
        return "(none — this playbook has no steps)"
    lines: list[str] = []
    for step in steps[:limit]:
        ref = step.get("step_id") or step.get("order") or "?"
        for key in ("title", "text", "action", "instruction"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"  [{ref}] {value.strip()[:240]}")
                break
    if len(steps) > limit:
        lines.append(f"  … and {len(steps) - limit} more")
    return "\n".join(lines) or "(none)"


def format_ontology_for_prompt(terms: list[dict[str, Any]] | None) -> str:
    """The tenant's own vocabulary.

    Empty is a legitimate and truthful answer for a tenant that has not built an
    ontology. The prompt handles that case explicitly; inventing a default here
    is exactly the hardcoded-product-name bug this codebase already fixed once.
    """
    if not terms:
        return "(none recorded for this tenant — use only the wording the playbook itself uses)"
    lines: list[str] = []
    for term in terms[:60]:
        if not isinstance(term, dict):
            continue
        canonical = str(term.get("canonical_term") or "").strip()
        if not canonical:
            continue
        kind = str(term.get("term_kind") or "term")
        aliases = [str(a) for a in (term.get("aliases") or []) if str(a).strip()]
        line = f"  - {canonical} ({kind})"
        if aliases:
            line += f" — also called: {', '.join(aliases[:5])}"
        lines.append(line)
    return "\n".join(lines) or "(none recorded for this tenant)"


def format_kb_summary(
    *, kb_status: str, resolved_count: int, searched_count: int
) -> str:
    """What the knowledge search found, in the model's terms.

    Told to the model so it does not compose a question that starts "I could not
    find…" when in fact we never looked, and so a retrieval failure is not
    described to the reviewer as an absence of documentation.
    """
    if kb_status == "retrieval_failed":
        return (
            "Knowledge retrieval FAILED for this round. Nothing was searched. Do not "
            "state or imply that documentation is missing — we do not know."
        )
    if kb_status == "no_results":
        return (
            "The knowledge search returned no applicable documents for this playbook's "
            "subject. These gaps are not answerable from approved documentation."
        )
    return (
        f"{searched_count} approved document(s) were searched; {resolved_count} gap(s) "
        "were answered from them and are not in the list below. The gaps below are the "
        "ones documentation did not answer."
    )


def _gaps_payload(gaps: list[InformationGap]) -> str:
    return json.dumps([gap.as_prompt_dict() for gap in gaps], indent=2)[:24000]


def _rewrite_block(
    guidance: str | None,
    previous_questions: dict[str, str] | None,
    gaps: list[InformationGap],
) -> str:
    """Instructions for a second attempt at questions a reviewer rejected.

    The previous wording is shown so the model can avoid repeating it. That is
    the whole value of a rewrite over a re-roll at temperature 0: without the
    rejected text in front of it, the same inputs produce the same output and
    the reviewer pays for an identical answer.

    The reviewer's own note is quoted rather than paraphrased, and is the one
    place in this prompt where text the model did not derive from the sources
    is allowed to steer it — a person saying "these are too vague, ask about
    the ordering" is the most useful signal available here.
    """
    note = (guidance or "").strip()
    wanted = {gap.gap_key for gap in gaps}
    shown = [
        (key, text)
        for key, text in (previous_questions or {}).items()
        # Only the gaps actually being asked about. A stale entry would tell
        # the model not to repeat wording it was never going to use, and would
        # leak another question's text into an unrelated prompt.
        if key in wanted and str(text or "").strip()
    ]
    # Whitespace-only guidance is not guidance. Emitting the header alone tells
    # the model a reviewer rejected its questions and gives it nothing to do
    # differently, which is worse than saying nothing.
    if not note and not shown:
        return ""

    lines = ["THIS IS A REWRITE. A reviewer rejected the previous questions."]
    if shown:
        lines.append("")
        lines.append("What you asked last time — do not repeat this wording:")
        for key, text in shown:
            lines.append(f"  [{key}] {text.strip()[:400]}")
    if note:
        lines.append("")
        lines.append("REVIEWER FEEDBACK — this is what they said was wrong:")
        lines.append(f"  “{note[:1200]}”")
        lines.append(
            "  Act on it. It still does not license inventing a product, "
            "component, version or value that the supplied material does not "
            "contain — rule 2 holds."
        )
    return "\n".join(lines)


# --- validating what came back -----------------------------------------------


def _coerce_choices(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:MAX_CHOICES]:
        text = str(item).strip()
        if text:
            out.append(text[:200])
    return out


def _validate_question(
    raw: Any, gap: InformationGap
) -> GeneratedQuestion | None:
    """One returned object, checked against the gap it names."""
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    if not question:
        return None

    answer_kind = str(raw.get("answer_kind") or ANSWER_KIND_TEXT).strip().lower()
    if answer_kind not in ANSWER_KINDS:
        answer_kind = ANSWER_KIND_TEXT
    choices = _coerce_choices(raw.get("choices"))
    if answer_kind == ANSWER_KIND_CHOICE and len(choices) < 2:
        # A choice question with one option or none is not a choice. Degrade to
        # text rather than render a radio group the reviewer cannot answer.
        answer_kind = ANSWER_KIND_TEXT
        choices = []
    if answer_kind != ANSWER_KIND_CHOICE:
        choices = []

    proposed = str(raw.get("obligation") or "").strip().lower()
    why = str(raw.get("why_it_matters") or "").strip() or None
    expected = str(raw.get("expected_format") or "").strip() or None

    return GeneratedQuestion(
        gap_key=gap.gap_key,
        question=question[:MAX_QUESTION_CHARS],
        why_it_matters=why[:600] if why else None,
        # Policy, not the model's opinion: a gap raised by a blocking finding is
        # mandatory whatever was proposed. See states.enforce_obligation.
        obligation=enforce_obligation(
            MANDATORY if proposed == MANDATORY else OPTIONAL, blocking=gap.blocking
        ),
        answer_kind=answer_kind,
        choices=choices,
        expected_format=expected[:300] if expected else None,
    )


def _fallback_question(gap: InformationGap) -> GeneratedQuestion:
    """What to show when the model returned nothing usable for a gap.

    Derived from this gap's own claim and explanation, so it is still specific
    to the defect rather than a template from a bank — but it is a validator's
    words, not a composed question, and it is flagged as such so the panel can
    say so. Dropping the gap instead would be worse in the one case that
    matters: a blocking defect would vanish, and the playbook would look ready.
    """
    claim = (gap.claim or "").strip()
    reason = (gap.explanation or "").strip()
    if claim and reason:
        text = f"{reason} What should this playbook say about: “{claim[:300]}”?"
    elif claim:
        text = f"What should this playbook say about: “{claim[:300]}”?"
    else:
        text = reason or f"Unresolved {gap.kind.replace('_', ' ')} — what is missing here?"
    return GeneratedQuestion(
        gap_key=gap.gap_key,
        question=text[:MAX_QUESTION_CHARS],
        why_it_matters=None,
        obligation=enforce_obligation(OPTIONAL, blocking=gap.blocking),
        answer_kind=ANSWER_KIND_TEXT,
        is_fallback=True,
    )


def _extract_questions(result: Any) -> list[Any]:
    """Pull the array out of whatever shape came back."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("questions", "items", "results"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


# --- the calls ---------------------------------------------------------------


async def generate_questions(
    gaps: list[InformationGap],
    *,
    content: dict[str, Any] | None,
    contract_prompt: str,
    ontology_terms: list[dict[str, Any]] | None = None,
    kb_status: str = "ok",
    kb_resolved_count: int = 0,
    kb_searched_count: int = 0,
    guidance: str | None = None,
    previous_questions: dict[str, str] | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> QuestionGenerationResult:
    """Compose one question per unresolved gap.

    Never raises. A round with no questions and a recorded error is a truthful
    state the reviewer can act on ("we could not compose the questions"); an
    exception here would take down the endpoint that opened the round and leave
    the playbook with a half-written one.
    """
    if not gaps:
        return QuestionGenerationResult(questions=[])

    by_key = {gap.gap_key: gap for gap in gaps}
    prompt = get_prompt("clarification_questions", tenant_id)

    async def _ask(subset: list[InformationGap]) -> tuple[list[Any], str | None]:
        user = prompt.format_user(
            playbook_title=str((content or {}).get("title") or "(untitled)")[:300],
            playbook_description=str((content or {}).get("description") or "(none)")[:1200],
            playbook_steps=format_steps_for_prompt(content),
            ontology_terms=format_ontology_for_prompt(ontology_terms),
            contract_obligations=contract_prompt[:8000] or "(no contract recorded)",
            kb_search_summary=format_kb_summary(
                kb_status=kb_status,
                resolved_count=kb_resolved_count,
                searched_count=kb_searched_count,
            ),
            gaps_json=_gaps_payload(subset),
        )
        # Appended to the user message rather than added as a template slot, so
        # prompt v1 stays byte-identical for the ordinary path and its version
        # attribution keeps meaning something. Same pattern the playbook
        # generator uses for the quality-contract block.
        rewrite = _rewrite_block(guidance, previous_questions, subset)
        if rewrite:
            user = f"{user}\n\n{rewrite}"
        try:
            raw = await llm_complete_json(
                user,
                task="clarification",
                system_prompt=prompt.system,
                tenant_id=tenant_id,
                db=db,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
            )
            return _extract_questions(raw), None
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.exception(
                "playbook_clarification.question_generation_failed",
                error=str(exc)[:400],
                gaps=len(subset),
            )
            return [], f"{type(exc).__name__}: {str(exc)[:200]}"

    items, error = await _ask(gaps)

    accepted: dict[str, GeneratedQuestion] = {}
    dropped_unknown = 0
    for item in items:
        key = str((item or {}).get("gap_key") or "").strip() if isinstance(item, dict) else ""
        gap = by_key.get(key)
        if gap is None:
            # A question about a gap we never reported. Counted rather than
            # silently ignored: a prompt that starts inventing gaps should be
            # visible in the counters, not only in a reviewer's confusion.
            dropped_unknown += 1
            continue
        if key in accepted:
            continue
        validated = _validate_question(item, gap)
        if validated is not None:
            accepted[key] = validated

    # Bounded repair: ask once more for only the keys that came back missing.
    # One retry, not a loop — a model that ignores half the gaps twice will
    # ignore them a third time, and the round has a cost budget.
    #
    # The retry runs after a hard failure too. It first did not, guarded on
    # `error is None` to avoid paying for a second call after the first one
    # broke — which got the priority backwards, and the first live run proved
    # it: the response was truncated mid-JSON, `_ask` raised, the guard skipped
    # the retry, and all three gaps fell back to raw validator text. A parse
    # failure is the most recoverable kind there is, transport errors are
    # already retried inside the provider, and the cost of one extra call is
    # far below the cost of a reviewer reading three validator explanations
    # where they were promised questions.
    missing = [gap for key, gap in by_key.items() if key not in accepted]
    if missing:
        retry_items, retry_error = await _ask(missing)
        for item in retry_items:
            key = str((item or {}).get("gap_key") or "").strip() if isinstance(item, dict) else ""
            gap = by_key.get(key)
            if gap is None or key in accepted:
                continue
            validated = _validate_question(item, gap)
            if validated is not None:
                accepted[key] = validated
        error = error or retry_error

    fallback_count = 0
    for key, gap in by_key.items():
        if key not in accepted:
            accepted[key] = _fallback_question(gap)
            fallback_count += 1

    if dropped_unknown or fallback_count:
        logger.warning(
            "playbook_clarification.question_repair",
            dropped_unknown_keys=dropped_unknown,
            fallbacks=fallback_count,
            gaps=len(gaps),
        )

    # Ordered by the gap order the caller supplied — worst first — rather than
    # by whatever order the model emitted.
    ordered = [accepted[gap.gap_key] for gap in gaps if gap.gap_key in accepted]

    notes: list[str] = []
    if error:
        notes.append(f"generation error: {error}")
    if fallback_count:
        notes.append(
            f"{fallback_count} gap(s) had no composed question; the defect text is shown instead"
        )
    if dropped_unknown:
        notes.append(f"{dropped_unknown} returned question(s) named an unknown gap and were dropped")

    return QuestionGenerationResult(
        questions=ordered,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        model_provenance=generation_provenance(prompt, task="clarification"),
        error="; ".join(notes) or None,
        dropped_unknown_keys=dropped_unknown,
        fallback_count=fallback_count,
    )


def format_answers_for_prompt(answers: list[dict[str, Any]]) -> str:
    """Q&A block for the revision prompt.

    The source of each answer is stated. An answer prefilled from a KB article
    and one typed by a support engineer are different kinds of authority, and a
    revision prompt that cannot tell them apart will treat a retrieval score as
    a person's decision.
    """
    if not answers:
        return "(none)"
    lines: list[str] = []
    for index, entry in enumerate(answers, start=1):
        source = str(entry.get("source") or "human")
        origin = {
            "human": "answered by the support reviewer",
            "kb": "prefilled from approved documentation and accepted",
            "context": "already present in the playbook",
            "carried": "carried forward from an earlier round",
        }.get(source, source)
        lines.append(f"[a-{index}] Q: {str(entry.get('question') or '').strip()[:600]}")
        lines.append(f"        A: {str(entry.get('answer') or '').strip()[:1500]}")
        lines.append(f"        ({origin})")
        target = entry.get("target_ref")
        if target:
            lines.append(f"        applies to: {entry.get('target_kind') or 'playbook'} {target}")
    return "\n".join(lines)


def format_skipped_for_prompt(skipped: list[dict[str, Any]]) -> str:
    if not skipped:
        return "(none)"
    return "\n".join(
        f"  - {str(entry.get('question') or '').strip()[:300]}" for entry in skipped
    )


HUMAN_ATTESTED = "human_attested"


def _claimed_human_attested(result: dict[str, Any]) -> set[int]:
    """Indices of steps the model says exist because a person answered.

    Captured *before* ``classify_step_grounding`` runs, because that function
    forces every step with no surviving ``source_refs`` to ``best_practice`` —
    correct for generation, wrong here. A step supplied by the support
    organisation is not the model's own suggestion, and a reviewer who cannot
    tell those apart will weigh a support decision as a model guess.

    A step with source_refs is not accepted as attested, mirroring the
    generator's rule that structure wins over the model's claim: grounded is
    the stronger status and the model may not trade it away.
    """
    claimed: set[int] = set()
    for index, step in enumerate(result.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if str(step.get("grounding_status") or "").strip() == HUMAN_ATTESTED:
            claimed.add(index)
    return claimed


def _restore_human_attested(
    result: dict[str, Any], claimed: set[int], *, round_number: int | None
) -> int:
    """Re-apply the attestation ``classify_step_grounding`` just erased."""
    restored = 0
    for index in claimed:
        steps = result.get("steps") or []
        if index >= len(steps) or not isinstance(steps[index], dict):
            continue
        step = steps[index]
        if step.get("source_refs"):
            continue
        step["grounding_status"] = HUMAN_ATTESTED
        step["step_classification"] = HUMAN_ATTESTED
        step["confidence"] = HUMAN_ATTESTED
        if round_number is not None:
            step["attested_in_round"] = round_number
        restored += 1
    if restored:
        grounding = result.get("grounding")
        if isinstance(grounding, dict):
            # The counts were computed with these steps in `best_practice`.
            # Leaving them there would understate how much of this playbook the
            # support organisation actually stands behind.
            grounding["best_practice"] = max(
                0, int(grounding.get("best_practice") or 0) - restored
            )
            grounding[HUMAN_ATTESTED] = restored
    return restored


async def revise_playbook(
    *,
    current_playbook: dict[str, Any],
    answers: list[dict[str, Any]],
    skipped: list[dict[str, Any]] | None = None,
    contract_prompt: str = "",
    knowledge_sources: list[Any] | None = None,
    product_label: str | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    episode_summaries: list[dict] | None = None,
    round_number: int | None = None,
) -> dict:
    """Fold the answers into the playbook that already exists.

    Post-processing is the *same* set of functions generation uses —
    ``validate_source_refs``, ``classify_step_grounding``,
    ``sanitize_branching_logic``. A revision path with its own post-processing
    is precisely how the manual generation endpoint ended up missing four of the
    five guards its worker twin had.
    """
    from contextedge.ai.generators.playbook_generator import (
        _build_ref_map,
        classify_step_grounding,
        sanitize_branching_logic,
        validate_source_refs,
    )
    from contextedge.services.knowledge_retrieval_service import format_knowledge_block

    knowledge_sources = knowledge_sources or []
    episode_summaries = episode_summaries or []
    prompt = get_prompt("playbook_revision", tenant_id)

    user = prompt.format_user(
        current_playbook=json.dumps(current_playbook, indent=2, default=str)[:40000],
        answers=format_answers_for_prompt(answers),
        skipped=format_skipped_for_prompt(skipped or []),
        contract_obligations=contract_prompt[:8000] or "(no contract recorded)",
        knowledge_sources=format_knowledge_block(knowledge_sources, product_label),
    )

    result = await llm_complete_json(
        user,
        # Same task label as generation: a revision returns a whole playbook and
        # needs the same 16k budget. Giving it a smaller one is how the JSON
        # started truncating in the first place.
        task="playbook",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if isinstance(result, dict):
        ref_map = _build_ref_map(knowledge_sources, episode_summaries)
        # Order matters and matches generation: citations are cleaned first, so
        # grounding is classified against refs that actually resolve. The
        # attestation capture sits between them because classification is what
        # erases it.
        validate_source_refs(result, ref_map)
        claimed = _claimed_human_attested(result)
        classify_step_grounding(result)
        restored = _restore_human_attested(result, claimed, round_number=round_number)
        sanitize_branching_logic(result)
        result[GENERATION_PROVENANCE_KEY] = generation_provenance(prompt, task="playbook")
        if restored:
            logger.info(
                "playbook_clarification.human_attested_steps",
                steps=restored,
                round_number=round_number,
            )
    return result
