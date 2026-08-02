"""Admin LLM-cost observability + budget endpoints.

Gated to ``tenant_admin`` / ``platform_super_admin`` roles — cost data
shouldn't be visible to every reviewer. Reads from the
``operational_events`` rows written by ``ai.observability.record_llm_usage``.
The budget endpoints (W7-9.1) let operators configure and inspect the
daily token / cost cap per tenant.
"""

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.admin_cost import (
    LlmUsageResponse,
    TenantBudgetResponse,
    TenantBudgetStatus,
    TenantBudgetUpsert,
)
from contextedge.services.admin_cost_service import get_llm_usage
from contextedge.services.tenant_budget_service import (
    check_budget,
    get_budget,
    upsert_budget,
)

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


def _serialise_budget(budget) -> TenantBudgetResponse:
    return TenantBudgetResponse(
        tenant_id=str(budget.tenant_id),
        daily_token_limit=budget.daily_token_limit,
        daily_cost_cap_usd=(
            float(budget.daily_cost_cap_usd)
            if budget.daily_cost_cap_usd is not None
            else None
        ),
        action_on_exceed=budget.action_on_exceed,
        updated_at=budget.updated_at,
    )


@router.get("/tenant-budget", response_model=TenantBudgetResponse | None)
async def get_tenant_budget(db: DbSession, user: AuthUser):
    """Return the caller's tenant's LLM budget, or ``null`` if none
    is configured (= uncapped)."""
    user.require_role("tenant_admin")
    budget = await get_budget(db, user.tenant_id)
    if budget is None:
        return None
    return _serialise_budget(budget)


@router.put("/tenant-budget", response_model=TenantBudgetResponse)
async def put_tenant_budget(
    body: TenantBudgetUpsert, db: DbSession, user: AuthUser,
):
    """Create or replace the caller's tenant's LLM budget."""
    user.require_role("tenant_admin")
    try:
        budget = await upsert_budget(
            db,
            tenant_id=user.tenant_id,
            daily_token_limit=body.daily_token_limit,
            daily_cost_cap_usd=(
                Decimal(str(body.daily_cost_cap_usd))
                if body.daily_cost_cap_usd is not None
                else None
            ),
            action_on_exceed=body.action_on_exceed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialise_budget(budget)


@router.get("/tenant-budget/status", response_model=TenantBudgetStatus)
async def get_tenant_budget_status(db: DbSession, user: AuthUser):
    """Live view: budget config + current-day usage + whether the next
    LLM call would be allowed. Powers the dashboard header — no extra
    round trip to compose cap + usage."""
    user.require_role("tenant_admin")
    budget = await get_budget(db, user.tenant_id)
    result = await check_budget(db, user.tenant_id, use_cache=False)
    # `result` already reflects whichever caps were enforced — the tenant's own
    # row when it has one, otherwise the deployment defaults — so the effective
    # limits come straight off it rather than being recomputed here.
    if budget is not None:
        limit_source = "tenant"
    elif result.token_limit is not None or result.cost_cap_usd is not None:
        limit_source = "default"
    else:
        limit_source = "none"
    return TenantBudgetStatus(
        budget=_serialise_budget(budget) if budget is not None else None,
        current_tokens=result.current_tokens,
        current_cost_usd=result.current_cost_usd,
        allowed=result.allowed,
        reason=result.reason,
        effective_token_limit=result.token_limit,
        effective_cost_cap_usd=result.cost_cap_usd,
        limit_source=limit_source,
    )
