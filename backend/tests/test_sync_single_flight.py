"""E4 sync single-flight: advisory lock per source object."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.sync_worker_service import (
    acquire_sync_lock,
    run_backfill_job,
    run_incremental_job,
)


def _lock_db(acquired: bool):
    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "pg_try_advisory_xact_lock" in text:
            result.scalar_one.return_value = acquired
            return result
        raise AssertionError("nothing else should run when lock denied")

    return SimpleNamespace(execute=execute)


@pytest.mark.asyncio
async def test_lock_acquisition_roundtrip():
    assert await acquire_sync_lock(_lock_db(True), uuid4()) is True
    assert await acquire_sync_lock(_lock_db(False), uuid4()) is False


@pytest.mark.asyncio
async def test_backfill_skips_when_locked():
    out = await run_backfill_job(_lock_db(False), uuid4(), uuid4(), uuid4())
    assert out == {"status": "skipped_locked"}


@pytest.mark.asyncio
async def test_incremental_skips_when_locked():
    out = await run_incremental_job(_lock_db(False), uuid4(), uuid4(), uuid4())
    assert out == {"status": "skipped_locked"}
