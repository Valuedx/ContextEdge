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
