"""Per-tenant LLM budget enforcement.

Enterprise gate §6 item 14. Without per-tenant caps, one misconfigured
tenant or a retry-storm on a provider 5xx can burn the whole org's
daily LLM budget. This module:

1. Reads ``tenant_llm_budgets`` to find the per-tenant cap (if any).
2. Sums the current UTC day's usage from the ``llm.usage`` operational
   events already written by ``ai/observability.record_llm_usage`` —
   no second source of truth, no new aggregation column to drift.
3. Returns a decision (``allowed`` / ``exceeded``) plus the enforcement
   ``action`` (``block`` / ``warn``) the tenant's row configured.

The pre-call check in ``ai/provider.llm_complete`` raises
``TenantBudgetExceeded`` on ``action="block"`` so upstream code can
degrade cleanly. On ``action="warn"`` the call proceeds but an
operational event ``llm.budget_warning`` is written, making "the day I
had to flip the switch" queryable after the fact.

We cache the current-day usage per tenant with a short TTL to avoid
issuing a large aggregation query on every LLM call. The cache is
deliberately simple: a module-level dict keyed by tenant_id with a
timestamp. TTL is short enough (60s) that caps catch within-minute
spikes; tighter real-time guarantees can upgrade to Redis later.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.events import OperationalEvent
from contextedge.models.tenant import BUDGET_ACTIONS, TenantLLMBudget
from contextedge.services.admin_cost_service import _estimate_cost

BudgetAction = Literal["block", "warn"]

# How long a usage total stays cached before the next LLM call rechecks
# the DB. 60s is fine for daily budgets measured in tens of thousands of
# tokens — a 60-second lag means at most one over-cap call slips through
# before we catch up. Tighten later if needed.
USAGE_CACHE_TTL_SECONDS = 60.0

# Review F-29: a per-tenant asyncio.Lock serialises check_budget calls
# inside one worker process. Two concurrent HTTP / Celery calls on the
# same tenant can otherwise both read the usage cache, both see room
# under the cap, and both proceed — overshooting the cap by one
# call's worth of tokens. With the lock, the second caller waits for
# the first to finish and then sees the updated usage (cache TTL aside).
#
# This does NOT protect against cross-worker races (gunicorn replicas,
# multiple Celery workers). For that, swap the in-memory cache + lock
# for a Redis-backed counter with INCRBY + atomic compare-against-limit.
# See the module docstring.
_TENANT_LOCKS: dict[uuid.UUID, asyncio.Lock] = {}


def _lock_for_tenant(tenant_id: uuid.UUID) -> asyncio.Lock:
    lock = _TENANT_LOCKS.get(tenant_id)
    if lock is None:
        lock = asyncio.Lock()
        _TENANT_LOCKS[tenant_id] = lock
    return lock


@dataclass(frozen=True)
class BudgetCheckResult:
    """Return value of ``check_budget`` — all the fields a caller needs
    to either proceed, degrade, or log a warning."""

    allowed: bool
    action: BudgetAction  # "block" → raise; "warn" → log + proceed
    reason: str  # "ok" | "token_limit_exceeded" | "cost_cap_exceeded" | "no_budget"
    current_tokens: int
    current_cost_usd: float
    token_limit: int | None
    cost_cap_usd: float | None


class TenantBudgetExceeded(Exception):
    """Raised from ``llm_complete`` when a tenant's daily cap is hit and
    the configured action is ``block``. Callers can choose to degrade
    (fall back to a cached answer, skip the extraction, …) or surface
    the error up the stack."""

    def __init__(self, result: BudgetCheckResult):
        self.result = result
        super().__init__(
            f"tenant budget exceeded: {result.reason} "
            f"(tokens={result.current_tokens}/{result.token_limit}, "
            f"cost=${result.current_cost_usd:.4f}/${result.cost_cap_usd})"
        )


# Module-level cache: tenant_id → (fetched_at, tokens, cost_usd).
_USAGE_CACHE: dict[uuid.UUID, tuple[float, int, float]] = {}


def _cache_hit(tenant_id: uuid.UUID) -> tuple[int, float] | None:
    entry = _USAGE_CACHE.get(tenant_id)
    if entry is None:
        return None
    fetched_at, tokens, cost = entry
    if time.monotonic() - fetched_at > USAGE_CACHE_TTL_SECONDS:
        return None
    return tokens, cost


def _cache_set(tenant_id: uuid.UUID, tokens: int, cost_usd: float) -> None:
    _USAGE_CACHE[tenant_id] = (time.monotonic(), tokens, cost_usd)


def invalidate_cache(tenant_id: uuid.UUID | None = None) -> None:
    """Drop cached usage for one tenant (or all). Exposed for tests
    and for admin endpoints that raise / reset caps."""
    if tenant_id is None:
        _USAGE_CACHE.clear()
    else:
        _USAGE_CACHE.pop(tenant_id, None)


# Review F-30: hook a SQLAlchemy after_delete listener on
# TenantLLMBudget so a tenant CASCADE-delete (or any explicit delete
# of the budget row) also evicts the cache entry. Without this, a
# stale cache entry could linger until TTL — harmless in practice
# (the tenant is gone) but confusing when debugging. The listener is
# process-local; it fires in whichever worker process committed the
# delete. Other worker processes still rely on TTL expiry, matching
# the existing cache semantics.
def _register_cache_invalidation_listener() -> None:
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(TenantLLMBudget, "after_delete")
    def _after_delete(mapper, connection, target):  # type: ignore[no-redef]
        try:
            invalidate_cache(target.tenant_id)
        except Exception:  # pragma: no cover — listener must never raise
            pass


_register_cache_invalidation_listener()


async def get_budget(db: AsyncSession, tenant_id: uuid.UUID) -> TenantLLMBudget | None:
    return await db.get(TenantLLMBudget, tenant_id)


async def get_current_day_usage(
    db: AsyncSession, tenant_id: uuid.UUID, *, use_cache: bool = True,
) -> tuple[int, float]:
    """Return (tokens, cost_usd) consumed by ``tenant_id`` so far in
    the current UTC day. Reads from the ``llm.usage`` operational
    events; rolls its own cost calculation using the same model-rate
    table as the admin cost dashboard."""
    if use_cache:
        cached = _cache_hit(tenant_id)
        if cached is not None:
            return cached

    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    rows = (
        await db.execute(
            select(OperationalEvent.payload).where(
                OperationalEvent.tenant_id == tenant_id,
                OperationalEvent.event_type == "llm.usage",
                OperationalEvent.occurred_at >= start_of_day,
            )
        )
    ).all()

    total_tokens = 0
    total_cost = 0.0
    for (payload,) in rows:
        if not isinstance(payload, dict):
            continue
        prompt = int(payload.get("prompt_tokens") or 0)
        completion = int(payload.get("completion_tokens") or 0)
        cached = int(payload.get("cached_tokens") or 0)
        total_tokens += prompt + completion
        total_cost += _estimate_cost(
            payload.get("model") or "", prompt, completion, cached,
        )

    if use_cache:
        _cache_set(tenant_id, total_tokens, total_cost)
    return total_tokens, total_cost


async def check_budget(
    db: AsyncSession, tenant_id: uuid.UUID, *, use_cache: bool = True,
) -> BudgetCheckResult:
    """Decide whether the next LLM call for ``tenant_id`` is allowed.

    Ordering: tokens checked before cost. A tenant with only a token
    cap configured will never see ``cost_cap_exceeded`` even if spend
    spikes. A tenant with both configured sees whichever fires first.

    Review F-29: serialised per tenant inside one worker process via
    an ``asyncio.Lock``. Concurrent callers on the same tenant queue
    rather than all reading the same stale usage number and all
    overshooting the cap by one call each. Note this is not
    cross-worker — see the note at the top of this module.
    """
    # Short-circuit: no budget row means no cap, no need to lock.
    budget = await get_budget(db, tenant_id)
    if budget is None:
        return BudgetCheckResult(
            allowed=True,
            action="warn",  # meaningless without a cap; kept for shape.
            reason="no_budget",
            current_tokens=0,
            current_cost_usd=0.0,
            token_limit=None,
            cost_cap_usd=None,
        )

    async with _lock_for_tenant(tenant_id):
        return await _check_budget_locked(db, tenant_id, budget, use_cache=use_cache)


async def _check_budget_locked(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    budget: TenantLLMBudget,
    *,
    use_cache: bool = True,
) -> BudgetCheckResult:
    action: BudgetAction = (
        budget.action_on_exceed if budget.action_on_exceed in BUDGET_ACTIONS else "warn"
    )  # type: ignore[assignment]

    tokens, cost = await get_current_day_usage(db, tenant_id, use_cache=use_cache)
    cost_cap = (
        float(budget.daily_cost_cap_usd) if budget.daily_cost_cap_usd is not None else None
    )

    if budget.daily_token_limit is not None and tokens >= budget.daily_token_limit:
        return BudgetCheckResult(
            allowed=False,
            action=action,
            reason="token_limit_exceeded",
            current_tokens=tokens,
            current_cost_usd=cost,
            token_limit=budget.daily_token_limit,
            cost_cap_usd=cost_cap,
        )
    if cost_cap is not None and cost >= cost_cap:
        return BudgetCheckResult(
            allowed=False,
            action=action,
            reason="cost_cap_exceeded",
            current_tokens=tokens,
            current_cost_usd=cost,
            token_limit=budget.daily_token_limit,
            cost_cap_usd=cost_cap,
        )

    return BudgetCheckResult(
        allowed=True,
        action=action,
        reason="ok",
        current_tokens=tokens,
        current_cost_usd=cost,
        token_limit=budget.daily_token_limit,
        cost_cap_usd=cost_cap,
    )


async def upsert_budget(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    daily_token_limit: int | None,
    daily_cost_cap_usd: Decimal | float | None,
    action_on_exceed: str,
) -> TenantLLMBudget:
    """Create or update the budget row for ``tenant_id``. Used by the
    admin API. Invalidates the cache so the new cap takes effect on
    the next call."""
    if action_on_exceed not in BUDGET_ACTIONS:
        raise ValueError(
            f"action_on_exceed must be one of {BUDGET_ACTIONS}, got {action_on_exceed!r}"
        )
    if daily_token_limit is not None and daily_token_limit < 0:
        raise ValueError("daily_token_limit must be non-negative or None")
    if daily_cost_cap_usd is not None and float(daily_cost_cap_usd) < 0:
        raise ValueError("daily_cost_cap_usd must be non-negative or None")

    existing = await get_budget(db, tenant_id)
    cost_value = (
        Decimal(str(daily_cost_cap_usd)) if daily_cost_cap_usd is not None else None
    )
    if existing is None:
        existing = TenantLLMBudget(
            tenant_id=tenant_id,
            daily_token_limit=daily_token_limit,
            daily_cost_cap_usd=cost_value,
            action_on_exceed=action_on_exceed,
        )
        db.add(existing)
    else:
        existing.daily_token_limit = daily_token_limit
        existing.daily_cost_cap_usd = cost_value
        existing.action_on_exceed = action_on_exceed
    await db.flush()
    invalidate_cache(tenant_id)
    return existing
