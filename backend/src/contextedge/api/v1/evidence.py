from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.evidence import AttachmentArtifact, EvidenceItem, Thread
from contextedge.schemas.common import StatusResponse
from contextedge.schemas.evidence import (
    AttachmentArtifactResponse,
    EvidenceAccessPolicyUpdate,
    EvidenceContextResponse,
    EvidenceItemDetail,
    EvidenceItemResponse,
    EvidenceBulkDeleteRequest,
    ThreadResponse,
)
from contextedge.models.source import Source
from contextedge.services.policy_assignment import assert_policy_assignment
from contextedge.search.access_control import resolve_excluded_access_policy_ids

router = APIRouter()


@router.get("", response_model=list[EvidenceItemResponse])
async def search_evidence(
    db: DbSession,
    user: AuthUser,
    query: str | None = None,
    source_id: UUID | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)

    if query and query.strip():
        from contextedge.search.pg_fts import search_evidence_fts

        fts_results = await search_evidence_fts(
            db,
            user.tenant_id,
            query.strip(),
            limit=limit,
            exclude_policy_ids=excluded_policy_ids,
        )
        return [item for item, _rank in fts_results]

    q = select(EvidenceItem).where(EvidenceItem.tenant_id == user.tenant_id)
    if excluded_policy_ids:
        q = q.where(
            or_(
                EvidenceItem.access_policy_id.is_(None),
                EvidenceItem.access_policy_id.notin_(excluded_policy_ids),
            )
        )
    if source_id:
        q = q.where(EvidenceItem.source_id == source_id)
    if relevance_state:
        q = q.where(EvidenceItem.relevance_state == relevance_state)
    if evidence_type:
        q = q.where(EvidenceItem.evidence_type == evidence_type)
    if domain_id:
        q = q.where(EvidenceItem.domain_id == domain_id)
    q = q.order_by(EvidenceItem.ingested_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{evidence_id}", response_model=EvidenceItemDetail)
async def get_evidence(evidence_id: UUID, db: DbSession, user: AuthUser):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item or (
        excluded_policy_ids and item.access_policy_id in set(excluded_policy_ids)
    ):
        raise HTTPException(status_code=404, detail="Evidence not found")
    return item


@router.get("/{evidence_id}/attachments", response_model=list[AttachmentArtifactResponse])
async def list_evidence_attachments(evidence_id: UUID, db: DbSession, user: AuthUser):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item or (
        excluded_policy_ids and item.access_policy_id in set(excluded_policy_ids)
    ):
        raise HTTPException(status_code=404, detail="Evidence not found")

    attachments = await db.execute(
        select(AttachmentArtifact)
        .where(AttachmentArtifact.evidence_id == evidence_id)
        .order_by(AttachmentArtifact.created_at.asc(), AttachmentArtifact.filename.asc())
    )
    return attachments.scalars().all()


@router.patch(
    "/{evidence_id}/access-policy",
    response_model=EvidenceItemDetail,
)
async def update_access_policy(
    evidence_id: UUID,
    body: EvidenceAccessPolicyUpdate,
    db: DbSession,
    user: AuthUser,
):
    if not (
        user.has_role("domain_admin")
        or user.has_role("knowledge_manager")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Domain admin or knowledge manager role required",
        )
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")
    await assert_policy_assignment(
        db, user.tenant_id, body.access_policy_id, "access"
    )
    item.access_policy_id = body.access_policy_id
    await db.flush()
    await db.refresh(item)
    return item


@router.patch("/{evidence_id}/relevance", response_model=StatusResponse)
async def update_relevance(
    evidence_id: UUID,
    relevance_state: str,
    db: DbSession,
    user: AuthUser,
):
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")
    item.relevance_state = relevance_state
    await db.flush()
    return {"status": "updated"}


@router.post("/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_delete_evidence(
    body: EvidenceBulkDeleteRequest,
    db: DbSession,
    user: AuthUser,
):
    """Permanently delete multiple evidence items."""
    user.require_role("domain_admin")

    from sqlalchemy import delete, or_
    from contextedge.models.evidence import AttachmentArtifact, RawEvidenceObject
    from contextedge.models.episode import CorrelationEdge

    ids = body.ids
    if not ids:
        return None

    # 1. Delete Correlation Edges
    await db.execute(
        delete(CorrelationEdge).where(
            or_(
                CorrelationEdge.source_evidence_id.in_(ids),
                CorrelationEdge.target_evidence_id.in_(ids)
            )
        )
    )

    # 2. Delete Attachment Artifacts
    await db.execute(
        delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(ids))
    )

    # 3. Delete Evidence Items
    await db.execute(
        delete(EvidenceItem).where(
            EvidenceItem.id.in_(ids),
            EvidenceItem.tenant_id == user.tenant_id
        )
    )

    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="evidence.bulk_deleted",
        resource_type="evidence",
        resource_id="multiple",
        details={"count": len(ids)},
    )
    return None


@router.delete("/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_evidence(db: DbSession, user: AuthUser):
    """Permanently delete ALL evidence records for the current tenant."""
    user.require_role("domain_admin")

    from sqlalchemy import delete, or_
    from contextedge.models.evidence import RawEvidenceObject, AttachmentArtifact
    from contextedge.models.episode import CorrelationEdge

    # 1. Resolve Evidence IDs to delete dependencies
    evidence_ids_q = await db.execute(
        select(EvidenceItem.id).where(EvidenceItem.tenant_id == user.tenant_id)
    )
    evidence_ids = evidence_ids_q.scalars().all()

    if evidence_ids:
        # 2. Delete Correlation Edges
        await db.execute(
            delete(CorrelationEdge).where(
                or_(
                    CorrelationEdge.source_evidence_id.in_(evidence_ids),
                    CorrelationEdge.target_evidence_id.in_(evidence_ids)
                )
            )
        )

        # 3. Delete Attachment Artifacts
        await db.execute(
            delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(evidence_ids))
        )

        # 4. Delete Evidence Items
        await db.execute(
            delete(EvidenceItem).where(EvidenceItem.tenant_id == user.tenant_id)
        )

    # 5. Delete Raw Evidence Objects
    await db.execute(
        delete(RawEvidenceObject).where(RawEvidenceObject.tenant_id == user.tenant_id)
    )

    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="evidence.purged",
        resource_type="evidence",
        resource_id="all",
        details={"message": "Bulk purge of all evidence items and raw objects"},
    )
    return None


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evidence(evidence_id: UUID, db: DbSession, user: AuthUser):
    """Permanently delete an evidence item."""
    user.require_role("domain_admin")

    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")

    await db.delete(item)
    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="evidence.deleted",
        resource_type="evidence",
        resource_id=str(evidence_id),
        details={"title": item.title},
    )
    return None


@router.get("/{evidence_id}/context", response_model=EvidenceContextResponse)
async def get_evidence_context(evidence_id: UUID, db: DbSession, user: AuthUser):
    """Retrieve resolved source name, domain name, and linked Episode/Pattern knowledge graph context."""
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Evidence not found")

    # 1. Resolve Source Name
    source_name = None
    if item.source_id:
        s_res = await db.execute(select(Source).where(Source.id == item.source_id))
        s_obj = s_res.scalar_one_or_none()
        if s_obj:
            source_name = s_obj.name

    # 2. Resolve Domain Name
    domain_name = None
    if item.domain_id:
        from contextedge.models.tenant import Domain
        d_res = await db.execute(select(Domain).where(Domain.id == item.domain_id))
        d_obj = d_res.scalar_one_or_none()
        if d_obj:
            domain_name = d_obj.name

    # 3. Resolve Linked Episode, Pattern, and Playbook records
    from contextedge.models.pattern import PatternEvidenceLink, Pattern, GraphEdge
    from contextedge.models.episode import Episode
    from contextedge.models.playbook import Playbook

    episodes = []
    patterns = []
    playbooks = []
    seen_episodes: set[UUID] = set()
    seen_patterns: set[UUID] = set()
    seen_playbooks: set[UUID] = set()

    # A. Check PatternEvidenceLink
    pel_q = await db.execute(
        select(PatternEvidenceLink).where(PatternEvidenceLink.evidence_id == evidence_id)
    )
    pel_links = pel_q.scalars().all()
    for link in pel_links:
        if link.episode_id and link.episode_id not in seen_episodes:
            seen_episodes.add(link.episode_id)
            ep_res = await db.execute(select(Episode).where(Episode.id == link.episode_id))
            ep = ep_res.scalar_one_or_none()
            if ep:
                episodes.append({
                    "id": str(ep.id),
                    "title": ep.title,
                    "case_ref": ep.primary_case_ref,
                    "status": ep.status,
                    "created_at": ep.created_at.isoformat() if ep.created_at else None,
                })

        if link.pattern_id and link.pattern_id not in seen_patterns:
            seen_patterns.add(link.pattern_id)
            pat_res = await db.execute(select(Pattern).where(Pattern.id == link.pattern_id))
            pat = pat_res.scalar_one_or_none()
            if pat:
                patterns.append({
                    "id": str(pat.id),
                    "title": pat.title,
                    "confidence": pat.confidence,
                })
                # Playbook linked to pattern
                pb_res = await db.execute(select(Playbook).where(Playbook.pattern_id == pat.id))
                pb = pb_res.scalar_one_or_none()
                if pb and pb.id not in seen_playbooks:
                    seen_playbooks.add(pb.id)
                    playbooks.append({
                        "id": str(pb.id),
                        "title": pb.title,
                        "risk_tier": pb.risk_tier,
                    })

    # B. Check GraphEdge links
    ge_q = await db.execute(
        select(GraphEdge).where(
            GraphEdge.tenant_id == user.tenant_id,
            GraphEdge.target_node_type == "evidence",
            GraphEdge.target_node_id == evidence_id,
        )
    )
    for ge in ge_q.scalars().all():
        if ge.source_node_type == "episode" and ge.source_node_id not in seen_episodes:
            seen_episodes.add(ge.source_node_id)
            ep_res = await db.execute(select(Episode).where(Episode.id == ge.source_node_id))
            ep = ep_res.scalar_one_or_none()
            if ep:
                episodes.append({
                    "id": str(ep.id),
                    "title": ep.title,
                    "case_ref": ep.primary_case_ref,
                    "status": ep.status,
                    "created_at": ep.created_at.isoformat() if ep.created_at else None,
                })

    return {
        "source_name": source_name,
        "domain_name": domain_name,
        "episodes": episodes,
        "patterns": patterns,
        "playbooks": playbooks,
    }
