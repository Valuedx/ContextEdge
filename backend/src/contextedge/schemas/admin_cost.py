"""Response shapes for the admin LLM-cost dashboard (`GET /admin/llm-usage`)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LlmUsageTotals(BaseModel):
    """Headline KPI card data."""

    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    estimated_cost_usd: float = Field(
        ...,
        description=(
            "Rough estimate derived from the tenant's model-cost table. "
            "Not an authoritative invoice — use the provider's billing "
            "dashboard for that."
        ),
    )
    cache_hit_rate: float = Field(
        ...,
        description=(
            "cached_tokens / prompt_tokens. 1.0 means every prompt "
            "token came from a cache hit; 0.0 means no caching active. "
            "Target is > 0.5 after the first worker warm-up period."
        ),
    )


class LlmUsageBreakdownEntry(BaseModel):
    """One row of the model × task breakdown."""

    model: str
    task: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class LlmUsageResponse(BaseModel):
    window_hours: int = Field(..., description="Aggregation window in hours.")
    from_time: datetime
    to_time: datetime
    totals: LlmUsageTotals
    by_model_task: list[LlmUsageBreakdownEntry] = Field(
        default_factory=list,
        description="Sorted by estimated_cost_usd desc, top N only.",
    )


# --- Tenant budget config (W7-9.1) ---------------------------------------


class TenantBudgetResponse(BaseModel):
    """Current per-tenant LLM budget row. Returned by GET
    /admin/tenant-budget. Nulls mean "this axis is not capped"."""

    tenant_id: str
    daily_token_limit: int | None
    daily_cost_cap_usd: float | None
    action_on_exceed: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantBudgetUpsert(BaseModel):
    """Body for PUT /admin/tenant-budget. Replaces (not patches) the
    caller's tenant's budget row."""

    daily_token_limit: int | None = Field(
        None, ge=0,
        description="Max prompt+completion tokens per UTC day. Null = no token cap.",
    )
    daily_cost_cap_usd: float | None = Field(
        None, ge=0.0,
        description=(
            "Max estimated USD spend per UTC day. Null = no cost cap. "
            "Either or both axes may be set; whichever is hit first "
            "triggers ``action_on_exceed``."
        ),
    )
    action_on_exceed: str = Field(
        "warn",
        description=(
            "``block`` raises an exception from llm_complete so callers "
            "can degrade. ``warn`` logs + emits an operational event "
            "but lets the call through — useful for rollout tuning."
        ),
    )


class TenantBudgetStatus(BaseModel):
    """GET /admin/tenant-budget/status — live view combining the cap
    with the current day's usage. Powers the cost-dashboard header."""

    budget: TenantBudgetResponse | None
    current_tokens: int
    current_cost_usd: float
    allowed: bool
    reason: str
