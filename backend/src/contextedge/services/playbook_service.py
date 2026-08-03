"""Playbook governance service with lifecycle state machine."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import (
    Playbook,
    PlaybookApproval,
    PlaybookEvidenceLink,
    PlaybookVersion,
)
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.memory_service import promote_playbook_memory

VALID_TRANSITIONS = {
    "candidate": {"under_review"},
    "under_review": {"approved", "candidate"},
    "approved": {"under_review", "restricted", "deprecated", "expired", "retired"},
    "restricted": {"approved", "deprecated", "retired"},
    "deprecated": {"retired"},
    "expired": {"under_review", "retired"},
    "retired": set(),
}


class InvalidTransitionError(Exception):
    pass


class DuplicateVersionError(Exception):
    pass


SEMANTIC_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def _parse_semantic_version(value: str) -> tuple[int, int, int] | None:
    match = SEMANTIC_VERSION_RE.fullmatch(value)
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _next_semantic_version(existing_versions: list[str]) -> str:
    used = set(existing_versions)
    parsed = [
        parsed
        for version in existing_versions
        if (parsed := _parse_semantic_version(version))
    ]
    if parsed:
        major, minor, patch = max(parsed)
        patch += 1
        candidate = f"{major}.{minor}.{patch}"
        while candidate in used:
            patch += 1
            candidate = f"{major}.{minor}.{patch}"
        return candidate

    patch = 0
    candidate = f"0.1.{patch}"
    while candidate in used:
        patch += 1
        candidate = f"0.1.{patch}"
    return candidate


async def _existing_semantic_versions(
    db: AsyncSession,
    playbook_id: uuid.UUID,
) -> list[str]:
    result = await db.execute(
        select(PlaybookVersion.semantic_version).where(
            PlaybookVersion.playbook_id == playbook_id,
        )
    )
    return list(result.scalars().all())


# Link types written from the generated evidence_refs blob. Richer,
# purpose-specific types (based_on_sop, supports_rollback, …) belong to
# the knowledge-provenance work; these two are what the current
# generation path actually knows.
EVIDENCE_LINK_TYPE = "derived_from_evidence"
EPISODE_LINK_TYPE = "derived_from_episode"
# Normative grounding: the approved KB/SOP this version implements, as
# distinct from the incidents it was derived from. Kept a separate link
# type so "which SOP does this playbook implement" is answerable without
# re-reading the JSON blob, and so a knowledge-drift check can find every
# playbook version citing an article that changed.
KNOWLEDGE_LINK_TYPE = "based_on_kb"

MAX_EVIDENCE_LINKS = 500


def _coerce_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _materialize_evidence_links(
    db: AsyncSession, version: PlaybookVersion, evidence_refs: object
) -> int:
    """Write normalized provenance rows from the ``evidence_refs`` JSON.

    Tolerant by design: ``evidence_refs`` is model-authored and has been
    seen as a dict of id lists, and a bare list. Anything unparseable is
    skipped rather than raised — losing a provenance row is recoverable,
    failing version creation is not.

    Bounded at ``MAX_EVIDENCE_LINKS`` so a runaway generation cannot
    write an unbounded number of rows for one version.
    """
    if not evidence_refs:
        return 0

    evidence_ids: list[object] = []
    episode_ids: list[object] = []
    knowledge_ids: list[object] = []

    if isinstance(evidence_refs, dict):
        raw_evidence = evidence_refs.get("evidence_ids")
        raw_episodes = evidence_refs.get("episode_ids")
        raw_knowledge = evidence_refs.get("knowledge_ids")
        if isinstance(raw_evidence, list):
            evidence_ids = raw_evidence
        if isinstance(raw_episodes, list):
            episode_ids = raw_episodes
        if isinstance(raw_knowledge, list):
            knowledge_ids = raw_knowledge
    elif isinstance(evidence_refs, list):
        evidence_ids = evidence_refs

    written = 0
    seen: set[tuple[str, str]] = set()

    for raw_id in evidence_ids[:MAX_EVIDENCE_LINKS]:
        parsed = _coerce_uuid(raw_id)
        if parsed is None or ("e", str(parsed)) in seen:
            continue
        seen.add(("e", str(parsed)))
        db.add(
            PlaybookEvidenceLink(
                playbook_version_id=version.id,
                evidence_id=parsed,
                link_type=EVIDENCE_LINK_TYPE,
            )
        )
        written += 1

    for raw_id in episode_ids[:MAX_EVIDENCE_LINKS]:
        parsed = _coerce_uuid(raw_id)
        if parsed is None or ("p", str(parsed)) in seen:
            continue
        seen.add(("p", str(parsed)))
        db.add(
            PlaybookEvidenceLink(
                playbook_version_id=version.id,
                episode_id=parsed,
                link_type=EPISODE_LINK_TYPE,
            )
        )
        written += 1

    for raw_id in knowledge_ids[:MAX_EVIDENCE_LINKS]:
        parsed = _coerce_uuid(raw_id)
        # Deduped against the evidence namespace: a KB article is an
        # EvidenceItem, so the same id could arrive on both lists. When
        # it does, the knowledge link is the more specific claim and the
        # first write wins.
        if parsed is None or ("e", str(parsed)) in seen:
            continue
        seen.add(("e", str(parsed)))
        db.add(
            PlaybookEvidenceLink(
                playbook_version_id=version.id,
                evidence_id=parsed,
                link_type=KNOWLEDGE_LINK_TYPE,
            )
        )
        written += 1

    return written


def _duplicate_version_message(semantic_version: str) -> str:
    return f"semantic_version '{semantic_version}' already exists for this playbook"


async def _current_playbook_version(
    db: AsyncSession,
    playbook: Playbook,
) -> PlaybookVersion:
    if playbook.current_version_id is None:
        raise InvalidTransitionError("Cannot approve playbook without a current version")
    version = await db.get(PlaybookVersion, playbook.current_version_id)
    if version is None or version.playbook_id != playbook.id:
        raise InvalidTransitionError("Current version not found for this playbook")
    return version


async def transition_playbook(
    db: AsyncSession,
    playbook: Playbook,
    new_state: str,
    actor_id: uuid.UUID,
    comments: str | None = None,
    *,
    redis=None,
) -> Playbook:
    """Transition playbook to a new lifecycle state.

    ``redis`` is optional; when provided, any cached runtime-match
    responses for this tenant are invalidated so stale scoring /
    explanations can't survive a transition. See review F-09.
    """
    current = playbook.lifecycle_state
    allowed = VALID_TRANSITIONS.get(current, set())
    approved_version: PlaybookVersion | None = None
    if new_state not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from '{current}' to '{new_state}'. Allowed: {allowed}"
        )

    # A playbook with no steps is not a procedure, and must not enter the
    # governance path as if it were.
    #
    # The generator can no longer emit one, but that guard is at creation
    # and does not cover what is already stored, versions authored
    # directly through the API (``steps`` defaults to an empty list), or
    # rows that predate it. Checking here covers all three, and does it at
    # the moment the claim is actually made: sending an empty draft to
    # review costs a reviewer their time to discover there is nothing to
    # read, and approving one produces something that looks like a
    # certified procedure and executes as a no-op.
    if new_state in ("under_review", "approved"):
        candidate_version = await _current_playbook_version(db, playbook)
        if not (candidate_version.steps or []):
            raise InvalidTransitionError(
                f"Cannot move playbook to '{new_state}': version "
                f"{candidate_version.semantic_version} has no steps. A "
                "playbook with no steps has nothing to review or execute — "
                "generation likely returned a truncated response."
            )

    playbook.lifecycle_state = new_state

    if new_state == "approved":
        now = datetime.now(UTC)
        version = await _current_playbook_version(db, playbook)
        approved_version = version
        playbook.approver_user_id = actor_id
        playbook.last_validated_at = now
        if version.published_at is None:
            version.published_at = now
        if version.published_by is None:
            version.published_by = actor_id

    approval = PlaybookApproval(
        playbook_id=playbook.id,
        playbook_version_id=playbook.current_version_id,
        approver_id=actor_id,
        action=new_state,
        comments=comments,
    )
    db.add(approval)
    await db.flush()
    await append_operational_event(
        db,
        tenant_id=playbook.tenant_id,
        actor_id=actor_id,
        entity_type="playbook",
        entity_id=playbook.id,
        event_type="playbook.transitioned",
        payload={
            "from_state": current,
            "to_state": new_state,
            "current_version_id": (
                str(playbook.current_version_id) if playbook.current_version_id else None
            ),
            "approval_id": str(approval.id),
            "comments": comments,
        },
    )
    if new_state == "approved" and approved_version is not None:
        await promote_playbook_memory(
            db,
            playbook=playbook,
            version=approved_version,
            actor_id=actor_id,
        )
        if playbook.embedding is None:
            # "approved" is exactly the state the agent seed resolver's
            # semantic layer filters on (migration 0035), so repair the
            # fingerprint here: pre-0035 rows and playbooks whose embed
            # failed transiently at version creation become semantically
            # matchable the moment a reviewer approves them. Best-effort —
            # a provider failure leaves FTS-only matching, never blocks
            # the transition.
            from contextedge.services.playbook_embedding import embed_playbook

            await embed_playbook(db, playbook, approved_version)

    # Invalidate any runtime-match cache for this tenant. The cached
    # payload may reference this playbook's old lifecycle_state; a
    # reviewer who just flipped it to "restricted" / "retired" should
    # not still see the old entry in /runtime/explain. The TTL is
    # 3600s so an at-most-hour of staleness becomes an at-most-
    # transition-flush here. See review F-09.
    if redis is not None:
        await _invalidate_runtime_match_cache(redis, playbook.tenant_id)

    return playbook


async def _invalidate_runtime_match_cache(redis, tenant_id: uuid.UUID) -> None:
    """Drop every ``runtime:match:*`` key belonging to ``tenant_id``.

    The cache key is opaque (match_id, UUID-random) so we can't target
    by tenant without SCAN-ing. SCAN is cursor-based and non-blocking;
    at a 3600s TTL the working set per tenant is small, and this is a
    rare path (reviewer transitions, not per-request work).
    """
    import json as _json

    try:
        async for key in redis.scan_iter(match="runtime:match:*", count=200):
            try:
                raw = await redis.get(key)
                if not raw:
                    continue
                data = _json.loads(raw)
                if data.get("tenant_id") == str(tenant_id):
                    await redis.delete(key)
            except Exception:
                # One bad key mustn't abort the sweep.
                continue
    except Exception:
        # Redis unavailable — log and move on. Transition still commits;
        # stale cache entries simply TTL out normally.
        logger = __import__("structlog").get_logger()
        logger.warning("runtime_match_cache_invalidation_failed", tenant_id=str(tenant_id))


async def create_playbook_version(
    db: AsyncSession,
    playbook: Playbook,
    version_data: dict,
) -> PlaybookVersion:
    """Create a new version of a playbook."""
    requested_semantic_version = version_data.get("semantic_version")
    if requested_semantic_version is not None:
        requested_semantic_version = str(requested_semantic_version)

    for _attempt in range(3):
        existing_versions = await _existing_semantic_versions(db, playbook.id)
        if requested_semantic_version is not None:
            semantic_version = requested_semantic_version
            if semantic_version in existing_versions:
                raise DuplicateVersionError(_duplicate_version_message(semantic_version))
        else:
            semantic_version = _next_semantic_version(existing_versions)

        try:
            async with db.begin_nested():
                version = PlaybookVersion(
                    playbook_id=playbook.id,
                    semantic_version=semantic_version,
                    trigger_conditions=version_data.get("trigger_conditions", {}),
                    branching_logic=version_data.get("branching_logic", {}),
                    inputs=version_data.get("inputs", []),
                    outputs=version_data.get("outputs", []),
                    steps=version_data.get("steps", []),
                    rollback_notes=version_data.get("rollback_notes"),
                    evidence_refs=version_data.get("evidence_refs"),
                    conflicts=version_data.get("conflicts"),
                    playbook_confidence=float(version_data.get("playbook_confidence", 0.5)),
                    execution_confidence_guidance=version_data.get("execution_confidence_guidance"),
                    verification_policy=version_data.get("verification_policy"),
                )
                db.add(version)
                await db.flush()

                # Materialize the JSON evidence_refs into normalized link
                # rows. Until this existed, PlaybookEvidenceLink was READ
                # in two search paths and WRITTEN nowhere in the codebase
                # — and vector_search inner-joins it when a query is
                # scoped to a playbook, so playbook-scoped semantic search
                # returned zero rows every time, silently. The JSON blob
                # stays as the cheap read; these rows are the queryable
                # provenance the ranker actually needs.
                _materialize_evidence_links(db, version, version_data.get("evidence_refs"))
                await db.flush()

                playbook.current_version_id = version.id
                await db.flush()
            await append_operational_event(
                db,
                tenant_id=playbook.tenant_id,
                entity_type="playbook_version",
                entity_id=version.id,
                event_type="playbook.version_created",
                payload={
                    "playbook_id": str(playbook.id),
                    "semantic_version": version.semantic_version,
                    "playbook_confidence": version.playbook_confidence,
                },
            )
            return version
        except IntegrityError as exc:
            if requested_semantic_version is not None:
                raise DuplicateVersionError(
                    _duplicate_version_message(semantic_version)
                ) from exc

    raise DuplicateVersionError("Could not allocate a unique semantic_version for this playbook")
