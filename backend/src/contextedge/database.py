from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from contextedge.config import settings
import contextedge.tenant_rls  # noqa: F401 — register Session.after_begin RLS rebind


def _clear_rls_gucs(dbapi_connection) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT set_config('app.bypass_rls', 'off', false)")
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
    }
    if use_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = 20
        kwargs["max_overflow"] = 10
        kwargs["pool_timeout"] = 30

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
