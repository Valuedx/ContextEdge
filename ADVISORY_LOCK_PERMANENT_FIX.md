# Permanent fix — ContextEdge advisory lock held across network calls

**Repo:** `D:\ContextEdge_pro\ContextEdge` (this is a ContextEdge change, not a Copilot one.)

## The bug in one sentence

`_tenant_budget_lock` takes a **transaction-scoped** Postgres lock, but the transaction it belongs to
is the whole HTTP request — so the lock stays held while ContextEdge makes a ~2-second call to Google
for an embedding, and every other request for that tenant queues behind it.

## The mechanism

`services/tenant_budget_service.py:115-154`

```python
@asynccontextmanager
async def _tenant_budget_lock(db: AsyncSession, tenant_id: uuid.UUID):
    async with _lock_for_tenant(tenant_id):
        acquired = False
        try:
            await db.execute(
                sa.text("SELECT pg_advisory_xact_lock(:k)"),   # ← transaction-scoped
                {"k": _advisory_key(tenant_id)},
            )
            acquired = True
        except Exception:
            logger.warning("llm.budget_advisory_lock_unavailable", ...)
        try:
            yield acquired
        finally:
            # pg_advisory_xact_lock releases on commit/rollback; there is no
            # unlock call to make, and trying would be the bug.
            pass                                               # ← releases nothing
```

The comment is correct about the mechanics and wrong about the consequence. Exiting the `async with`
releases **nothing**, because `pg_advisory_xact_lock` only releases on commit or rollback. So the real
sequence on `/api/v1/runtime/match` is:

```
deps.py:106        set_config('app.tenant_id', ...)        ← transaction opens here
provider.py:804    check_budget()  → advisory lock TAKEN
provider.py:826    await litellm.aembedding(...)           ← ~2000ms, lock still held
provider.py:842    record_llm_usage() → flush(), no commit ← more SQL on the same transaction
database.py:69     session.commit()                        ← lock finally released, after the response
```

The lock key is derived from the tenant UUID (`tenant_budget_service.py:99-112`), so **all concurrent
work for one tenant serialises on one key**. Every embedding for the tenant queues behind the one
before it, and if any single Google call hangs, the lock is held until something kills the session.

Three things make it worse than it looks:

- `config.py:243` sets `default_daily_token_limit = 2_000_000`, so the "no budget configured" early
  return at `tenant_budget_service.py:324` is **dead in production**. Every tenant takes the lock on
  every LLM and embedding call.
- `provider.py:900-917` (`generate_embeddings_batch`) re-checks the budget per sub-batch, so one task
  holds the lock across N sequential round-trips.
- `workers/chunk_tasks.py:162-184` does up to **13 batched embedding calls inside one transaction**
  (`asyncio_runner.py:137-158` wraps the entire Celery task in a single commit).

---

## Fix 1 — make the lock release when the block exits (the outage fix)

Swap the transaction-scoped lock for a session-scoped one that is explicitly released. This is a
change to one function and nothing else.

```python
@asynccontextmanager
async def _tenant_budget_lock(db: AsyncSession, tenant_id: uuid.UUID):
    """Serialise budget checks for one tenant across processes.

    Session-scoped, not transaction-scoped: the caller's transaction stays open
    across a multi-second provider call, so a transaction-scoped lock would be
    held for that whole call and serialise every request for the tenant.
    """
    key = _advisory_key(tenant_id)
    async with _lock_for_tenant(tenant_id):
        acquired = False
        try:
            # Never wait longer than the budget check itself should take.
            await db.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            await db.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": key})
            acquired = True
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
                try:
                    await db.execute(
                        sa.text("SELECT pg_advisory_unlock(:k)"), {"k": key}
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "llm.budget_advisory_unlock_failed", tenant_id=str(tenant_id)
                    )
```

The lock now covers only the budget read, which is milliseconds. The provider call happens after it
is released.

**The one risk to know about.** A session-scoped lock belongs to the *connection*, not the
transaction, so if `pg_advisory_unlock` never runs the lock rides back into the pool. The `finally`
covers normal and exception paths; the remaining hole is a process killed mid-block. Two cheap
guards:

- Add `pool_recycle=1800` in `database.py:39-56` (currently unset, so connections are never recycled).
  A recycled connection drops any lock it was carrying.
- The `idle_in_transaction_session_timeout` already set on the database catches the rest.

If you would rather have no leak risk at all, the alternative is to run `check_budget` on its **own
short-lived session** that commits immediately, keeping `pg_advisory_xact_lock` as-is. That is
strictly safer but costs one extra connection checkout per LLM call — a real consideration at
`max_connections = 100` with `pool_size=20 + max_overflow=10` per process.

## Fix 2 — stop holding the connection across provider calls (the worker fix)

`db/external_call.py:51-89` already contains exactly the right helper:

```python
@asynccontextmanager
async def released(db: AsyncSession, *, reason: str):
    """Commit and drop the connection for the duration of the block."""
    await db.commit()
    ...
```

**A repo-wide grep returns zero call sites.** The module is dead code; its own docstring names the
migration that was never done. Wrap the provider calls with it:

- `provider.py:388` — `litellm.acompletion`
- `provider.py:826` — `litellm.aembedding`
- `provider.py:928` — `litellm.aembedding` (batch)

or, if committing inside `provider.py` is too invasive, at the call sites:
`search/playbook_candidates.py:159`, `search/vector_search.py:227` and `:279`,
`workers/chunk_tasks.py:169`.

Fix 1 stops the queueing. Fix 2 stops connections sitting `idle in transaction` for seconds at a
time — which matters most in the workers, where 13 sequential embeddings share one transaction.

## Fix 3 — codify the database guards

The production fix was applied by hand:

```sql
ALTER DATABASE "AEProdSupport" SET idle_in_transaction_session_timeout = '60s';
```

Nothing in the codebase sets `statement_timeout` or `idle_in_transaction_session_timeout` (grepped
the whole package). Put them in the engine so a fresh environment inherits them:

```python
kwargs = {
    "echo": False,
    "pool_pre_ping": True,
    "pool_recycle": 1800,
    "connect_args": {
        "server_settings": {
            "idle_in_transaction_session_timeout": "60000",
            "statement_timeout": "30000",
        }
    },
}
```

Check `statement_timeout` against your slowest legitimate query before enabling it — long ingestion
queries may need a higher value or an exemption on the worker role.

---

## Order of work

1. **Fix 1** — one function, removes the blocking. Ship this first.
2. **Fix 3** — engine-level guards, so this cannot reach 16 minutes again anywhere.
3. **Fix 2** — the `released()` migration, worth doing properly in the workers.

Long term, the module's own docstring (`tenant_budget_service.py:63-66`) proposes a Redis `INCRBY`
counter instead of a database lock. That is the right destination — a rate-limit counter does not
belong in a transaction that also does the work being counted.

## How to verify

Before and after, under real traffic:

```sql
SELECT count(*) FILTER (WHERE query LIKE '%advisory%' AND state='active') AS waiting_on_lock,
       count(*) FILTER (WHERE state='idle in transaction')                AS idle_in_tx
FROM pg_stat_activity;
```

`waiting_on_lock` should stay at 0. Two concurrent chats on the same tenant should now return in
roughly the same time as one — before the fix, the second waited for the first.
