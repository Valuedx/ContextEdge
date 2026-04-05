"""Playbook governance service with lifecycle state machine."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookApproval, PlaybookVersion

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
) -> Playbook:
    """Transition playbook to a new lifecycle state."""
    current = playbook.lifecycle_state
    allowed = VALID_TRANSITIONS.get(current, set())
    if new_state not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from '{current}' to '{new_state}'. Allowed: {allowed}"
        )

    playbook.lifecycle_state = new_state

    if new_state == "approved":
        now = datetime.now(UTC)
        version = await _current_playbook_version(db, playbook)
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
    return playbook


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
                    playbook_confidence=float(version_data.get("playbook_confidence", 0.5)),
                    execution_confidence_guidance=version_data.get("execution_confidence_guidance"),
                )
                db.add(version)
                await db.flush()

                playbook.current_version_id = version.id
                await db.flush()
            return version
        except IntegrityError as exc:
            if requested_semantic_version is not None:
                raise DuplicateVersionError(
                    _duplicate_version_message(semantic_version)
                ) from exc

    raise DuplicateVersionError("Could not allocate a unique semantic_version for this playbook")
