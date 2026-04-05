from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.episode import Episode, EpisodeStep
from contextedge.schemas.evidence import EpisodeDetail, EpisodeResponse, EpisodeUpdate

router = APIRouter()


@router.get("", response_model=list[EpisodeResponse])
async def list_episodes(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = None,
    status: str | None = None,
    reviewer_state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Episode).where(Episode.tenant_id == user.tenant_id)
    if domain_id:
        q = q.where(Episode.domain_id == domain_id)
    if status:
        q = q.where(Episode.status == status)
    if reviewer_state:
        q = q.where(Episode.reviewer_state == reviewer_state)
    q = q.order_by(Episode.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{episode_id}", response_model=EpisodeDetail)
async def get_episode(episode_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Episode)
        .where(Episode.id == episode_id, Episode.tenant_id == user.tenant_id)
        .options(selectinload(Episode.steps))
    )
    episode = result.scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


@router.patch("/{episode_id}", response_model=EpisodeResponse)
async def update_episode(
    episode_id: UUID,
    body: EpisodeUpdate,
    db: DbSession,
    user: AuthUser,
):
    result = await db.execute(
        select(Episode).where(Episode.id == episode_id, Episode.tenant_id == user.tenant_id)
    )
    episode = result.scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(episode, field, value)
    await db.flush()
    await db.refresh(episode)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="episode.updated",
        resource_type="episode",
        resource_id=str(episode.id),
        details=update_data,
    )
    return episode


@router.post("/{episode_id}/approve", response_model=EpisodeResponse)
async def approve_episode(episode_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(Episode).where(Episode.id == episode_id, Episode.tenant_id == user.tenant_id)
    )
    episode = result.scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    episode.status = "approved"
    episode.reviewer_state = "approved"
    episode.reviewer_user_id = user.user_id
    await db.flush()
    await db.refresh(episode)

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="episode.approved",
        resource_type="episode",
        resource_id=str(episode.id),
    )
    return episode
