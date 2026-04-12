import uuid as uuid_mod
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
    PlaybookResponse,
    PlaybookTransition,
    PlaybookUpdate,
    PlaybookVersionCreate,
    PlaybookVersionResponse,
)
from contextedge.services.playbook_service import (
    DuplicateVersionError,
    InvalidTransitionError,
    create_playbook_version,
    transition_playbook,
)
from pydantic import BaseModel, Field

router = APIRouter()
logger = structlog.get_logger()


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
    stable_key = f"pb-{uuid_mod.uuid4().hex[:12]}"
    playbook = Playbook(
        tenant_id=user.tenant_id,
        domain_id=body.domain_id,
        stable_key=stable_key,
        title=body.title,
        description=body.description,
        risk_tier=body.risk_tier,
        automation_mode=body.automation_mode,
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

    for field, value in body.model_dump(exclude_unset=True).items():
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
    from contextedge.graph.builder import link_node_to_identities

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
