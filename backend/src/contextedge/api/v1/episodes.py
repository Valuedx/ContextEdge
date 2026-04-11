from uuid import UUID

from datetime import datetime, timedelta, UTC
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.episode import Episode, EpisodeStep
from contextedge.schemas.evidence import (
    EpisodeDetail,
    EpisodeResponse,
    EpisodeUpdate,
    ReconstructRequest,
)

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


@router.post("/reconstruct", status_code=status.HTTP_202_ACCEPTED)
async def trigger_manual_reconstruction(
    body: ReconstructRequest,
    db: DbSession,
    user: AuthUser,
):
    """Manually trigger episode reconstruction from evidence."""
    user.require_role("domain_admin")

    evidence_ids = body.evidence_ids

    if not evidence_ids:
        # Fallback: Find all relevant evidence from the last 24 hours
        from contextedge.models.evidence import EvidenceItem
        since = datetime.now(UTC) - timedelta(hours=24)
        q = select(EvidenceItem.id).where(
            EvidenceItem.tenant_id == user.tenant_id,
            EvidenceItem.relevance_state.in_(["relevant", "operational"]),
            EvidenceItem.ingested_at >= since
        )
        result = await db.execute(q)
        evidence_ids = [r for r in result.scalars().all()]

    if not evidence_ids:
        raise HTTPException(
            status_code=400,
            detail="No relevant evidence found to reconstruct an episode."
        )

    from contextedge.workers.extraction_tasks import _reconstruct
    cluster_id = ",".join([str(eid) for eid in evidence_ids])

    # Call _reconstruct directly to bypass Celery worker lag on Windows
    await _reconstruct(db, cluster_id, user.tenant_id)

    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="episode.reconstruction_triggered",
        resource_type="episode",
        resource_id="manual",
        details={"evidence_count": len(evidence_ids)},
    )

    return {
        "status": "reconstruction_queued",
        "evidence_count": len(evidence_ids),
        "correlation_id": cluster_id
    }


@router.delete("/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode(episode_id: UUID, db: DbSession, user: AuthUser):
    """Permanently delete an episode and its steps."""
    user.require_role("domain_admin")

    episode = (
        await db.execute(
            select(Episode).where(Episode.id == episode_id, Episode.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    from sqlalchemy import delete
    from contextedge.models.episode import EpisodeStep

    # 1. Delete Episode Steps
    await db.execute(
        delete(EpisodeStep).where(EpisodeStep.episode_id == episode_id)
    )

    # 2. Finally delete the episode itself
    await db.delete(episode)
    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="episode.deleted",
        resource_type="episode",
        resource_id=str(episode_id),
        details={"title": episode.title},
    )
    return None
