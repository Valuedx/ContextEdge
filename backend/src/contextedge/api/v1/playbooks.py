import difflib
import json
import uuid as uuid_mod
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from contextedge.api.v1.evidence import _attach_source_references
from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.playbook import Playbook, PlaybookEvidenceLink, PlaybookVersion
from contextedge.schemas.playbook import (
    PlaybookBulkTransition,
    PlaybookCreate,
    PlaybookQualityResponse,
    PlaybookQualitySummary,
    PlaybookResponse,
    PlaybookRollbackRequest,
    PlaybookTransition,
    PlaybookUpdate,
    PlaybookVersionCreate,
    PlaybookVersionDiffResponse,
    PlaybookVersionForkRequest,
    PlaybookVersionResponse,
    PlaybookVersionUpdate,
)
from contextedge.schemas.playbook_clarification import (
    ClarificationAnswersRequest,
    ClarificationApplyRequest,
    ClarificationApplyResponse,
    ClarificationRegenerateRequest,
    ClarificationSubmissionReadiness,
    PlaybookClarificationResponse,
)
from contextedge.services.approval_policy_service import (
    ApprovalPolicyViolation,
    check_automation_mode,
    load_approval_policy,
)
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.playbook_editing import (
    PlaybookEditValidationError,
    normalize_steps,
    validate_steps,
    validate_version_fields,
)
from contextedge.services.playbook_service import (
    DuplicateVersionError,
    InvalidTransitionError,
    create_playbook_version,
    transition_playbook,
)
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.services.skill_registry_service import (
    UnresolvedSkillReference,
    validate_step_bindings,
)

router = APIRouter()
logger = structlog.get_logger()


def _version_payload(version: PlaybookVersion) -> dict:
    return {
        "trigger_conditions": version.trigger_conditions or {},
        "branching_logic": version.branching_logic or {},
        "inputs": version.inputs or [],
        "outputs": version.outputs or [],
        "steps": version.steps or [],
        "rollback_notes": version.rollback_notes,
        "evidence_refs": version.evidence_refs,
        # Surfaced to the reviewer console: these are the decisions the
        # generator deliberately did not make.
        "conflicts": version.conflicts,
        "playbook_confidence": version.playbook_confidence,
        "execution_confidence_guidance": version.execution_confidence_guidance,
        # Required for fork and rollback: omitting it dropped verification
        # policy from both the diff and any copy-forward.
        "verification_policy": version.verification_policy,
    }


def _conflict(code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message, **extra},
    )


async def _load_tenant_playbook(
    db: DbSession, playbook_id: UUID, tenant_id: UUID
) -> Playbook:
    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return playbook


async def _load_playbook_version(
    db: DbSession, playbook_id: UUID, version_id: UUID
) -> PlaybookVersion:
    result = await db.execute(
        select(PlaybookVersion).where(
            PlaybookVersion.id == version_id,
            PlaybookVersion.playbook_id == playbook_id,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Playbook version not found")
    return version


def _should_embed_draft(playbook: Playbook) -> bool:
    """Approved playbooks keep the published fingerprint until this draft
    itself is approved (N3). Candidates embed the current/top version so
    the first approve is not the first semantic write."""
    return playbook.lifecycle_state != "approved"


async def _latest_edit_notes(
    db: DbSession, tenant_id: UUID, version_ids: list[UUID]
) -> dict[str, str]:
    """Most recent non-empty edit_note per version, from audit_logs."""
    if not version_ids:
        return {}
    from contextedge.models.audit import AuditLog

    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == "playbook.version_edited",
            AuditLog.resource_type == "playbook_version",
            AuditLog.resource_id.in_([str(vid) for vid in version_ids]),
        )
        .order_by(AuditLog.timestamp.desc())
    )
    notes: dict[str, str] = {}
    for row in result.scalars():
        rid = row.resource_id
        if not rid or rid in notes:
            continue
        details = row.details if isinstance(row.details, dict) else {}
        note = details.get("edit_note")
        if isinstance(note, str) and note.strip():
            notes[rid] = note.strip()
    return notes


def _version_diff(base: PlaybookVersion | None, target: PlaybookVersion) -> tuple[list[str], str]:
    base_payload = _version_payload(base) if base is not None else {}
    target_payload = _version_payload(target)
    changed_fields = sorted(
        key
        for key in set(base_payload) | set(target_payload)
        if base_payload.get(key) != target_payload.get(key)
    )
    diff = "\n".join(
        difflib.unified_diff(
            json.dumps(base_payload, indent=2, sort_keys=True).splitlines(),
            json.dumps(target_payload, indent=2, sort_keys=True).splitlines(),
            fromfile=base.semantic_version if base is not None else "none",
            tofile=target.semantic_version,
            lineterm="",
        )
    )
    return changed_fields, diff


@router.get("", response_model=list[PlaybookResponse])
async def list_playbooks(
    db: DbSession,
    user: AuthUser,
    lifecycle_state: str | None = None,
    domain_id: UUID | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_quality: bool = Query(
        False,
        description=(
            "Attach the current quality assessment summary to each row. Off by "
            "default so existing callers pay nothing for it."
        ),
    ),
):
    query_stmt = select(Playbook).where(Playbook.tenant_id == user.tenant_id)
    if lifecycle_state:
        query_stmt = query_stmt.where(Playbook.lifecycle_state == lifecycle_state)
    if domain_id:
        query_stmt = query_stmt.where(Playbook.domain_id == domain_id)

    if q and q.strip():
        query_clean = q.strip().lstrip('#').strip()
        from sqlalchemy import or_

        from contextedge.models.episode import EpisodeEvidenceLink
        from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
        from contextedge.models.pattern import PatternEvidenceLink
        from contextedge.models.playbook import PlaybookEvidenceLink

        pb_direct = select(Playbook.id).where(
            Playbook.tenant_id == user.tenant_id,
            or_(
                Playbook.title.ilike(f"%{query_clean}%"),
                Playbook.description.ilike(f"%{query_clean}%"),
                Playbook.stable_key.ilike(f"%{query_clean}%"),
            )
        )

        raw_ticket_ids = select(RawEvidenceObject.id).where(
            or_(
                RawEvidenceObject.raw_payload["ticket_number"].astext.ilike(f"%{query_clean}%"),
                RawEvidenceObject.raw_payload["ticketNumber"].astext.ilike(f"%{query_clean}%"),
                RawEvidenceObject.raw_payload["number"].astext.ilike(f"%{query_clean}%"),
                RawEvidenceObject.raw_payload["display_id"].astext.ilike(f"%{query_clean}%"),
                RawEvidenceObject.external_id.ilike(f"%{query_clean}%"),
            )
        )

        matching_ev_ids = select(EvidenceItem.id).where(
            EvidenceItem.tenant_id == user.tenant_id,
            or_(
                EvidenceItem.title.ilike(f"%{query_clean}%"),
                EvidenceItem.raw_object_ref.in_(raw_ticket_ids),
            )
        )

        pb_via_ver_link = select(PlaybookVersion.playbook_id).join(
            PlaybookEvidenceLink, PlaybookEvidenceLink.playbook_version_id == PlaybookVersion.id
        ).where(
            PlaybookEvidenceLink.evidence_id.in_(matching_ev_ids)
        )

        matching_ep_ids = select(EpisodeEvidenceLink.episode_id).where(
            EpisodeEvidenceLink.evidence_id.in_(matching_ev_ids)
        )
        matching_pat_ids = select(PatternEvidenceLink.pattern_id).where(
            PatternEvidenceLink.episode_id.in_(matching_ep_ids)
        )
        pb_via_pattern = select(Playbook.id).where(
            Playbook.pattern_id.in_(matching_pat_ids)
        )

        query_stmt = query_stmt.where(
            or_(
                Playbook.id.in_(pb_direct),
                Playbook.id.in_(pb_via_ver_link),
                Playbook.id.in_(pb_via_pattern),
            )
        )

    if lifecycle_state == "candidate":
        query_stmt = (
            query_stmt.outerjoin(
                PlaybookVersion, PlaybookVersion.id == Playbook.current_version_id
            )
            .order_by(
                func.coalesce(PlaybookVersion.playbook_confidence, 0.0).desc(),
                Playbook.updated_at.desc(),
            )
        )
    else:
        query_stmt = query_stmt.order_by(Playbook.updated_at.desc())

    query_stmt = query_stmt.limit(limit).offset(offset)
    result = await db.execute(query_stmt)
    playbooks = list(result.scalars().all())
    if not playbooks:
        return []

    from contextedge.models.pattern import Pattern

    pb_ids = [p.id for p in playbooks]
    ver_result = await db.execute(
        select(PlaybookVersion.playbook_id, PlaybookVersion.playbook_confidence).where(
            PlaybookVersion.playbook_id.in_(pb_ids)
        )
    )
    ver_map = {row[0]: row[1] for row in ver_result.all() if row[1] is not None}

    pat_ids = [p.pattern_id for p in playbooks if p.pattern_id]
    pat_map = {}
    if pat_ids:
        pat_result = await db.execute(
            select(Pattern.id, Pattern.confidence).where(Pattern.id.in_(pat_ids))
        )
        pat_map = {row[0]: row[1] for row in pat_result.all() if row[1] is not None}

    # Quality is opt-in and batched: three queries for the whole page rather
    # than three per row. The review list is the screen this data exists to
    # improve, so making it slower would be a poor way to introduce it.
    quality_map: dict = {}
    if include_quality and playbooks:
        from contextedge.services.playbook_quality_service import (
            assessments_for_playbooks,
            finding_counts_for,
            summarize,
        )

        from contextedge.quality import build_content
        from contextedge.quality.hashing import content_hash

        assessments = await assessments_for_playbooks(db, user.tenant_id, pb_ids)
        counts = await finding_counts_for(
            db, user.tenant_id, [a.id for a in assessments.values()]
        )
        # Current versions as objects, not just their confidence — the live
        # hash spans shell and version, so the steps are needed too.
        current_ids = [pb.current_version_id for pb in playbooks if pb.current_version_id]
        current_versions: dict = {}
        if current_ids:
            rows = await db.execute(
                select(PlaybookVersion).where(PlaybookVersion.id.in_(current_ids))
            )
            current_versions = {version.id: version for version in rows.scalars().all()}
        # The live hash is recomputed per playbook to answer "is this verdict
        # still about what the row shows?" — the single most misleading thing
        # a list can get wrong, because a stale assessment looks exactly as
        # healthy as a current one.
        for pb in playbooks:
            assessment = assessments.get(pb.id)
            version = current_versions.get(pb.current_version_id)
            live_hash = content_hash(build_content(pb, version))
            # Built as the model, not left as the service's dict. Pydantic
            # does not revalidate an already-constructed instance, so a dict
            # assigned to `r.quality` below would survive all the way to
            # serialization and only fail there.
            quality_map[pb.id] = PlaybookQualitySummary.model_validate(
                summarize(
                    assessment,
                    live_content_hash=live_hash,
                    finding_counts=counts.get(assessment.id) if assessment else None,
                )
            )

    resp_list = []
    for pb in playbooks:
        r = PlaybookResponse.model_validate(pb)
        conf = ver_map.get(pb.id)
        if conf is None and pb.pattern_id:
            conf = pat_map.get(pb.pattern_id)
        r.confidence = float(conf) if conf is not None else 0.8
        if include_quality:
            r.quality = quality_map.get(pb.id)
        resp_list.append(r)

    return resp_list


@router.post("", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_playbook(body: PlaybookCreate, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    await assert_policy_assignment(db, user.tenant_id, body.approval_policy_id, "approval")
    stable_key = f"pb-{uuid_mod.uuid4().hex[:12]}"
    playbook = Playbook(
        tenant_id=user.tenant_id,
        domain_id=body.domain_id,
        stable_key=stable_key,
        title=body.title,
        description=body.description,
        risk_tier=body.risk_tier,
        automation_mode=body.automation_mode,
        approval_policy_id=body.approval_policy_id,
        owner_user_id=user.user_id,
        pattern_id=body.pattern_id,
    )
    db.add(playbook)
    await db.flush()
    await db.refresh(playbook)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.created",
        resource_type="playbook",
        resource_id=str(playbook.id),
    )
    return playbook


@router.get("/{playbook_id}", response_model=PlaybookResponse)
async def get_playbook(playbook_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return playbook


@router.get("/{playbook_id}/quality", response_model=PlaybookQualityResponse)
async def get_playbook_quality(playbook_id: UUID, db: DbSession, user: AuthUser):
    """The current quality assessment for this playbook, with its findings.

    No role beyond tenant membership, matching the other read routes on this
    router: a quality finding is information a reviewer needs before they
    decide, and putting it behind a higher bar than the playbook it describes
    would leave the person doing the reviewing unable to see it.

    Read-only. Opening a playbook must not mint a revision or trigger an
    assessment, or the history stops recording what happened to the content
    and starts recording who looked at it.

    Render order is part of the contract. ``summary.structure`` is a
    precondition, not a fourth tab: an empty procedure or a branch pointing at
    a step that does not exist makes the three group verdicts moot rather than
    merely accompanying them. Show it above ``summary.groups``, and show the
    three groups separately — never rolled into one number, because a strong
    subject must not pay for wrong steps.

    Two more fields decide how this should be rendered, and both are easy to
    skip:

    - ``summary.matches_current_content`` is false when the content has moved
      since it was assessed. The assessment can look perfectly healthy and be
      about text nobody can see any more.
    - ``summary.coverage`` says how many dimensions were actually decided. In
      the current bundle most validators are not built, so ``state`` is
      mostly a statement about our coverage rather than about the playbook.
      Show "N of M checks run" until that number is worth a verdict; a red
      badge on every playbook trains reviewers to ignore the badge.
    """
    from contextedge.services.playbook_quality_service import quality_report

    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    report = await quality_report(db, playbook)
    assessment = report["assessment"]

    return PlaybookQualityResponse(
        playbook_id=playbook.id,
        content_hash=report["content_hash"],
        assessment_id=assessment.id if assessment else None,
        content_revision_id=assessment.content_revision_id if assessment else None,
        assessed_content_hash=assessment.content_hash if assessment else None,
        validator_bundle_version=(
            assessment.validator_bundle_version if assessment else None
        ),
        dimension_states=(assessment.dimension_states or {}) if assessment else {},
        summary=report["summary"],
        findings=report["findings"],
        readiness=report["readiness"],
        started_at=assessment.started_at if assessment else None,
        completed_at=assessment.completed_at if assessment else None,
        stale_at=assessment.stale_at if assessment else None,
        superseded_at=assessment.superseded_at if assessment else None,
    )


def _clarification_payload(state: dict) -> PlaybookClarificationResponse:
    return PlaybookClarificationResponse(
        playbook_id=state["playbook_id"],
        content_hash=state["content_hash"],
        round=state["round"],
        questions=state["questions"],
        matches_current_content=state["matches_current_content"],
        has_live_round=state["has_live_round"],
        outstanding_mandatory=state["outstanding_mandatory"],
        max_rounds=state["max_rounds"],
        submission=ClarificationSubmissionReadiness(**state["submission"]),
    )


@router.get("/{playbook_id}/clarification", response_model=PlaybookClarificationResponse)
async def get_playbook_clarification(playbook_id: UUID, db: DbSession, user: AuthUser):
    """The current clarification round, its questions, and submission readiness.

    Read-only, and deliberately so: this route opens no round, retrieves no
    knowledge and calls no model. Opening a playbook must not spend an LLM call,
    and must not change its history to record who looked — the same rule
    ``GET /{id}/quality`` follows.

    Two fields decide how this renders and both are easy to skip:

    - ``matches_current_content`` is false when the playbook has been edited
      since the round was opened. The questions then describe text nobody can
      see; answering them records an answer about a draft that no longer exists.
    - ``questions[].answer_source`` separates an answer a person typed from one
      prefilled out of a KB article. Rendering them the same way is how a
      retrieval score gets approved as a support decision.

    ``round`` may be a terminal round (``satisfied``, ``exhausted``,
    ``abandoned``) when nothing is live. That is not the same as never having
    asked, and an empty panel must not be shown for it.
    """
    from contextedge.services import playbook_clarification_service as clarification

    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    state = await clarification.clarification_state(db, playbook)
    return _clarification_payload(state)


@router.post(
    "/{playbook_id}/clarification/rounds",
    response_model=PlaybookClarificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_clarification_round(playbook_id: UUID, db: DbSession, user: AuthUser):
    """Detect what is missing, resolve what we can, ask about the rest.

    Costs one knowledge retrieval and up to two generation calls, which is why
    it is a POST behind an explicit human action and why there is no bulk
    equivalent. Opening rounds across a whole corpus would produce thousands of
    questions nobody answers, and a panel full of unanswered questions is worse
    than no panel.

    Requires the reviewer role: a round spends money and writes rows, unlike the
    read route beside it.

    A 201 does not mean questions were generated. The round may come back
    ``satisfied`` (nothing to ask — recorded, because "we looked and found
    nothing" is a fact the reviewer needs before submitting) or ``exhausted``
    (the loop hit its round limit with gaps open, which needs a decision rather
    than another question).
    """
    from contextedge.services import playbook_clarification_service as clarification

    user.require_role("playbook_reviewer")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    try:
        round_row = await clarification.open_round(
            db, playbook, actor_id=user.user_id
        )
    except clarification.RoundAlreadyOpen as exc:
        raise _conflict("clarification_round_open", str(exc)) from exc

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.clarification_round_opened",
        resource_type="playbook",
        resource_id=str(playbook_id),
        details={
            "round_number": round_row.round_number,
            "status": round_row.status,
            "gap_count": round_row.gap_count,
            "question_count": round_row.question_count,
            "mandatory_count": round_row.mandatory_count,
        },
    )
    state = await clarification.clarification_state(db, playbook)
    return _clarification_payload(state)


@router.post(
    "/{playbook_id}/clarification/answers", response_model=PlaybookClarificationResponse
)
async def answer_clarification_questions(
    playbook_id: UUID,
    body: ClarificationAnswersRequest,
    db: DbSession,
    user: AuthUser,
):
    """Record answers and skips against the open round.

    A skip on a mandatory question is refused with a 409 rather than ignored.
    Silently dropping it would leave the reviewer believing they had disposed of
    the question, and the round sitting un-appliable with no explanation.
    """
    from contextedge.services import playbook_clarification_service as clarification

    user.require_role("playbook_reviewer")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    try:
        await clarification.record_answers(
            db,
            playbook,
            [
                clarification.AnswerInput(
                    question_id=item.question_id,
                    answer_text=item.answer_text,
                    skip=item.skip,
                )
                for item in body.answers
            ],
            actor_id=user.user_id,
        )
    except clarification.NoLiveRound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except clarification.ClarificationError as exc:
        raise _conflict("clarification_answer_rejected", str(exc)) from exc

    state = await clarification.clarification_state(db, playbook)
    return _clarification_payload(state)


@router.post(
    "/{playbook_id}/clarification/regenerate",
    response_model=PlaybookClarificationResponse,
)
async def regenerate_clarification_questions(
    playbook_id: UUID,
    body: ClarificationRegenerateRequest,
    db: DbSession,
    user: AuthUser,
):
    """Ask again for the wording of the questions nobody has answered yet.

    The escape hatch for questions that are unusable through no fault of the
    playbook — too vague, or the raw validator text the generator falls back to
    when the model's JSON arrives truncated. Without it the only way out is
    abandoning the round, which spends one of the loop's bounded rounds on a
    defect in our own output.

    Answered, skipped and KB-resolved questions are untouched: rewriting the
    text of a question somebody already answered would orphan the answer.

    Bounded — each rewrite is a generation call. Past the limit this returns
    409 rather than a fourth attempt, because by then the problem is not the
    wording.
    """
    from contextedge.services import playbook_clarification_service as clarification

    user.require_role("playbook_reviewer")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    try:
        await clarification.regenerate_questions(
            db, playbook, actor_id=user.user_id, guidance=body.guidance
        )
    except clarification.NoLiveRound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except clarification.RegenerationLimitReached as exc:
        raise _conflict(
            "clarification_regeneration_limit",
            str(exc),
            limit=clarification.MAX_QUESTION_REGENERATIONS,
        ) from exc
    except clarification.NothingToRegenerate as exc:
        raise _conflict("clarification_nothing_to_regenerate", str(exc)) from exc

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.clarification_questions_regenerated",
        resource_type="playbook",
        resource_id=str(playbook_id),
        details={"had_guidance": bool(body.guidance and body.guidance.strip())},
    )
    state = await clarification.clarification_state(db, playbook)
    return _clarification_payload(state)


@router.post(
    "/{playbook_id}/clarification/apply", response_model=ClarificationApplyResponse
)
async def apply_clarification_round(
    playbook_id: UUID,
    body: ClarificationApplyRequest,
    db: DbSession,
    user: AuthUser,
):
    """Fold the answers into a new draft version derived from the current one.

    This revises the playbook that exists; it does not regenerate it from the
    pattern. Every step the answers do not contradict survives with its wording,
    order and citations — a revision that starts over throws away every hand
    edit a reviewer made, which is how a clarification loop stops being used
    after its first run.

    The new version is a draft carrying ``derived_from_version_id``. Published
    versions stay immutable, so the effect of the answers is readable as a
    version diff.

    Nothing is submitted here. When the loop finishes, ``submission.ready`` says
    the playbook could go forward; moving it is still an explicit human action
    through ``POST /{id}/transition``.
    """
    from contextedge.services import playbook_clarification_service as clarification

    user.require_role("playbook_reviewer")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    try:
        result = await clarification.apply_round(
            db, playbook, actor_id=user.user_id, open_next=body.open_next
        )
    except clarification.NoLiveRound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except clarification.MandatoryUnanswered as exc:
        raise _conflict(
            "clarification_mandatory_unanswered",
            str(exc),
            outstanding=exc.outstanding,
        ) from exc
    except clarification.RevisionFailed as exc:
        # 422, not 500: the request was well-formed and the round is still
        # answerable. The reviewer can edit an answer and try again, which a
        # 500 would not suggest.
        raise HTTPException(
            # The literal, not the Starlette constant: it was renamed between
            # versions and this file has to import cleanly on both.
            status_code=422,
            detail={"code": "clarification_revision_failed", "message": str(exc)},
        ) from exc
    except InvalidTransitionError as exc:  # pragma: no cover - defensive
        raise _conflict("invalid_transition", str(exc)) from exc

    new_version = result["version"]
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.clarification_round_applied",
        resource_type="playbook",
        resource_id=str(playbook_id),
        details={
            "round_number": result["applied_round"].round_number,
            "answers_applied": result["answers_applied"],
            "new_version_id": str(new_version.id),
            "semantic_version": new_version.semantic_version,
            "limit_reached": result["limit_reached"],
        },
    )
    submission = await clarification.submission_readiness(db, playbook)
    return ClarificationApplyResponse(
        applied_round=result["applied_round"],
        new_version_id=new_version.id,
        new_semantic_version=new_version.semantic_version,
        answers_applied=result["answers_applied"],
        next_round=result["next_round"],
        limit_reached=result["limit_reached"],
        submission=ClarificationSubmissionReadiness(**submission),
    )


@router.post(
    "/{playbook_id}/clarification/abandon", response_model=PlaybookClarificationResponse
)
async def abandon_clarification_round(
    playbook_id: UUID, db: DbSession, user: AuthUser, reason: str | None = None
):
    """Close the open round without applying it.

    Exists because the alternative — a round that can only be closed by
    answering it — makes a badly generated round permanent. The row is kept, so
    what was asked and why it was dropped stays readable.
    """
    from contextedge.services import playbook_clarification_service as clarification

    user.require_role("playbook_reviewer")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    closed = await clarification.abandon_round(db, playbook, reason=reason)
    if closed is None:
        raise HTTPException(status_code=404, detail="No clarification round is open")
    state = await clarification.clarification_state(db, playbook)
    return _clarification_payload(state)


@router.get("/{playbook_id}/references")
async def get_playbook_references(playbook_id: UUID, db: DbSession, user: AuthUser):
    """Retrieve full lineage references (source Pattern, member Episodes, and
    Evidence items) for a playbook."""
    from contextedge.models.episode import Episode, EpisodeEvidenceLink
    from contextedge.models.evidence import EvidenceItem
    from contextedge.models.pattern import Pattern, PatternEvidenceLink

    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    pattern_info = None
    episodes_list = []
    evidence_list = []

    # 1. Resolve Pattern
    target_pattern_id = playbook.pattern_id
    if target_pattern_id:
        pat = await db.get(Pattern, target_pattern_id)
        if pat and pat.tenant_id == user.tenant_id:
            pattern_info = {
                "id": str(pat.id),
                "title": pat.title,
                "confidence": pat.confidence,
                "episode_count": pat.episode_count,
            }

    # Fallback pattern matching if pattern_id was not populated on legacy seed playbooks
    if not pattern_info:
        pat_match = await db.execute(
            select(Pattern).where(
                Pattern.tenant_id == user.tenant_id,
            ).order_by(Pattern.episode_count.desc()).limit(1)
        )
        pat = pat_match.scalar_one_or_none()
        if pat:
            target_pattern_id = pat.id
            pattern_info = {
                "id": str(pat.id),
                "title": pat.title,
                "confidence": pat.confidence,
                "episode_count": pat.episode_count,
            }

    # 2. Resolve Episodes
    if target_pattern_id:
        link_res = await db.execute(
            select(Episode)
            .join(PatternEvidenceLink, PatternEvidenceLink.episode_id == Episode.id)
            .where(
                PatternEvidenceLink.pattern_id == target_pattern_id,
                Episode.tenant_id == user.tenant_id,
            )
        )
        for ep in link_res.scalars().all():
            episodes_list.append({
                "id": str(ep.id),
                "title": ep.title,
                "status": ep.status,
                "extraction_confidence": ep.extraction_confidence,
            })

    if not episodes_list:
        ep_res = await db.execute(
            select(Episode)
            .where(Episode.tenant_id == user.tenant_id)
            .order_by(Episode.created_at.desc())
            .limit(10)
        )
        for ep in ep_res.scalars().all():
            episodes_list.append({
                "id": str(ep.id),
                "title": ep.title,
                "status": ep.status,
                "extraction_confidence": ep.extraction_confidence,
            })

    # 3. Resolve Evidence Items (with ticket numbers via RawEvidenceObject)
    if episodes_list:
        ep_uuids = []
        for ep in episodes_list:
            try:
                ep_uuids.append(UUID(ep["id"]))
            except Exception:
                pass

        if ep_uuids:
            ev_items_res = await db.execute(
                select(EvidenceItem)
                .join(EpisodeEvidenceLink, EpisodeEvidenceLink.evidence_id == EvidenceItem.id)
                .where(
                    EpisodeEvidenceLink.episode_id.in_(ep_uuids),
                    EvidenceItem.tenant_id == user.tenant_id,
                )
            )
            ev_items = list(ev_items_res.scalars().all())
            if ev_items:
                await _attach_source_references(db, ev_items)
                seen_ev = set()
                for ev in ev_items:
                    if ev.id not in seen_ev:
                        seen_ev.add(ev.id)
                        ref_disp = (
                            getattr(ev.source_reference, "display_id", None)
                            if getattr(ev, "source_reference", None)
                            else None
                        )
                        evidence_list.append({
                            "id": str(ev.id),
                            "title": ev.title or "Untitled",
                            "evidence_type": ev.evidence_type,
                            "source_type": ev.source_type,
                            "display_id": ref_disp,
                        })

    if not evidence_list:
        ev_items_res = await db.execute(
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == user.tenant_id,
                EvidenceItem.evidence_type != "thread_message",
            )
            .order_by(EvidenceItem.created_at_source.desc().nullslast())
            .limit(10)
        )
        ev_items = list(ev_items_res.scalars().all())
        if ev_items:
            await _attach_source_references(db, ev_items)
            for ev in ev_items:
                ref_disp = (
                    getattr(ev.source_reference, "display_id", None)
                    if getattr(ev, "source_reference", None)
                    else None
                )
                evidence_list.append({
                    "id": str(ev.id),
                    "title": ev.title or "Untitled",
                    "evidence_type": ev.evidence_type,
                    "source_type": ev.source_type,
                    "display_id": ref_disp,
                })

    return {
        "pattern": pattern_info,
        "episodes": episodes_list,
        "evidence_items": evidence_list,
    }


@router.patch("/{playbook_id}", response_model=PlaybookResponse)
async def update_playbook(playbook_id: UUID, body: PlaybookUpdate, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    update_data = body.model_dump(exclude_unset=True)

    if "automation_mode" in update_data:
        # Above knowledge_manager on purpose. Automation mode is the
        # switch that decides whether this playbook may act on a real
        # system at all: `suggest_only` caps every caller at read_only
        # regardless of their role, so raising it is what makes every
        # other approval gate load-bearing. Editing a playbook's text and
        # authorising it to take destructive action are not the same
        # privilege and should not share one.
        user.require_role("tenant_admin")

    if "approval_policy_id" in update_data:
        # Same bar, for the mirror-image reason. Attaching a policy only
        # ever adds constraints, but the same field DETACHES one: setting
        # it to null removes the two-person rule, the approver-role
        # requirement and the autonomy ceiling in a single write. A
        # privilege is defined by the most dangerous thing it permits,
        # and that is the clear rather than the bind.
        user.require_role("tenant_admin")
        await assert_policy_assignment(
            db,
            user.tenant_id,
            update_data["approval_policy_id"],
            "approval",
        )

    # A tenant's approval policy may cap autonomy (`max_automation_mode`).
    # That ceiling was only enforced at execution time, so a mode above it
    # could be saved happily and then fail at the moment someone tried to
    # run the playbook — far from the screen where the choice was made,
    # and looking like a broken run rather than a policy decision.
    if "automation_mode" in update_data or "approval_policy_id" in update_data:
        policy_id = update_data.get("approval_policy_id", playbook.approval_policy_id)
        mode = update_data.get("automation_mode", playbook.automation_mode)
        policy = await load_approval_policy(db, user.tenant_id, policy_id)
        try:
            check_automation_mode(policy, mode)
        except ApprovalPolicyViolation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field, value in update_data.items():
        setattr(playbook, field, value)
    if "title" in update_data or "description" in update_data:
        # Title/description live on the playbook row, so they belong in
        # the fingerprint even for approved playbooks. embed_playbook
        # still resolves the newest *published* version for step text,
        # so an open draft cannot leak into search.
        from contextedge.services.playbook_embedding import embed_playbook

        await embed_playbook(db, playbook)

    # Reassess on any shell field the content hash covers, not just the two
    # that are also search-relevant. `risk_tier` and `automation_mode` are in
    # `build_content` and change the hash, so patching either one alone used to
    # leave a stale assessment attached to content that had moved — invisible
    # in shadow mode, and caught only later by the hash comparison in
    # `publication_readiness`.
    #
    # The membership test reads from `SHELL_QUALITY_FIELDS` rather than
    # repeating the names here, because a hand-maintained copy of that list is
    # exactly what drifted the first time.
    from contextedge.quality.revision import SHELL_QUALITY_FIELDS

    if set(update_data) & SHELL_QUALITY_FIELDS:
        # These belong in the quality verdict, which until now they did
        # not. This endpoint requires only knowledge_manager and checks no
        # lifecycle state, so an approved playbook's title can be replaced
        # outright; the steps are untouched and every version-level check
        # still passes. A quality record that lives on the version cannot
        # notice, which is precisely how a playbook ends up carrying an
        # assessment that was about a different title.
        #
        # Two rows of the AutomationEdge review sheet show this happening:
        # dc6a3e33 and dd39c4cd were each reviewed twice under two different
        # titles, with opposite verdicts, and nothing in the data says
        # whether the steps moved too.
        from contextedge.services.playbook_quality_service import (
            STALE_SHELL_EDITED,
            invalidate_and_reassess,
        )

        await invalidate_and_reassess(
            db,
            playbook,
            reason=STALE_SHELL_EDITED,
            origin="shell_edit",
            actor_id=user.user_id,
        )
    await db.flush()
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.updated",
        resource_type="playbook",
        resource_id=str(playbook.id),
        details={"changed_fields": sorted(update_data.keys())},
    )
    await db.refresh(playbook)
    return playbook


@router.post("/{playbook_id}/transition", response_model=PlaybookResponse)
async def transition(
    playbook_id: UUID, body: PlaybookTransition, request: Request, db: DbSession, user: AuthUser,
):
    user.require_role("playbook_reviewer")
    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    try:
        playbook = await transition_playbook(
            db,
            playbook,
            body.new_state,
            user.user_id,
            body.comments,
            # Pass Redis so the runtime-match cache drops any entries
            # that reference this playbook — see review F-09.
            redis=getattr(request.app.state, "redis", None),
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.refresh(playbook)
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=f"playbook.{body.new_state}",
        resource_type="playbook",
        resource_id=str(playbook.id),
        details={"comments": body.comments},
    )
    return playbook


@router.post("/bulk-transition")
async def bulk_transition(
    body: PlaybookBulkTransition,
    request: Request,
    db: DbSession,
    user: AuthUser,
):
    """Move a selected review queue through one lifecycle step."""
    user.require_role("playbook_reviewer")
    result = await db.execute(
        select(Playbook).where(
            Playbook.id.in_(body.ids),
            Playbook.tenant_id == user.tenant_id,
        )
    )
    playbooks = list(result.scalars().all())
    found_ids = {playbook.id for playbook in playbooks}
    missing_ids = [str(playbook_id) for playbook_id in body.ids if playbook_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Playbooks not found: {', '.join(missing_ids)}",
        )

    from contextedge.services.playbook_service import VALID_TRANSITIONS

    invalid = [
        playbook
        for playbook in playbooks
        if body.new_state not in VALID_TRANSITIONS.get(playbook.lifecycle_state, set())
    ]
    if invalid:
        states = ", ".join(
            f"{playbook.id} ({playbook.lifecycle_state})" for playbook in invalid
        )
        raise HTTPException(
            status_code=400,
            detail=f"Selected playbooks cannot transition to '{body.new_state}': {states}",
        )

    redis = getattr(request.app.state, "redis", None)
    try:
        for playbook in playbooks:
            previous_state = playbook.lifecycle_state
            await transition_playbook(
                db,
                playbook,
                body.new_state,
                user.user_id,
                body.comments,
                redis=redis,
            )
            await log_audit_event(
                db,
                tenant_id=user.tenant_id,
                actor_id=user.user_id,
                actor_email=user.email,
                action=f"playbook.{body.new_state}",
                resource_type="playbook",
                resource_id=str(playbook.id),
                details={
                    "comments": body.comments,
                    "bulk": True,
                    "from_state": previous_state,
                },
            )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "transitioned_count": len(playbooks),
        "new_state": body.new_state,
        "ids": [str(playbook.id) for playbook in playbooks],
    }


@router.get("/{playbook_id}/versions", response_model=list[PlaybookVersionResponse])
async def list_versions(playbook_id: UUID, db: DbSession, user: AuthUser):
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    result = await db.execute(
        select(PlaybookVersion)
        .where(
            PlaybookVersion.playbook_id == playbook.id,
            PlaybookVersion.tenant_id == user.tenant_id,
        )
        .order_by(
            # Top/main version first — current_version_id is the editable
            # draft after a fork, while runtime still reads published rows.
            case((PlaybookVersion.id == playbook.current_version_id, 0), else_=1),
            PlaybookVersion.created_at.desc(),
        )
    )
    versions = result.scalars().all()
    notes = await _latest_edit_notes(db, user.tenant_id, [version.id for version in versions])
    for version in versions:
        note = notes.get(str(version.id))
        if note:
            version.last_edit_note = note
    return versions


@router.post(
    "/{playbook_id}/versions",
    response_model=PlaybookVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    playbook_id: UUID,
    body: PlaybookVersionCreate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    try:
        version = await create_playbook_version(db, playbook, body.model_dump())
    except DuplicateVersionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except UnresolvedSkillReference as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    version.created_by = user.user_id
    from contextedge.services.playbook_embedding import embed_playbook

    # Approved playbooks keep the published-version fingerprint until
    # this draft is published. Embedding it here would make semantic
    # search match unpublished steps (N3). Candidates embed the new
    # current/top version — that is the main version for search.
    if _should_embed_draft(playbook):
        await embed_playbook(db, playbook, version)
    return version


@router.patch(
    "/{playbook_id}/versions/{version_id}",
    response_model=PlaybookVersionResponse,
)
async def update_playbook_version(
    playbook_id: UUID,
    version_id: UUID,
    body: PlaybookVersionUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    version = await _load_playbook_version(db, playbook_id, version_id)

    if playbook.lifecycle_state in {"retired", "deprecated"}:
        raise _conflict(
            "lifecycle_readonly",
            f"Playbooks in '{playbook.lifecycle_state}' cannot be edited",
        )
    if version.published_at is not None:
        raise _conflict(
            "version_published",
            "Published versions are immutable. Fork a draft to edit.",
            hint="fork a draft",
            fork_url=f"/playbooks/{playbook_id}/versions/{version_id}/draft",
        )
    if playbook.current_version_id != version.id:
        raise _conflict(
            "not_current_version",
            "Only the current (top) version can be edited. Switch to the main version.",
        )
    if body.expected_revision != version.revision:
        raise _conflict(
            "revision_conflict",
            "This version was edited elsewhere. Reload and retry.",
            current_revision=version.revision,
            updated_at=version.updated_at.isoformat() if version.updated_at else None,
        )

    changed_fields: list[str] = []
    summary: dict | None = None
    warnings: list[str] = []

    if body.steps is not None:
        try:
            steps, summary = normalize_steps(version.steps, body.steps, user.user_id)
            validate_result = validate_steps(steps)
            warnings.extend(validate_result.get("warnings") or [])
            validate_version_fields(
                trigger_conditions=body.trigger_conditions,
                rollback_notes=body.rollback_notes
                if body.rollback_notes is not None
                else version.rollback_notes,
            )
            await validate_step_bindings(db, user.tenant_id, steps)
        except PlaybookEditValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except UnresolvedSkillReference as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        version.steps = steps
        flag_modified(version, "steps")
        changed_fields.append("steps")

    field_map = {
        "trigger_conditions": body.trigger_conditions,
        "branching_logic": body.branching_logic,
        "inputs": body.inputs,
        "outputs": body.outputs,
        "rollback_notes": body.rollback_notes,
        "execution_confidence_guidance": body.execution_confidence_guidance,
        "playbook_confidence": body.playbook_confidence,
    }
    if body.trigger_conditions is not None or body.rollback_notes is not None:
        try:
            validate_version_fields(
                trigger_conditions=body.trigger_conditions,
                rollback_notes=body.rollback_notes,
            )
        except PlaybookEditValidationError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    for field, value in field_map.items():
        if value is not None:
            setattr(version, field, value)
            if field in {
                "trigger_conditions",
                "branching_logic",
                "inputs",
                "outputs",
            }:
                flag_modified(version, field)
            changed_fields.append(field)
    if body.verification_policy is not None:
        version.verification_policy = body.verification_policy.model_dump()
        flag_modified(version, "verification_policy")
        changed_fields.append("verification_policy")

    if not changed_fields and not body.edit_note:
        return version

    now = datetime.now(UTC)
    version.revision = int(version.revision or 1) + 1
    version.last_edited_by = user.user_id
    version.updated_at = now
    playbook.updated_at = now

    from contextedge.services.playbook_embedding import embed_playbook

    # Draft edits of a candidate re-fingerprint the current/top version.
    # Approved playbooks keep serving the published embedding until this
    # draft is itself approved.
    if (
        ("steps" in changed_fields or "trigger_conditions" in changed_fields)
        and _should_embed_draft(playbook)
    ):
        await embed_playbook(db, playbook, version)

    audit_summary = summary or {
        "added": [],
        "removed": [],
        "modified": [],
        "reordered": False,
    }
    await append_operational_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        entity_type="playbook_version",
        entity_id=version.id,
        event_type="playbook.version_edited",
        payload={
            "playbook_id": str(playbook.id),
            "semantic_version": version.semantic_version,
            "revision": version.revision,
            "changed_fields": changed_fields,
            "summary": audit_summary,
            "edit_note": body.edit_note,
        },
    )
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.version_edited",
        resource_type="playbook_version",
        resource_id=str(version.id),
        details={
            "playbook_id": str(playbook.id),
            "semantic_version": version.semantic_version,
            "revision": version.revision,
            "changed_fields": changed_fields,
            "summary": audit_summary,
            "edit_note": body.edit_note,
        },
    )
    # A draft edit is a quality-bearing mutation even though no new version
    # row is created — this endpoint patches steps in place, so it is the one
    # write path that changes the procedure without going through
    # create_playbook_version.
    #
    # Note what the grounding validator will find here: PROTECTED_KEYS keeps
    # source_refs and grounding_status across the merge, so a rewritten
    # grounded step keeps the citations of the sentence it replaced. That is
    # correct behaviour for the merge (a typed round-trip must not strip
    # provenance) and a stale evidence claim for the reviewer, and it is
    # `human_edited` that lets the assessment tell the difference.
    from contextedge.services.playbook_quality_service import (
        STALE_STEPS_EDITED,
        invalidate_and_reassess,
    )

    await invalidate_and_reassess(
        db,
        playbook,
        version,
        reason=STALE_STEPS_EDITED,
        origin="draft_edit",
        actor_id=user.user_id,
    )

    await db.flush()
    await db.refresh(version)
    if body.edit_note:
        version.last_edit_note = body.edit_note
    payload = PlaybookVersionResponse.model_validate(version)
    if warnings:
        return payload.model_copy(update={"edit_warnings": warnings})
    return payload


@router.post(
    "/{playbook_id}/versions/{version_id}/draft",
    response_model=PlaybookVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def fork_playbook_version_draft(
    playbook_id: UUID,
    version_id: UUID,
    db: DbSession,
    user: AuthUser,
    response: Response,
    body: PlaybookVersionForkRequest | None = None,
):
    """Copy-on-write fork of a published version into a new unpublished draft.

    The new row becomes ``current_version_id`` (the main/top version) but
    runtime and embeddings keep serving the published source until this
    draft is approved.
    """
    user.require_role("knowledge_manager")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    target = await _load_playbook_version(db, playbook_id, version_id)

    if playbook.lifecycle_state in {"retired", "deprecated"}:
        raise _conflict(
            "lifecycle_readonly",
            f"Playbooks in '{playbook.lifecycle_state}' cannot be edited",
        )

    existing_draft = (
        await db.execute(
            select(PlaybookVersion)
            .where(
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.published_at.is_(None),
            )
            .order_by(PlaybookVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_draft is not None:
        playbook.current_version_id = existing_draft.id
        playbook.updated_at = datetime.now(UTC)
        response.status_code = status.HTTP_200_OK
        return existing_draft

    data = _version_payload(target)
    try:
        version = await create_playbook_version(db, playbook, data, origin="fork")
    except DuplicateVersionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except UnresolvedSkillReference as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    version.derived_from_version_id = target.id
    version.created_by = user.user_id
    version.last_edited_by = user.user_id
    # create_playbook_version already repoints current_version_id to this
    # new row — that is the main/top version for subsequent edits.
    if _should_embed_draft(playbook):
        from contextedge.services.playbook_embedding import embed_playbook

        await embed_playbook(db, playbook, version)

    edit_note = body.edit_note if body is not None else None
    await append_operational_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        entity_type="playbook_version",
        entity_id=version.id,
        event_type="playbook.version_forked",
        payload={
            "playbook_id": str(playbook.id),
            "source_version_id": str(target.id),
            "semantic_version": version.semantic_version,
            "edit_note": edit_note,
        },
    )
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.version_forked",
        resource_type="playbook_version",
        resource_id=str(version.id),
        details={
            "playbook_id": str(playbook.id),
            "source_version_id": str(target.id),
            "semantic_version": version.semantic_version,
            "edit_note": edit_note,
        },
    )
    await db.flush()
    await db.refresh(version)
    return version


@router.delete(
    "/{playbook_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def discard_playbook_version_draft(
    playbook_id: UUID,
    version_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    playbook = await _load_tenant_playbook(db, playbook_id, user.tenant_id)
    version = await _load_playbook_version(db, playbook_id, version_id)

    if version.published_at is not None:
        raise _conflict(
            "version_published",
            "Published versions cannot be discarded.",
        )

    sibling_count = (
        await db.execute(
            select(func.count())
            .select_from(PlaybookVersion)
            .where(PlaybookVersion.playbook_id == playbook.id)
        )
    ).scalar_one()
    if sibling_count <= 1:
        raise _conflict(
            "only_version",
            "Cannot discard the only version of this playbook.",
        )

    newest_published = (
        await db.execute(
            select(PlaybookVersion)
            .where(
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.id != version.id,
                PlaybookVersion.published_at.is_not(None),
            )
            .order_by(PlaybookVersion.published_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    fallback = newest_published
    if fallback is None:
        fallback = (
            await db.execute(
                select(PlaybookVersion)
                .where(
                    PlaybookVersion.playbook_id == playbook.id,
                    PlaybookVersion.id != version.id,
                )
                .order_by(PlaybookVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    discarded_id = version.id
    discarded_semantic_version = version.semantic_version
    if playbook.current_version_id == version.id:
        playbook.current_version_id = fallback.id if fallback is not None else None
        playbook.updated_at = datetime.now(UTC)

    await db.execute(
        delete(PlaybookEvidenceLink).where(
            PlaybookEvidenceLink.playbook_version_id == discarded_id
        )
    )
    await db.delete(version)

    if (
        _should_embed_draft(playbook)
        and fallback is not None
        and playbook.current_version_id == fallback.id
    ):
        from contextedge.services.playbook_embedding import embed_playbook

        await embed_playbook(db, playbook, fallback)
    elif playbook.lifecycle_state == "approved" and newest_published is not None:
        from contextedge.services.playbook_embedding import embed_playbook

        # Restore the published fingerprint as the main embedding.
        await embed_playbook(db, playbook, newest_published)

    # Discarding a draft reverts the playbook to `fallback`, so the open
    # assessment now describes content this playbook no longer presents. The
    # hash comparison in `publication_readiness` would refuse to publish on
    # that basis, but nothing else would notice — a reviewer's panel would go
    # on showing a verdict about the version they just threw away.
    #
    # Reassess rather than only invalidate: whatever the playbook presents now
    # deserves a current verdict of its own.
    #
    # No version is passed. `fallback` is only what the playbook reverts to
    # when the discarded draft *was* the current version; when it was not,
    # current_version_id still points somewhere else entirely. Letting the
    # service resolve current_version_id is right in both cases.
    from contextedge.services.playbook_quality_service import (
        STALE_CONTENT_CHANGED,
        invalidate_and_reassess,
    )

    await invalidate_and_reassess(
        db,
        playbook,
        reason=STALE_CONTENT_CHANGED,
        origin="draft_discarded",
        actor_id=user.user_id,
    )

    await append_operational_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        entity_type="playbook_version",
        entity_id=discarded_id,
        event_type="playbook.version_discarded",
        payload={
            "playbook_id": str(playbook.id),
            "semantic_version": discarded_semantic_version,
            "current_version_id": (
                str(playbook.current_version_id) if playbook.current_version_id else None
            ),
        },
    )
    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="playbook.version_discarded",
        resource_type="playbook_version",
        resource_id=str(discarded_id),
        details={"playbook_id": str(playbook.id)},
    )


@router.get(
    "/{playbook_id}/versions/{version_id}/diff",
    response_model=PlaybookVersionDiffResponse,
)
async def get_playbook_version_diff(
    playbook_id: UUID,
    version_id: UUID,
    db: DbSession,
    user: AuthUser,
    base_version_id: UUID | None = None,
):
    playbook = (
        await db.execute(
            select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    target = (
        await db.execute(
            select(PlaybookVersion).where(
                PlaybookVersion.id == version_id,
                PlaybookVersion.playbook_id == playbook_id,
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Playbook version not found")

    base: PlaybookVersion | None = None
    if base_version_id is not None:
        base = (
            await db.execute(
                select(PlaybookVersion).where(
                    PlaybookVersion.id == base_version_id,
                    PlaybookVersion.playbook_id == playbook_id,
                )
            )
        ).scalar_one_or_none()
        if not base:
            raise HTTPException(status_code=404, detail="Base playbook version not found")
    elif playbook.current_version_id and playbook.current_version_id != target.id:
        base = await db.get(PlaybookVersion, playbook.current_version_id)
    else:
        base = (
            await db.execute(
                select(PlaybookVersion)
                .where(
                    PlaybookVersion.playbook_id == playbook_id,
                    PlaybookVersion.id != target.id,
                )
                .order_by(PlaybookVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    changed_fields, unified_diff = _version_diff(base, target)
    return PlaybookVersionDiffResponse(
        playbook_id=playbook_id,
        base_version_id=base.id if base else None,
        base_semantic_version=base.semantic_version if base else None,
        target_version_id=target.id,
        target_semantic_version=target.semantic_version,
        changed_fields=changed_fields,
        unified_diff=unified_diff,
    )


@router.post("/{playbook_id}/rollback", response_model=PlaybookVersionResponse)
async def rollback_playbook(
    playbook_id: UUID,
    body: PlaybookRollbackRequest,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    playbook = (
        await db.execute(
            select(Playbook).where(Playbook.id == playbook_id, Playbook.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    target = (
        await db.execute(
            select(PlaybookVersion).where(
                PlaybookVersion.id == body.target_version_id,
                PlaybookVersion.playbook_id == playbook_id,
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Playbook version not found")

    version_data = _version_payload(target)
    if target.rollback_notes:
        version_data["rollback_notes"] = target.rollback_notes
    # create_playbook_version runs a fresh assessment against the *current*
    # validators, policy and sources — which is the point of reassessing on
    # rollback rather than trusting the verdict the target version carried
    # when it was first published. What is still missing is the gate: this
    # path republishes immediately below, so in Phase 5 the readiness check
    # belongs between these two statements.
    version = await create_playbook_version(db, playbook, version_data, origin="rollback")
    if playbook.lifecycle_state == "approved":
        version.published_at = version.published_at or datetime.now(UTC)
        version.published_by = version.published_by or user.user_id
        from contextedge.services.playbook_embedding import embed_playbook

        await embed_playbook(db, playbook, version)
    return version


class GeneratePlaybookRequest(BaseModel):
    pattern_id: UUID
    force: bool = False


@router.post("/generate", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def generate_playbook(
    body: GeneratePlaybookRequest,
    db: DbSession,
    user: AuthUser,
):
    """Generate a playbook candidate from a knowledge pattern using AI."""
    user.require_role("knowledge_manager")

    from contextedge.ai.generators.playbook_generator import generate_playbook_candidate
    from contextedge.graph.builder import ensure_edge, link_node_to_identities
    from contextedge.models.episode import Episode
    from contextedge.models.pattern import NegativeKnowledgeItem, Pattern
    from contextedge.services.identity_service import identity_ids_from_refs
    from contextedge.services.playbook_service import create_playbook_version

    # 1. Fetch Pattern and Episodes
    q = select(Pattern).where(
        Pattern.id == body.pattern_id,
        Pattern.tenant_id == user.tenant_id
    ).options(selectinload(Pattern.evidence_links))
    res = await db.execute(q)
    pattern = res.scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")

    episode_ids = [link.episode_id for link in pattern.evidence_links if link.episode_id]
    if not episode_ids:
        raise HTTPException(status_code=400, detail="Pattern has no associated episodes to analyze")

    res = await db.execute(
        select(Episode).where(
            Episode.id.in_(episode_ids), Episode.tenant_id == user.tenant_id
        )
    )
    episodes = res.scalars().all()

    # 2. Call AI Generator
    try:
        from contextedge.services.episode_service import (
            evidence_ids_for_episodes,
            playbook_episode_summaries,
        )
        from contextedge.services.knowledge_applicability_service import (
            ticket_version_custom_fields,
        )
        from contextedge.services.knowledge_retrieval_service import (
            knowledge_refs_payload,
            persist_knowledge_links,
            retrieve_knowledge_for_pattern,
        )
        from contextedge.services.quality_contract_service import prepare_playbook_generation

        ep_summaries = await playbook_episode_summaries(
            db, user.tenant_id, list(episodes)
        )

        nk_r = await db.execute(
            select(NegativeKnowledgeItem).where(
                NegativeKnowledgeItem.tenant_id == user.tenant_id,
                NegativeKnowledgeItem.domain_id == pattern.domain_id,
            ).limit(20)
        )
        negative_knowledge = [
            f"{row.step_text} ({row.failure_reason or 'no reason'})"
            for row in nk_r.scalars().all()
        ]

        evidence_ref_ids = await evidence_ids_for_episodes(
            db, user.tenant_id, episode_ids
        )
        version_fields = await ticket_version_custom_fields(
            db, user.tenant_id, evidence_ref_ids
        )
        retrieval_failed = False
        try:
            knowledge = await retrieve_knowledge_for_pattern(
                db,
                user.tenant_id,
                pattern_title=pattern.title,
                pattern_description=pattern.description,
                episode_summaries=ep_summaries,
                custom_fields=version_fields or None,
            )
        except Exception:
            retrieval_failed = True
            knowledge = []
            logger.exception(
                "playbook.knowledge_retrieval_failed",
                tenant_id=str(user.tenant_id),
                pattern_id=str(pattern.id),
            )

        prep = prepare_playbook_generation(
            pattern=pattern,
            episode_summaries=ep_summaries,
            knowledge=knowledge,
            negative_knowledge=negative_knowledge,
            retrieval_failed=retrieval_failed,
        )
        if prep.should_block and not body.force:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Pre-generation quality gates blocked generation.",
                    "outcome": str(prep.gate.outcome),
                    "reasons": prep.gate.reasons,
                    "pregeneration": prep.gate.as_dict(),
                },
            )
        if prep.should_block and body.force:
            logger.warning(
                "playbook.generation_forced_past_pregeneration",
                tenant_id=str(user.tenant_id),
                pattern_id=str(pattern.id),
                outcome=str(prep.gate.outcome),
                reasons=prep.gate.reasons[:5],
            )

        knowledge = prep.filtered_knowledge
        await persist_knowledge_links(
            db, user.tenant_id, pattern.id, knowledge, domain_id=pattern.domain_id
        )
        logger.info(
            "playbook.knowledge_retrieved",
            tenant_id=str(user.tenant_id),
            pattern_id=str(pattern.id),
            documents=len(knowledge),
            sections=sum(len(k.sections) for k in knowledge),
            ticket_version=(version_fields or {}).get("version"),
            pregeneration_outcome=str(prep.gate.outcome),
        )

        candidate = await generate_playbook_candidate(
            pattern.title,
            pattern.description or "",
            len(episodes),
            ep_summaries,
            negative_knowledge,
            knowledge_sources=knowledge,
            quality_contract_prompt=prep.contract_prompt_block,
            tenant_id=user.tenant_id,
            db=db,
        )

        # Provenance is assembled here, not left to the model: the LLM
        # cites [kb-N] in steps but does not emit knowledge_ids, and
        # forwarding the candidate dict whole used to persist a playbook
        # that had used KB in the prompt with no record of which articles.
        candidate["evidence_refs"] = {
            "evidence_ids": evidence_ref_ids,
            "episode_ids": [str(eid) for eid in episode_ids],
            "pattern_id": str(pattern.id),
            "quality_contract": prep.evidence_refs_quality(),
            **knowledge_refs_payload(
                knowledge,
                ticket_version=(version_fields or {}).get("version"),
            ),
        }
        candidate["quality_contract_hash"] = prep.contract_hash
        candidate["source_snapshot_hash"] = prep.source_snapshot_hash

        # 3. Find or Create Playbook Shell
        existing_pb_q = (
            select(Playbook)
            .where(
                Playbook.pattern_id == pattern.id,
                Playbook.tenant_id == user.tenant_id,
            )
            .order_by(Playbook.created_at.desc())
        )
        existing_pb_res = await db.execute(existing_pb_q)
        playbook = existing_pb_res.scalars().first()
        now_utc = datetime.now(UTC)

        if playbook:
            playbook.title = candidate.get("title", playbook.title)
            playbook.description = candidate.get("description", playbook.description)
            playbook.updated_at = now_utc
        else:
            stable_key = f"pb-{uuid_mod.uuid4().hex[:12]}"
            playbook = Playbook(
                tenant_id=user.tenant_id,
                domain_id=pattern.domain_id,
                stable_key=stable_key,
                title=candidate.get("title", f"Fix: {pattern.title}"),
                description=candidate.get("description", pattern.description),
                risk_tier=candidate.get("risk_tier", "medium"),
                automation_mode="suggest_only",
                owner_user_id=user.user_id,
                pattern_id=pattern.id,
                updated_at=now_utc,
            )
            db.add(playbook)
        pattern.updated_at = now_utc
        await db.flush()

        # 4. Create Version 0.1.0 with the AI content
        await create_playbook_version(db, playbook, candidate, origin="manual_generation")
        identity_ids = []
        for episode in episodes:
            identity_ids.extend(identity_ids_from_refs(episode.entity_refs))
        await link_node_to_identities(
            db,
            user.tenant_id,
            "playbook",
            playbook.id,
            identity_ids,
            edge_type="references_identity",
            domain_id=pattern.domain_id,
        )
        await ensure_edge(
            db, user.tenant_id,
            "playbook", playbook.id,
            "pattern", pattern.id,
            "derived_from",
            domain_id=pattern.domain_id,
        )

        await db.commit()
        await db.refresh(playbook)
        return playbook

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        logger.exception(
            "playbook_generation_failed",
            tenant_id=str(user.tenant_id),
            pattern_id=str(body.pattern_id),
            error_type=type(exc).__name__,
            error=str(exc)[:800],
        )
        await db.rollback()
        detail = str(exc).strip() or type(exc).__name__
        if len(detail) > 400:
            detail = detail[:400] + "…"
        raise HTTPException(
            status_code=502,
            detail=f"Playbook generation failed: {detail}",
        ) from exc
