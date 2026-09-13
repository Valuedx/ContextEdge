"""Run async coroutines with a DB session from Celery tasks.

**One event loop and one pooled engine per worker process.**

This module used to call ``asyncio.run()`` per task and build a fresh
``NullPool`` engine inside it, disposing the engine on the way out. The
comment blamed a Windows "Event loop is closed" error during connection
check-in — a real problem, but the cause was the loop, not the pool: an
asyncpg connection belongs to the loop that opened it, so a pool that
outlives ``asyncio.run()`` holds connections bound to a dead loop.

Fixing the loop lifetime fixes the pool. The process keeps one loop for its
lifetime, the engine is created once on that loop, and connections are
reused. On a 10,547-item corpus that is one engine instead of 10,547, and one
TCP + TLS + auth handshake per pooled connection instead of per task.

``Scope`` is unchanged: tasks still declare what they run as, and RLS still
applies to them.
"""

import asyncio
import atexit
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contextedge.tenant_rls import bind_session_scope

logger = structlog.get_logger()


@dataclass(frozen=True)
class Scope:
    """Who a task runs as.

    ``msp_id=None, tenant_id=None`` is the platform scope: admitted by
    ``ce_platform_all``/``ce_owner_all``. It is spelled out rather than
    defaulted so a task that forgot to declare a scope does not get the
    widest one by accident.
    """

    msp_id: UUID | None = None
    tenant_id: UUID | None = None

    @property
    def is_platform(self) -> bool:
        return self.msp_id is None and self.tenant_id is None


PLATFORM = Scope()


def scope_for_tenant(tenant_id: UUID, msp_id: UUID | None = None) -> Scope:
    """Scope a task to one client. ``msp_id`` is resolved if not supplied."""
    return Scope(msp_id=msp_id, tenant_id=tenant_id)


# --- per-process loop + engine ------------------------------------------------
# Guarded by a lock because Celery's threads/gevent pools can enter run_async
# concurrently within one process; prefork cannot, but the cost is one
# uncontended lock acquisition and the failure mode without it is two engines.
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_engine = None
_session_factory: async_sessionmaker | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
        return _loop


def _get_session_factory() -> async_sessionmaker:
    """The engine, created once on this process's loop.

    Built lazily rather than at import: the engine must be created on the
    loop that will use it, and at import time there is no loop yet.
    """
    global _engine, _session_factory
    with _lock:
        if _session_factory is None:
            from contextedge.database import create_db_engine

            _engine = create_db_engine()
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        return _session_factory


def _shutdown() -> None:
    """Dispose the pool before the loop goes away.

    Without this the interpreter exits with connections still checked out and
    asyncpg logs the "Event loop is closed" error this module was originally
    written to avoid — the same symptom, now at process exit instead of per
    task, and harmless rather than load-bearing.
    """
    global _engine, _session_factory, _loop
    with _lock:
        if _engine is not None and _loop is not None and not _loop.is_closed():
            try:
                _loop.run_until_complete(_engine.dispose())
            except Exception:  # noqa: BLE001 — best effort at process exit
                logger.warning("worker_engine_dispose_failed")
        _engine = None
        _session_factory = None
        if _loop is not None and not _loop.is_closed():
            _loop.close()
        _loop = None


atexit.register(_shutdown)

try:  # pragma: no cover - only meaningful inside a Celery worker
    from celery.signals import worker_process_shutdown

    @worker_process_shutdown.connect
    def _on_worker_process_shutdown(**_kwargs) -> None:
        _shutdown()
except Exception:  # noqa: BLE001 — importable outside Celery (tests, scripts)
    pass


async def _resolve_msp(db: AsyncSession, tenant_id: UUID) -> UUID | None:
    from contextedge.tenant_rls import resolve_msp_for_tenant

    return await resolve_msp_for_tenant(db, tenant_id)


async def _with_session[T](
    fn: Callable[[AsyncSession], Awaitable[T]],
    scope: Scope,
) -> T:
    factory = _get_session_factory()
    async with factory() as db:
        msp_id = scope.msp_id
        if scope.tenant_id is not None and msp_id is None:
            # Resolved before scoping: the lookup needs to see a tenant row
            # the client policy would not yet admit.
            msp_id = await _resolve_msp(db, scope.tenant_id)
        await bind_session_scope(db, msp_id=msp_id, tenant_id=scope.tenant_id)
        try:
            out = await fn(db)
            await db.commit()
            return out
        except Exception:
            await db.rollback()
            raise
        # No dispose here: the engine outlives the task now. The pool's
        # checkin hook clears the scope GUCs, so the next checkout starts
        # unscoped rather than inheriting this task's keys.


def run_async[T](
    fn: Callable[[AsyncSession], Awaitable[T]],
    scope: Scope = PLATFORM,
) -> T:
    """Execute async work with commit/rollback semantics matching API `get_db`.

    ``scope`` defaults to ``PLATFORM`` to keep existing call sites working
    while they are migrated one at a time. That default is the thing to remove
    once they are: a default-wide scope is the same mistake as the old bypass
    flag, only quieter.
    """
    return _get_loop().run_until_complete(_with_session(fn, scope))
