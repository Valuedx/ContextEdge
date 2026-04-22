"""Admin LLM-cost observability endpoint.

Gated to ``tenant_admin`` / ``platform_super_admin`` roles — cost data
shouldn't be visible to every reviewer. Reads from the
``operational_events`` rows written by ``ai.observability.record_llm_usage``.
"""

from fastapi import APIRouter, Query

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.admin_cost import LlmUsageResponse
from contextedge.services.admin_cost_service import get_llm_usage

router = APIRouter()


@router.get("/llm-usage", response_model=LlmUsageResponse)
async def admin_llm_usage(
    db: DbSession,
    user: AuthUser,
    window_hours: int = Query(
        24,
        ge=1,
        le=720,
        description="Aggregation window in hours (max 30 days).",
    ),
    top_n_breakdown: int = Query(
        10,
        ge=1,
        le=50,
        description="How many (model, task) rows to return, ranked by cost.",
    ),
):
    """Per-tenant LLM usage + cost aggregation.

    Returns headline totals (requests, tokens split by prompt/completion/
    cached, estimated USD, cache hit rate) plus a top-N breakdown by
    (model, task). Intended for the admin cost dashboard.
    """
    user.require_role("tenant_admin")
    return await get_llm_usage(
        db,
        tenant_id=user.tenant_id,
        window_hours=window_hours,
        top_n_breakdown=top_n_breakdown,
    )
