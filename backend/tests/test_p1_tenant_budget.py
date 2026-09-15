"""Tests for per-tenant LLM budget enforcement (W7-9.1)."""

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from contextedge.services.tenant_budget_service import (
    BudgetCheckResult,
    TenantBudgetExceeded,
    _tenant_budget_lock,
    check_budget,
    get_current_day_usage,
    invalidate_cache,
    upsert_budget,
)


@dataclass
class _FakeBudget:
    tenant_id: object
    daily_token_limit: int | None
    daily_cost_cap_usd: Decimal | None
    action_on_exceed: str


class _Rows:
    """Mimic ``(await db.execute(...)).all()`` returning ``[(payload,)]``."""

    def __init__(self, payloads):
        self._rows = [(p,) for p in payloads]

    def all(self):
        return self._rows


def _db_with(budget=None, usage_events=None, *, lock_acquired=True):
    async def get(model, key):
        return budget

    async def execute(stmt, *args, **kwargs):
        return _Rows(usage_events or [])

    async def scalar(stmt, *args, **kwargs):
        return lock_acquired

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        execute=AsyncMock(side_effect=execute),
        scalar=AsyncMock(side_effect=scalar),
        add=lambda obj: None,
        flush=AsyncMock(),
    )
    return db


def _sql_texts(db) -> list[str]:
    texts = [str(call.args[0]).lower() for call in db.execute.await_args_list]
    texts.extend(str(call.args[0]).lower() for call in db.scalar.await_args_list)
    return texts


def _is_advisory_sql(stmt) -> bool:
    return "advisory" in str(stmt).lower()


def _aggregation_calls(db) -> int:
    """How many usage aggregations were issued.

    `db.execute` also carries the session-scoped advisory unlock. Counting
    every execute would make these tests fail whenever a non-aggregating
    statement is added, which measures plumbing rather than the caching
    behaviour they exist to pin down.
    """
    return sum(
        1
        for call in db.execute.await_args_list
        if not _is_advisory_sql(call.args[0])
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.mark.asyncio
async def test_no_budget_row_falls_back_to_deployment_defaults():
    """An unconfigured tenant is capped by the deployment default.

    This deliberately reverses the original contract. "No row" used to
    mean "no cap", which made the normal case — a tenant nobody has got
    around to configuring — the *only* uncapped one, and therefore the
    only one that could run up an unbounded LLM bill.
    """
    from contextedge.config import settings

    tenant_id = uuid4()
    db = _db_with(budget=None)
    result = await check_budget(db, tenant_id)
    assert result.allowed is True
    assert result.reason == "ok"
    assert result.token_limit == settings.default_daily_token_limit
    assert result.cost_cap_usd == settings.default_daily_cost_cap_usd


@pytest.mark.asyncio
async def test_deployment_defaults_can_be_disabled_for_unlimited():
    """The documented escape hatch, which was otherwise untested: set
    both deployment defaults to None to restore genuinely unlimited
    spend for unconfigured tenants."""
    from contextedge.config import settings

    tenant_id = uuid4()
    db = _db_with(budget=None)
    with (
        patch.object(settings, "default_daily_token_limit", None),
        patch.object(settings, "default_daily_cost_cap_usd", None),
    ):
        result = await check_budget(db, tenant_id)
    assert result.allowed is True
    assert result.reason == "no_budget"
    assert result.token_limit is None


@pytest.mark.asyncio
async def test_under_token_limit_allowed():
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=10_000, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [
        {"model": "gpt-4o-mini", "prompt_tokens": 1000, "completion_tokens": 200, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is True
    assert result.reason == "ok"
    assert result.current_tokens == 1200
    assert result.token_limit == 10_000


@pytest.mark.asyncio
async def test_over_token_limit_blocked():
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=1000, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [
        {"model": "gpt-4o-mini", "prompt_tokens": 800, "completion_tokens": 300, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is False
    assert result.reason == "token_limit_exceeded"
    assert result.action == "block"


@pytest.mark.asyncio
async def test_over_cost_cap_blocked():
    tenant_id = uuid4()
    budget = _FakeBudget(
        tenant_id, daily_token_limit=None, daily_cost_cap_usd=Decimal("0.10"),
        action_on_exceed="block",
    )
    # gpt-4o: $2.50/M input, $10.00/M output.
    # 100K input + 50K output = $2.50 × 0.1 + $10.00 × 0.05 = $0.25 + $0.50 = $0.75 > $0.10 cap
    usage = [
        {"model": "gpt-4o", "prompt_tokens": 100_000, "completion_tokens": 50_000, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is False
    assert result.reason == "cost_cap_exceeded"
    assert result.current_cost_usd >= 0.10


@pytest.mark.asyncio
async def test_warn_action_still_returns_not_allowed_with_action_warn():
    """action='warn' doesn't block the call upstream — but check_budget
    itself returns allowed=False so the caller can decide what to do."""
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=100, daily_cost_cap_usd=None, action_on_exceed="warn")
    usage = [
        {"model": "gpt-4o-mini", "prompt_tokens": 200, "completion_tokens": 0, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is False
    assert result.action == "warn"


@pytest.mark.asyncio
async def test_token_limit_checked_before_cost_cap():
    """When both caps are configured and both are breached, the token
    limit reason wins (documented ordering)."""
    tenant_id = uuid4()
    budget = _FakeBudget(
        tenant_id, daily_token_limit=10, daily_cost_cap_usd=Decimal("0.01"),
        action_on_exceed="block",
    )
    usage = [
        {"model": "gpt-4o", "prompt_tokens": 100_000, "completion_tokens": 100_000, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.reason == "token_limit_exceeded"


@pytest.mark.asyncio
async def test_exception_carries_structured_result():
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=10, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [{"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": 0}]
    db = _db_with(budget=budget, usage_events=usage)

    result = await check_budget(db, tenant_id)
    exc = TenantBudgetExceeded(result)
    assert exc.result is result
    assert "token_limit_exceeded" in str(exc)


@pytest.mark.asyncio
async def test_malformed_payload_rows_are_ignored():
    """Ingesting a broken event (non-dict payload, missing fields) must
    not crash enforcement — the rest of the day's events still count."""
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=10_000, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [
        None,  # Not a dict
        "not a dict either",
        {},  # Empty
        {"model": "gpt-4o-mini", "prompt_tokens": 500, "completion_tokens": 100, "cached_tokens": 0},
    ]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is True
    assert result.current_tokens == 600


@pytest.mark.asyncio
async def test_cache_reuses_usage_within_ttl():
    """Two back-to-back check_budget calls on the same tenant must hit
    the cache and only issue one DB aggregation."""
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=10_000, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [{"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 50, "cached_tokens": 0}]
    db = _db_with(budget=budget, usage_events=usage)

    await check_budget(db, tenant_id)
    await check_budget(db, tenant_id)

    # db.get is called twice (once per check for the budget row) but the
    # expensive aggregation runs only once thanks to the cache. The
    # advisory-lock statement is excluded: it is not an aggregation.
    assert _aggregation_calls(db) == 1


@pytest.mark.asyncio
async def test_invalidate_cache_forces_requery():
    tenant_id = uuid4()
    budget = _FakeBudget(tenant_id, daily_token_limit=10_000, daily_cost_cap_usd=None, action_on_exceed="block")
    usage = [{"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 50, "cached_tokens": 0}]
    db = _db_with(budget=budget, usage_events=usage)

    await check_budget(db, tenant_id)
    invalidate_cache(tenant_id)
    await check_budget(db, tenant_id)
    assert _aggregation_calls(db) == 2


@pytest.mark.asyncio
async def test_get_current_day_usage_without_cache_always_queries():
    tenant_id = uuid4()
    usage = [{"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 50, "cached_tokens": 0}]
    db = _db_with(usage_events=usage)
    await get_current_day_usage(db, tenant_id, use_cache=False)
    await get_current_day_usage(db, tenant_id, use_cache=False)
    assert _aggregation_calls(db) == 2


@pytest.mark.asyncio
async def test_upsert_budget_rejects_invalid_action():
    db = _db_with(budget=None)
    with pytest.raises(ValueError, match="action_on_exceed must be"):
        await upsert_budget(
            db, tenant_id=uuid4(), daily_token_limit=1000,
            daily_cost_cap_usd=None, action_on_exceed="throw_a_tantrum",
        )


@pytest.mark.asyncio
async def test_upsert_budget_rejects_negative_limits():
    db = _db_with(budget=None)
    with pytest.raises(ValueError, match="daily_token_limit"):
        await upsert_budget(
            db, tenant_id=uuid4(), daily_token_limit=-1,
            daily_cost_cap_usd=None, action_on_exceed="warn",
        )
    with pytest.raises(ValueError, match="daily_cost_cap_usd"):
        await upsert_budget(
            db, tenant_id=uuid4(), daily_token_limit=None,
            daily_cost_cap_usd=Decimal("-0.01"), action_on_exceed="warn",
        )


@pytest.mark.asyncio
async def test_upsert_budget_creates_new_row_when_none_exists():
    tenant_id = uuid4()
    added: list = []

    async def get(model, key):
        return None

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),  # upsert now refreshes server defaults
    )

    budget = await upsert_budget(
        db, tenant_id=tenant_id, daily_token_limit=5000,
        daily_cost_cap_usd=Decimal("10.00"), action_on_exceed="block",
    )
    assert added == [budget]
    assert budget.daily_token_limit == 5000
    assert budget.daily_cost_cap_usd == Decimal("10.00")
    assert budget.action_on_exceed == "block"


@pytest.mark.asyncio
async def test_budget_check_result_is_frozen_dataclass():
    """Regression: the result shape is part of the public API — don't
    accidentally make it mutable."""
    result = BudgetCheckResult(
        allowed=True, action="warn", reason="ok",
        current_tokens=0, current_cost_usd=0.0,
        token_limit=None, cost_cap_usd=None,
    )
    with pytest.raises(Exception):
        result.allowed = False  # type: ignore[misc]


# --- batch embedding goes through the same gate -------------------------


@pytest.mark.asyncio
async def test_batch_embedding_blocked_tenant_never_reaches_the_provider():
    """The batch path used to accept no tenant context at all, so ingestion
    embeddings bypassed a blocked tenant's cap and recorded as unknown spend.
    With tenant_id/db supplied, a blocked budget must raise BEFORE any
    provider call."""
    from contextedge.ai import provider as provider_mod

    blocked = BudgetCheckResult(
        allowed=False,
        action="block",
        reason="token_limit_exceeded",
        current_tokens=999,
        current_cost_usd=1.0,
        token_limit=100,
        cost_cap_usd=None,
    )
    aembedding = AsyncMock()
    with (
        patch(
            "contextedge.services.tenant_budget_service.check_budget",
            AsyncMock(return_value=blocked),
        ),
        patch.object(provider_mod.litellm, "aembedding", aembedding),
    ):
        with pytest.raises(TenantBudgetExceeded):
            await provider_mod.generate_embeddings_batch(
                ["some text"], tenant_id=uuid4(), db=SimpleNamespace()
            )
    aembedding.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_embedding_without_tenant_context_still_works():
    """Legacy callers (no tenant kwargs) keep functioning — unattributed,
    but never broken by the new gate."""
    from contextedge.ai import provider as provider_mod

    response = SimpleNamespace(
        data=[{"embedding": [0.0] * 3072}], usage=None
    )
    with (
        patch.object(
            provider_mod.litellm, "aembedding", AsyncMock(return_value=response)
        ),
        patch(
            "contextedge.ai.provider.record_llm_usage", AsyncMock()
        ),
    ):
        out = await provider_mod.generate_embeddings_batch(["some text"])
    assert len(out) == 1 and len(out[0]) == 3072


def test_tenant_lock_is_per_event_loop():
    """Regression for the threads-pool crash found live (roadmap A3
    sweep, 499/499 failures): an asyncio.Lock binds to the loop that
    created it, and the Celery threads pool gives every task its own
    asyncio.run loop. The registry must hand each loop its own lock —
    the SAME loop must keep getting the SAME lock (that is the F-29
    serialisation guarantee), and a DIFFERENT loop must never receive a
    lock bound elsewhere.
    """
    import asyncio
    import uuid as _uuid

    from contextedge.services.tenant_budget_service import _lock_for_tenant

    tenant = _uuid.uuid4()

    async def grab_twice():
        return _lock_for_tenant(tenant), _lock_for_tenant(tenant)

    a1, a2 = asyncio.run(grab_twice())
    assert a1 is a2  # same loop -> same lock (serialisation preserved)

    async def grab_and_use():
        lock = _lock_for_tenant(tenant)
        async with lock:  # would raise "bound to a different event loop" pre-fix
            pass
        return lock

    b = asyncio.run(grab_and_use())
    assert b is not a1  # new loop -> its own lock, not the dead loop's


def _lock_db(*, acquired: bool = True):
    async def scalar(stmt, *args, **kwargs):
        return acquired

    async def execute(stmt, *args, **kwargs):
        return None

    return SimpleNamespace(
        scalar=AsyncMock(side_effect=scalar),
        execute=AsyncMock(side_effect=execute),
    )


@pytest.mark.asyncio
async def test_budget_lock_uses_session_lock_and_unlocks_after_the_block():
    """The lock must not be transaction-scoped: the caller's transaction
    stays open across the provider call, so an xact lock would serialise
    the tenant for seconds. Unlock happens when the block exits, before
    commit.
    """
    db = _lock_db(acquired=True)
    tenant_id = uuid4()

    async with _tenant_budget_lock(db, tenant_id) as acquired:
        assert acquired is True
        during = _sql_texts(db)
        assert any("pg_try_advisory_lock" in sql for sql in during)
        assert not any("pg_advisory_unlock" in sql for sql in during)
        assert not any("xact" in sql for sql in during)

    after = _sql_texts(db)
    assert any("pg_advisory_unlock" in sql for sql in after)
    assert not any("xact" in sql for sql in after)


@pytest.mark.asyncio
async def test_budget_lock_unlocks_when_the_body_raises():
    db = _lock_db(acquired=True)
    with pytest.raises(RuntimeError, match="provider exploded"):
        async with _tenant_budget_lock(db, uuid4()):
            raise RuntimeError("provider exploded")
    assert any("pg_advisory_unlock" in sql for sql in _sql_texts(db))


@pytest.mark.asyncio
async def test_budget_lock_contended_falls_back_without_unlock(monkeypatch):
    from contextedge.services import tenant_budget_service as mod

    monkeypatch.setattr(mod, "_LOCK_WAIT_SECONDS", 0.0)
    db = _lock_db(acquired=False)
    with patch.object(mod.logger, "warning") as warning:
        async with mod._tenant_budget_lock(db, uuid4()) as acquired:
            assert acquired is False
    assert not any("unlock" in sql for sql in _sql_texts(db))
    assert any(
        call.args and call.args[0] == "llm.budget_advisory_lock_contended"
        for call in warning.call_args_list
    )


@pytest.mark.asyncio
async def test_check_budget_does_not_take_a_transaction_scoped_lock():
    tenant_id = uuid4()
    budget = _FakeBudget(
        tenant_id, daily_token_limit=10_000, daily_cost_cap_usd=None, action_on_exceed="block",
    )
    usage = [{"model": "gpt-4o-mini", "prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": 0}]
    db = _db_with(budget=budget, usage_events=usage)
    result = await check_budget(db, tenant_id)
    assert result.allowed is True
    texts = _sql_texts(db)
    assert any("pg_try_advisory_lock" in sql for sql in texts)
    assert any("pg_advisory_unlock" in sql for sql in texts)
    assert not any("xact" in sql for sql in texts)


def test_pooled_engine_recycles_and_sets_server_timeouts(monkeypatch):
    from contextedge import database as dbmod

    captured: dict = {}
    fake = MagicMock()
    fake.sync_engine = MagicMock()

    def fake_create(url, **kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(dbmod, "create_async_engine", fake_create)
    monkeypatch.setattr(dbmod.event, "listen", lambda *args, **kwargs: None)
    dbmod.create_db_engine()
    assert captured["pool_recycle"] == 1800
    assert captured["pool_pre_ping"] is True
    settings = captured["connect_args"]["server_settings"]
    assert settings["idle_in_transaction_session_timeout"] == "60000"
    assert settings["statement_timeout"] == "30000"


def test_null_pool_engine_still_sets_server_timeouts(monkeypatch):
    from sqlalchemy.pool import NullPool
    from contextedge import database as dbmod

    captured: dict = {}
    fake = MagicMock()
    fake.sync_engine = MagicMock()

    def fake_create(url, **kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(dbmod, "create_async_engine", fake_create)
    monkeypatch.setattr(dbmod.event, "listen", lambda *args, **kwargs: None)
    dbmod.create_db_engine(use_null_pool=True)
    assert captured["poolclass"] is NullPool
    assert "pool_recycle" not in captured
    settings = captured["connect_args"]["server_settings"]
    assert settings["idle_in_transaction_session_timeout"] == "60000"
    assert settings["statement_timeout"] == "30000"
