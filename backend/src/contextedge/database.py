from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import contextedge.tenant_rls  # noqa: F401 — register Session.after_begin RLS rebind
from contextedge.config import settings


def _clear_rls_gucs(dbapi_connection) -> None:
    """Wipe every scope GUC on pool checkout and checkin.

    Both keys, not just the tenant. The MSP policy tests `app.msp_id` ALONE,
    so a connection returned to the pool still carrying an msp_id would admit
    that MSP's rows to whoever checks it out next — a cross-request leak that
    clearing only `app.tenant_id` does not close. `bypass_rls` is dead after
    `0097` and is cleared anyway: the downgrade path restores the policy that
    reads it, and a stale 'on' surviving a downgrade would disable isolation
    entirely.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT set_config('app.bypass_rls', 'off', false)")
        cursor.execute("SELECT set_config('app.msp_id', '', false)")
        cursor.execute("SELECT set_config('app.tenant_id', '', false)")
    finally:
        cursor.close()


def _reset_rls_gucs_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
    _clear_rls_gucs(dbapi_connection)


def _reset_rls_gucs_checkin(dbapi_connection, connection_record) -> None:
    _clear_rls_gucs(dbapi_connection)


def create_db_engine(use_null_pool: bool = False):
    """Create a new async engine. Use NullPool for worker tasks on Windows
    to avoid loop conflicts."""
    kwargs = {
        "echo": False,
        "pool_pre_ping": True,
        # Server-side guards, set here so every environment inherits them
        # rather than depending on an ALTER DATABASE someone ran by hand.
        #
        # idle_in_transaction_session_timeout: a request's transaction stays
        # open across provider calls, so a hung provider used to leave a
        # session idle-in-transaction indefinitely, holding whatever locks it
        # had. Observed live at 16+ minutes, blocking every other request for
        # the tenant. 60s is far above any legitimate idle gap.
        #
        # statement_timeout: bounds a single runaway query. Raise it, or unset
        # it for the worker role, if a legitimate ingestion query needs longer.
        "connect_args": {
            "server_settings": {
                "idle_in_transaction_session_timeout": "60000",
                "statement_timeout": "30000",
            }
        },
    }
    if use_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = 20
        kwargs["max_overflow"] = 10
        kwargs["pool_timeout"] = 30
        # Recycle connections every 30 minutes. Session-scoped advisory locks
        # belong to the connection, so if an unlock is ever missed, recycling
        # bounds how long a pooled connection can carry one.
        kwargs["pool_recycle"] = 1800

    engine = create_async_engine(settings.database_url, **kwargs)
    event.listen(engine.sync_engine, "checkout", _reset_rls_gucs_checkout)
    event.listen(engine.sync_engine, "checkin", _reset_rls_gucs_checkin)
    return engine

engine = create_db_engine()
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for obtaining an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            # Only commit if the session was actually used for mutations
            # This is a bit heuristic but safer than always committing
            if session.is_active:
                await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
