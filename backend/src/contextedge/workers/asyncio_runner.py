"""Run async coroutines with a short-lived DB session from Celery tasks.

**Scope is now required.** This runner used to bind ``bypass=True``
unconditionally, so every Celery task ran with row-level security switched
off — 25 of 27 worker modules across 43 call sites, which is the half of the
system that does the most cross-tenant data movement (SupportFlo D28). A
task now declares the scope it needs and the policies apply to it like any
other caller.

``run_async`` keeps its signature for tasks that genuinely span tenants — a
retention sweep, a scheduler poll. Those pass ``scope=PLATFORM`` explicitly,
which connects as the owner rather than silently disabling a control: the
difference is that a reader can grep for the tasks that cross the boundary
and there are few enough to audit.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.database import async_sessionmaker, create_db_engine
from contextedge.tenant_rls import bind_session_scope


@dataclass(frozen=True)
class Scope:
    """Who a task runs as.

    ``msp_id=None, tenant_id=None`` is the platform scope: the owner
    connection, admitted by ``ce_owner_all``. It is spelled out rather than
    defaulted so that a task which forgot to declare a scope does not get
    the widest one.
    """

    msp_id: UUID | None = None
    tenant_id: UUID | None = None

    @property
    def is_platform(self) -> bool:
        return self.msp_id is None and self.tenant_id is None


PLATFORM = Scope()


def scope_for_tenant(tenant_id: UUID, msp_id: UUID | None = None) -> Scope:
    """Scope a task to one client.

    ``msp_id`` is optional only because callers holding just a tenant id are
    common; ``_resolve_msp`` fills it in from the tenant row so the client
    predicate (which needs both keys) can be satisfied.
    """
    return Scope(msp_id=msp_id, tenant_id=tenant_id)


async def _resolve_msp(db: AsyncSession, tenant_id: UUID) -> UUID | None:
    from sqlalchemy import select

    from contextedge.models.tenant import Tenant

    row = await db.execute(select(Tenant.msp_id).where(Tenant.id == tenant_id))
    return row.scalar_one_or_none()


async def _with_session[T](
    fn: Callable[[AsyncSession], Awaitable[T]],
    scope: Scope,
) -> T:
    # On Windows/Celery, we create a fresh NullPool engine for each task
    # to avoid the "Event loop is closed" issue during connection check-in.
    # KNOWN COST: this is an engine, a TCP connect and a teardown per task.
    # It is a dev-machine workaround that became the architecture and is
    # tracked for the follow-up branch; it is left alone here so the
    # isolation change can be reviewed on its own.
    worker_engine = create_db_engine(use_null_pool=True)
    worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

    async with worker_session_factory() as db:
        msp_id = scope.msp_id
        if scope.tenant_id is not None and msp_id is None:
            # Resolved before scoping, on the owner connection, because the
            # lookup itself needs to see a tenant row the client policy
            # would not yet admit.
            msp_id = await _resolve_msp(db, scope.tenant_id)
        await bind_session_scope(db, msp_id=msp_id, tenant_id=scope.tenant_id)
        try:
            out = await fn(db)
            await db.commit()
            return out
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
            await worker_engine.dispose()


def run_async[T](
    fn: Callable[[AsyncSession], Awaitable[T]],
    scope: Scope = PLATFORM,
) -> T:
    """Execute async work with commit/rollback semantics matching API `get_db`.

    ``scope`` defaults to ``PLATFORM`` to keep the 43 existing call sites
    working while they are migrated one at a time. That default is the thing
    to remove once they are: a default-wide scope is the same mistake as the
    bypass flag, just quieter. Each migrated task names its scope and the
    count of un-migrated ones is asserted by
    ``test_worker_scope_migration_progress``.
    """
    import asyncio
    return asyncio.run(_with_session(fn, scope))
