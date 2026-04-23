"""Regression tests for the Batch 3 dedup uniqueness fixes (F-24, L-02).

The migration ``0026_dedup_uniqueness`` adds unique indexes that turn
previously-silent duplicate inserts into ``IntegrityError``. These
tests pin the service-level handlers that catch the exception and
fall through to the existing-row path, so the race is closed at both
the DB layer and the Python layer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# F-24: _get_or_create_contradiction handles IntegrityError on race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_contradiction_handles_race():
    """Two concurrent scanners both miss the SELECT and both try to
    INSERT. The second one hits the unique-index IntegrityError; the
    service must rollback, re-SELECT, and return the winning row with
    ``created=False``."""
    from contextedge.services.contradiction_service import _get_or_create_contradiction

    tenant_id = uuid4()

    # First execute: SELECT returns None (nothing there yet).
    # Second execute (inside except): SELECT returns the winning row.
    winning_row = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        source_a_ref="pb-1/step-0",
        source_b_ref="ev-42",
        description="old description",
        resolution_status="dismissed",
    )

    first_select = Mock()
    first_select.scalar_one_or_none.return_value = None
    second_select = Mock()
    second_select.scalar_one_or_none.return_value = winning_row
    # .scalar_one() path used after rollback
    second_select.scalar_one.return_value = winning_row

    execute_mock = AsyncMock(side_effect=[first_select, second_select])

    # First flush succeeds (the INSERT we build below). We want the
    # flush to raise IntegrityError on the new row, then succeed on
    # the post-rollback update flush.
    flush_call_count = {"n": 0}

    async def flush():
        flush_call_count["n"] += 1
        if flush_call_count["n"] == 1:
            raise IntegrityError("unique violation", None, Exception())

    db = SimpleNamespace(
        execute=execute_mock,
        add=lambda obj: None,
        flush=flush,
        rollback=AsyncMock(),
    )

    row, created = await _get_or_create_contradiction(
        db,
        tenant_id=tenant_id,
        source_a_ref="pb-1/step-0",
        source_b_ref="ev-42",
        description="new description",
    )

    assert row is winning_row
    assert created is False
    # Conflict path hit — rollback was called, and the existing row's
    # mutable fields were refreshed.
    db.rollback.assert_awaited_once()
    assert winning_row.description == "new description"
    assert winning_row.resolution_status == "unresolved"


@pytest.mark.asyncio
async def test_get_or_create_contradiction_fast_path_when_row_exists():
    """No conflict — existing row is found on the first SELECT; no
    INSERT attempt, no rollback."""
    from contextedge.services.contradiction_service import _get_or_create_contradiction

    tenant_id = uuid4()
    existing = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        source_a_ref="pb-1/step-0",
        source_b_ref="ev-42",
        description="prev",
        resolution_status="dismissed",
    )
    first_select = Mock()
    first_select.scalar_one_or_none.return_value = existing

    db = SimpleNamespace(
        execute=AsyncMock(return_value=first_select),
        add=lambda obj: None,
        flush=AsyncMock(),
        rollback=AsyncMock(),
    )

    row, created = await _get_or_create_contradiction(
        db, tenant_id=tenant_id, source_a_ref="pb-1/step-0",
        source_b_ref="ev-42", description="new",
    )

    assert row is existing
    assert created is False
    db.rollback.assert_not_awaited()
    assert existing.description == "new"
    assert existing.resolution_status == "unresolved"


# ---------------------------------------------------------------------------
# L-02: _normalize handles IntegrityError on concurrent dedupe race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_handles_dedup_race():
    """Two normalize workers both SELECT, both see no existing row,
    both INSERT. The second one's flush raises IntegrityError; the
    worker rolls back, re-fetches the winning row, and returns a
    ``raced=True`` dedup result without re-running the enrichment
    pipeline."""
    from contextedge.workers import extraction_tasks as et

    tenant_id = uuid4()
    raw_id = uuid4()
    raw = SimpleNamespace(
        id=raw_id,
        tenant_id=tenant_id,
        source_id=uuid4(),
        source_object_id=None,
    )
    winner = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        content_hash="h",
        embedding=[0.1] * 3072,
    )

    # Three execute calls:
    # 1. initial SELECT for existing → None (race window)
    # 2. after IntegrityError on flush → re-SELECT → winner
    r1 = Mock(); r1.scalar_one_or_none.return_value = None
    r2 = Mock(); r2.scalar_one.return_value = winner

    # Also: _normalize may do other db.execute calls earlier for
    # Domain lookup etc. We short-circuit by patching load_raw_payload
    # and evidence helpers so the flow reaches the INSERT quickly.
    execute_mock = AsyncMock(side_effect=[r1, r2])

    flush_count = {"n": 0}

    async def flush():
        flush_count["n"] += 1
        if flush_count["n"] == 1:
            raise IntegrityError("unique violation", None, Exception())

    db = SimpleNamespace(
        get=AsyncMock(return_value=raw),
        execute=execute_mock,
        add=lambda obj: None,
        flush=flush,
        rollback=AsyncMock(),
    )

    with (
        patch.object(et, "load_raw_payload", AsyncMock(return_value={"title": "T", "body": "B"})),
        patch.object(et, "evidence_title_from_payload", lambda p: "T"),
        patch.object(et, "evidence_body_from_payload", lambda p: "B"),
        patch.object(et, "evidence_content_hash_from_payload", lambda p: "h"),
        patch.object(et, "redact_evidence_fields", lambda title, body, enabled: (title, body, {})),
        patch.object(et, "redact", lambda text, enabled: (text, {})),
    ):
        result = await et._normalize(db, str(raw_id), tenant_id)

    assert result["deduped"] is True
    assert result["raced"] is True
    assert result["evidence_id"] == str(winner.id)
    # Rollback fired exactly once on the conflict path.
    db.rollback.assert_awaited_once()
