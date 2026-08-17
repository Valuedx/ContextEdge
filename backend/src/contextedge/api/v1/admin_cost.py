"""Admin LLM-cost observability + budget endpoints.

Gated to ``tenant_admin`` / ``platform_super_admin`` roles — cost data
shouldn't be visible to every reviewer. Reads from the
``operational_events`` rows written by ``ai.observability.record_llm_usage``.
The budget endpoints (W7-9.1) let operators configure and inspect the
daily token / cost cap per tenant.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

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
    all_time: bool = Query(False, description="Ignore the window: everything to date."),
    sync_run_id: UUID | None = Query(
        None, description="Scope to one sync run's own start/end window."
    ),
):
    """Per-tenant LLM usage + cost aggregation.

    Returns headline totals (requests, tokens split by prompt/completion/
    cached, estimated USD, cache hit rate) plus a top-N breakdown by
    (model, task). Intended for the admin cost dashboard.
    """
    user.require_role("tenant_admin")
    since = until = None
    if sync_run_id is not None:
        # "This sync": bounded by the run's own start and end, so the number
        # answers "what did THAT cost" rather than "what happened in a window
        # that happens to contain it". A run still going has no end yet, which
        # is what makes the meter live.
        from contextedge.models.source import SyncRun

        run = await db.get(SyncRun, sync_run_id)
        if run is None or run.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="Sync run not found")
        since = run.started_at or run.created_at
        until = run.completed_at
    elif all_time:
        # The rolling window caps at 30 days; "overall to date" is a
        # different question and needs no lower bound at all.
        since = datetime(1970, 1, 1, tzinfo=UTC)
    return await get_llm_usage(
        db,
        tenant_id=user.tenant_id,
        window_hours=window_hours,
        top_n_breakdown=top_n_breakdown,
        since=since,
        until=until,
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
    await db.refresh(budget)
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


@router.get("/pipeline-health")
async def admin_pipeline_health(db: DbSession, user: AuthUser):
    """Queue depths, throughput, latency and the graph chain, in one read.

    Separate from `/llm-usage` because the question is different: that one
    asks what the run cost, this one asks whether it is getting anywhere.
    A run can be spending steadily and producing nothing — that is exactly
    the failure this exists to make visible.
    """
    user.require_role("tenant_admin")
    from contextedge.services.pipeline_health_service import get_pipeline_health

    return await get_pipeline_health(db, user.tenant_id)
