"""Run async coroutines with a short-lived DB session from Celery tasks."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.database import async_session_factory

T = TypeVar("T")


from contextedge.database import create_db_engine, async_sessionmaker

async def _with_session(
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


import asyncio
import sys

def run_async(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Execute async work with commit/rollback semantics matching API `get_db`.
    
    On Windows, this uses a custom loop management strategy to avoid the common
    'Event loop is closed' crash during proactor teardown.
    """
    if sys.platform != "win32":
        return asyncio.run(_with_session(fn))

    # Windows-specific resilient runner
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_with_session(fn))
    finally:
        try:
            # Cancel all pending tasks
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                # Give tasks a moment to respond to cancellation
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        
        loop.close()
        asyncio.set_event_loop(None)
