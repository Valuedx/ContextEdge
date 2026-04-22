"""Aggregate LLM-usage events into dashboard-ready totals.

Data source is ``operational_events`` rows written by
``ai/observability.record_llm_usage`` with ``event_type="llm.usage"``.
This service aggregates them into the shapes the admin dashboard renders.

Cost is estimated from a small in-process table of per-million-token
prices. Values are approximations for dashboard UX; the provider's own
billing dashboard is authoritative. Adjust ``MODEL_COST_USD_PER_M_TOKENS``
as provider pricing changes — keeping it in-process avoids a new table
and is easy to override per deployment via env later if needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.events import OperationalEvent

# Conservative April-2026 estimates. Tweakable without redeploying if
# we ever surface these via config. Cached input tokens are priced at
# 10% of the standard input-token rate for OpenAI 4o-family and Anthropic
# ephemeral cache reads — good enough approximation for a dashboard.
MODEL_COST_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    # key: model name (substring match acceptable)
    # value: {input, output, cached_input}
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached_input": 0.075},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached_input": 1.25},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0, "cached_input": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0, "cached_input": 0.0},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cached_input": 0.10},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-opus-4-7": {"input": 15.00, "output": 75.00, "cached_input": 1.50},
}

# Fallback rate if the model doesn't match any known entry.
_FALLBACK_RATE = {"input": 1.00, "output": 3.00, "cached_input": 0.10}


def _lookup_rate(model: str) -> dict[str, float]:
    """Substring-match against the known models table."""
    for key, rate in MODEL_COST_USD_PER_M_TOKENS.items():
        if key in (model or ""):
            return rate
    return _FALLBACK_RATE


def _estimate_cost(model: str, prompt: int, completion: int, cached: int) -> float:
    rate = _lookup_rate(model)
    non_cached_prompt = max(prompt - cached, 0)
    cost = (
        (non_cached_prompt / 1_000_000.0) * rate["input"]
        + (cached / 1_000_000.0) * rate["cached_input"]
        + (completion / 1_000_000.0) * rate["output"]
    )
    return round(cost, 6)


async def get_llm_usage(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    window_hours: int = 24,
    top_n_breakdown: int = 10,
) -> dict[str, Any]:
    """Aggregate llm.usage events for one tenant over the last N hours.

    Queries ``operational_events`` once, sums token fields in Python (the
    rows are small and bounded by the window). Splitting into a single
    materialised round trip keeps latency flat and avoids N+1 queries on
    the dashboard's polling refresh.
    """
    now = datetime.now(timezone.utc)
    from_time = now - timedelta(hours=window_hours)

    stmt = (
        select(OperationalEvent.payload, OperationalEvent.recorded_at)
        .where(
            OperationalEvent.tenant_id == tenant_id,
            OperationalEvent.event_type == "llm.usage",
            OperationalEvent.recorded_at >= from_time,
        )
        .order_by(OperationalEvent.recorded_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    # Totals
    total_requests = 0
    total_prompt = 0
    total_completion = 0
    total_cached = 0
    total_cost = 0.0

    # (model, task) → aggregate
    breakdown: dict[tuple[str, str], dict[str, Any]] = {}

    for payload, _recorded_at in rows:
        model = payload.get("model", "unknown")
        task = payload.get("task", "unknown")
        prompt = int(payload.get("prompt_tokens", 0) or 0)
        completion = int(payload.get("completion_tokens", 0) or 0)
        cached = int(payload.get("cached_tokens", 0) or 0)
        cost = _estimate_cost(model, prompt, completion, cached)

        total_requests += 1
        total_prompt += prompt
        total_completion += completion
        total_cached += cached
        total_cost += cost

        key = (model, task)
        agg = breakdown.setdefault(
            key,
            {
                "model": model,
                "task": task,
                "request_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        agg["request_count"] += 1
        agg["prompt_tokens"] += prompt
        agg["completion_tokens"] += completion
        agg["cached_tokens"] += cached
        agg["total_tokens"] += prompt + completion
        agg["estimated_cost_usd"] = round(agg["estimated_cost_usd"] + cost, 6)

    cache_hit_rate = 0.0 if total_prompt == 0 else round(total_cached / total_prompt, 4)

    by_model_task = sorted(
        breakdown.values(),
        key=lambda r: r["estimated_cost_usd"],
        reverse=True,
    )[:top_n_breakdown]

    return {
        "window_hours": window_hours,
        "from_time": from_time,
        "to_time": now,
        "totals": {
            "request_count": total_requests,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "cached_tokens": total_cached,
            "total_tokens": total_prompt + total_completion,
            "estimated_cost_usd": round(total_cost, 4),
            "cache_hit_rate": cache_hit_rate,
        },
        "by_model_task": by_model_task,
    }
