"""Tests for M3 + C2: evidence baseline schema fields and the
compute_evidence_baseline service."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.models.evidence import EvidenceItem
from contextedge.services.evidence_baseline_service import (
    DEFAULT_WINDOW_DAYS,
    DELTA_SIGNALS,
    compute_evidence_baseline,
)


def _make_item(
    *,
    tenant_id,
    evidence_type: str = "incident",
    source_object_id=None,
    ingested_at: datetime | None = None,
    item_id=None,
    delta_signal: str | None = None,
) -> EvidenceItem:
    item = EvidenceItem(
        id=item_id or uuid4(),
        tenant_id=tenant_id,
        source_id=uuid4(),
        source_object_id=source_object_id,
        evidence_type=evidence_type,
        title=None,
        body_text=None,
        body_summary=None,
        relevance_state="unclassified",
        baseline_ref=None,
        delta_signal=delta_signal,
    )
    item.ingested_at = ingested_at or datetime.now(timezone.utc)
    return item


# =========================================================================
# Model + schema surface
# =========================================================================


def test_model_has_baseline_columns():
    cols = {c.name for c in EvidenceItem.__table__.columns}
    assert "baseline_ref" in cols
    assert "delta_signal" in cols


def test_evidence_schemas_surface_new_fields():
    from contextedge.schemas.evidence import EvidenceItemDetail, EvidenceItemResponse

    assert "delta_signal" in EvidenceItemResponse.model_fields
    assert "baseline_ref" in EvidenceItemDetail.model_fields
    assert "delta_signal" in EvidenceItemDetail.model_fields


def test_delta_signals_are_ui_color_levels():
    """The signals must match the Zone 4 color semantics — UI compares strings."""
    assert set(DELTA_SIGNALS) == {"neutral", "amber", "red"}


# =========================================================================
# compute_evidence_baseline — skip conditions
# =========================================================================


@pytest.mark.asyncio
async def test_returns_none_when_evidence_missing():
    db = SimpleNamespace(get=AsyncMock(return_value=None), flush=AsyncMock())
    result = await compute_evidence_baseline(
        db, tenant_id=uuid4(), evidence_id=uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_cross_tenant_access():
    """Evidence belonging to another tenant must be ignored — defense in depth."""
    tenant_id = uuid4()
    other_tenant = uuid4()
    item = _make_item(tenant_id=other_tenant, source_object_id=uuid4())
    db = SimpleNamespace(get=AsyncMock(return_value=item), flush=AsyncMock())

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )
    assert result is None


@pytest.mark.asyncio
async def test_skips_when_no_source_object_id():
    """No source_object_id → no stable dedup key → skip."""
    tenant_id = uuid4()
    item = _make_item(tenant_id=tenant_id, source_object_id=None)
    db = SimpleNamespace(get=AsyncMock(return_value=item), flush=AsyncMock())

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )
    assert result is None
    assert item.baseline_ref is None
    assert item.delta_signal is None


# =========================================================================
# compute_evidence_baseline — first observation path
# =========================================================================


@pytest.mark.asyncio
async def test_first_seen_when_no_prior_in_window():
    tenant_id = uuid4()
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=uuid4(),
        evidence_type="intune_device_snapshot",
    )

    class _ExecResult:
        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=AsyncMock(return_value=_ExecResult()),
        flush=AsyncMock(),
    )

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )

    assert result is not None
    assert result["first_seen_in_window"] is True
    assert result["window_days"] == DEFAULT_WINDOW_DAYS
    assert "first observation" in result["comparison_label"]
    assert item.baseline_ref == result
    assert item.delta_signal == "neutral"


# =========================================================================
# compute_evidence_baseline — prior-found path with label variants
# =========================================================================


@pytest.mark.asyncio
async def test_prior_found_records_days_ago_label():
    tenant_id = uuid4()
    source_obj = uuid4()
    now = datetime.now(timezone.utc)

    prior = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        evidence_type="intune_device_snapshot",
        ingested_at=now - timedelta(days=3, hours=2),
    )
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        evidence_type="intune_device_snapshot",
        ingested_at=now,
    )

    class _ExecResult:
        def scalar_one_or_none(self):
            return prior

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=AsyncMock(return_value=_ExecResult()),
        flush=AsyncMock(),
    )

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )

    assert result is not None
    assert result["first_seen_in_window"] is False
    assert result["prior_evidence_id"] == str(prior.id)
    assert result["days_since_prior"] == 3
    assert "3 days ago" in result["comparison_label"]
    assert item.baseline_ref == result
    assert item.delta_signal == "neutral"


@pytest.mark.asyncio
async def test_prior_found_uses_singular_day_for_exactly_one_day():
    tenant_id = uuid4()
    source_obj = uuid4()
    now = datetime.now(timezone.utc)

    prior = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        ingested_at=now - timedelta(days=1, minutes=5),
    )
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        ingested_at=now,
    )

    class _ExecResult:
        def scalar_one_or_none(self):
            return prior

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=AsyncMock(return_value=_ExecResult()),
        flush=AsyncMock(),
    )

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )
    assert "1 day ago" in result["comparison_label"]
    # Guard against the "1 days ago" bug.
    assert "1 days" not in result["comparison_label"]


@pytest.mark.asyncio
async def test_prior_found_hours_label_when_under_a_day():
    tenant_id = uuid4()
    source_obj = uuid4()
    now = datetime.now(timezone.utc)

    prior = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        ingested_at=now - timedelta(hours=4),
    )
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        ingested_at=now,
    )

    class _ExecResult:
        def scalar_one_or_none(self):
            return prior

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=AsyncMock(return_value=_ExecResult()),
        flush=AsyncMock(),
    )

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )
    assert result["days_since_prior"] == 0
    assert "4 hours ago" in result["comparison_label"]


@pytest.mark.asyncio
async def test_existing_delta_signal_not_overwritten():
    """If a connector stamped an amber/red signal at ingest, the generic
    worker must not downgrade it to neutral."""
    tenant_id = uuid4()
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=uuid4(),
        delta_signal="red",
    )

    class _ExecResult:
        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=AsyncMock(return_value=_ExecResult()),
        flush=AsyncMock(),
    )

    await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id,
    )

    # Connector-stamped signal preserved; baseline_ref still populated.
    assert item.delta_signal == "red"
    assert item.baseline_ref is not None


# =========================================================================
# compute_evidence_baseline — window enforcement in the SQL statement
# =========================================================================


@pytest.mark.asyncio
async def test_window_filter_is_present_in_query():
    """Verifies the generated statement filters by ingested_at ≥ window_start,
    so prior evidence older than the window is not considered."""
    tenant_id = uuid4()
    source_obj = uuid4()
    now = datetime.now(timezone.utc)
    item = _make_item(
        tenant_id=tenant_id,
        source_object_id=source_obj,
        ingested_at=now,
    )

    captured = {}

    class _ExecResult:
        def scalar_one_or_none(self):
            return None

    async def _execute(stmt):
        captured["stmt"] = str(stmt)
        return _ExecResult()

    db = SimpleNamespace(
        get=AsyncMock(return_value=item),
        execute=_execute,
        flush=AsyncMock(),
    )

    result = await compute_evidence_baseline(
        db, tenant_id=tenant_id, evidence_id=item.id, window_days=2,
    )
    assert result is not None
    sql = captured["stmt"].lower()
    # SQLAlchemy renders BETWEEN as two comparisons; verify both bounds present.
    assert "ingested_at" in sql
    assert ">=" in sql or ">" in sql
    assert "<=" in sql or "<" in sql
    assert result["window_days"] == 2
