"""Run async coroutines with a short-lived DB session from Celery tasks."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.database import async_session_factory

T = TypeVar("T")


async def _with_session(
    fn: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    async with async_session_factory() as db:
        try:
            out = await fn(db)
            await db.commit()
            return out
        except Exception:
            await db.rollback()
            raise


def run_async(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Execute async work with commit/rollback semantics matching API `get_db`."""
    import asyncio

    return asyncio.run(_with_session(fn))
