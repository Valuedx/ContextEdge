"""Answer a gap from what we already have, before asking a person.

The user-facing requirement is explicit about the order: *"If the required data
is already available in the KB, use it. If the data is not available anywhere,
ask questions."* This module is that ordering, and it is also the difference
between a loop support will use and one they will switch off. A question whose
answer was already sitting in the artifact — or in an approved article — reads
as the system not having read its own inputs, and after two of those a reviewer
stops answering the ones that matter.

Three resolution attempts, in cost order, stopping at the first hit:

1. **Context** — the fact is already in the playbook or its contract. Free, and
   entirely deterministic. These are gaps the detector raised because a
   validator's overlap threshold is imperfect rather than because anything is
   missing.
2. **Knowledge** — an approved article section answers it. One retrieval per
   round, matched with the same polarity-aware helpers the validators use, so a
   section saying "do **not** restart the agent" can never be offered as the
   answer to "how do I restart the agent".
3. **Nothing** — the gap goes to the question generator.

A KB-resolved gap still becomes a **visible, prefilled, editable** question.
Silently folding a retrieval into the playbook would let a wrong match enter as
though a person had approved it, which is the failure mode the whole quality
plan exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from contextedge.quality.claim_match import tokens
from contextedge.quality.clarification.gaps import InformationGap
from contextedge.quality.clarification.states import (
    ANSWER_CONTEXT,
    ANSWER_KB,
    KB_FAILED,
    KB_NO_RESULTS,
    KB_OK,
)
from contextedge.quality.polarity import polarity_agrees
from contextedge.quality.semantic_match import combined_entailment_score

logger = structlog.get_logger()

# Inherited from the completeness validator, deliberately rather than
# coincidentally: if this were looser than the validator's threshold, a gap
# would be "resolved from context" while the validator that raised it keeps
# raising it, and the loop would never converge. Plan §12.2 tracks calibrating
# both together against the review corpus.
SUPPORT_THRESHOLD = 0.45

# Overlap alone is not enough, and the first version of this module learned it
# from a test: ``overlap_ratio`` divides by the *shorter* token set, so the step
# "Apply the patch." (three tokens, one of them "the") scores 0.33 against a
# completely unrelated obligation. At the completeness validator's 0.25 that
# reads as "already answered", and the gap is silently swallowed.
#
# The validator can afford that threshold because its failure mode is declining
# to raise a finding. Here the failure mode is telling a reviewer their question
# is already settled and dropping it — so the bar is higher, and a match must
# also share real vocabulary rather than function words.
MIN_SHARED_DISTINCTIVE_TOKENS = 2
_DISTINCTIVE_MIN_LEN = 4

# Gap kinds no retrieval can settle. A conflict between two sources is not
# resolved by finding a third; a scope decision about what this playbook is
# about is not in any article. Sending these to the KB wastes the round's
# retrieval budget and, worse, sometimes produces a confident irrelevant match.
KB_UNRESOLVABLE_KINDS: frozenset[str] = frozenset(
    {
        "source_conflict",
        "subject_split",
        "subject_scope",
        "artifact_type",
        "policy_alternative",
        "policy_condition",
    }
)


@dataclass
class GapResolution:
    """What we found for one gap without asking anybody."""

    gap: InformationGap
    answer_text: str | None = None
    answer_source: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return bool(self.answer_text and self.answer_source)


@dataclass
class ResolutionOutcome:
    """The round's whole resolution pass."""

    resolutions: list[GapResolution]
    kb_status: str = KB_OK

    @property
    def unresolved(self) -> list[InformationGap]:
        return [r.gap for r in self.resolutions if not r.resolved]

    @property
    def resolved(self) -> list[GapResolution]:
        return [r for r in self.resolutions if r.resolved]

    def counts(self) -> dict[str, int]:
        from_kb = sum(1 for r in self.resolved if r.answer_source == ANSWER_KB)
        from_context = sum(1 for r in self.resolved if r.answer_source == ANSWER_CONTEXT)
        return {
            "gaps": len(self.resolutions),
            "resolved_from_kb": from_kb,
            "resolved_from_context": from_context,
            "unresolved": len(self.unresolved),
        }


# --- 1. the artifact already answers it -------------------------------------


def _step_texts(content: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for step in content.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in ("text", "title", "action", "instruction", "expected_outcome"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


def _distinctive(text: str) -> set[str]:
    """Tokens that carry meaning. Function words are excluded by length rather
    than by a stopword list, matching the rest of ``claim_match``: a curated
    English vocabulary would quietly stop working on a tenant's own jargon."""
    return {t for t in tokens(text) if len(t) >= _DISTINCTIVE_MIN_LEN}


def supports(claim: str, candidate: str) -> float:
    """How strongly ``candidate`` answers ``claim``. ``0.0`` when it does not.

    Three conditions, all necessary:

    - **Polarity agrees.** A sentence that declines the action a gap asks about
      matches its words perfectly and answers nothing. Same guard the grounding
      validator uses.
    - **Enough shared real vocabulary.** Guards against the short-text artifact
      described at ``MIN_SHARED_DISTINCTIVE_TOKENS``.
    - **Enough similarity.** ``combined_entailment_score`` so a reordered
      paraphrase still counts.
    """
    if not claim or not candidate:
        return 0.0
    if not polarity_agrees(claim, candidate):
        return 0.0
    if len(_distinctive(claim) & _distinctive(candidate)) < MIN_SHARED_DISTINCTIVE_TOKENS:
        return 0.0
    score = combined_entailment_score(claim, candidate)
    return score if score >= SUPPORT_THRESHOLD else 0.0


def attested_answers(contract: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Answers a person already gave, keyed by the gap they answered.

    Stored on the contract snapshot by ``apply.py``. Keyed lookup, **not**
    lexical matching: a reviewer answering "contact platform-ops after the
    second failed restart" has settled the obligation "escalate if the restart
    fails twice" while sharing almost no vocabulary with it. Matching those two
    by overlap would fail, the question would be asked again, and the loop would
    not terminate. The ``gap_key`` is exact and is the whole point of having one.
    """
    if not isinstance(contract, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in contract.get("human_attested_answers") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("gap_key") or "")
        if key and str(entry.get("answer") or "").strip():
            out[key] = entry
    return out


def resolve_from_context(
    gap: InformationGap,
    *,
    content: dict[str, Any] | None,
    contract: dict[str, Any] | None,
) -> GapResolution:
    """Is the fact already in an earlier answer, in the field, or in a step?"""
    content = content or {}

    # 1. A person already answered exactly this gap. Exact, keyed, and checked
    #    first — this is the branch that makes the loop terminate.
    attested = attested_answers(contract).get(gap.gap_key)
    if attested is not None:
        return GapResolution(
            gap=gap,
            answer_text=str(attested.get("answer"))[:2000],
            answer_source=ANSWER_CONTEXT,
            provenance={
                "attested_in_round": attested.get("round"),
                "answered_by": attested.get("answered_by"),
                "originally": attested.get("source"),
            },
        )

    claim = (gap.claim or "").strip()
    if not claim:
        return GapResolution(gap=gap)

    # 2. A field gap is about a field being empty. It is answered by that field
    #    having content, not by something elsewhere resembling it — a rollback
    #    obligation described inside step 4 does not put anything in
    #    rollback_notes, and treating it as resolved leaves the field empty
    #    forever.
    if gap.target_kind == "field" and gap.target_ref:
        value = content.get(gap.target_ref)
        filled = bool(value) if not isinstance(value, str) else bool(value.strip())
        if not filled:
            return GapResolution(gap=gap)
        return GapResolution(
            gap=gap,
            answer_text=str(value)[:2000] if isinstance(value, str) else None,
            answer_source=ANSWER_CONTEXT if isinstance(value, str) else None,
            provenance={"field": gap.target_ref},
        )

    # 3. The procedure already says it, under different wording than the
    #    validator's threshold recognised.
    candidates = _step_texts(content)
    rollback = str(content.get("rollback_notes") or "").strip()
    if rollback:
        candidates.append(rollback)

    best_score = 0.0
    best_text: str | None = None
    for candidate in candidates:
        score = supports(claim, candidate)
        if score > best_score:
            best_score, best_text = score, candidate

    if best_text is not None:
        return GapResolution(
            gap=gap,
            answer_text=best_text[:2000],
            answer_source=ANSWER_CONTEXT,
            provenance={"match": "playbook_content", "score": round(best_score, 3)},
        )
    return GapResolution(gap=gap)


# --- 2. approved knowledge answers it ---------------------------------------


def _document_sections(document: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for section in getattr(document, "sections", None) or []:
        text = str(getattr(section, "text", "") or "").strip()
        if text:
            out.append((text, section))
    return out


def resolve_from_knowledge(
    gap: InformationGap, documents: list[Any]
) -> GapResolution:
    """Best approved-knowledge section that actually answers this gap."""
    claim = (gap.claim or "").strip()
    if not claim or not documents or gap.kind in KB_UNRESOLVABLE_KINDS:
        return GapResolution(gap=gap)

    best_score = 0.0
    best: tuple[Any, Any, str] | None = None
    for document in documents:
        for text, section in _document_sections(document):
            # ``supports`` carries the polarity guard — a section forbidding the
            # action a gap asks about scores high on overlap and would otherwise
            # be handed to a reviewer as its answer.
            score = supports(claim, text)
            if score > best_score:
                best_score, best = score, (document, section, text)

    if best is None:
        return GapResolution(gap=gap)

    document, section, text = best
    return GapResolution(
        gap=gap,
        answer_text=text[:2000],
        answer_source=ANSWER_KB,
        provenance={
            "evidence_id": str(getattr(document, "evidence_id", "") or "") or None,
            "title": str(getattr(document, "title", "") or "")[:200] or None,
            "section_ref": getattr(section, "section_ref", None),
            "page": getattr(section, "page", None),
            "score": round(best_score, 3),
            # Surfaced so a reviewer can weigh a paraphrase differently from
            # parsed text — the same distinction the generation prompt makes.
            "model_derived": bool(getattr(section, "model_derived", False)),
        },
    )


# --- the pass ----------------------------------------------------------------


def resolve_gaps(
    gaps: list[InformationGap],
    *,
    content: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    documents: list[Any] | None = None,
    retrieval_failed: bool = False,
) -> ResolutionOutcome:
    """Run the resolution ladder over every gap.

    ``retrieval_failed`` is carried through rather than inferred from an empty
    document list: a round with no KB hits because the index was down must not
    read like one where the KB genuinely had nothing to say. The reviewer's
    correct reaction to those two is different.
    """
    documents = documents or []
    resolutions: list[GapResolution] = []

    for gap in gaps:
        resolution = resolve_from_context(gap, content=content, contract=contract)
        if not resolution.resolved and not retrieval_failed:
            resolution = resolve_from_knowledge(gap, documents)
        resolutions.append(resolution)

    if retrieval_failed:
        kb_status = KB_FAILED
    elif not documents:
        kb_status = KB_NO_RESULTS
    else:
        kb_status = KB_OK

    outcome = ResolutionOutcome(resolutions=resolutions, kb_status=kb_status)
    logger.info("playbook_clarification.resolution", kb_status=kb_status, **outcome.counts())
    return outcome


def retrieval_query_for(gaps: list[InformationGap], *, subject: str | None) -> str:
    """One query covering the round's gaps.

    Composed from the playbook subject plus the gap claims rather than issuing
    one retrieval per gap: a round with fifteen gaps would otherwise be fifteen
    vector searches, and the articles that answer them are overwhelmingly the
    same handful.
    """
    parts: list[str] = []
    if subject and subject.strip():
        parts.append(subject.strip())
    for gap in gaps:
        claim = (gap.claim or "").strip()
        if claim:
            parts.append(claim[:300])
    return "\n".join(parts[:20])
