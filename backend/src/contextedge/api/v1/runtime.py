import json
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from contextedge.config import settings
from contextedge.deps import AuthUser, DbSession
from contextedge.models.evaluation import RetrievalFeedback
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.tenant import Domain
from contextedge.schemas.common import MutationAckResponse
from contextedge.schemas.playbook import (
    FeedbackSubmission,
    PlaybookVersionResponse,
    RuntimeExplainResponse,
    RuntimeMatchRequest,
    RuntimeMatchResponse,
    RuntimeMatchResult,
)
from contextedge.schemas.review import RetrievalFeedbackResponse
from contextedge.search.hybrid_ranker import RankedPlaybook, rank_playbooks
from contextedge.search.risk_policy import effective_max_risk_tier, risk_within_cap
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.memory_service import build_runtime_memory_context
from contextedge.services.retrieval_feedback_service import (
    persist_runtime_match,
    record_feedback,
)
from contextedge.services.session_service import append_trace_event

router = APIRouter()

MATCH_CACHE_TTL_SEC = 3600


def _to_match_result(r: RankedPlaybook) -> RuntimeMatchResult:
    return RuntimeMatchResult(
        playbook_id=r.playbook.id,
        playbook_title=r.playbook.title,
        stable_key=r.playbook.stable_key,
        match_score=round(r.score, 4),
        confidence=round(r.confidence, 4),
        retrieval_score=round(r.score, 4),
        playbook_confidence=round(r.playbook_confidence, 4),
        freshness_status=r.freshness_status,
        evidence_count=r.evidence_count,
        risk_tier=r.playbook.risk_tier,
        automation_mode=r.playbook.automation_mode,
        scoring_breakdown=r.breakdown,
        playbook_version_id=r.playbook_version_id,
        semantic_version=r.semantic_version,
        applicability=r.applicability,
        applicability_factors=r.applicability_factors,
        applicability_differences=r.applicability_differences,
        confidence_calibrated=(
            round(r.confidence_calibrated, 4) if r.confidence_calibrated is not None else None
        ),
        selection_margin=r.selection_margin,
    )


def _service_token_domain_allowlist(user: AuthUser) -> list[uuid.UUID] | None:
    """When set, service tokens may only use these domains (plus tenant-wide playbooks).

    ``None`` means no domain allowlist (full tenant). ``[]`` means only resources with no domain.
    """
    if user.principal_type != "service_account":
        return None
    return user.allowed_domain_ids


def _effective_max_risk_tier(user: AuthUser) -> str | None:
    """Cap playbook risk tier returned at runtime based on caller role."""
    return effective_max_risk_tier(user)


async def _resolve_runtime_published_version(
    db: DbSession,
    playbook: Playbook,
) -> PlaybookVersion | None:
    """Resolve a **published** version; ignores unpublished ``current_version_id`` pointers."""
    if playbook.current_version_id:
        cur = await db.get(PlaybookVersion, playbook.current_version_id)
        if cur is not None and cur.published_at is not None:
            return cur
    q = await db.execute(
        select(PlaybookVersion)
        .where(
            PlaybookVersion.playbook_id == playbook.id,
            PlaybookVersion.published_at.is_not(None),
        )
        .order_by(PlaybookVersion.published_at.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()


async def _assert_domain_in_tenant(
    db: DbSession, tenant_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    r = await db.execute(
        select(Domain.id).where(Domain.id == domain_id, Domain.tenant_id == tenant_id)
    )
    if r.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="domain_id does not belong to this tenant",
        )


@router.post("/match", response_model=RuntimeMatchResponse)
async def runtime_match(
    request: Request,
    body: RuntimeMatchRequest,
    db: DbSession,
    user: AuthUser,
):
    """Match case context against approved playbooks with hybrid ranking."""
    if body.domain_id is not None:
        await _assert_domain_in_tenant(db, user.tenant_id, body.domain_id)

    sa_domains = _service_token_domain_allowlist(user)
    if sa_domains is not None and body.domain_id is not None and body.domain_id not in sa_domains:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="domain_id is not allowed for this service token",
        )

    memory_context = await build_runtime_memory_context(
        db,
        tenant_id=user.tenant_id,
        symptoms=body.symptoms,
        entities=body.entities,
        context=body.context,
        session_id=body.session_id,
        domain_id=body.domain_id,
        top_k=body.top_k,
    )
    query_text = memory_context.query_text

    max_risk = _effective_max_risk_tier(user)
    filters_applied: dict = {
        "domain_id": str(body.domain_id) if body.domain_id else None,
        "max_risk_tier": max_risk,
        "risk_cap_source": "caller_role",
        "service_token_domain_allowlist": (
            [str(d) for d in sa_domains] if sa_domains is not None else None
        ),
    }
    filters_applied.update(memory_context.filters_payload())
    if settings.ranking_shadow_mode:
        filters_applied = {
            **filters_applied,
            "shadow_mode": True,
            "shadow_policy": "log_fused_serve_linear",
        }

    ranked = await rank_playbooks(
        db,
        tenant_id=user.tenant_id,
        query_text=query_text,
        entities=body.entities,
        top_k=body.top_k,
        domain_id=body.domain_id,
        max_risk_tier=max_risk,
        allowed_domain_ids=sa_domains,
        caller_roles=user.roles,
        case_frame=getattr(memory_context, "case_frame", None),
        environment=body.environment,
    )

    match_id = str(uuid.uuid4())
    results = [_to_match_result(r) for r in ranked]

    fallback = None
    if not results or (results and results[0].confidence < 0.3):
        fallback = "Low confidence match. Consider manual investigation or broadening search terms."

    if body.session_id is not None:
        trace = await append_trace_event(
            db,
            tenant_id=user.tenant_id,
            session_id=body.session_id,
            event_type="retrieve",
            inputs={
                "query_text": query_text,
                "symptoms": body.symptoms,
                "entities": body.entities,
                "domain_id": str(body.domain_id) if body.domain_id else None,
                "memory_summary": memory_context.filters_payload()["memory_summary"],
            },
            outputs={
                "results": [
                    {
                        "playbook_id": str(r.playbook.id),
                        "stable_key": r.playbook.stable_key,
                        "score": round(r.score, 4),
                        "playbook_confidence": round(r.playbook_confidence, 4),
                    }
                    for r in ranked
                ],
                "reasoning_memory": memory_context.reasoning,
            },
            confidence=round(ranked[0].confidence, 4) if ranked else None,
        )
        if trace is None:
            raise HTTPException(status_code=404, detail="Session not found")

    await append_operational_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.user_id,
        entity_type="runtime_match",
        entity_id=match_id,
        session_id=body.session_id,
        event_type="runtime.match_completed",
        payload={
            "query_text": query_text,
            "domain_id": str(body.domain_id) if body.domain_id else None,
            "symptoms": body.symptoms,
            "entities": body.entities,
            "result_count": len(results),
            "top_result": results[0].model_dump(mode="json") if results else None,
            "fallback_guidance": fallback,
            "filters_applied": filters_applied,
            "memory_summary": memory_context.filters_payload()["memory_summary"],
        },
    )

    payload = {
        "tenant_id": str(user.tenant_id),
        "principal": user.principal_type,
        "session_id": str(body.session_id) if body.session_id else None,
        "query_text": query_text,
        "symptoms": body.symptoms,
        "entities": body.entities,
        "environment": body.environment,
        "results": [m.model_dump(mode="json") for m in results],
        "fallback_guidance": fallback,
        "filters_applied": filters_applied,
        "memory_summary": memory_context.filters_payload()["memory_summary"],
    }
    try:
        redis = request.app.state.redis
        await redis.setex(
            f"runtime:match:{match_id}",
            MATCH_CACHE_TTL_SEC,
            json.dumps(payload),
        )
    except Exception:
        pass

    try:
        await persist_runtime_match(
            db,
            tenant_id=user.tenant_id,
            match_id=match_id,
            query_frame={
                "query_text": query_text,
                "symptoms": body.symptoms,
                "entities": body.entities,
                "environment": body.environment,
                "session_id": str(body.session_id) if body.session_id else None,
            },
            ranked_results=[m.model_dump(mode="json") for m in results],
            filters_applied=filters_applied,
            calibrated_confidence=(
                results[0].confidence_calibrated if results else None
            ),
        )
    except Exception:
        pass

    return RuntimeMatchResponse(
        match_id=match_id,
        session_id=body.session_id,
        results=results,
        fallback_guidance=fallback,
        filters_applied=filters_applied,
    )


@router.get("/explain/{match_id}", response_model=RuntimeExplainResponse)
async def runtime_explain(match_id: str, request: Request, user: AuthUser):
    """Return stored scoring breakdown and query context for a prior `/match` call."""
    raw = await request.app.state.redis.get(f"runtime:match:{match_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Match not found or expired")
    data = json.loads(raw)
    if data.get("tenant_id") != str(user.tenant_id):
        raise HTTPException(status_code=403, detail="Match belongs to another tenant")
    return RuntimeExplainResponse(
        match_id=match_id,
        query_text=data.get("query_text", ""),
        symptoms=data.get("symptoms", []),
        entities=data.get("entities", []),
        environment=data.get("environment") or {},
        ranked_results=data.get("results", []),
        fallback_guidance=data.get("fallback_guidance"),
        filters_applied=data.get("filters_applied") or {},
    )


@router.get("/playbooks/{stable_key}", response_model=PlaybookVersionResponse)
async def get_runtime_playbook(
    stable_key: str,
    db: DbSession,
    user: AuthUser,
    version: str | None = None,
    version_id: uuid.UUID | None = Query(
        None,
        description="When set, return this exact published version (agent pin).",
    ),
    domain_id: uuid.UUID | None = Query(
        None,
        description=(
            "When set, playbook must be tenant-wide (no domain) or bound to this domain; "
            "must belong to the tenant."
        ),
    ),
):
    """Fetch an approved playbook by stable key for runtime consumption.

    Enforces the same role-based risk tier cap as ``POST /runtime/match`` and optional
    domain scope when ``domain_id`` is provided.
    """
    result = await db.execute(
        select(Playbook).where(
            Playbook.stable_key == stable_key,
            Playbook.tenant_id == user.tenant_id,
            Playbook.lifecycle_state == "approved",
        )
    )
    playbook = result.scalar_one_or_none()
    if not playbook:
        raise HTTPException(status_code=404, detail="Approved playbook not found")

    if domain_id is not None:
        await _assert_domain_in_tenant(db, user.tenant_id, domain_id)
        if playbook.domain_id is not None and playbook.domain_id != domain_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Playbook is not in the requested domain scope",
            )

    max_risk = _effective_max_risk_tier(user)
    if not risk_within_cap(playbook.risk_tier, max_risk):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller is not allowed to retrieve playbooks at this risk tier",
        )

    sa_domains = _service_token_domain_allowlist(user)
    if sa_domains is not None:
        if playbook.domain_id is not None and playbook.domain_id not in sa_domains:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Playbook is outside the domain scope for this service token",
            )
        if domain_id is not None and domain_id not in sa_domains:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="domain_id is not allowed for this service token",
            )

    ver: PlaybookVersion | None = None
    if version_id is not None:
        vq = await db.execute(
            select(PlaybookVersion).where(
                PlaybookVersion.id == version_id,
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.published_at.is_not(None),
            )
        )
        ver = vq.scalar_one_or_none()
        if not ver:
            raise HTTPException(status_code=404, detail="Published playbook version not found")
    elif version:
        vq = await db.execute(
            select(PlaybookVersion)
            .where(
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.semantic_version == version,
                PlaybookVersion.published_at.is_not(None),
            )
            .order_by(PlaybookVersion.published_at.desc(), PlaybookVersion.created_at.desc())
            .limit(1)
        )
        ver = vq.scalars().first()
        if not ver:
            raise HTTPException(status_code=404, detail="Published playbook version not found")
    else:
        ver = await _resolve_runtime_published_version(db, playbook)

    if ver:
        return ver

    raise HTTPException(status_code=404, detail="No published version found")


@router.post(
    "/feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=MutationAckResponse,
)
async def submit_feedback(body: FeedbackSubmission, db: DbSession, user: AuthUser):
    """Submit structured feedback on a runtime match result."""
    feedback = await record_feedback(
        db,
        tenant_id=user.tenant_id,
        match_id=body.match_id,
        playbook_id=body.playbook_id,
        playbook_version_id=body.playbook_version_id,
        feedback_type=body.feedback_type,
        details=body.details,
        submitted_by=user.user_id,
    )
    return MutationAckResponse(status="feedback_recorded", id=str(feedback.id))


@router.get("/feedback", response_model=list[RetrievalFeedbackResponse])
async def list_feedback(
    db: DbSession,
    user: AuthUser,
    playbook_id: uuid.UUID | None = None,
    feedback_type: str | None = None,
    match_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = select(RetrievalFeedback).where(RetrievalFeedback.tenant_id == user.tenant_id)
    if playbook_id is not None:
        stmt = stmt.where(RetrievalFeedback.playbook_id == playbook_id)
    if feedback_type is not None:
        stmt = stmt.where(RetrievalFeedback.feedback_type == feedback_type)
    if match_id is not None:
        stmt = stmt.where(RetrievalFeedback.match_id == match_id)
    stmt = stmt.order_by(RetrievalFeedback.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()
