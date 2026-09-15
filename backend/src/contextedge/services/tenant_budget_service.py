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
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
import structlog
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

# How long a caller polls for the cross-process budget lock before giving up
# and proceeding on the in-process lock alone. The lock now covers only the
# budget read, so uncontended acquisition is immediate; this bound exists so a
# stuck holder degrades the cap's precision instead of stalling the request.
_LOCK_WAIT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.05

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
#
# Keyed PER EVENT LOOP: an asyncio.Lock binds to the loop it was created
# under, and the `-P threads` Celery pool runs every task in its own
# thread with its own asyncio.run loop. A single module-level dict made
# the FIRST task's loop own every lock, and every task on another thread
# died with "bound to a different event loop" — found live when the A3
# reclassification sweep failed 499/499. WeakKeyDictionary so a finished
# task's loop releases its locks instead of accumulating one entry per
# task. Within one loop the serialisation semantics are unchanged (the
# API server keeps its cross-request protection); across worker threads
# the overshoot is bounded by concurrency, as documented in the RUNBOOK.
logger = structlog.get_logger()


_TENANT_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[uuid.UUID, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _lock_for_tenant(tenant_id: uuid.UUID) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    per_loop = _TENANT_LOCKS.get(loop)
    if per_loop is None:
        per_loop = {}
        _TENANT_LOCKS[loop] = per_loop
    lock = per_loop.get(tenant_id)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[tenant_id] = lock
    return lock


def _advisory_key(tenant_id: uuid.UUID) -> int:
    """A stable signed 64-bit key for this tenant's budget lock.

    Postgres advisory locks are keyed by integers, not by a name, so the
    tenant UUID is folded into one. Collisions across tenants would
    over-serialise rather than under-serialise — two tenants queueing behind
    each other is a latency cost, not a correctness one — which is the right
    direction for the failure to go.

    The first 8 bytes are enough: they are random in a v4 UUID.
    """
    raw = int.from_bytes(tenant_id.bytes[:8], "big", signed=False)
    # Namespaced so this cannot collide with another feature's advisory lock.
    return (raw ^ 0x4C4C4D42_55444745) - (1 << 63)


@asynccontextmanager
async def _tenant_budget_lock(db: AsyncSession, tenant_id: uuid.UUID):
    """Serialise budget checks for one tenant ACROSS processes.

    The asyncio.Lock above only ever served callers sharing one event loop.
    In the API process that is real. In a Celery worker it was not: the
    runtime gave every task its own loop, so the per-loop dict held one lock
    with one waiter and serialised a caller against itself. Even now that
    workers keep one loop per process, prefork means N processes and N
    independent locks — so the cap could be overshot N times over.

    A Postgres advisory lock is held by the database, so it works across
    processes. The in-process lock is kept as well: it is free, and it keeps a
    burst of coroutines in one loop from each taking a turn at the database.

    The lock is SESSION-scoped (``pg_try_advisory_lock``) and released in
    ``finally``. This is the important part, and it is why the previous
    ``pg_advisory_xact_lock`` was wrong here. A transaction-scoped lock only
    releases on commit or rollback, and the caller's transaction is the whole
    HTTP request or Celery task: ``generate_embedding`` takes this lock at
    ``provider.py:804`` and does not reach ``litellm.aembedding`` until
    ``provider.py:826``. The lock was therefore held across the entire
    multi-second provider round-trip, serialising every request for the tenant
    behind it — and a hung provider call held it until the session was killed.
    Scoping the lock to this block keeps it to the budget read, which is
    milliseconds.

    Acquisition is a bounded poll rather than a blocking wait, so a stuck
    holder costs this caller ``_LOCK_WAIT_SECONDS``, not its whole request.

    Degrades to the in-process lock alone if the advisory lock cannot be
    taken — a budget check must not become the reason an LLM call fails.
    """
    key = _advisory_key(tenant_id)
    async with _lock_for_tenant(tenant_id):
        acquired = False
        try:
            deadline = time.monotonic() + _LOCK_WAIT_SECONDS
            while True:
                if await db.scalar(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
                ):
                    acquired = True
                    break
                if time.monotonic() >= deadline:
                    logger.warning(
                        "llm.budget_advisory_lock_contended",
                        tenant_id=str(tenant_id),
                        waited_seconds=_LOCK_WAIT_SECONDS,
                        detail="falling back to the in-process lock only",
                    )
                    break
                await asyncio.sleep(_LOCK_POLL_SECONDS)
        except Exception:  # noqa: BLE001 — never fail a call on the lock
            logger.warning(
                "llm.budget_advisory_lock_unavailable",
                tenant_id=str(tenant_id),
                detail="falling back to the in-process lock only",
            )
        try:
            yield acquired
        finally:
            if acquired:
                # A session-scoped lock outlives this block unless released,
                # and a pooled connection would carry it back into the pool.
                try:
                    await db.execute(
                        sa.text("SELECT pg_advisory_unlock(:k)"), {"k": key}
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "llm.budget_advisory_unlock_failed",
                        tenant_id=str(tenant_id),
                        detail="pool_recycle drops the connection holding it",
                    )


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


@dataclass(frozen=True)
class _DefaultBudget:
    """Stand-in for a missing ``tenant_llm_budgets`` row.

    Carries exactly the three attributes ``_check_budget_locked`` reads, so the
    deployment-default caps go through the same evaluation path as a
    configured row — no second implementation of the limit logic to drift.
    Not persisted: writing a row on first use would silently create config
    nobody asked for, and would then shadow later changes to the defaults.
    """

    daily_token_limit: int | None
    daily_cost_cap_usd: float | None
    action_on_exceed: str


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

    start_of_day = datetime.now(UTC).replace(
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
    budget = await get_budget(db, tenant_id)
    if budget is None:
        # No row used to mean "no cap", which left the normal case — a tenant
        # nobody has configured yet — as the only uncapped one. Fall back to
        # the deployment defaults instead. Set both to None in config to
        # restore the old unlimited behaviour.
        from contextedge.config import settings as _settings

        default_tokens = _settings.default_daily_token_limit
        default_cost = _settings.default_daily_cost_cap_usd
        if default_tokens is None and default_cost is None:
            return BudgetCheckResult(
                allowed=True,
                action="warn",  # meaningless without a cap; kept for shape.
                reason="no_budget",
                current_tokens=0,
                current_cost_usd=0.0,
                token_limit=None,
                cost_cap_usd=None,
            )
        async with _tenant_budget_lock(db, tenant_id):
            return await _check_budget_locked(
                db,
                tenant_id,
                _DefaultBudget(
                    daily_token_limit=default_tokens,
                    daily_cost_cap_usd=default_cost,
                    action_on_exceed=_settings.default_budget_action_on_exceed,
                ),
                use_cache=use_cache,
            )

    async with _tenant_budget_lock(db, tenant_id):
        return await _check_budget_locked(
            db, tenant_id, budget, use_cache=use_cache
        )




# Fraction of a cap above which a cached usage figure is no longer good
# enough. 0.9 is a judgement, not a measurement: it leaves a 10% band for a
# TTL's worth of concurrent spend to land in, which is wide for the call
# sizes seen here (a ~7k-token extraction against a daily cap).
NEAR_CAP_FRACTION = 0.9


def _near_cap(tokens: int, cost: float, budget) -> bool:
    """Whether usage is close enough to a cap that staleness would matter."""
    token_limit = getattr(budget, "daily_token_limit", None)
    cost_cap = getattr(budget, "daily_cost_cap_usd", None)
    if token_limit and tokens >= int(token_limit) * NEAR_CAP_FRACTION:
        return True
    return bool(cost_cap and float(cost) >= float(cost_cap) * NEAR_CAP_FRACTION)


async def _check_budget_locked(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    budget: TenantLLMBudget | _DefaultBudget,
    *,
    use_cache: bool = True,
) -> BudgetCheckResult:
    action: BudgetAction = (
        budget.action_on_exceed if budget.action_on_exceed in BUDGET_ACTIONS else "warn"
    )  # type: ignore[assignment]

    tokens, cost = await get_current_day_usage(db, tenant_id, use_cache=use_cache)

    # A cached figure is 60s stale at worst. Far from the cap that is
    # irrelevant; near it, it is the difference between enforcing and not.
    # So pay for a fresh aggregation only in the band where it changes the
    # answer, rather than on every call (which is what this module's cache
    # exists to avoid) or never (which is what let the cap be overshot).
    if use_cache and _near_cap(tokens, cost, budget):
        tokens, cost = await get_current_day_usage(db, tenant_id, use_cache=False)

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
    await db.refresh(existing)
    invalidate_cache(tenant_id)
    return existing
