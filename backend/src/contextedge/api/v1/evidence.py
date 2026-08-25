from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select, text

from contextedge.deps import AuthUser, DbSession
from contextedge.middleware.audit import log_audit_event
from contextedge.models.evidence import (
    AttachmentArtifact,
    EvidenceItem,
    RawEvidenceObject,
)
from contextedge.models.source import Source
from contextedge.schemas.common import StatusResponse
from contextedge.schemas.evidence import (
    AttachmentArtifactResponse,
    EvidenceAccessPolicyUpdate,
    EvidenceBulkDeleteRequest,
    EvidenceContextResponse,
    EvidenceItemDetail,
    EvidenceItemResponse,
)
from contextedge.search.access_control import resolve_excluded_access_policy_ids
from contextedge.services.policy_assignment import assert_policy_assignment

router = APIRouter()


@router.get("/zoho-ticket/{ticket_id}/live-context")
async def get_live_zoho_ticket_context(
    ticket_id: str,
    db: DbSession,
    user: AuthUser,
):
    """Read one exact Zoho ticket and its conversation using tenant credentials."""
    user.require_role("domain_admin")
    if not ticket_id.isdigit() or len(ticket_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid Zoho ticket id")

    source_result = await db.execute(
        select(Source)
        .where(
            Source.tenant_id == user.tenant_id,
            Source.source_type == "zoho_desk",
            Source.is_active.is_(True),
        )
        .order_by(Source.created_at.desc())
        .limit(1)
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Zoho Desk source is not configured")

    from contextedge.models.source import SourceCredential
    from contextedge.connectors.registry import get_connector
    from contextedge.services.source_service import decrypt_credentials

    credential_result = await db.execute(
        select(SourceCredential).where(
            SourceCredential.source_id == source.id,
            SourceCredential.status == "active",
        )
    )
    credential = credential_result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(status_code=409, detail="Zoho Desk credentials are unavailable")

    try:
        decrypted = await decrypt_credentials(credential.encrypted_credentials)
    except Exception as exc:
        from cryptography.fernet import InvalidToken

        if isinstance(exc, InvalidToken):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Zoho Desk credentials cannot be decrypted with the current "
                    "ENCRYPTION_KEY; rotate or re-save the source credentials."
                ),
            ) from exc
        raise
    connector = get_connector(source.source_type, source.config, decrypted)
    fetch = getattr(connector, "fetch_ticket_context", None)
    if fetch is None:
        raise HTTPException(status_code=501, detail="Live ticket retrieval is unsupported")
    try:
        return await fetch(ticket_id)
    except HTTPException:
        raise
    except Exception as exc:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Zoho ticket not found") from exc
        raise HTTPException(
            status_code=502,
            detail=f"Zoho live ticket retrieval failed ({type(exc).__name__})",
        ) from exc


@router.get("", response_model=list[EvidenceItemResponse])
async def search_evidence(
    db: DbSession,
    user: AuthUser,
    query: str | None = None,
    external_id: str | None = Query(None, max_length=500),
    source_id: UUID | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    source_type: str | None = None,
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    excluded_policy_ids = await resolve_excluded_access_policy_ids(db, user.tenant_id, user.roles)

    if query and query.strip() and not external_id:
        from contextedge.search.pg_fts import search_evidence_fts

        fts_results = await search_evidence_fts(
            db,
            user.tenant_id,
            query.strip(),
            limit=limit,
            exclude_policy_ids=excluded_policy_ids,
            relevance_state=relevance_state,
            evidence_type=evidence_type,
            source_type=source_type,
        )
        return await _attach_source_references(
            db, [item for item, _rank in fts_results]
        )

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
    if external_id:
        q = q.join(
            RawEvidenceObject,
            EvidenceItem.raw_object_ref == RawEvidenceObject.id,
        ).where(
            RawEvidenceObject.tenant_id == user.tenant_id,
            RawEvidenceObject.external_id == external_id.strip(),
        )
    if relevance_state:
        q = q.where(EvidenceItem.relevance_state == relevance_state)
    if evidence_type:
        q = q.where(EvidenceItem.evidence_type == evidence_type)
    else:
        # Hide hydrated thread messages from the default list view.
        # They are individual replies/comments that belong under the parent
        # ticket's ThreadConversation section (served via
        # GET /threads/{thread_id}/evidence), not as top-level items.
        # Callers can still fetch them by passing evidence_type=thread_message.
        q = q.where(EvidenceItem.evidence_type != "thread_message")
    if source_type:
        q = q.where(EvidenceItem.source_type == source_type)
    if domain_id:
        q = q.where(EvidenceItem.domain_id == domain_id)
    q = (
        q.order_by(
            EvidenceItem.created_at_source.desc().nullslast(),
            EvidenceItem.ingested_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(q)
    return await _attach_source_references(db, list(result.scalars().all()))


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

    detail = EvidenceItemDetail.model_validate(item, from_attributes=True)
    detail.source_reference = await _source_reference(db, item)
    return detail


async def _attach_source_references(db, items: list[EvidenceItem]) -> list:
    """Add the source record number to a page of evidence.

    ONE query for the page, not one per row: the list is capped at 200
    and an N+1 here would be 200 round trips to render a table.

    Only the handful of payload keys that can hold a record number are
    pulled out, in SQL. Selecting whole raw payloads would drag every
    ticket body and thread through the API process to render a column
    two dozen characters wide.
    """
    from contextedge.schemas.evidence import SourceReference

    # The reference is ATTACHED to each row rather than the rows being
    # converted here. FastAPI validates against the response model at the
    # boundary anyway, so converting first would validate every row
    # twice — 200 rows of wasted work per page — and would replace the
    # objects the caller returned with copies.
    raw_refs = {
        ref
        for ref in (getattr(item, "raw_object_ref", None) for item in items)
        if ref
    }
    if not raw_refs:
        return items

    rows = (
        await db.execute(
            text(
                """
                WITH target_objects AS (
                    SELECT id, external_id, raw_payload
                    FROM raw_evidence_objects
                    WHERE id = ANY(:ids)
                )
                SELECT
                    t.id,
                    t.external_id,
                    COALESCE(
                        NULLIF(t.raw_payload->>'ticket_number', ''),
                        NULLIF(t.raw_payload->>'ticketNumber', ''),
                        NULLIF(t.raw_payload->>'number', ''),
                        NULLIF(t.raw_payload->>'display_id', ''),
                        NULLIF(t.raw_payload->>'record_number', ''),
                        NULLIF(t.raw_payload->>'key', ''),
                        NULLIF(t.raw_payload->>'incident_number', ''),
                        NULLIF(t.raw_payload->>'caseNumber', ''),
                        NULLIF(t.raw_payload->>'case_number', ''),
                        NULLIF(p.raw_payload->>'ticket_number', ''),
                        NULLIF(p.raw_payload->>'ticketNumber', ''),
                        NULLIF(p.raw_payload->>'number', '')
                    ) AS display_id,
                    COALESCE(
                        t.raw_payload->>'web_url',
                        t.raw_payload->>'webUrl',
                        t.raw_payload->>'url',
                        t.raw_payload->>'permalink',
                        t.raw_payload->>'link',
                        t.raw_payload->>'portal_url',
                        p.raw_payload->>'web_url',
                        p.raw_payload->>'webUrl',
                        p.raw_payload->>'url'
                    ) AS url
                FROM target_objects t
                LEFT JOIN raw_evidence_objects p ON (
                    t.external_id LIKE 'zoho_ticket:%:msg:%'
                    AND p.external_id = split_part(t.external_id, ':', 2)
                )
                """
            ),
            {"ids": list(raw_refs)},
        )
    ).all()
    by_raw_id = {row.id: row for row in rows}

    for item in items:
        row = by_raw_id.get(getattr(item, "raw_object_ref", None))
        if row is None:
            continue
        url = row.url if (row.url or "").startswith(("http://", "https://")) else None
        source_type = getattr(item, "source_type", None)
        if not url and source_type == "zoho_desk" and row.external_id:
            raw_ext = str(row.external_id)
            clean_id = raw_ext.split(":")[1] if raw_ext.startswith("zoho_ticket:") else raw_ext
            url = f"https://support.automationedge.com/support/automationedge/ShowHomePage.do#Cases/dv/{clean_id}"
        item.source_reference = SourceReference(
            external_id=row.external_id,
            display_id=row.display_id or row.external_id,
            url=url,
            source_type=source_type,
        )
    return items


async def _source_reference(db, item: EvidenceItem):
    """Which ticket this evidence is, and where to open it.

    Read from the raw object rather than duplicated onto the evidence
    row: the raw payload is the record as the source system had it, and
    copying a number out of it at ingest would go stale the moment a
    connector changed which field it wrote.

    Returns ``None`` for evidence with no raw object (uploads), rather
    than an empty shell that renders as a blank field.
    """
    from contextedge.schemas.evidence import SourceReference
    from contextedge.services.evidence_typing import source_reference_from_payload

    if item.raw_object_ref is None:
        return None
    raw = await db.get(RawEvidenceObject, item.raw_object_ref)
    if raw is None:
        return None
    return SourceReference(
        **source_reference_from_payload(
            raw.raw_payload, raw.external_id, item.source_type
        )
    )


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
    """Permanently delete multiple evidence items.

    Authorization happens on the RESOLVED set, not the request: dependency
    deletion used to run against caller-supplied UUIDs before any tenant
    check, so a caller could delete another tenant's correlation edges and
    attachments by guessing (or leaking) evidence ids. Every id must resolve
    inside the caller's tenant before anything is touched, and evidence under
    legal hold refuses deletion outright — a hold that can be cleared by the
    delete button is not a hold.
    """
    user.require_role("domain_admin")

    from sqlalchemy import delete, or_

    from contextedge.models.episode import CorrelationEdge
    from contextedge.models.evidence import AttachmentArtifact

    ids = body.ids
    if not ids:
        return None

    # 1. Resolve and authorize FIRST. The delete targets are the ids that
    # exist inside this tenant — nothing else is ever passed to a delete.
    resolved = (
        await db.execute(
            select(EvidenceItem.id, EvidenceItem.sensitivity_label).where(
                EvidenceItem.id.in_(ids),
                EvidenceItem.tenant_id == user.tenant_id,
            )
        )
    ).all()
    if len(resolved) != len(set(ids)):
        # At least one id is unknown or belongs to another tenant. Refuse the
        # whole request rather than partially applying it: a bulk delete that
        # silently skips some ids reads as success and leaves the caller
        # believing gone things still exist — and the 404 does not disclose
        # which ids were foreign.
        raise HTTPException(status_code=404, detail="One or more evidence items not found")

    held = [str(eid) for eid, label in resolved if label == "legal_hold"]
    if held:
        raise HTTPException(
            status_code=409,
            detail=f"{len(held)} item(s) are under legal hold and cannot be deleted",
        )
    delete_ids = [eid for eid, _ in resolved]

    # 2. Delete Correlation Edges (authorized set only)
    await db.execute(
        delete(CorrelationEdge).where(
            or_(
                CorrelationEdge.source_evidence_id.in_(delete_ids),
                CorrelationEdge.target_evidence_id.in_(delete_ids)
            )
        )
    )

    # 3. Delete Attachment Artifacts (authorized set only)
    await db.execute(
        delete(AttachmentArtifact).where(AttachmentArtifact.evidence_id.in_(delete_ids))
    )

    # 4. Delete Evidence Items
    await db.execute(
        delete(EvidenceItem).where(
            EvidenceItem.id.in_(delete_ids),
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

    from contextedge.models.episode import CorrelationEdge
    from contextedge.models.evidence import AttachmentArtifact, RawEvidenceObject

    # 1. Resolve Evidence IDs to delete dependencies — EXCLUDING legal hold.
    # A purge that clears held evidence defeats the point of a hold; the held
    # rows (and their raw objects, attachments, and edges) survive the purge
    # and the audit event records how many were preserved.
    from contextedge.services.evidence_filters import exclude_legal_hold

    evidence_ids_q = await db.execute(
        select(EvidenceItem.id).where(
            EvidenceItem.tenant_id == user.tenant_id,
            exclude_legal_hold(),
        )
    )
    evidence_ids = evidence_ids_q.scalars().all()

    held_q = await db.execute(
        select(EvidenceItem.id, EvidenceItem.raw_object_ref).where(
            EvidenceItem.tenant_id == user.tenant_id,
            EvidenceItem.sensitivity_label == "legal_hold",
        )
    )
    held_rows = held_q.all()
    held_count = len(held_rows)
    held_raw_refs = [ref for _, ref in held_rows if ref is not None]

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

        # 4. Delete Evidence Items (held rows excluded by the same predicate)
        await db.execute(
            delete(EvidenceItem).where(
                EvidenceItem.tenant_id == user.tenant_id,
                exclude_legal_hold(),
            )
        )

    # 5. Delete Raw Evidence Objects — except those backing held evidence,
    # which must stay recoverable for as long as the hold stands.
    raw_delete = delete(RawEvidenceObject).where(
        RawEvidenceObject.tenant_id == user.tenant_id
    )
    if held_raw_refs:
        raw_delete = raw_delete.where(RawEvidenceObject.id.not_in(held_raw_refs))
    await db.execute(raw_delete)

    await db.commit()

    await log_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action="evidence.purged",
        resource_type="evidence",
        resource_id="all",
        details={
            "message": "Bulk purge of all evidence items and raw objects",
            "legal_hold_preserved": held_count,
        },
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
    if item.sensitivity_label == "legal_hold":
        raise HTTPException(
            status_code=409,
            detail="Evidence is under legal hold and cannot be deleted",
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


@router.get("/{evidence_id}/context", response_model=EvidenceContextResponse)
async def get_evidence_context(evidence_id: UUID, db: DbSession, user: AuthUser):
    """Retrieve resolved source name, domain name, and linked Episode/Pattern
    knowledge graph context."""
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
        s_res = await db.execute(
            select(Source).where(
                Source.id == item.source_id,
                Source.tenant_id == user.tenant_id,
            )
        )
        s_obj = s_res.scalar_one_or_none()
        if s_obj:
            source_name = s_obj.display_name

    # 2. Resolve Domain Name
    domain_name = None
    if item.domain_id:
        from contextedge.models.tenant import Domain
        d_res = await db.execute(
            select(Domain).where(
                Domain.id == item.domain_id,
                Domain.tenant_id == user.tenant_id,
            )
        )
        d_obj = d_res.scalar_one_or_none()
        if d_obj:
            domain_name = d_obj.name

    # 3. Resolve Linked Episode, Pattern, and Playbook records
    from contextedge.models.episode import Episode
    from contextedge.models.pattern import GraphEdge, Pattern, PatternEvidenceLink
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
            ep_res = await db.execute(
                select(Episode).where(
                    Episode.id == link.episode_id,
                    Episode.tenant_id == user.tenant_id,
                )
            )
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
            pat_res = await db.execute(
                select(Pattern).where(
                    Pattern.id == link.pattern_id,
                    Pattern.tenant_id == user.tenant_id,
                )
            )
            pat = pat_res.scalar_one_or_none()
            if pat:
                patterns.append({
                    "id": str(pat.id),
                    "title": pat.title,
                    "confidence": pat.confidence,
                })
                pb_res = await db.execute(
                    select(Playbook).where(
                        Playbook.pattern_id == pat.id,
                        Playbook.tenant_id == user.tenant_id,
                    )
                )
                for pb in pb_res.scalars().all():
                    if pb.id not in seen_playbooks:
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
            ep_res = await db.execute(
                select(Episode).where(
                    Episode.id == ge.source_node_id,
                    Episode.tenant_id == user.tenant_id,
                )
            )
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
