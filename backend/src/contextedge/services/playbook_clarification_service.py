"""The clarification loop, wired to the database.

One entry point per verb — open a round, record answers, apply them — so the
endpoint, a test and a future worker cannot each grow their own notion of what
a round is. The pure logic (which gaps exist, what resolves them, how answers
merge into provenance) lives in ``contextedge.quality.clarification`` and is
tested without a session; this module is the part that reads, writes and spends
money.

Three properties this module is responsible for and nothing else is:

**Reading never writes.** ``clarification_state`` opens no round, retrieves no
knowledge and calls no model. Loading a playbook must not spend an LLM call, and
must not change its history to record who looked — the same rule
``GET /quality`` follows.

**One live round per playbook.** Enforced in the database by a partial unique
index, and here by refusing to open a second. Two open rounds means two sets of
questions about the same defects and an answer recorded against whichever one
the panel happened to load.

**Failure is a state, not an exception.** A retrieval that fails, a model that
returns nothing usable, a revision that comes back without steps — each is
recorded on the round and surfaced, because a reviewer who sees "we could not
compose the questions" can act, and one who sees an empty panel reads it as
"nothing to ask".

Nothing here blocks anything. The playbook can be approved with every question
unanswered, exactly as it can today.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.playbook_clarification import (
    PlaybookClarificationQuestion,
    PlaybookClarificationRound,
)
from contextedge.quality.clarification.apply import (
    answers_payload,
    merge_clarification_into_evidence_refs,
    skipped_payload,
    version_data_from_revision,
)
from contextedge.quality.clarification.gaps import InformationGap, detect_gaps
from contextedge.quality.clarification.kb_resolution import (
    resolve_gaps,
    retrieval_query_for,
)
from contextedge.quality.clarification.states import (
    ANSWER_CARRIED,
    ANSWER_HUMAN,
    ANSWER_KB,
    KB_FAILED,
    LIVE_ROUND_STATUSES,
    MANDATORY,
    Q_ANSWERED,
    Q_OPEN,
    Q_RESOLVED_CONTEXT,
    Q_RESOLVED_KB,
    Q_SKIPPED,
    ROUND_ANSWERED,
    ROUND_APPLIED,
    ROUND_EXHAUSTED,
    ROUND_OPEN,
    ROUND_SATISFIED,
    mandatory_outstanding,
    round_is_answerable,
)
from contextedge.quality.hashing import content_hash
from contextedge.quality.revision import build_content
from contextedge.quality.states import BLOCKING_SEVERITIES

logger = structlog.get_logger()

ORIGIN_APPLY = "clarification_apply"

# Why a round can come back empty without that being reassuring. Stored on the
# round rather than only logged, because the reviewer looking at the panel is
# the person who needs to know the difference.
_NO_INPUTS_NOTE = (
    "Nothing to derive questions from: this playbook has no quality assessment "
    "of its current content and no stored quality contract, which is normal for "
    "one generated before the quality pipeline existed. Assess it — save an edit, "
    "or regenerate it through the current pipeline — and then ask again. This is "
    "not a finding that the playbook is complete."
)


class ClarificationError(Exception):
    """Base for the loop's refusals. Each maps to a distinct HTTP status."""


class RoundAlreadyOpen(ClarificationError):
    """A live round exists. Answer or abandon it before opening another."""


class NoLiveRound(ClarificationError):
    """Nothing is open to answer or apply."""


class MandatoryUnanswered(ClarificationError):
    """Mandatory questions are still outstanding."""

    def __init__(self, outstanding: int) -> None:
        super().__init__(f"{outstanding} mandatory question(s) still unanswered")
        self.outstanding = outstanding


class RevisionFailed(ClarificationError):
    """The model returned nothing usable; the round stays answerable."""


def max_rounds() -> int:
    """How many times the loop may repeat before it stops on its own.

    A clarification loop that can spend an unbounded number of generation calls
    on one playbook is a cost incident waiting for a corpus refresh to trigger
    it. Five is a starting guess; plan §12.3 tracks calibrating it against the
    observed rounds-to-satisfied distribution.
    """
    from contextedge.config import settings

    value = getattr(settings, "playbook_clarification_max_rounds", 5)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 5


# --- loading ------------------------------------------------------------------


async def _resolve_version(
    db: AsyncSession, playbook: Playbook, version: PlaybookVersion | None
) -> PlaybookVersion | None:
    if version is not None:
        return version
    if playbook.current_version_id is None:
        return None
    return await db.get(PlaybookVersion, playbook.current_version_id)


async def live_round(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> PlaybookClarificationRound | None:
    result = await db.execute(
        select(PlaybookClarificationRound)
        .where(
            PlaybookClarificationRound.tenant_id == tenant_id,
            PlaybookClarificationRound.playbook_id == playbook_id,
            PlaybookClarificationRound.status.in_(tuple(LIVE_ROUND_STATUSES)),
        )
        .order_by(PlaybookClarificationRound.round_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_round(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> PlaybookClarificationRound | None:
    result = await db.execute(
        select(PlaybookClarificationRound)
        .where(
            PlaybookClarificationRound.tenant_id == tenant_id,
            PlaybookClarificationRound.playbook_id == playbook_id,
        )
        .order_by(PlaybookClarificationRound.round_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def questions_for(
    db: AsyncSession, tenant_id: uuid.UUID, round_id: uuid.UUID
) -> list[PlaybookClarificationQuestion]:
    """Questions in one round, mandatory first, then in creation order.

    Ordered here rather than in the UI because every consumer wants the same
    order, and a reviewer with time for three answers should not have to hunt
    for the three that block.
    """
    result = await db.execute(
        select(PlaybookClarificationQuestion)
        .where(
            PlaybookClarificationQuestion.tenant_id == tenant_id,
            PlaybookClarificationQuestion.round_id == round_id,
        )
        .order_by(
            PlaybookClarificationQuestion.obligation.asc(),  # 'mandatory' < 'optional'
            PlaybookClarificationQuestion.created_at.asc(),
        )
    )
    return list(result.scalars().all())


async def _next_round_number(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(PlaybookClarificationRound.round_number), 0)).where(
            PlaybookClarificationRound.tenant_id == tenant_id,
            PlaybookClarificationRound.playbook_id == playbook_id,
        )
    )
    return int(result.scalar() or 0) + 1


async def _prior_answers_by_gap(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> dict[str, PlaybookClarificationQuestion]:
    """The most recent answer this playbook has for each gap.

    Carry-forward is half of why the loop converges: a gap that legitimately
    survives into the next round arrives already answered, and the reviewer sees
    what they said rather than being asked again. Newest wins — a later round's
    answer is a revised position, not a duplicate.
    """
    result = await db.execute(
        select(PlaybookClarificationQuestion)
        .join(
            PlaybookClarificationRound,
            PlaybookClarificationQuestion.round_id == PlaybookClarificationRound.id,
        )
        .where(
            PlaybookClarificationQuestion.tenant_id == tenant_id,
            PlaybookClarificationQuestion.playbook_id == playbook_id,
            PlaybookClarificationQuestion.answer_text.isnot(None),
        )
        .order_by(PlaybookClarificationRound.round_number.asc())
    )
    out: dict[str, PlaybookClarificationQuestion] = {}
    for question in result.scalars().all():
        out[question.gap_key] = question
    return out


# --- context ------------------------------------------------------------------


@dataclass
class _Snapshot:
    """Everything a round needs about the playbook as it stands right now."""

    version: PlaybookVersion | None
    content: dict[str, Any]
    content_hash: str
    contract: dict[str, Any] | None
    assessment: Any | None
    findings: list[Any]
    # Whether the assessment we found is actually about the content in front of
    # us. False when there is no assessment at all, or when the playbook has
    # been edited since it was assessed.
    assessment_matches: bool = False

    @property
    def has_inputs(self) -> bool:
        """Whether there was anything to derive questions from.

        A playbook generated before contracts existed, and never assessed
        against its current content, gives the gap detector nothing to read.
        "We found no gaps" and "we had nothing to look at" then produce the
        identical empty result, and only one of them is good news — see
        ``open_round``.
        """
        return bool(self.assessment_matches or self.contract)


async def _snapshot(
    db: AsyncSession, playbook: Playbook, version: PlaybookVersion | None
) -> _Snapshot:
    from contextedge.quality.context_loader import contract_from_evidence_refs
    from contextedge.services.playbook_quality_service import (
        findings_for,
        latest_assessment,
    )

    version = await _resolve_version(db, playbook, version)
    content = build_content(playbook, version)
    digest = content_hash(content)
    refs = getattr(version, "evidence_refs", None)
    contract = contract_from_evidence_refs(refs if isinstance(refs, dict) else None)

    assessment = await latest_assessment(db, playbook.tenant_id, playbook.id)
    findings: list[Any] = []
    matches = False
    if assessment is not None:
        # Findings about content the playbook has since moved away from would
        # generate questions about text nobody can see. The assessment is still
        # shown by the quality panel, flagged as out of date; asking questions
        # from it is a step further than that and is not safe.
        matches = assessment.content_hash == digest
        if matches:
            findings = await findings_for(db, playbook.tenant_id, assessment.id)
        else:
            logger.info(
                "playbook_clarification.assessment_stale_for_questions",
                playbook_id=str(playbook.id),
                assessed=assessment.content_hash[:12],
                live=digest[:12],
            )

    return _Snapshot(
        version=version,
        content=content,
        content_hash=digest,
        contract=contract,
        assessment=assessment,
        findings=findings,
        assessment_matches=matches,
    )


async def _retrieve_for_gaps(
    db: AsyncSession, playbook: Playbook, gaps: list[InformationGap]
) -> tuple[list[Any], bool]:
    """One retrieval covering the round's gaps. Never raises.

    Returns ``(documents, retrieval_failed)``. The flag is carried rather than
    inferred from an empty list: a round with no KB hits because the index was
    down must not read like one where the KB genuinely had nothing, and the
    reviewer's correct reaction to those two differs.
    """
    from contextedge.services.knowledge_retrieval_service import (
        retrieve_knowledge_for_pattern,
    )

    query_extra = retrieval_query_for(gaps, subject=playbook.title)
    try:
        documents = await retrieve_knowledge_for_pattern(
            db,
            playbook.tenant_id,
            pattern_title=playbook.title or "",
            pattern_description=(playbook.description or "") + "\n" + query_extra,
        )
        return list(documents or []), False
    except Exception as exc:  # noqa: BLE001 - a retrieval problem is not a quality verdict
        logger.warning(
            "playbook_clarification.retrieval_failed",
            playbook_id=str(playbook.id),
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return [], True


async def _ontology_terms(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    from contextedge.services.quality_policy_service import active_ontology_terms

    try:
        terms, _version = await active_ontology_terms(db, tenant_id)
        return list(terms or [])
    except Exception:  # noqa: BLE001 - wording must never stop a round
        return []


def _contract_prompt(contract: dict[str, Any] | None) -> str:
    if not contract:
        return ""
    from contextedge.quality.contract import format_contract_obligations

    try:
        return format_contract_obligations(contract)
    except Exception:  # noqa: BLE001
        return ""


# --- opening a round ----------------------------------------------------------


async def open_round(
    db: AsyncSession,
    playbook: Playbook,
    *,
    actor_id: uuid.UUID | None = None,
    version: PlaybookVersion | None = None,
) -> PlaybookClarificationRound:
    """Detect what is missing, resolve what we can, ask about the rest.

    Spends one knowledge retrieval and (at most) two generation calls, so it is
    only ever called from an explicit human action. There is deliberately no
    bulk-open path: opening rounds across a 422-playbook corpus would produce
    thousands of questions nobody answers, and a panel full of unanswered
    questions is worse than no panel.
    """
    existing = await live_round(db, playbook.tenant_id, playbook.id)
    if existing is not None:
        raise RoundAlreadyOpen(
            f"round {existing.round_number} is still {existing.status}"
        )

    snapshot = await _snapshot(db, playbook, version)
    gaps = detect_gaps(
        content=snapshot.content,
        contract=snapshot.contract,
        findings=snapshot.findings,
        evidence_refs=(
            snapshot.version.evidence_refs
            if snapshot.version is not None
            and isinstance(snapshot.version.evidence_refs, dict)
            else None
        ),
    )
    number = await _next_round_number(db, playbook.tenant_id, playbook.id)

    if not gaps:
        # Recorded, not skipped. "We looked and there was nothing to ask" is a
        # fact the reviewer needs before submitting; an absent round is
        # indistinguishable from one nobody ever opened.
        #
        # But an empty result has two very different causes, and only one of
        # them is good news. A playbook generated before quality contracts
        # existed, and never assessed against its current content, gives the
        # detector nothing to read: no findings, no contract, no gate verdict.
        # It produces exactly the same empty list as a playbook we examined
        # closely and found clean. Reporting the second when it is the first
        # tells a reviewer their oldest, least-checked playbooks are the ones
        # with nothing wrong.
        if not snapshot.has_inputs:
            logger.info(
                "playbook_clarification.no_inputs",
                playbook_id=str(playbook.id),
                has_assessment=snapshot.assessment is not None,
                assessment_matches=snapshot.assessment_matches,
                has_contract=snapshot.contract is not None,
            )
        return await _persist_round(
            db,
            playbook,
            number=number,
            snapshot=snapshot,
            status=ROUND_SATISFIED,
            actor_id=actor_id,
            gap_count=0,
            questions=[],
            resolutions=[],
            kb_status="ok",
            notes=None if snapshot.has_inputs else _NO_INPUTS_NOTE,
        )

    if number > max_rounds():
        # Returned, not raised. The bound being reached is a fact about this
        # playbook that a reviewer has to act on, and a raise would roll the row
        # back with the request — leaving the loop able to be restarted forever
        # by anyone who clicks the button again, which is the opposite of a
        # bound.
        logger.warning(
            "playbook_clarification.exhausted",
            playbook_id=str(playbook.id),
            rounds=max_rounds(),
            remaining_gaps=len(gaps),
        )
        return await _persist_round(
            db,
            playbook,
            number=number,
            snapshot=snapshot,
            status=ROUND_EXHAUSTED,
            actor_id=actor_id,
            gap_count=len(gaps),
            questions=[],
            resolutions=[],
            kb_status="ok",
            notes=(
                f"{len(gaps)} gap(s) remain after {max_rounds()} rounds. "
                "The loop stops here; this needs a decision rather than another question."
            ),
        )

    documents, retrieval_failed = await _retrieve_for_gaps(db, playbook, gaps)
    outcome = resolve_gaps(
        gaps,
        content=snapshot.content,
        contract=snapshot.contract,
        documents=documents,
        retrieval_failed=retrieval_failed,
    )

    unresolved = outcome.unresolved
    generated = None
    if unresolved:
        from contextedge.ai.generators.clarification_generator import generate_questions

        generated = await generate_questions(
            unresolved,
            content=snapshot.content,
            contract_prompt=_contract_prompt(snapshot.contract),
            ontology_terms=await _ontology_terms(db, playbook.tenant_id),
            kb_status=outcome.kb_status,
            kb_resolved_count=len(outcome.resolved),
            kb_searched_count=len(documents),
            tenant_id=playbook.tenant_id,
            db=db,
        )

    return await _persist_round(
        db,
        playbook,
        number=number,
        snapshot=snapshot,
        status=ROUND_OPEN,
        actor_id=actor_id,
        gap_count=len(gaps),
        questions=(generated.questions if generated else []),
        resolutions=outcome.resolved,
        kb_status=outcome.kb_status,
        gap_by_key={gap.gap_key: gap for gap in gaps},
        prompt_name=(generated.prompt_name if generated else None),
        prompt_version=(generated.prompt_version if generated else None),
        model_provenance=(generated.model_provenance if generated else None),
        generation_error=(generated.error if generated else None),
    )


async def _persist_round(
    db: AsyncSession,
    playbook: Playbook,
    *,
    number: int,
    snapshot: _Snapshot,
    status: str,
    actor_id: uuid.UUID | None,
    gap_count: int,
    questions: list[Any],
    resolutions: list[Any],
    kb_status: str,
    gap_by_key: dict[str, InformationGap] | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    model_provenance: dict[str, Any] | None = None,
    generation_error: str | None = None,
    notes: str | None = None,
) -> PlaybookClarificationRound:
    """Write the round and its questions, carrying forward prior answers.

    ``gap_by_key`` is threaded in rather than kept in module state: two
    reviewers opening rounds on different playbooks at the same time share this
    process, and a module-level lookup would give one of them the other's gaps.
    """
    gap_by_key = gap_by_key or {}
    now = datetime.now(UTC)
    round_row = PlaybookClarificationRound(
        tenant_id=playbook.tenant_id,
        playbook_id=playbook.id,
        round_number=number,
        content_hash=snapshot.content_hash,
        assessment_id=(
            snapshot.assessment.id
            if snapshot.assessment is not None
            and snapshot.assessment.content_hash == snapshot.content_hash
            else None
        ),
        status=status,
        gap_count=gap_count,
        kb_status=kb_status,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        model_provenance=model_provenance,
        generation_error=generation_error,
        opened_by=actor_id,
        notes=notes,
        closed_at=now if status not in LIVE_ROUND_STATUSES else None,
    )
    db.add(round_row)
    await db.flush()

    prior = await _prior_answers_by_gap(db, playbook.tenant_id, playbook.id)
    rows: list[PlaybookClarificationQuestion] = []

    # Gaps the system answered for itself. Still shown, still editable: silently
    # folding a retrieval into the playbook would let a wrong match enter as
    # though a person had approved it.
    for resolution in resolutions:
        gap = resolution.gap
        rows.append(
            PlaybookClarificationQuestion(
                tenant_id=playbook.tenant_id,
                round_id=round_row.id,
                playbook_id=playbook.id,
                gap_key=gap.gap_key,
                gap_kind=gap.kind,
                gap_origin=gap.origin,
                source_finding_id=gap.source_finding_id,
                target_kind=gap.target_kind,
                target_ref=gap.target_ref,
                claim=gap.claim,
                severity=gap.severity,
                question_text=(
                    resolution.gap.explanation
                    or f"Confirm what the playbook should say about: {gap.claim or gap.kind}"
                )[:2000],
                why_it_matters=None,
                # An answer we found ourselves never makes a demand of a
                # person: it is already settled unless they disagree.
                obligation="optional",
                answer_kind="text",
                status=(
                    Q_RESOLVED_KB
                    if resolution.answer_source == ANSWER_KB
                    else Q_RESOLVED_CONTEXT
                ),
                answer_text=resolution.answer_text,
                answer_source=resolution.answer_source,
                answer_provenance=resolution.provenance or None,
            )
        )

    for generated in questions:
        carried = prior.get(generated.gap_key)
        gap = gap_by_key.get(generated.gap_key)
        rows.append(
            PlaybookClarificationQuestion(
                tenant_id=playbook.tenant_id,
                round_id=round_row.id,
                playbook_id=playbook.id,
                gap_key=generated.gap_key,
                gap_kind=getattr(gap, "kind", None) or "unknown",
                gap_origin=getattr(gap, "origin", None) or "finding",
                source_finding_id=getattr(gap, "source_finding_id", None),
                target_kind=getattr(gap, "target_kind", None) or "playbook",
                target_ref=getattr(gap, "target_ref", None),
                claim=getattr(gap, "claim", None),
                severity=getattr(gap, "severity", None),
                question_text=generated.question,
                why_it_matters=generated.why_it_matters,
                obligation=generated.obligation,
                answer_kind=generated.answer_kind,
                choices=list(generated.choices or []),
                expected_format=generated.expected_format,
                # Carried forward rather than re-asked. The reviewer sees what
                # they said last round and can revise it.
                status=Q_ANSWERED if carried is not None else Q_OPEN,
                answer_text=carried.answer_text if carried is not None else None,
                answer_source=ANSWER_CARRIED if carried is not None else None,
                answer_provenance=(
                    {"carried_from_question_id": str(carried.id)}
                    if carried is not None
                    else None
                ),
                answered_by=carried.answered_by if carried is not None else None,
                answered_at=carried.answered_at if carried is not None else None,
            )
        )

    for row in rows:
        db.add(row)
    await db.flush()

    round_row.question_count = len(rows)
    round_row.mandatory_count = sum(1 for r in rows if r.obligation == MANDATORY)
    round_row.resolved_from_kb_count = sum(
        1 for r in rows if r.answer_source == ANSWER_KB
    )
    round_row.resolved_from_context_count = sum(
        1 for r in rows if r.status == Q_RESOLVED_CONTEXT
    )
    if round_row.status == ROUND_OPEN and round_is_answerable(rows):
        # Every question was answered by carry-forward or by us. The round is
        # ready to apply without the reviewer typing anything.
        round_row.status = ROUND_ANSWERED
    await db.flush()

    logger.info(
        "playbook_clarification.round_opened",
        tenant_id=str(playbook.tenant_id),
        playbook_id=str(playbook.id),
        round=number,
        status=round_row.status,
        gaps=gap_count,
        questions=len(rows),
        mandatory=round_row.mandatory_count,
        resolved_from_kb=round_row.resolved_from_kb_count,
        resolved_from_context=round_row.resolved_from_context_count,
        kb_status=kb_status,
    )
    return round_row


# --- rewriting the questions ----------------------------------------------------

# How many times one round's questions may be rewritten. Each is a generation
# call, so this is a spend bound, not a usability one — three attempts is
# already well past the point where the problem is the prompt rather than the
# wording.
MAX_QUESTION_REGENERATIONS = 3


class RegenerationLimitReached(ClarificationError):
    """The round's questions have been rewritten as often as allowed."""


class NothingToRegenerate(ClarificationError):
    """Every question in the round is already settled."""


def _gap_from_question(question: PlaybookClarificationQuestion) -> InformationGap:
    """Rebuild the gap a stored question was about.

    Reconstructed from the row rather than re-detected, so a rewrite asks about
    exactly the gaps this round was opened for. Re-running detection would let
    the gap set shift underneath answers a reviewer has already given, and a
    question whose gap changed is a question their answer no longer fits.

    ``blocking`` is derived from severity, which reproduces it exactly: a
    finding gap is blocking iff its severity blocks, and the contract, gate and
    structure gaps that are unconditionally blocking are all stored at ``major``
    or ``critical``. It is derived rather than stored because getting it from
    the row is what keeps a mandatory question mandatory across a rewrite.

    ``explanation`` is not stored, so a rewrite has slightly less context than
    the first attempt. The reviewer's own note more than replaces it.
    """
    return InformationGap(
        kind=question.gap_kind,
        origin=question.gap_origin,
        claim=question.claim,
        target_kind=question.target_kind,
        target_ref=question.target_ref,
        severity=question.severity or "minor",
        blocking=(question.severity or "") in BLOCKING_SEVERITIES,
        source_finding_id=question.source_finding_id,
    )


async def regenerate_questions(
    db: AsyncSession,
    playbook: Playbook,
    *,
    actor_id: uuid.UUID | None = None,
    guidance: str | None = None,
) -> PlaybookClarificationRound:
    """Ask again for the wording of the questions nobody has answered yet.

    Exists because the alternative is abandoning the round, which spends one of
    the loop's bounded rounds on a defect in *our* output rather than on the
    playbook. It happens: a truncated model response falls back to raw
    validator text, and a reviewer handed three validator explanations where
    they were promised questions has no way forward that does not cost them a
    round.

    Two things this must not do, and both are easy to get wrong:

    **It must not touch an answered question.** Rewriting the text of a question
    somebody already answered orphans the answer — it is now an answer to a
    question that was never asked. Answered, skipped and KB-resolved rows are
    left exactly as they are.

    **It must not re-detect gaps.** The round's gap set is fixed at open time.
    Recomputing it here would let the set shift underneath answers already
    given, and would quietly turn a rewrite into a new round without the round
    counter noticing.
    """
    current = await live_round(db, playbook.tenant_id, playbook.id)
    if current is None:
        raise NoLiveRound("no clarification round is open for this playbook")

    if current.regeneration_count >= MAX_QUESTION_REGENERATIONS:
        raise RegenerationLimitReached(
            f"these questions have already been rewritten "
            f"{current.regeneration_count} times; the problem is unlikely to be "
            "the wording"
        )

    rows = await questions_for(db, playbook.tenant_id, current.id)
    open_rows = [row for row in rows if row.status == Q_OPEN]
    if not open_rows:
        raise NothingToRegenerate(
            "every question in this round is already answered, skipped or resolved"
        )

    gaps = [_gap_from_question(row) for row in open_rows]
    by_key = {row.gap_key: row for row in open_rows}
    previous = {row.gap_key: row.question_text for row in open_rows}

    snapshot = await _snapshot(db, playbook, None)
    from contextedge.ai.generators.clarification_generator import generate_questions

    generated = await generate_questions(
        gaps,
        content=snapshot.content,
        contract_prompt=_contract_prompt(snapshot.contract),
        ontology_terms=await _ontology_terms(db, playbook.tenant_id),
        kb_status=current.kb_status,
        # The KB pass is not re-run: these gaps already survived it, and paying
        # for a second retrieval to reach the same conclusion is waste.
        kb_resolved_count=current.resolved_from_kb_count,
        kb_searched_count=0,
        guidance=guidance,
        previous_questions=previous,
        tenant_id=playbook.tenant_id,
        db=db,
    )

    rewritten = 0
    for question in generated.questions:
        row = by_key.get(question.gap_key)
        if row is None:  # pragma: no cover - generator already drops unknown keys
            continue
        if question.is_fallback:
            # The fallback is the defect text the reviewer is trying to get away
            # from. Replacing a real question with it, or replacing it with
            # itself, is not a rewrite — leave the row alone and let the round's
            # note explain why nothing changed.
            continue
        row.question_text = question.question
        row.why_it_matters = question.why_it_matters
        row.obligation = question.obligation
        row.answer_kind = question.answer_kind
        row.choices = list(question.choices or [])
        row.expected_format = question.expected_format
        rewritten += 1

    current.regeneration_count += 1
    current.prompt_name = generated.prompt_name or current.prompt_name
    current.prompt_version = generated.prompt_version or current.prompt_version
    current.model_provenance = generated.model_provenance or current.model_provenance
    current.generation_error = (
        generated.error
        if rewritten
        else (
            generated.error
            or "The rewrite produced nothing usable; the questions are unchanged."
        )
    )
    await db.flush()

    logger.info(
        "playbook_clarification.questions_regenerated",
        playbook_id=str(playbook.id),
        round=current.round_number,
        attempt=current.regeneration_count,
        candidates=len(open_rows),
        rewritten=rewritten,
        had_guidance=bool(guidance and guidance.strip()),
    )
    return current


# --- answering ----------------------------------------------------------------


@dataclass
class AnswerInput:
    question_id: uuid.UUID
    answer_text: str | None = None
    skip: bool = False


async def record_answers(
    db: AsyncSession,
    playbook: Playbook,
    answers: list[AnswerInput],
    *,
    actor_id: uuid.UUID | None = None,
) -> PlaybookClarificationRound:
    """Record answers and skips against the live round.

    A skip on a mandatory question is refused rather than ignored. Silently
    dropping it would leave the reviewer believing they had disposed of the
    question, and the round would sit un-appliable with no explanation.
    """
    current = await live_round(db, playbook.tenant_id, playbook.id)
    if current is None:
        raise NoLiveRound("no clarification round is open for this playbook")

    rows = await questions_for(db, playbook.tenant_id, current.id)
    by_id = {row.id: row for row in rows}
    now = datetime.now(UTC)

    for answer in answers:
        row = by_id.get(answer.question_id)
        if row is None:
            raise ClarificationError(
                f"question {answer.question_id} is not part of the open round"
            )
        if answer.skip:
            if row.obligation == MANDATORY:
                raise ClarificationError(
                    "a mandatory question cannot be skipped: " + row.question_text[:200]
                )
            row.status = Q_SKIPPED
            row.answer_text = None
            row.answer_source = None
            row.answered_by = actor_id
            row.answered_at = now
            continue

        text = (answer.answer_text or "").strip()
        if not text:
            # Clearing an answer is a legitimate edit; it reopens the question
            # rather than recording an empty one, which the database would
            # reject anyway.
            row.status = Q_OPEN
            row.answer_text = None
            row.answer_source = None
            row.answered_by = None
            row.answered_at = None
            continue

        row.status = Q_ANSWERED
        row.answer_text = text[:8000]
        row.answer_source = ANSWER_HUMAN
        row.answered_by = actor_id
        row.answered_at = now
        # A reviewer editing a KB prefill makes it theirs. Keeping the
        # retrieval provenance under it would attribute their words to an
        # article that does not contain them.
        if row.answer_provenance and row.answer_provenance.get("evidence_id"):
            row.answer_provenance = {
                "superseded_kb_match": row.answer_provenance,
                "edited_by_reviewer": True,
            }

    await db.flush()

    refreshed = await questions_for(db, playbook.tenant_id, current.id)
    outstanding = mandatory_outstanding(refreshed)
    current.status = ROUND_OPEN if outstanding else ROUND_ANSWERED
    await db.flush()

    logger.info(
        "playbook_clarification.answers_recorded",
        playbook_id=str(playbook.id),
        round=current.round_number,
        recorded=len(answers),
        outstanding_mandatory=len(outstanding),
        status=current.status,
    )
    return current


async def abandon_round(
    db: AsyncSession, playbook: Playbook, *, reason: str | None = None
) -> PlaybookClarificationRound | None:
    """Close the live round without applying it."""
    current = await live_round(db, playbook.tenant_id, playbook.id)
    if current is None:
        return None
    from contextedge.quality.clarification.states import ROUND_ABANDONED

    current.status = ROUND_ABANDONED
    current.closed_at = datetime.now(UTC)
    current.notes = reason or current.notes
    await db.flush()
    return current


# --- applying -----------------------------------------------------------------


async def apply_round(
    db: AsyncSession,
    playbook: Playbook,
    *,
    actor_id: uuid.UUID | None = None,
    open_next: bool = True,
) -> dict[str, Any]:
    """Revise the playbook from the answers, then continue or stop.

    ``open_next`` defaults to true because the reviewer pressing Apply is asking
    to continue the loop, and stopping to make them press a second button after
    every round is how a five-round loop becomes a one-round one. The cost is
    bounded by ``max_rounds``.
    """
    current = await live_round(db, playbook.tenant_id, playbook.id)
    if current is None:
        raise NoLiveRound("no clarification round is open for this playbook")

    rows = await questions_for(db, playbook.tenant_id, current.id)
    outstanding = mandatory_outstanding(rows)
    if outstanding:
        raise MandatoryUnanswered(len(outstanding))

    answers = answers_payload(rows)
    skipped = skipped_payload(rows)
    if not answers:
        raise RevisionFailed(
            "the round has no answers to apply — every question was skipped"
        )

    snapshot = await _snapshot(db, playbook, None)
    if snapshot.version is None:
        raise RevisionFailed("the playbook has no version to revise")

    documents, _failed = await _retrieve_for_gaps(db, playbook, [])
    product_label = await _product_label(db, playbook.tenant_id)

    from contextedge.ai.generators.clarification_generator import revise_playbook

    try:
        revised = await revise_playbook(
            current_playbook=snapshot.content,
            answers=answers,
            skipped=skipped,
            contract_prompt=_contract_prompt(snapshot.contract),
            knowledge_sources=documents,
            product_label=product_label,
            tenant_id=playbook.tenant_id,
            db=db,
            round_number=current.round_number,
        )
    except Exception as exc:  # noqa: BLE001 - recorded, not raised past the caller
        current.generation_error = f"revision failed: {type(exc).__name__}: {str(exc)[:200]}"
        await db.flush()
        logger.exception(
            "playbook_clarification.revision_failed",
            playbook_id=str(playbook.id),
            round=current.round_number,
        )
        raise RevisionFailed(current.generation_error) from exc

    steps = revised.get("steps") if isinstance(revised, dict) else None
    if not isinstance(steps, list) or not steps:
        # A revision that returns no steps is a failed revision, not an
        # instruction to empty the playbook. Writing it would destroy a working
        # procedure because a model truncated its output — which is exactly how
        # this codebase lost playbooks before the token budget was fixed.
        current.generation_error = "revision returned no steps; nothing was written"
        await db.flush()
        raise RevisionFailed(current.generation_error)

    evidence_refs = merge_clarification_into_evidence_refs(
        snapshot.version.evidence_refs
        if isinstance(snapshot.version.evidence_refs, dict)
        else None,
        round_id=current.id,
        round_number=current.round_number,
        answers=answers,
        skipped=skipped,
    )
    version_data = version_data_from_revision(
        revised, previous=snapshot.version, evidence_refs=evidence_refs
    )

    from contextedge.services.playbook_service import create_playbook_version

    new_version = await create_playbook_version(
        db, playbook, version_data, origin=ORIGIN_APPLY
    )
    # Set after creation because ``create_playbook_version`` does not take them.
    # Neither field is in the content hash, so the assessment it already ran is
    # unaffected — but the lineage matters to a reviewer reading the diff.
    new_version.derived_from_version_id = snapshot.version.id
    new_version.created_by = actor_id
    new_version.last_edited_by = actor_id
    await db.flush()

    current.status = ROUND_APPLIED
    current.applied_version_id = new_version.id
    current.closed_at = datetime.now(UTC)
    await db.flush()

    logger.info(
        "playbook_clarification.round_applied",
        playbook_id=str(playbook.id),
        round=current.round_number,
        answers=len(answers),
        skipped=len(skipped),
        new_version=str(new_version.id),
        semantic_version=new_version.semantic_version,
    )

    result: dict[str, Any] = {
        "applied_round": current,
        "version": new_version,
        "answers_applied": len(answers),
        "next_round": None,
        "limit_reached": False,
    }

    if open_next:
        try:
            following = await open_round(
                db, playbook, actor_id=actor_id, version=new_version
            )
        except RoundAlreadyOpen:  # pragma: no cover - defensive
            following = await live_round(db, playbook.tenant_id, playbook.id)
        result["next_round"] = following
        result["limit_reached"] = bool(
            following is not None and following.status == ROUND_EXHAUSTED
        )
    return result


async def _product_label(db: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    from contextedge.services.quality_policy_service import active_product_label

    try:
        return await active_product_label(db, tenant_id)
    except Exception:  # noqa: BLE001
        return None


# --- reading ------------------------------------------------------------------


async def submission_readiness(
    db: AsyncSession, playbook: Playbook, version: PlaybookVersion | None = None
) -> dict[str, Any]:
    """Whether the playbook is ready for a person to submit.

    Reports; never acts. Consistent with shadow mode, the transition stays an
    explicit human action through the existing endpoint — a system that moves
    playbooks forward on its own judgement is the thing the support
    organisation rejected 28 playbooks for.

    ``blocked_reasons`` is always populated when ``ready`` is false. A gate that
    refuses without saying why is a gate that gets bypassed.
    """
    from contextedge.services.playbook_quality_service import publication_readiness

    version = await _resolve_version(db, playbook, version)
    quality = await publication_readiness(db, playbook, version)

    current = await live_round(db, playbook.tenant_id, playbook.id)
    outstanding: list[Any] = []
    if current is not None:
        outstanding = mandatory_outstanding(
            await questions_for(db, playbook.tenant_id, current.id)
        )

    reasons: list[str] = []
    if outstanding:
        reasons.append("mandatory_questions_outstanding")
    if not quality.get("ready"):
        reason = quality.get("blocked_reason") or "quality_not_ready"
        reasons.append(f"quality:{reason}")

    return {
        "ready": not reasons,
        "blocked_reasons": reasons,
        "outstanding_mandatory": len(outstanding),
        "open_round_id": current.id if current is not None else None,
        "open_round_status": current.status if current is not None else None,
        "quality": quality,
    }


async def clarification_state(
    db: AsyncSession, playbook: Playbook, version: PlaybookVersion | None = None
) -> dict[str, Any]:
    """Everything the clarification panel needs. Read-only.

    Opens no round, retrieves nothing, calls no model. Loading a playbook must
    not spend an LLM call — and must not change its history to record who
    looked.
    """
    version = await _resolve_version(db, playbook, version)
    live_hash = content_hash(build_content(playbook, version))

    current = await live_round(db, playbook.tenant_id, playbook.id)
    shown = current or await latest_round(db, playbook.tenant_id, playbook.id)
    questions = (
        await questions_for(db, playbook.tenant_id, shown.id) if shown is not None else []
    )

    return {
        "playbook_id": playbook.id,
        "content_hash": live_hash,
        "round": shown,
        "questions": questions,
        # False when the round's questions are about content the playbook has
        # since moved away from. The panel must say so rather than presenting
        # stale questions as current.
        "matches_current_content": bool(
            shown is not None and shown.content_hash == live_hash
        ),
        "has_live_round": current is not None,
        "outstanding_mandatory": len(mandatory_outstanding(questions)),
        "max_rounds": max_rounds(),
        "submission": await submission_readiness(db, playbook, version),
    }
