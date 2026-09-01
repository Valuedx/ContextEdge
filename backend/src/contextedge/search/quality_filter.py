"""Runtime retrieval filter — exclude playbooks with bad quality assessments."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.playbook_quality import PlaybookQualityAssessment
from contextedge.quality import build_content
from contextedge.quality.hashing import content_hash
from contextedge.quality.states import STATE_ERROR, STATE_FAIL, STATE_STALE
from contextedge.services.playbook_quality_service import assessments_for_playbooks

logger = structlog.get_logger()

RUNTIME_EXCLUDED_STATES = frozenset({STATE_FAIL, STATE_ERROR, STATE_STALE})


def _runtime_filter_enabled() -> bool:
    try:
        from contextedge.config import settings

        return bool(getattr(settings, "playbook_runtime_quality_filter", True))
    except Exception:  # noqa: BLE001
        return True


def assessment_excludes_runtime(
    assessment: PlaybookQualityAssessment | None,
    *,
    live_content_hash: str | None,
) -> bool:
    """True when this assessment must not appear in SupportCopilot retrieval.

    ``live_content_hash`` of ``None`` means "we could not work out what the
    playbook currently says". That is not a mismatch and must not exclude:
    an unknown is the one input that, treated as a failure, silently empties
    the whole corpus rather than dropping the bad rows. The state checks above
    still apply.
    """
    if assessment is None:
        return False
    if assessment.overall_state in RUNTIME_EXCLUDED_STATES:
        return True
    if assessment.stale_at is not None:
        return True
    if live_content_hash and assessment.content_hash != live_content_hash:
        return True
    return False


async def _load_current_versions(
    db: AsyncSession,
    playbooks: dict[uuid.UUID, Playbook],
    supplied: dict[uuid.UUID, object] | None,
) -> dict[uuid.UUID, object]:
    """Fill in the current version for any playbook the caller did not supply.

    The content hash spans the shell *and* the version, so a hash computed
    with the version missing can never equal the one the assessment stored.
    Leaving that to the caller made the parameter a trap: ``hybrid_ranker``
    passed its versions and worked, ``playbook_candidates`` did not and
    excluded every assessed playbook in the tenant — with a log line that read
    like the filter doing its job.

    One query for whatever is missing, rather than trusting five call sites to
    remember.
    """
    versions = dict(supplied or {})
    missing = [
        pb.current_version_id
        for pid, pb in playbooks.items()
        if pid not in versions and pb.current_version_id is not None
    ]
    if not missing:
        return versions
    rows = await db.execute(
        select(PlaybookVersion).where(PlaybookVersion.id.in_(missing))
    )
    by_id = {version.id: version for version in rows.scalars().all()}
    for pid, pb in playbooks.items():
        if pid in versions:
            continue
        found = by_id.get(pb.current_version_id) if pb.current_version_id else None
        if found is not None:
            versions[pid] = found
    return versions


async def filter_runtime_eligible(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbooks: dict[uuid.UUID, Playbook],
    *,
    versions_by_playbook: dict[uuid.UUID, object] | None = None,
) -> dict[uuid.UUID, Playbook]:
    """Drop playbooks whose latest assessment is fail, error, stale, or out of date.

    ``versions_by_playbook`` is an optimisation for callers that already hold
    the versions; anything missing is loaded here. See ``_load_current_versions``
    for why this is not left to the caller.
    """
    if not playbooks or not _runtime_filter_enabled():
        return playbooks

    assessments = await assessments_for_playbooks(db, tenant_id, list(playbooks))
    if not assessments:
        # Nothing assessed: no hashes to compare and nothing to exclude. Skip
        # the version load entirely — this is the common case today, with the
        # corpus unassessed.
        return playbooks

    versions = await _load_current_versions(db, playbooks, versions_by_playbook)
    kept: dict[uuid.UUID, Playbook] = {}
    dropped = 0

    for pid, pb in playbooks.items():
        assessment = assessments.get(pid)
        version = versions.get(pid)
        # No version resolved and the playbook claims to have one: we cannot
        # say what it currently contains, so pass None and let the state
        # checks decide rather than reporting a mismatch we did not observe.
        if version is None and pb.current_version_id is not None:
            live_hash = None
        else:
            live_hash = content_hash(build_content(pb, version))
        if assessment_excludes_runtime(assessment, live_content_hash=live_hash):
            dropped += 1
            continue
        kept[pid] = pb

    if dropped:
        logger.info(
            "playbook_runtime_quality.filtered",
            tenant_id=str(tenant_id),
            dropped=dropped,
            kept=len(kept),
        )
    return kept
