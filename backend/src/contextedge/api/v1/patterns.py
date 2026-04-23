from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import structlog

from contextedge.deps import AuthUser, DbSession
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.schemas.common import TaskDispatchResponse
from contextedge.schemas.playbook import PatternResponse
from contextedge.schemas.review import PatternEvidenceLinkCreate, PatternEvidenceLinkResponse
from pydantic import BaseModel

router = APIRouter()
logger = structlog.get_logger()
from contextedge.workers.pattern_tasks import cluster_episodes


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
async def get_pattern_graph(
    pattern_id: UUID,
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None, description="Scope edges to a domain (includes domain-less edges)"),
):
    from contextedge.graph.queries import get_pattern_subgraph
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Pattern not found")
    return await get_pattern_subgraph(db, user.tenant_id, pattern_id, domain_id=domain_id)


@router.get("/{pattern_id}/evidence-links", response_model=list[PatternEvidenceLinkResponse])
async def list_pattern_evidence_links(pattern_id: UUID, db: DbSession, user: AuthUser):
    pattern = (
        await db.execute(
            select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")
    result = await db.execute(
        select(PatternEvidenceLink)
        .where(PatternEvidenceLink.pattern_id == pattern_id)
        .order_by(PatternEvidenceLink.created_at.desc())
    )
    return result.scalars().all()


@router.post("/{pattern_id}/evidence-links", response_model=PatternEvidenceLinkResponse, status_code=201)
async def create_pattern_evidence_link(
    pattern_id: UUID,
    body: PatternEvidenceLinkCreate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    pattern = (
        await db.execute(
            select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")
    if body.evidence_id is None and body.episode_id is None:
        raise HTTPException(status_code=400, detail="episode_id or evidence_id is required")

    link = PatternEvidenceLink(
        pattern_id=pattern_id,
        episode_id=body.episode_id,
        evidence_id=body.evidence_id,
        link_type=body.link_type,
        weight=body.weight,
    )
    db.add(link)
    await db.flush()
    await db.refresh(link)
    return link


@router.delete("/{pattern_id}/evidence-links/{link_id}", status_code=204)
async def delete_pattern_evidence_link(
    pattern_id: UUID,
    link_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    link = (
        await db.execute(
            select(PatternEvidenceLink)
            .join(Pattern, Pattern.id == PatternEvidenceLink.pattern_id)
            .where(
                PatternEvidenceLink.id == link_id,
                PatternEvidenceLink.pattern_id == pattern_id,
                Pattern.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Pattern evidence link not found")
    await db.delete(link)
    return None


@router.delete("/{pattern_id}", status_code=204)
async def delete_pattern(
    pattern_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    """Delete a pattern and its associated evidence links."""
    user.require_role("knowledge_manager")
    
    # 1. Fetch pattern to check ownership
    pattern = (
        await db.execute(
            select(Pattern).where(
                Pattern.id == pattern_id, 
                Pattern.tenant_id == user.tenant_id
            )
        )
    ).scalar_one_or_none()
    
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")
        
    # 2. Cleanup associated evidence links manually if not using SQL-level CASCADE
    from sqlalchemy import delete
    await db.execute(
        delete(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pattern_id)
    )
    
    # 3. Delete the pattern itself
    await db.delete(pattern)
    await db.commit()
    
    return None


class PatternDiscoverRequest(BaseModel):
    episode_ids: list[UUID]


@router.post("/discover", response_model=PatternResponse, status_code=201)
async def discover_pattern(
    body: PatternDiscoverRequest,
    db: DbSession,
    user: AuthUser,
):
    """Analyze episodes to synthesize a recurring knowledge pattern."""
    user.require_role("domain_admin")

    from contextedge.models.episode import Episode
    from contextedge.ai.extractors.pattern_extractor import synthesize_pattern
    from contextedge.services.pattern_service import create_pattern_from_episodes
    from contextedge.graph.builder import build_episode_graph
    from contextedge.services.identity_service import identity_ids_from_refs

    # 1. Fetch episodes
    res = await db.execute(
        select(Episode)
        .where(
            Episode.id.in_(body.episode_ids),
            Episode.tenant_id == user.tenant_id
        )
        .options(selectinload(Episode.steps))
    )
    episodes = res.scalars().all()
    if not episodes:
        raise HTTPException(status_code=400, detail="No episodes found to analyze")

    # 2. Convert episodes to dicts for the extractor
    # Simplification for demo: just pass basic fields
    ep_data = []
    for ep in episodes:
        ep_data.append({
            "title": ep.title,
            "root_cause_summary": ep.root_cause_summary,
            "final_outcome": ep.final_outcome,
            "steps": [{"text": s.text} for s in ep.steps]
        })

    # 3. Call AI to synthesize pattern
    try:
        synthesis = await synthesize_pattern(ep_data, tenant_id=user.tenant_id, db=db)

        # 4. Create the Pattern in DB
        pattern = await create_pattern_from_episodes(
            db,
            user.tenant_id,
            episodes[0].domain_id,
            synthesis["title"],
            [ep.id for ep in episodes],
            confidence=synthesis.get("confidence", 0.5),
            description=synthesis.get("description"),
            trigger_conditions=synthesis.get("trigger_conditions"),
            core_entities=synthesis.get("core_entities"),
            observed_errors=synthesis.get("observed_errors"),
            root_causes=synthesis.get("root_causes"),
            resolution_steps=synthesis.get("resolution_steps"),
            evidence_summary=synthesis.get("evidence_summary"),
        )
        await db.flush()

        # 5. Build Graph Edges for visual clustering
        domain_id = episodes[0].domain_id
        for ep in episodes:
            await build_episode_graph(
                db,
                user.tenant_id,
                ep.id,
                pattern.id,
                identity_ids_from_refs(ep.entity_refs),
                domain_id=domain_id,
            )

        await db.commit()
        await db.refresh(pattern)
        return pattern

    except Exception:
        logger.exception(
            "pattern_discovery_failed",
            tenant_id=str(user.tenant_id),
            episode_ids=[str(episode_id) for episode_id in body.episode_ids],
        )
        await db.rollback()
        raise HTTPException(status_code=500, detail="Pattern synthesis failed")
@router.post("/cluster", status_code=202, response_model=TaskDispatchResponse)
async def trigger_episode_clustering(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None, description="Scope clustering to a specific domain"),
):
    """Trigger background clustering of approved episodes into patterns."""
    user.require_role("domain_admin")

    if not domain_id:
        # Fallback: Find the first available domain for the tenant
        from contextedge.models.tenant import Domain
        res = await db.execute(
            select(Domain.id).where(Domain.tenant_id == user.tenant_id).limit(1)
        )
        domain_id = res.scalar_one_or_none()

    if not domain_id:
        raise HTTPException(
            status_code=400,
            detail="No domain found to scope pattern clustering."
        )

    task = cluster_episodes.delay(str(domain_id), str(user.tenant_id))

    return TaskDispatchResponse(
        status="clustering_queued",
        task_id=task.id,
        detail={"domain_id": str(domain_id)},
    )
