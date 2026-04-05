from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.schemas.playbook import PatternResponse

router = APIRouter()


@router.get("", response_model=list[PatternResponse])
async def list_patterns(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = None,
    active_only: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Pattern).where(Pattern.tenant_id == user.tenant_id)
    if domain_id:
        q = q.where(Pattern.domain_id == domain_id)
    if active_only:
        q = q.where(Pattern.active_flag.is_(True))
    q = q.order_by(Pattern.episode_count.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{pattern_id}", response_model=PatternResponse)
async def get_pattern(pattern_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    pattern = result.scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")
    return pattern


@router.get("/{pattern_id}/graph")
async def get_pattern_graph(pattern_id: UUID, db: DbSession, user: AuthUser):
    from contextedge.graph.queries import get_pattern_subgraph
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Pattern not found")
    return await get_pattern_subgraph(db, user.tenant_id, pattern_id)
