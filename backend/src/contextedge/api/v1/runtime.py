import json
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.evaluation import RetrievalFeedback
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.tenant import Domain
from contextedge.schemas.playbook import (
    FeedbackSubmission,
    PlaybookVersionResponse,
    RuntimeExplainResponse,
    RuntimeMatchRequest,
    RuntimeMatchResponse,
    RuntimeMatchResult,
)
from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.search.risk_policy import risk_within_cap

router = APIRouter()

MATCH_CACHE_TTL_SEC = 3600


def _effective_max_risk_tier(user: AuthUser) -> str | None:
    """Cap playbook risk tier returned at runtime based on caller role."""
    if user.has_role("platform_super_admin") or user.has_role("tenant_admin"):
        return None
    if user.has_role("domain_admin"):
        return None
    if user.has_role("knowledge_manager"):
        return "high"
    if user.principal_type == "service_account":
        return "high"
    return "medium"


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
    query_text = " ".join(body.symptoms + body.entities)
    if body.context:
        query_text += " " + body.context

    if body.domain_id is not None:
        await _assert_domain_in_tenant(db, user.tenant_id, body.domain_id)

    max_risk = _effective_max_risk_tier(user)
    filters_applied: dict = {
        "domain_id": str(body.domain_id) if body.domain_id else None,
        "max_risk_tier": max_risk,
        "risk_cap_source": "caller_role",
    }

    ranked = await rank_playbooks(
        db,
        tenant_id=user.tenant_id,
        query_text=query_text,
        symptoms=body.symptoms,
        entities=body.entities,
        top_k=body.top_k,
        domain_id=body.domain_id,
        max_risk_tier=max_risk,
    )

    match_id = str(uuid.uuid4())
    results = []
    for r in ranked:
        results.append(
            RuntimeMatchResult(
                playbook_id=r.playbook.id,
                playbook_title=r.playbook.title,
                stable_key=r.playbook.stable_key,
                match_score=round(r.score, 4),
                confidence=round(r.confidence, 4),
                freshness_status=r.freshness_status,
                evidence_count=r.evidence_count,
                risk_tier=r.playbook.risk_tier,
                automation_mode=r.playbook.automation_mode,
                scoring_breakdown=r.breakdown,
            )
        )

    fallback = None
    if not results or (results and results[0].confidence < 0.3):
        fallback = "Low confidence match. Consider manual investigation or broadening search terms."

    payload = {
        "tenant_id": str(user.tenant_id),
        "principal": user.principal_type,
        "query_text": query_text,
        "symptoms": body.symptoms,
        "entities": body.entities,
        "environment": body.environment,
        "results": [m.model_dump(mode="json") for m in results],
        "fallback_guidance": fallback,
        "filters_applied": filters_applied,
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

    return RuntimeMatchResponse(
        match_id=match_id,
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

    if playbook.current_version_id:
        ver_result = await db.execute(
            select(PlaybookVersion).where(PlaybookVersion.id == playbook.current_version_id)
        )
        ver = ver_result.scalar_one_or_none()
        if ver:
            return ver

    raise HTTPException(status_code=404, detail="No published version found")


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(body: FeedbackSubmission, db: DbSession, user: AuthUser):
    """Submit structured feedback on a runtime match result."""
    feedback = RetrievalFeedback(
        tenant_id=user.tenant_id,
        match_id=body.match_id,
        playbook_id=body.playbook_id,
        feedback_type=body.feedback_type,
        details=body.details,
        submitted_by=user.user_id,
    )
    db.add(feedback)
    await db.flush()
    return {"status": "feedback_recorded", "id": str(feedback.id)}
