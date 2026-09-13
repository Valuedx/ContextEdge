"""Hold no database transaction across a call to somebody else's server.

The pipeline's shape was: open a transaction, make LLM and embedding calls
over the network, commit at the end. Measured on `main`: `extraction_tasks`
has 9 LLM and 8 embedding call sites, `pattern_tasks` 4 and 4, `chunk_tasks`
1 and 13 — all inside the single transaction `run_async` commits for them.

A transaction open across an external call holds its connection, holds its
locks, pins the snapshot vacuum needs to advance past, and lasts as long as
someone else's inference latency. A provider timeout or a circuit-breaker
pause becomes a database problem.

``released`` is the seam. It commits, which returns the connection to the
pool, awaits the external work with nothing held, and lets the next query
begin a fresh transaction. The ``Session.after_begin`` hook in
``tenant_rls`` re-applies the scope GUCs on that new transaction, so RLS
follows the session across the gap — that hook exists for exactly this.

**The tradeoff, stated plainly.** Committing early means partial work
survives a later failure. That is a real change and it is not free:

* It is *right* for the per-item tasks (normalize one evidence row, classify
  one item), where the unit of work is one row, retries are content-hash
  idempotent, and the alternative is redoing an LLM call you already paid
  for.
* It is *wrong* anywhere a set of rows must land together. Those callers
  must not use this; they should gather their inputs, close the transaction
  themselves, call out, and reopen — or accept holding it.

So this is a tool with a precondition, not a decorator to spread over the
pipeline. Each call site converted says in a comment why its work is
per-item.

A second, quieter benefit: the spend recorded by the previous LLM call is
committed before the next budget check reads it, so concurrent workers stop
reading each other's uncommitted usage as zero. See
``tenant_budget_service`` for the lock that makes the rest of that safe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


@asynccontextmanager
async def released(db: AsyncSession, *, reason: str):
    """Commit and drop the connection for the duration of the block.

    Usage::

        async with released(db, reason="llm.extract"):
            result = await llm_complete_json(...)

    On exit nothing is reopened eagerly: the next statement on ``db`` begins
    its own transaction, which is when the scope is re-bound. Failing to
    reopen here is deliberate — a block that does not touch the database
    afterwards should not pay for a transaction it never uses.
    """
    await db.commit()
    logger.debug("db.connection_released", reason=reason)
    try:
        yield
    finally:
        # Nothing to undo: the transaction is already closed. The `finally`
        # exists so a raising block still leaves the session in the same
        # state a succeeding one does — closed, not half-open.
        pass


async def call_without_transaction[T](
    db: AsyncSession,
    fn: Callable[[], Awaitable[T]],
    *,
    reason: str,
) -> T:
    """``released`` for the common case of a single awaitable.

    Exists so a call site becomes one line and reads as what it is, rather
    than a two-line block whose indentation is the only thing marking the
    boundary.
    """
    async with released(db, reason=reason):
        return await fn()
