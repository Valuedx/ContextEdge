"""Playbook governance service with lifecycle state machine."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookApproval, PlaybookVersion

VALID_TRANSITIONS = {
    "candidate": {"under_review"},
    "under_review": {"approved", "candidate"},
    "approved": {"restricted", "deprecated", "expired", "retired"},
    "restricted": {"approved", "deprecated", "retired"},
    "deprecated": {"retired"},
    "expired": {"under_review", "retired"},
    "retired": set(),
}


class InvalidTransitionError(Exception):
    pass


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
        playbook.approver_user_id = actor_id
        playbook.last_validated_at = datetime.now(timezone.utc)

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
    published_by: uuid.UUID,
) -> PlaybookVersion:
    """Create a new version of a playbook."""
    version = PlaybookVersion(
        playbook_id=playbook.id,
        semantic_version=version_data.get("semantic_version", "0.1.0"),
        trigger_conditions=version_data.get("trigger_conditions", {}),
        branching_logic=version_data.get("branching_logic", {}),
        inputs=version_data.get("inputs", []),
        outputs=version_data.get("outputs", []),
        steps=version_data.get("steps", []),
        rollback_notes=version_data.get("rollback_notes"),
        evidence_refs=version_data.get("evidence_refs"),
        playbook_confidence=float(version_data.get("playbook_confidence", 0.5)),
        execution_confidence_guidance=version_data.get("execution_confidence_guidance"),
        published_by=published_by,
    )
    db.add(version)
    await db.flush()

    playbook.current_version_id = version.id
    await db.flush()
    return version
