"""Change-risk assessment (Phase 4): deterministic, explainable scoring."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.change_risk_service import (
    _score,
    assess_change_risk,
)

GW_SYS_ID = "1" * 32


def _entity(**kw):
    return SimpleNamespace(
        id=kw.get("id", uuid4()),
        name=kw.get("name", "vpn-gw-east-01"),
        external_id=GW_SYS_ID,
        attributes={"ci_class": "cmdb_ci_netgear"},
        last_synced_at=kw.get("last_synced_at"),
        is_active=kw.get("is_active", True),
    )


# --- scoring ----------------------------------------------------------------


def test_score_no_history_is_low_with_explanation():
    level, factors = _score(
        changes=0, blamed_changes=0, incidents=0, alert_days=0, dependents=0
    )
    assert level == "low"
    assert factors == ["no adverse history for this CI in the window"]


def test_score_high_blame_rate_scores_double():
    level, factors = _score(
        changes=6, blamed_changes=3, incidents=0, alert_days=0, dependents=6
    )
    # rate 0.5 (+2) + dependents (+1) = 3 → high
    assert level == "high"
    assert any("3 of 6 changes" in f for f in factors)
    assert any("dependents" in f for f in factors)


def test_score_low_blame_rate_scores_single():
    level, factors = _score(
        changes=20, blamed_changes=1, incidents=0, alert_days=0, dependents=0
    )
    assert level == "medium"  # one weak signal
    assert any("1 of 20 changes" in f for f in factors)


def test_score_noisy_ci_and_alerts_accumulate():
    level, factors = _score(
        changes=0, blamed_changes=0, incidents=7, alert_days=3, dependents=5
    )
    # incidents (+1) + alerts (+1) + dependents (+1) = 3 → high
    assert level == "high"
    assert len(factors) == 3


# --- assessment -------------------------------------------------------------


def _rows_result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


@pytest.mark.asyncio
async def test_assessment_composes_history_into_profile():
    tenant_id = uuid4()
    entity = _entity(last_synced_at=datetime(2026, 7, 30, tzinfo=UTC))

    change_ev_a, change_ev_b = uuid4(), uuid4()
    dependent = SimpleNamespace(name="radius-prod-01")

    evidence_rows = [
        (change_ev_a, "change_request:" + "c" * 32),
        (change_ev_b, "change_request:" + "d" * 32),
        (uuid4(), "incident:" + "e" * 32),
        (uuid4(), "incident:" + "e" * 32),  # second version, same record
        (uuid4(), f"em_alert_rollup:{GW_SYS_ID}:2026-07-30"),
        (uuid4(), f"em_alert_rollup:{GW_SYS_ID}:2026-07-31"),
    ]
    dependent_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _rows_result(evidence_rows),
                _scalars_result([change_ev_a]),  # blamed change evidence
                _scalars_result([dependent_id]),
                _scalars_result([dependent]),
            ]
        )
    )

    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=entity),
    ):
        result = await assess_change_risk(db, tenant_id, "vpn-gw-east-01")

    assert result["changes_on_ci"] == 2
    assert result["incident_causing_changes"] == 1
    assert result["change_incident_rate"] == 0.5
    assert result["incidents_on_ci"] == 1  # two versions, one record
    assert result["alert_activity_days"] == 2
    assert result["dependents_cached"] == 1
    assert result["dependent_names"] == ["radius-prod-01"]
    assert result["risk_level"] == "high"  # rate 0.5 (+2) + alerts (+1)
    assert "2026-07-30" in result["topology_note"]


@pytest.mark.asyncio
async def test_assessment_without_changes_skips_blame_query():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _rows_result([]),  # no evidence on CI
                _scalars_result([]),  # dependents
            ]
        )
    )
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=_entity()),
    ):
        result = await assess_change_risk(db, tenant_id, GW_SYS_ID)

    assert result["risk_level"] == "low"
    assert result["change_incident_rate"] is None
    assert db.execute.await_count == 2  # blame query short-circuited
    assert "never fetched" in result["topology_note"]


@pytest.mark.asyncio
async def test_assessment_errors_for_unknown_or_empty_ci():
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=None),
    ):
        unknown = await assess_change_risk(SimpleNamespace(), uuid4(), "no-such-host")
    assert unknown["error"]["code"] == "unknown_ci"

    empty = await assess_change_risk(SimpleNamespace(), uuid4(), "   ")
    assert empty["error"]["code"] == "invalid_ci"


@pytest.mark.asyncio
async def test_assessment_clamps_window():
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_rows_result([]), _scalars_result([])])
    )
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=_entity()),
    ):
        result = await assess_change_risk(db, uuid4(), GW_SYS_ID, window_days=99999)
    assert result["window_days"] == 730


# --- MAF tool ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_maf_tool_passthrough_and_error_fencing():
    pytest.importorskip("agent_framework")
    from contextedge.integrations.maf.tools import ChangeRiskTools

    calls = []

    class _Client:
        async def assess(self, ci, window_days):
            calls.append((ci, window_days))
            if ci == "boom":
                raise RuntimeError("db exploded at /etc/secrets")
            return {"risk_level": "low", "factors": []}

    toolset = ChangeRiskTools(_Client())
    ok = await toolset.assess_change_risk.invoke(
        arguments={"ci": "vpn-gw-east-01", "window_days": 90}, skip_parsing=True
    )
    assert ok["risk_level"] == "low"
    assert calls[0] == ("vpn-gw-east-01", 90)

    err = await toolset.assess_change_risk.invoke(
        arguments={"ci": "boom"}, skip_parsing=True
    )
    assert err["error"]["code"] == "risk_assessment_unavailable"
    assert "secrets" not in str(err)

    empty = await toolset.assess_change_risk.invoke(
        arguments={"ci": " "}, skip_parsing=True
    )
    assert empty["error"]["code"] == "invalid_ci"


@pytest.mark.asyncio
async def test_assessment_survives_garbage_window():
    """Programmatic callers bypass API/tool validation — the service must
    not crash on a None window."""
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_rows_result([]), _scalars_result([])])
    )
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=_entity()),
    ):
        result = await assess_change_risk(db, uuid4(), GW_SYS_ID, window_days=None)
    assert result["window_days"] == 180


@pytest.mark.asyncio
async def test_dependent_query_excludes_retired_entities():
    """Retired CIs must not inflate the blast radius."""
    from contextedge.services.change_risk_service import _cached_dependents

    captured = []

    async def execute(stmt):
        captured.append(str(stmt))
        return _scalars_result([uuid4()]) if len(captured) == 1 else _scalars_result([])

    db = SimpleNamespace(execute=execute)
    await _cached_dependents(db, uuid4(), uuid4())
    assert "is_active" in captured[1]


@pytest.mark.asyncio
async def test_retired_ci_is_assessed_but_flagged():
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_rows_result([]), _scalars_result([])])
    )
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=_entity(is_active=False)),
    ):
        result = await assess_change_risk(db, uuid4(), GW_SYS_ID)
    assert result["ci"]["active"] is False
    assert result["risk_level"] == "low"  # history still assessed
