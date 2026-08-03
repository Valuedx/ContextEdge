"""Run async coroutines with a short-lived DB session from Celery tasks."""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.database import async_sessionmaker, create_db_engine


async def _with_session[T](
    fn: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    # On Windows/Celery, we create a fresh NullPool engine for each task
    # to avoid the "Event loop is closed" issue during connection check-in.
    worker_engine = create_db_engine(use_null_pool=True)
    worker_session_factory = async_sessionmaker(worker_engine, expire_on_commit=False)

    async with worker_session_factory() as db:
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


def run_async[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Execute async work with commit/rollback semantics matching API `get_db`."""
    import asyncio
    return asyncio.run(_with_session(fn))
