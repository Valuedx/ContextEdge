import difflib
import json
import uuid as uuid_mod
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import structlog

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.schemas.playbook import (
    PlaybookCreate,
    PlaybookRollbackRequest,
    PlaybookResponse,
    PlaybookTransition,
    PlaybookUpdate,
    PlaybookVersionCreate,
    PlaybookVersionDiffResponse,
    PlaybookVersionResponse,
)
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.services.playbook_service import (
    DuplicateVersionError,
    InvalidTransitionError,
    create_playbook_version,
    transition_playbook,
)
from pydantic import BaseModel, Field

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
        "playbook_confidence": version.playbook_confidence,
        "execution_confidence_guidance": version.execution_confidence_guidance,
    }


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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Playbook).where(Playbook.tenant_id == user.tenant_id)
    if lifecycle_state:
        q = q.where(Playbook.lifecycle_state == lifecycle_state)
    if domain_id:
        q = q.where(Playbook.domain_id == domain_id)
    q = q.order_by(Playbook.updated_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


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
    if "approval_policy_id" in update_data:
        await assert_policy_assignment(
            db,
            user.tenant_id,
            update_data["approval_policy_id"],
            "approval",
        )
    for field, value in update_data.items():
        setattr(playbook, field, value)
    await db.flush()
    await db.refresh(playbook)
    return playbook


@router.post("/{playbook_id}/transition", response_model=PlaybookResponse)
async def transition(playbook_id: UUID, body: PlaybookTransition, db: DbSession, user: AuthUser):
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


@router.get("/{playbook_id}/versions", response_model=list[PlaybookVersionResponse])
async def list_versions(playbook_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(PlaybookVersion)
        .where(PlaybookVersion.playbook_id == playbook_id)
        .order_by(PlaybookVersion.created_at.desc())
    )
    return result.scalars().all()


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
    return version


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
    version = await create_playbook_version(db, playbook, version_data)
    if playbook.lifecycle_state == "approved":
        version.published_at = version.published_at or datetime.now(UTC)
        version.published_by = version.published_by or user.user_id
    return version


class GeneratePlaybookRequest(BaseModel):
    pattern_id: UUID


@router.post("/generate", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def generate_playbook(
    body: GeneratePlaybookRequest,
    db: DbSession,
    user: AuthUser,
):
    """Generate a playbook candidate from a knowledge pattern using AI."""
    user.require_role("knowledge_manager")

    from contextedge.models.pattern import NegativeKnowledgeItem, Pattern
    from contextedge.models.episode import Episode
    from contextedge.ai.generators.playbook_generator import generate_playbook_candidate
    from contextedge.services.playbook_service import create_playbook_version
    from contextedge.services.identity_service import identity_ids_from_refs
    from contextedge.graph.builder import ensure_edge, link_node_to_identities

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

    res = await db.execute(select(Episode).where(Episode.id.in_(episode_ids)))
    episodes = res.scalars().all()

    # 2. Call AI Generator
    try:
        ep_summaries = []
        for ep in episodes:
            ep_summaries.append({
                "title": ep.title,
                "root_cause": ep.root_cause_summary,
                "outcome": ep.final_outcome
            })

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

        candidate = await generate_playbook_candidate(
            pattern.title,
            pattern.description or "",
            len(episodes),
            ep_summaries,
            negative_knowledge
        )

        # 3. Create Playbook Shell
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
        )
        db.add(playbook)
        await db.flush()

        # 4. Create Version 0.1.0 with the AI content
        await create_playbook_version(db, playbook, candidate)
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

    except Exception:
        logger.exception(
            "playbook_generation_failed",
            tenant_id=str(user.tenant_id),
            pattern_id=str(body.pattern_id),
        )
        await db.rollback()
        raise HTTPException(status_code=500, detail="Playbook generation failed")
