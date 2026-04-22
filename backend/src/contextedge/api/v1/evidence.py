from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.evidence import AttachmentArtifact, EvidenceItem, Thread
from contextedge.schemas.evidence import (
    AttachmentArtifactResponse,
    EvidenceAccessPolicyUpdate,
    EvidenceItemDetail,
    EvidenceItemResponse,
    EvidenceBulkDeleteRequest,
    ThreadResponse,
)
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


@router.patch("/{evidence_id}/relevance")
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

    # 1. Delete Correlation Edges (Handled by DB cascade but good to have)
    await db.execute(
        delete(CorrelationEdge).where(
            or_(
                CorrelationEdge.source_evidence_id.in_(ids),
                CorrelationEdge.target_evidence_id.in_(ids)
            )
        )
    )

    # 2. Delete Case Links (NECESSARY - No DB cascade)
    from contextedge.models.session import CaseLink
    await db.execute(
        delete(CaseLink).where(CaseLink.evidence_id.in_(ids))
    )

    # 3. Delete Pattern Evidence Links (Cleanup)
    from contextedge.models.pattern import PatternEvidenceLink, GraphEdge
    await db.execute(
        delete(PatternEvidenceLink).where(PatternEvidenceLink.evidence_id.in_(ids))
    )

    # 4. Delete Graph Edges (Cleanup)
    await db.execute(
        delete(GraphEdge).where(
            (GraphEdge.source_node_type == "evidence") & (GraphEdge.source_node_id.in_(ids))
        )
    )
    await db.execute(
        delete(GraphEdge).where(
            (GraphEdge.target_node_type == "evidence") & (GraphEdge.target_node_id.in_(ids))
        )
    )

    # 5. Delete Attachment Artifacts
    await db.execute(
        delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(ids))
    )

    # 6. Delete Evidence Items
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

        # 3. Delete Case Links
        from contextedge.models.session import CaseLink
        await db.execute(
            delete(CaseLink).where(CaseLink.tenant_id == user.tenant_id)
        )

        # 4. Delete Pattern Links & Graph Edges
        from contextedge.models.pattern import PatternEvidenceLink, GraphEdge
        await db.execute(
            delete(PatternEvidenceLink).where(PatternEvidenceLink.evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(GraphEdge).where(
                (GraphEdge.tenant_id == user.tenant_id) & 
                ((GraphEdge.source_node_type == "evidence") | (GraphEdge.target_node_type == "evidence"))
            )
        )

        # 5. Delete Attachment Artifacts
        await db.execute(
            delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(evidence_ids))
        )

        # 6. Delete Evidence Items
        await db.execute(
            delete(EvidenceItem).where(EvidenceItem.tenant_id == user.tenant_id)
        )

    # 7. Delete Raw Evidence Objects
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

    from sqlalchemy import delete, or_
    from contextedge.models.session import CaseLink
    from contextedge.models.pattern import PatternEvidenceLink, GraphEdge
    from contextedge.models.episode import CorrelationEdge

    # Manually clean up dependencies not covered by CASCADE
    await db.execute(delete(CaseLink).where(CaseLink.evidence_id == evidence_id))
    await db.execute(delete(PatternEvidenceLink).where(PatternEvidenceLink.evidence_id == evidence_id))
    await db.execute(
        delete(GraphEdge).where(
            ((GraphEdge.source_node_type == "evidence") & (GraphEdge.source_node_id == evidence_id)) |
            ((GraphEdge.target_node_type == "evidence") & (GraphEdge.target_node_id == evidence_id))
        )
    )
    # Ensure correlations are also removed manually as a safety measure
    await db.execute(
        delete(CorrelationEdge).where(
            or_(
                CorrelationEdge.source_evidence_id == evidence_id,
                CorrelationEdge.target_evidence_id == evidence_id
            )
        )
    )

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
