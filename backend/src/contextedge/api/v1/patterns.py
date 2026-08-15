from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.schemas.common import TaskDispatchResponse
from contextedge.schemas.playbook import PatternResponse
from contextedge.schemas.review import PatternEvidenceLinkCreate, PatternEvidenceLinkResponse
from contextedge.workers.pattern_tasks import cluster_episodes

logger = structlog.get_logger()

router = APIRouter()


@router.post("/audit-domains")
async def audit_pattern_domains_endpoint(db: DbSession, user: AuthUser):
    """C7: flag pre-guard patterns whose members belong to other
    domains. Flags for review via operational events - never deletes;
    reviewers fix membership through the pattern-link APIs."""
    user.require_role("knowledge_manager")
    from contextedge.services.pattern_audit_service import audit_pattern_domains

    return await audit_pattern_domains(db, user.tenant_id)
@router.post("/deduplicate")
async def deduplicate_patterns_endpoint(db: DbSession, user: AuthUser):
    """Scan and merge duplicate patterns and playbooks for the user's tenant."""
    from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks

    result = await deduplicate_patterns_and_playbooks(db, user.tenant_id)
    await db.commit()
    return {"status": "success", "data": result}


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
    patterns = list(result.scalars().all())
    if not patterns:
        return []

    from contextedge.models.playbook import Playbook

    pat_ids = [p.id for p in patterns]
    pb_result = await db.execute(
        select(Playbook.id, Playbook.pattern_id, Playbook.updated_at).where(
            Playbook.tenant_id == user.tenant_id,
            Playbook.pattern_id.in_(pat_ids),
        )
    )
    pb_map = {row[1]: (row[0], row[2]) for row in pb_result.all()}

    response_list = []
    for pat in patterns:
        resp = PatternResponse.model_validate(pat)
        if pat.id in pb_map:
            pb_id, pb_updated_at = pb_map[pat.id]
            resp.has_playbook = True
            resp.playbook_id = pb_id
            if (
                pb_updated_at
                and pat.updated_at
                and (pat.updated_at - pb_updated_at).total_seconds() > 5
            ):
                resp.playbook_status = "review_needed"
            else:
                resp.playbook_status = "generated"
        else:
            resp.has_playbook = False
            resp.playbook_status = "none"
        response_list.append(resp)

    return response_list


@router.get("/{pattern_id}", response_model=PatternResponse)
async def get_pattern(pattern_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    pattern = result.scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")

    from contextedge.models.playbook import Playbook

    pb_result = await db.execute(
        select(Playbook.id, Playbook.updated_at).where(
            Playbook.tenant_id == user.tenant_id,
            Playbook.pattern_id == pattern.id,
        ).limit(1)
    )
    pb_row = pb_result.first()

    resp = PatternResponse.model_validate(pattern)
    if pb_row:
        resp.has_playbook = True
        resp.playbook_id = pb_row[0]
        if (
            pb_row[1]
            and pattern.updated_at
            and (pattern.updated_at - pb_row[1]).total_seconds() > 5
        ):
            resp.playbook_status = "review_needed"
        else:
            resp.playbook_status = "generated"
    else:
        resp.has_playbook = False
        resp.playbook_status = "none"

    return resp


@router.post("/{pattern_id}/approve", response_model=PatternResponse)
async def approve_pattern(pattern_id: UUID, db: DbSession, user: AuthUser):
    """Approve a synthesized knowledge pattern and activate it."""
    user.require_role("domain_admin")
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    pattern = result.scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")

    pattern.active_flag = True
    await db.commit()
    await db.refresh(pattern)

    from contextedge.middleware.audit import log_audit_event

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="pattern.approved",
        resource_type="pattern",
        resource_id=str(pattern_id),
        details={"title": pattern.title},
    )
    return pattern


@router.get("/{pattern_id}/graph")
async def get_pattern_graph(
    pattern_id: UUID,
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(
        None,
        description="Scope edges to a domain (includes domain-less edges)",
    ),
    as_of: datetime | None = Query(None, description="Point-in-time traversal timestamp"),
):
    from contextedge.graph.queries import get_pattern_subgraph
    from contextedge.graph.temporal import normalize_graph_as_of
    result = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Pattern not found")
    return await get_pattern_subgraph(
        db,
        user.tenant_id,
        pattern_id,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )


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


@router.post(
    "/{pattern_id}/evidence-links", response_model=PatternEvidenceLinkResponse, status_code=201
)
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

    from contextedge.ai.extractors.pattern_extractor import synthesize_pattern
    from contextedge.graph.builder import build_episode_graph
    from contextedge.models.episode import Episode
    from contextedge.services.identity_service import identity_ids_from_refs
    from contextedge.services.pattern_service import create_pattern_from_episodes

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

    # Pattern domain is derived from the SET of episode domains, not from
    # episodes[0] (row order must never decide between success and 400):
    # one distinct domain → that domain (tenant-global episodes may join);
    # none → tenant-global pattern; several → actionable 400 before any
    # LLM spend. The service guard re-enforces this as a second layer.
    episode_domains = {ep.domain_id for ep in episodes if ep.domain_id is not None}
    if len(episode_domains) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Selected episodes span multiple domains "
                f"({len(episode_domains)}); a pattern must be built from "
                "one domain (plus tenant-global episodes)."
            ),
        )
    pattern_domain = next(iter(episode_domains), None)

    # 3. Call AI to synthesize pattern
    try:
        synthesis = await synthesize_pattern(ep_data, tenant_id=user.tenant_id, db=db)

        # 4. Create the Pattern in DB
        pattern = await create_pattern_from_episodes(
            db,
            user.tenant_id,
            pattern_domain,
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
        domain_id = pattern_domain
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

    except Exception as exc:
        from contextedge.services.pattern_service import DomainMismatchError

        await db.rollback()
        if isinstance(exc, DomainMismatchError):
            # Second-layer guard fired (should be pre-empted by the set
            # check above) — still an actionable 400, never a 500.
            raise HTTPException(status_code=400, detail=str(exc))
        logger.exception(
            "pattern_discovery_failed",
            tenant_id=str(user.tenant_id),
            episode_ids=[str(episode_id) for episode_id in body.episode_ids],
        )
        raise HTTPException(status_code=500, detail="Pattern synthesis failed")
@router.post("/cluster", status_code=202, response_model=TaskDispatchResponse)
async def trigger_episode_clustering(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None, description="Scope clustering to a specific domain"),
):
    """Trigger background clustering of approved episodes into patterns."""
    user.require_role("domain_admin")

    if domain_id:
        task = cluster_episodes.delay(str(domain_id), str(user.tenant_id))
        return TaskDispatchResponse(
            status="clustering_queued",
            task_id=task.id,
            detail={"domain_id": str(domain_id)},
        )

    # No domain given: one strictly-scoped pass per tenant domain, plus a
    # global pass over tenant-global (NULL-domain) episodes. The previous
    # fallback picked the FIRST domain and clustered tenant-wide under
    # its tag — the exact cross-domain leak the miner no longer permits.
    from contextedge.models.tenant import Domain

    res = await db.execute(
        select(Domain.id)
        .where(Domain.tenant_id == user.tenant_id)
        .order_by(Domain.id)
    )
    domain_ids = list(res.scalars().all())
    dispatched = []
    for tenant_domain_id in domain_ids:
        task = cluster_episodes.delay(str(tenant_domain_id), str(user.tenant_id))
        dispatched.append({"domain_id": str(tenant_domain_id), "task_id": task.id})
    global_task = cluster_episodes.delay(None, str(user.tenant_id))
    dispatched.append({"domain_id": None, "task_id": global_task.id})

    return TaskDispatchResponse(
        status="clustering_queued",
        task_id=global_task.id,
        detail={"passes": dispatched},
    )
