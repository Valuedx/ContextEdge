"""Celery tasks for the review-queue bundle cache.

`prefetch_review_context` runs on session creation (and could be invoked on
any mutation that invalidates the session's review state) to pre-warm the
Redis cache backing `GET /api/v1/review-queue/{session_id}/context`. This
is what turns the bundle endpoint into a sub-2s render on the reviewer
console — the round trip that happens when the engineer clicks a queue
item hits Redis, not Postgres.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis
import structlog

from contextedge.config import settings
from contextedge.services.review_queue_service import (
    build_review_context,
    write_cache,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def prefetch_review_context(self, tenant_id: str, session_id: str):
    """Build the review-queue bundle and SETEX it into Redis.

    Silently no-ops when the session does not exist (e.g. deleted between
    enqueue and run) so callers don't need to guard enqueuing.
    """

    async def work(db):
        tid = uuid.UUID(tenant_id)
        sid = uuid.UUID(session_id)

        bundle = await build_review_context(db, tenant_id=tid, session_id=sid)
        if bundle is None:
            logger.info(
                "review_queue.prefetch_skipped",
                tenant_id=tenant_id,
                session_id=session_id,
                reason="session_not_found",
            )
            return {"status": "skipped", "reason": "session_not_found"}

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await write_cache(
                redis, tenant_id=tid, session_id=sid, bundle=bundle,
            )
        finally:
            await redis.aclose()

        return {"status": "warmed", "session_id": session_id}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception(
            "review_queue.prefetch_failed",
            tenant_id=tenant_id,
            session_id=session_id,
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc
