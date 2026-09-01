"""The single entry point every mutation path calls to keep quality state honest.

Generation, manual generation, version creation, draft edits, title edits,
forks, rollbacks and transitions each used to do their own thing. That is how a
title-only edit kept a passing verdict about a different title, and how the
manual generation endpoint ended up without four of the five guards its worker
twin has. This module is the shared orchestration the plan asks for: one place
that mints a revision, runs the cascade, persists the result, and marks what is
now stale.

**Nothing here blocks anything.** ``evaluation_mode`` is ``shadow`` and every
public function is failure-tolerant: a quality problem must never stop a
reviewer saving a draft or an operator publishing. The enforcement switch is
Phase 5, and ``publication_readiness`` below is written now precisely so that
turning it on is a call site change rather than a redesign.

Failure-tolerant does not mean silent. Every swallowed error is logged, and a
path that could not be assessed produces a persisted ``error`` assessment
rather than no row — "we tried and could not" is a fact the review queue needs,
and an absent row is indistinguishable from content that was never submitted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.playbook_quality import (
    PlaybookContentRevision,
    PlaybookQualityAssessment,
    PlaybookQualityFinding,
)
from contextedge.quality import (
    VALIDATOR_BUNDLE_VERSION,
    AssessmentOutcome,
    ValidationContext,
    assess,
    build_content,
    error_outcome,
)
from contextedge.quality.hashing import content_hash
from contextedge.quality.states import (
    NON_PASSING_STATES,
    SEVERITIES,
    STATE_PASS,
    STATE_STALE,
    coverage,
    group_states,
    structure_state,
)

logger = structlog.get_logger()

MODE_SHADOW = "shadow"
MODE_ENFORCING = "enforcing"

# Why an assessment stopped being trustworthy. Stored on the row so the
# reviewer sees "the KB article this cites changed" rather than a bare flag.
STALE_CONTENT_CHANGED = "content_changed"
STALE_SHELL_EDITED = "shell_edited"
STALE_STEPS_EDITED = "steps_edited"
STALE_SOURCE_CHANGED = "source_changed"
STALE_POLICY_CHANGED = "policy_changed"
STALE_ONTOLOGY_CHANGED = "ontology_changed"
STALE_VALIDATOR_RETIRED = "validator_retired"
STALE_FORKED = "forked_from_other_revision"
STALE_ROLLED_BACK = "rolled_back"


def _mode() -> str:
    """Shadow unless a tenant explicitly opts in. Defaults are the ones you get
    when nobody made a decision, so the default is the safe one."""
    from contextedge.config import settings

    mode = getattr(settings, "playbook_quality_mode", MODE_SHADOW)
    return mode if mode in (MODE_SHADOW, MODE_ENFORCING) else MODE_SHADOW


async def _next_revision_number(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(PlaybookContentRevision.revision_number), 0)).where(
            PlaybookContentRevision.tenant_id == tenant_id,
            PlaybookContentRevision.playbook_id == playbook_id,
        )
    )
    return int(result.scalar() or 0) + 1


async def ensure_content_revision(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None,
    *,
    origin: str = "unknown",
    actor_id: uuid.UUID | None = None,
    quality_contract_hash: str | None = None,
    source_snapshot_hash: str | None = None,
) -> PlaybookContentRevision:
    """Return the revision for this playbook's current content, minting it if new.

    Idempotent by content hash. Saving a draft without changing anything must
    not mint a revision — that would invalidate a good assessment for no
    reason, and after a few no-op saves the reviewer is looking at a history of
    identical entries and cannot find the edit that mattered.

    The unique constraint is the real guard; the SELECT is the fast path. Two
    concurrent writers of identical content both lose the race harmlessly and
    the loser re-reads the winner's row.
    """
    content = build_content(playbook, version)
    digest = content_hash(content)

    existing = await db.execute(
        select(PlaybookContentRevision).where(
            PlaybookContentRevision.tenant_id == playbook.tenant_id,
            PlaybookContentRevision.playbook_id == playbook.id,
            PlaybookContentRevision.content_hash == digest,
        )
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    revision = PlaybookContentRevision(
        tenant_id=playbook.tenant_id,
        playbook_id=playbook.id,
        playbook_version_id=getattr(version, "id", None),
        revision_number=await _next_revision_number(db, playbook.tenant_id, playbook.id),
        content_hash=digest,
        content=content,
        quality_contract_hash=quality_contract_hash,
        source_snapshot_hash=source_snapshot_hash,
        created_by=actor_id,
        origin=origin,
    )
    try:
        async with db.begin_nested():
            db.add(revision)
            await db.flush()
    except IntegrityError:
        # Lost the race, or the revision_number collided. Either way the
        # winner's row is the answer.
        again = await db.execute(
            select(PlaybookContentRevision).where(
                PlaybookContentRevision.tenant_id == playbook.tenant_id,
                PlaybookContentRevision.playbook_id == playbook.id,
                PlaybookContentRevision.content_hash == digest,
            )
        )
        winner = again.scalar_one_or_none()
        if winner is None:
            raise
        return winner
    return revision


async def _supersede_open_assessments(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    *,
    superseded_by: uuid.UUID | None,
    now: datetime,
) -> None:
    await db.execute(
        update(PlaybookQualityAssessment)
        .where(
            PlaybookQualityAssessment.tenant_id == tenant_id,
            PlaybookQualityAssessment.playbook_id == playbook_id,
            PlaybookQualityAssessment.superseded_at.is_(None),
            PlaybookQualityAssessment.id != superseded_by,
        )
        .values(superseded_at=now, superseded_by_id=superseded_by)
    )


async def record_assessment(
    db: AsyncSession,
    playbook: Playbook,
    revision: PlaybookContentRevision,
    outcome: AssessmentOutcome,
    *,
    mode: str | None = None,
) -> PlaybookQualityAssessment:
    """Persist one assessment and its findings, superseding the previous one.

    Append-only: the prior assessment is marked superseded, never updated in
    place and never deleted. Threshold calibration, validator A/B and override
    analysis all read that history, and a system that overwrites it can only
    ever answer "what do we think now", not "what did we think when this was
    approved".
    """
    now = datetime.now(UTC)
    assessment = PlaybookQualityAssessment(
        tenant_id=playbook.tenant_id,
        playbook_id=playbook.id,
        content_revision_id=revision.id,
        content_hash=revision.content_hash,
        quality_contract_hash=revision.quality_contract_hash,
        source_snapshot_hash=revision.source_snapshot_hash,
        validator_bundle_version=outcome.validator_bundle_version or VALIDATOR_BUNDLE_VERSION,
        evaluation_mode=mode or _mode(),
        overall_state=outcome.overall_state,
        dimension_states=outcome.dimension_states,
        started_at=outcome.started_at,
        completed_at=outcome.completed_at or now,
    )
    db.add(assessment)
    await db.flush()

    for finding in outcome.findings:
        payload = finding.as_dict()
        db.add(
            PlaybookQualityFinding(
                tenant_id=playbook.tenant_id,
                assessment_id=assessment.id,
                category=payload["category"],
                dimension=payload["dimension"],
                severity=payload["severity"],
                target_kind=payload["target_kind"],
                target_ref=payload["target_ref"],
                claim=payload["claim"],
                explanation=payload["explanation"],
                supporting_spans=payload["supporting_spans"],
                contradicting_spans=payload["contradicting_spans"],
                validator=payload["validator"],
                confidence=payload["confidence"],
                remediation_category=payload["remediation_category"],
            )
        )

    await _supersede_open_assessments(
        db, playbook.tenant_id, playbook.id, superseded_by=assessment.id, now=now
    )
    await db.flush()
    return assessment


async def assess_playbook(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None = None,
    *,
    origin: str = "unknown",
    actor_id: uuid.UUID | None = None,
    quality_contract_hash: str | None = None,
    source_snapshot_hash: str | None = None,
) -> PlaybookQualityAssessment | None:
    """Mint the revision, run the cascade, persist the verdict.

    Returns ``None`` only when persistence itself failed — that is a bug in
    this module or a database problem, not a quality verdict, and the caller
    must not read it as one. Everything else, including a validator crash,
    comes back as a persisted assessment with a non-passing state.

    Never raises. A quality system that can stop a reviewer saving their work
    will be switched off within a week, and then it protects nothing.
    """
    try:
        if version is None and playbook.current_version_id is not None:
            version = await db.get(PlaybookVersion, playbook.current_version_id)

        revision = await ensure_content_revision(
            db,
            playbook,
            version,
            origin=origin,
            actor_id=actor_id,
            quality_contract_hash=quality_contract_hash,
            source_snapshot_hash=source_snapshot_hash,
        )
        contract = _contract_from_version(version)
        context = ValidationContext(
            content=revision.content,
            content_hash=revision.content_hash,
            playbook_id=str(playbook.id),
            tenant_id=str(playbook.tenant_id),
            contract=contract,
        )
        outcome = assess(context)
        assessment = await record_assessment(db, playbook, revision, outcome)
        logger.info(
            "playbook_quality.assessed",
            tenant_id=str(playbook.tenant_id),
            playbook_id=str(playbook.id),
            revision=revision.revision_number,
            content_hash=revision.content_hash[:12],
            state=outcome.overall_state,
            findings=len(outcome.findings),
            origin=origin,
            mode=assessment.evaluation_mode,
        )
        return assessment
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception(
            "playbook_quality.assessment_failed",
            playbook_id=str(getattr(playbook, "id", None)),
            origin=origin,
            error=str(exc)[:400],
        )
        return await _record_error_assessment(db, playbook, origin, str(exc)[:200])


async def _record_error_assessment(
    db: AsyncSession, playbook: Playbook, origin: str, reason: str
) -> PlaybookQualityAssessment | None:
    """Best-effort 'we could not assess this' row.

    Runs in a savepoint because the failure above may have left the session
    dirty, and a second failure here must not take the caller's transaction
    down with it.
    """
    try:
        async with db.begin_nested():
            revision = await ensure_content_revision(db, playbook, None, origin=origin)
            return await record_assessment(
                db, playbook, revision, error_outcome(f"{origin}: {reason}")
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "playbook_quality.error_assessment_unpersisted",
            playbook_id=str(getattr(playbook, "id", None)),
        )
        return None


async def signal_quality_stale(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    *,
    reason: str,
    origin: str,
) -> int:
    """Mark open assessments stale when an external signal says revalidate.

    Used by contradiction and drift scans — quality must never break those
    passes if invalidation fails.
    """
    try:
        count = await invalidate_assessments(db, tenant_id, playbook_id, reason=reason)
        if count:
            logger.info(
                "playbook_quality.signalled_stale",
                tenant_id=str(tenant_id),
                playbook_id=str(playbook_id),
                reason=reason,
                origin=origin,
                assessments=count,
            )
        return count
    except Exception:  # noqa: BLE001
        logger.exception(
            "playbook_quality.signal_stale_failed",
            tenant_id=str(tenant_id),
            playbook_id=str(playbook_id),
            reason=reason,
            origin=origin,
        )
        return 0


async def invalidate_assessments(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    *,
    reason: str,
) -> int:
    """Mark this playbook's open assessments stale.

    Stale is not fail. It says the verdict was about inputs that have since
    changed, which is a different thing a reviewer needs to see differently:
    a failed assessment means we found a defect, a stale one means we no
    longer know.

    Called on every mutation that changes what the verdict was about,
    including the ones that do not touch steps — a title edit, a superseded
    source, a policy-pack change.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(PlaybookQualityAssessment)
        .where(
            PlaybookQualityAssessment.tenant_id == tenant_id,
            PlaybookQualityAssessment.playbook_id == playbook_id,
            PlaybookQualityAssessment.superseded_at.is_(None),
            PlaybookQualityAssessment.stale_at.is_(None),
        )
        .values(stale_at=now, stale_reason=reason, overall_state=STATE_STALE)
    )
    count = result.rowcount or 0
    if count:
        logger.info(
            "playbook_quality.invalidated",
            tenant_id=str(tenant_id),
            playbook_id=str(playbook_id),
            reason=reason,
            assessments=count,
        )
    return count


async def invalidate_and_reassess(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None = None,
    *,
    reason: str,
    origin: str,
    actor_id: uuid.UUID | None = None,
    quality_contract_hash: str | None = None,
    source_snapshot_hash: str | None = None,
) -> PlaybookQualityAssessment | None:
    """Invalidate then reassess. The normal shape of a mutation hook.

    Order matters: invalidating first means that if reassessment fails, what
    remains is a stale verdict rather than a fresh-looking one about content
    that no longer exists.
    """
    try:
        await invalidate_assessments(db, playbook.tenant_id, playbook.id, reason=reason)
    except Exception:  # noqa: BLE001
        logger.exception(
            "playbook_quality.invalidate_failed", playbook_id=str(playbook.id), reason=reason
        )
    return await assess_playbook(
        db,
        playbook,
        version,
        origin=origin,
        actor_id=actor_id,
        quality_contract_hash=quality_contract_hash,
        source_snapshot_hash=source_snapshot_hash,
    )


def _contract_from_version(version: PlaybookVersion | None) -> dict[str, Any] | None:
    """Load the stored quality contract summary from evidence_refs, if any."""
    if version is None:
        return None
    refs = version.evidence_refs
    if not isinstance(refs, dict):
        return None
    qc = refs.get("quality_contract")
    if not isinstance(qc, dict):
        return None
    artifact_type = qc.get("artifact_type")
    if not artifact_type:
        return None
    return {"artifact_type": artifact_type, "outcome": qc.get("outcome"), "hash": qc.get("hash")}


async def latest_assessment(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_id: uuid.UUID
) -> PlaybookQualityAssessment | None:
    result = await db.execute(
        select(PlaybookQualityAssessment)
        .where(
            PlaybookQualityAssessment.tenant_id == tenant_id,
            PlaybookQualityAssessment.playbook_id == playbook_id,
        )
        .order_by(PlaybookQualityAssessment.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def assessments_for_playbooks(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PlaybookQualityAssessment]:
    """Current assessment per playbook, for the review queue's list render.

    One query for the page rather than one per row — the review queue is the
    screen this data exists to improve, and making it slower would be a poor
    way to introduce it.
    """
    if not playbook_ids:
        return {}
    result = await db.execute(
        select(PlaybookQualityAssessment)
        .where(
            PlaybookQualityAssessment.tenant_id == tenant_id,
            PlaybookQualityAssessment.playbook_id.in_(playbook_ids),
            PlaybookQualityAssessment.superseded_at.is_(None),
        )
        .order_by(PlaybookQualityAssessment.created_at.desc())
    )
    out: dict[uuid.UUID, PlaybookQualityAssessment] = {}
    for assessment in result.scalars().all():
        out.setdefault(assessment.playbook_id, assessment)
    return out


async def findings_for(
    db: AsyncSession, tenant_id: uuid.UUID, assessment_id: uuid.UUID
) -> list[PlaybookQualityFinding]:
    """Findings for one assessment, worst first.

    Ordered by severity here rather than in the UI because every consumer
    wants the same order, and a reviewer scanning a long list should not have
    to find the critical one.
    """
    result = await db.execute(
        select(PlaybookQualityFinding)
        .where(
            PlaybookQualityFinding.tenant_id == tenant_id,
            PlaybookQualityFinding.assessment_id == assessment_id,
        )
        .order_by(
            case(
                {name: index for index, name in enumerate(SEVERITIES)},
                value=PlaybookQualityFinding.severity,
                else_=len(SEVERITIES),
            ),
            PlaybookQualityFinding.dimension,
            PlaybookQualityFinding.created_at,
        )
    )
    return list(result.scalars().all())


def summarize(
    assessment: PlaybookQualityAssessment | None,
    *,
    live_content_hash: str | None = None,
    finding_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """The compact shape a list row needs.

    ``matches_current_content`` is the field that stops a stale verdict being
    read as a live one: an assessment whose hash no longer matches what the
    playbook presents is describing content that has since moved, and the row
    must say so even though the assessment itself looks perfectly healthy.
    """
    if assessment is None:
        return {
            "state": None,
            "structure": None,
            "groups": {"subject": None, "steps": None, "coherence": None},
            "coverage": {"decided": 0, "undecided": 0, "total": 0},
            "finding_counts": {severity: 0 for severity in SEVERITIES},
            "matches_current_content": False,
            "assessed_at": None,
            "stale_reason": None,
            "evaluation_mode": None,
        }
    states = assessment.dimension_states or {}
    return {
        "state": assessment.overall_state,
        # Above the three groups, not beside them: a malformed artifact makes
        # the other verdicts moot rather than merely accompanying them.
        "structure": structure_state(states),
        "groups": group_states(states),
        "coverage": coverage(states),
        "finding_counts": finding_counts or {severity: 0 for severity in SEVERITIES},
        "matches_current_content": (
            live_content_hash is None or assessment.content_hash == live_content_hash
        ),
        "assessed_at": assessment.completed_at or assessment.created_at,
        "stale_reason": assessment.stale_reason,
        "evaluation_mode": assessment.evaluation_mode,
    }


async def finding_counts_for(
    db: AsyncSession, tenant_id: uuid.UUID, assessment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Severity histogram per assessment, in one query.

    One query for the page, not one per row. The review queue is the screen
    this data exists to improve; making it slower would be a poor way to
    introduce it.
    """
    if not assessment_ids:
        return {}
    result = await db.execute(
        select(
            PlaybookQualityFinding.assessment_id,
            PlaybookQualityFinding.severity,
            func.count(),
        )
        .where(
            PlaybookQualityFinding.tenant_id == tenant_id,
            PlaybookQualityFinding.assessment_id.in_(assessment_ids),
        )
        .group_by(
            PlaybookQualityFinding.assessment_id, PlaybookQualityFinding.severity
        )
    )
    out: dict[uuid.UUID, dict[str, int]] = {
        assessment_id: {severity: 0 for severity in SEVERITIES}
        for assessment_id in assessment_ids
    }
    for assessment_id, severity, count in result.all():
        out.setdefault(assessment_id, {severity: 0 for severity in SEVERITIES})
        out[assessment_id][severity] = count
    return out


async def quality_report(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None = None,
) -> dict[str, Any]:
    """Everything the reviewer panel needs for one playbook.

    Read-only and side-effect free: it never mints a revision and never
    triggers an assessment. Opening a playbook must not change its quality
    history, or the history stops being a record of what happened and becomes
    a record of who looked.
    """
    if version is None and playbook.current_version_id is not None:
        version = await db.get(PlaybookVersion, playbook.current_version_id)

    live_hash = content_hash(build_content(playbook, version))
    assessment = await latest_assessment(db, playbook.tenant_id, playbook.id)
    readiness = await publication_readiness(db, playbook, version)

    if assessment is None:
        return {
            "playbook_id": playbook.id,
            "content_hash": live_hash,
            "assessment": None,
            "summary": summarize(None, live_content_hash=live_hash),
            "findings": [],
            "readiness": readiness,
        }

    findings = await findings_for(db, playbook.tenant_id, assessment.id)
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    return {
        "playbook_id": playbook.id,
        "content_hash": live_hash,
        "assessment": assessment,
        "summary": summarize(
            assessment, live_content_hash=live_hash, finding_counts=counts
        ),
        "findings": findings,
        "readiness": readiness,
    }


async def publication_readiness(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None = None,
) -> dict[str, Any]:
    """Would the Phase 5 gate let this be approved?

    Nothing calls this to decide anything yet. It exists now so that switching
    enforcement on is a call-site change with a known answer, and so shadow
    mode can report exactly how many playbooks the gate *would* have stopped —
    which is the number product and support need before they agree to turn it
    on.

    ``blocked_reason`` is always populated when ``ready`` is false, because a
    gate that refuses without saying why is a gate that gets bypassed.
    """
    if version is None and playbook.current_version_id is not None:
        version = await db.get(PlaybookVersion, playbook.current_version_id)

    live_hash = content_hash(build_content(playbook, version))
    assessment = await latest_assessment(db, playbook.tenant_id, playbook.id)

    if assessment is None:
        return {
            "ready": False,
            "state": None,
            "blocked_reason": "no_assessment",
            "content_hash": live_hash,
        }
    if assessment.content_hash != live_hash:
        # The content moved after it was judged. This is the check that makes
        # the whole scheme work: without it, an edit between assessment and
        # approval publishes text nothing ever looked at.
        return {
            "ready": False,
            "state": assessment.overall_state,
            "blocked_reason": "content_changed_since_assessment",
            "content_hash": live_hash,
            "assessed_hash": assessment.content_hash,
        }
    if assessment.stale_at is not None:
        return {
            "ready": False,
            "state": STATE_STALE,
            "blocked_reason": assessment.stale_reason or "stale",
            "content_hash": live_hash,
        }
    if assessment.overall_state in NON_PASSING_STATES:
        return {
            "ready": False,
            "state": assessment.overall_state,
            "blocked_reason": f"assessment_{assessment.overall_state}",
            "content_hash": live_hash,
        }
    return {
        "ready": assessment.overall_state == STATE_PASS,
        "state": assessment.overall_state,
        "blocked_reason": None,
        "content_hash": live_hash,
    }
