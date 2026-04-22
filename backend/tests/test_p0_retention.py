from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.retention_service import (
    DEFAULT_ARCHIVE_GRACE_DAYS,
    apply_retention_policy,
    purge_archived_evidence,
)


@pytest.mark.asyncio
async def test_retention_skips_legal_hold():
    tenant_id = uuid4()
    captured = {}
    active_item = SimpleNamespace(
        relevance_state="relevant",
        ingested_at=datetime.now(timezone.utc) - timedelta(days=45),
        evidence_type="message",
        canonical_entity_refs=None,
    )
    db_result = Mock()
    db_result.scalars.return_value.all.return_value = [active_item]

    async def execute(stmt):
        captured["stmt"] = stmt
        return db_result

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), flush=AsyncMock())

    archived = await apply_retention_policy(
        db,
        tenant_id=tenant_id,
        retention_days=30,
    )

    assert archived == 1
    assert active_item.relevance_state == "archived"
    assert any(value == "legal_hold" for value in captured["stmt"].compile().params.values())
    db.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# purge_archived_evidence — hard_delete + soft_purge + legal hold + dry_run
# ---------------------------------------------------------------------------


def _archived_item(age_days: int, legal_hold: bool = False):
    """Build an archived EvidenceItem stub with ``updated_at`` aged appropriately."""
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        relevance_state="archived",
        sensitivity_label="legal_hold" if legal_hold else None,
        updated_at=now - timedelta(days=age_days),
        embedding=[0.1] * 3072,
        body_text="original body",
        body_summary="original summary",
        title="original title",
    )


def _build_db(items_to_return: list):
    """Make a db mock that returns ``items_to_return`` from the purge query."""
    result = Mock()
    result.scalars.return_value.all.return_value = items_to_return
    return SimpleNamespace(
        execute=AsyncMock(return_value=result),
        flush=AsyncMock(),
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_purge_hard_delete_removes_candidates():
    tenant_id = uuid4()
    items = [_archived_item(age_days=45), _archived_item(age_days=90)]
    db = _build_db(items)

    result = await purge_archived_evidence(
        db, tenant_id=tenant_id, mode="hard_delete",
    )

    assert result["mode"] == "hard_delete"
    assert result["processed_count"] == 2
    assert result["candidate_count"] == 2
    assert result["dry_run"] is False
    # Each candidate should have been ``db.delete``-d.
    assert db.delete.await_count == 2
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_soft_purge_scrubs_content_but_keeps_row():
    """soft_purge NULLs embedding + body, replaces title, never calls delete."""
    tenant_id = uuid4()
    items = [_archived_item(age_days=45)]
    db = _build_db(items)

    result = await purge_archived_evidence(
        db, tenant_id=tenant_id, mode="soft_purge",
    )

    assert result["mode"] == "soft_purge"
    assert result["processed_count"] == 1
    # Content scrubbed
    item = items[0]
    assert item.embedding is None
    assert item.body_text is None
    assert item.body_summary is None
    assert item.title == "[purged]"
    # Never deleted
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_dry_run_does_not_mutate():
    """dry_run reports candidate count without deleting or scrubbing."""
    tenant_id = uuid4()
    items = [_archived_item(age_days=45), _archived_item(age_days=60)]
    db = _build_db(items)

    result = await purge_archived_evidence(
        db, tenant_id=tenant_id, mode="hard_delete", dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["candidate_count"] == 2
    assert result["processed_count"] == 0
    db.delete.assert_not_awaited()
    # flush should NOT be called on a pure dry_run
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_limit_reached_flag_set_correctly():
    """When the candidate list equals the limit, limit_reached=True to
    signal the caller that another tick will find more work."""
    tenant_id = uuid4()
    items = [_archived_item(age_days=45) for _ in range(5)]
    db = _build_db(items)

    result = await purge_archived_evidence(
        db, tenant_id=tenant_id, limit=5, dry_run=True,
    )

    assert result["limit_reached"] is True


@pytest.mark.asyncio
async def test_purge_empty_candidate_set_is_noop():
    tenant_id = uuid4()
    db = _build_db([])

    result = await purge_archived_evidence(db, tenant_id=tenant_id)

    assert result["candidate_count"] == 0
    assert result["processed_count"] == 0
    assert result["limit_reached"] is False
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode must be"):
        await purge_archived_evidence(
            SimpleNamespace(),
            tenant_id=uuid4(),
            mode="totally_wrong",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_purge_query_excludes_legal_hold_via_where_clause():
    """The SQL built by purge_archived_evidence must filter out legal-hold
    items — tested at the query level, not via row filtering."""
    tenant_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["stmt"] = stmt
        result = Mock()
        result.scalars.return_value.all.return_value = []
        return result

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        delete=AsyncMock(),
    )
    await purge_archived_evidence(db, tenant_id=tenant_id, mode="hard_delete")

    compiled = captured["stmt"].compile()
    param_values = list(compiled.params.values())
    assert "legal_hold" in param_values
    # Grace-day cutoff is present as a datetime param (exact value is
    # "now - 30 days" — we just check the type landed).
    assert any(isinstance(v, datetime) for v in param_values)


def test_default_grace_is_30_days():
    assert DEFAULT_ARCHIVE_GRACE_DAYS == 30
