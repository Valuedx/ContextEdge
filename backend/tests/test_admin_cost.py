"""Tests for services/admin_cost_service.py — LLM usage aggregation + cost estimation."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.services.admin_cost_service import (
    MODEL_COST_USD_PER_M_TOKENS,
    _estimate_cost,
    _lookup_rate,
    get_llm_usage,
)


# ---------------------------------------------------------------------------
# Cost estimation helpers
# ---------------------------------------------------------------------------


def test_lookup_rate_substring_match():
    # gpt-4o-mini should match the exact key, not gpt-4o.
    rate = _lookup_rate("gpt-4o-mini")
    assert rate == MODEL_COST_USD_PER_M_TOKENS["gpt-4o-mini"]


def test_lookup_rate_unknown_model_returns_fallback():
    rate = _lookup_rate("some-new-model-v99")
    assert rate["input"] > 0
    # fallback has non-zero output too
    assert rate["output"] > 0


def test_estimate_cost_subtracts_cached_from_prompt_for_non_cached_billing():
    """Non-cached prompt tokens are billed at input rate; cached at cached rate."""
    # gpt-4o-mini: input=0.15, cached_input=0.075, output=0.60 per M.
    # 1M prompt with 500k cached, 100k output.
    # Non-cached: 500k × 0.15/M = $0.075
    # Cached: 500k × 0.075/M = $0.0375
    # Output: 100k × 0.60/M = $0.060
    # Total: $0.1725
    cost = _estimate_cost("gpt-4o-mini", prompt=1_000_000, completion=100_000, cached=500_000)
    assert cost == pytest.approx(0.1725, abs=1e-6)


def test_estimate_cost_handles_cached_exceeding_prompt():
    """Defensive: if cached > prompt (weird provider response), clamp to 0."""
    cost = _estimate_cost("gpt-4o", prompt=100, completion=50, cached=999)
    # Should not return a negative non-cached portion.
    assert cost >= 0


def test_estimate_cost_embedding_model_has_zero_output_rate():
    """Embedding models produce no completion tokens; output cost should be 0."""
    cost = _estimate_cost("text-embedding-3-small", prompt=1_000_000, completion=0, cached=0)
    assert cost == pytest.approx(0.02, abs=1e-6)


# ---------------------------------------------------------------------------
# get_llm_usage — aggregation logic
# ---------------------------------------------------------------------------


def _make_row(model, task, prompt, completion, cached=0, seconds_ago=1):
    payload = {
        "model": model,
        "task": task,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "total_tokens": prompt + completion,
    }
    recorded_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return (payload, recorded_at)


@pytest.mark.asyncio
async def test_get_llm_usage_empty_window_returns_zero_totals():
    """When no events exist in the window, all totals are zero."""
    class _EmptyResult:
        def all(self):
            return []

    db = SimpleNamespace(execute=AsyncMock(return_value=_EmptyResult()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24)

    totals = result["totals"]
    assert totals["request_count"] == 0
    assert totals["prompt_tokens"] == 0
    assert totals["completion_tokens"] == 0
    assert totals["cached_tokens"] == 0
    assert totals["total_tokens"] == 0
    assert totals["estimated_cost_usd"] == 0.0
    assert totals["cache_hit_rate"] == 0.0
    assert result["by_model_task"] == []


@pytest.mark.asyncio
async def test_get_llm_usage_aggregates_totals_correctly():
    rows = [
        _make_row("gpt-4o-mini", "classification", prompt=1000, completion=200, cached=500),
        _make_row("gpt-4o-mini", "classification", prompt=1000, completion=200, cached=800),
        _make_row("gpt-4o", "extraction", prompt=5000, completion=2000, cached=0),
    ]

    class _R:
        def all(self):
            return rows

    db = SimpleNamespace(execute=AsyncMock(return_value=_R()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24)

    totals = result["totals"]
    assert totals["request_count"] == 3
    assert totals["prompt_tokens"] == 7000
    assert totals["completion_tokens"] == 2400
    assert totals["cached_tokens"] == 1300
    assert totals["total_tokens"] == 9400
    # 1300 / 7000 ≈ 0.1857
    assert totals["cache_hit_rate"] == pytest.approx(0.1857, abs=1e-3)


@pytest.mark.asyncio
async def test_get_llm_usage_breakdown_is_sorted_by_cost_desc():
    # gpt-4o at 5k prompt costs more than gpt-4o-mini at 10k prompt.
    rows = [
        _make_row("gpt-4o-mini", "classification", prompt=10_000, completion=2_000, cached=0),
        _make_row("gpt-4o", "extraction", prompt=5_000, completion=3_000, cached=0),
    ]

    class _R:
        def all(self):
            return rows

    db = SimpleNamespace(execute=AsyncMock(return_value=_R()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24)

    breakdown = result["by_model_task"]
    assert len(breakdown) == 2
    # gpt-4o-extraction should be first (higher cost).
    assert breakdown[0]["model"] == "gpt-4o"
    assert breakdown[0]["task"] == "extraction"
    assert breakdown[0]["estimated_cost_usd"] > breakdown[1]["estimated_cost_usd"]


@pytest.mark.asyncio
async def test_get_llm_usage_merges_same_model_task_rows():
    rows = [
        _make_row("gpt-4o-mini", "classification", prompt=100, completion=50, cached=25),
        _make_row("gpt-4o-mini", "classification", prompt=200, completion=80, cached=100),
    ]

    class _R:
        def all(self):
            return rows

    db = SimpleNamespace(execute=AsyncMock(return_value=_R()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24)

    breakdown = result["by_model_task"]
    assert len(breakdown) == 1
    entry = breakdown[0]
    assert entry["request_count"] == 2
    assert entry["prompt_tokens"] == 300
    assert entry["completion_tokens"] == 130
    assert entry["cached_tokens"] == 125


@pytest.mark.asyncio
async def test_get_llm_usage_respects_top_n_breakdown():
    """Breakdown is capped at top_n_breakdown; totals still count all rows."""
    rows = [
        _make_row(f"model-{i}", "task", prompt=100 * (i + 1), completion=50)
        for i in range(20)
    ]

    class _R:
        def all(self):
            return rows

    db = SimpleNamespace(execute=AsyncMock(return_value=_R()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24, top_n_breakdown=5)

    assert len(result["by_model_task"]) == 5
    assert result["totals"]["request_count"] == 20


@pytest.mark.asyncio
async def test_get_llm_usage_handles_missing_payload_fields():
    """Some older operational events might lack new fields. Don't crash."""
    rows = [
        ({"model": "gpt-4o"}, datetime.now(timezone.utc)),  # no token fields
        ({"task": "extraction", "prompt_tokens": 100}, datetime.now(timezone.utc)),
    ]

    class _R:
        def all(self):
            return rows

    db = SimpleNamespace(execute=AsyncMock(return_value=_R()))
    result = await get_llm_usage(db, tenant_id=uuid4(), window_hours=24)

    # Row 1: all zeros → contributes nothing to totals except the count.
    # Row 2: 100 prompt tokens, model unknown → fallback rate applies.
    assert result["totals"]["request_count"] == 2
    assert result["totals"]["prompt_tokens"] == 100
