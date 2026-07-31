"""em_alert rollup ingestion (Phase 3): per-(CI, day) aggregation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.connectors.servicenow.alert_rollup import (
    INCIDENT_REFS_CAP,
    SAMPLE_LINES_CAP,
    rollup_alert_events,
)
from contextedge.connectors.servicenow.connector import ServiceNowConnector

GW_CI = "1" * 32
DB_CI = "2" * 32
INC_A = "a" * 32
INC_B = "b" * 32


def _alert(**kw):
    return {
        "sys_id": kw.get("sys_id", uuid4().hex),
        "number": kw.get("number", "Alert0010045"),
        "severity": kw.get("severity", "1"),
        "short_description": kw.get(
            "short_description", "RADIUS authentication timeout"
        ),
        "cmdb_ci": {"value": kw.get("ci", GW_CI)} if kw.get("ci", GW_CI) else "",
        "cmdb_ci.name": kw.get("ci_name", "vpn-gw-east-01"),
        "incident": {"value": kw["incident"]} if kw.get("incident") else "",
        "initial_event_time": kw.get("initial", "2026-07-31 08:05:00"),
        "last_event_time": kw.get("last", "2026-07-31 08:45:00"),
        "sys_updated_on": kw.get("updated", "2026-07-31 09:00:00"),
    }


# --- grouping ---------------------------------------------------------------


def test_rollup_groups_by_ci_and_day():
    events = rollup_alert_events(
        [
            _alert(),
            _alert(updated="2026-07-31 10:00:00"),
            _alert(ci=DB_CI, ci_name="orders-db-01", updated="2026-07-31 11:00:00"),
            _alert(updated="2026-08-01 00:10:00"),
        ]
    )
    keys = [e.external_id for e in events]
    assert keys == [
        f"em_alert_rollup:{GW_CI}:2026-07-31",
        f"em_alert_rollup:{GW_CI}:2026-08-01",
        f"em_alert_rollup:{DB_CI}:2026-07-31",
    ]
    gateway_day = events[0]
    assert gateway_day.content["alert_count"] == 2
    assert gateway_day.thread_id == gateway_day.external_id
    assert gateway_day.timestamp.hour == 10  # max sys_updated_on in group


def test_rollup_unassigned_ci_bucket_has_no_ci_reference():
    (event,) = rollup_alert_events([_alert(ci="", ci_name="")])
    assert event.external_id == "em_alert_rollup:unassigned:2026-07-31"
    assert "cmdb_ci" not in event.content
    assert "unassigned CIs" in event.content["short_description"]


def test_rollup_title_and_samples_carry_symptom_vocabulary():
    (event,) = rollup_alert_events(
        [_alert(severity="2"), _alert(severity="1", number="Alert0010046")]
    )
    assert (
        event.content["short_description"]
        == "Alert activity on vpn-gw-east-01 (2026-07-31): 2 alerts, worst critical"
    )
    assert "RADIUS authentication timeout" in event.content["description"]
    assert event.content["severity_counts"] == {"1": 1, "2": 1}
    assert event.content["worst_severity"] == 1
    assert event.content["first_event_time"] == "2026-07-31 08:05:00"


def test_rollup_bounds_samples_but_counts_everything():
    events = rollup_alert_events([_alert(sys_id=uuid4().hex) for _ in range(50)])
    (event,) = events
    assert event.content["alert_count"] == 50
    assert len(event.content["description"].splitlines()) == SAMPLE_LINES_CAP
    assert len(event.content["alert_numbers"]) <= SAMPLE_LINES_CAP


def test_rollup_dedupes_and_caps_incident_references():
    alerts = [_alert(incident=INC_A), _alert(incident=INC_A), _alert(incident=INC_B)]
    alerts += [_alert(incident=uuid4().hex) for _ in range(30)]
    (event,) = rollup_alert_events(alerts)
    refs = event.content["alert_incidents"]
    assert refs[:2] == [INC_A, INC_B]
    assert len(refs) == INCIDENT_REFS_CAP


def test_rollup_ignores_non_numeric_severity():
    (event,) = rollup_alert_events([_alert(severity=""), _alert(severity="3")])
    assert event.content["worst_severity"] == 3
    assert event.content["severity_counts"] == {"3": 1}


# --- connector wiring -------------------------------------------------------


def _connector(config=None):
    return ServiceNowConnector(
        config or {},
        {"instance_url": "https://acme.service-now.com", "username": "u", "password": "p"},
    )


@pytest.mark.asyncio
async def test_fetch_changes_rolls_up_alerts_and_filters_severity():
    from contextedge.connectors.base import Checkpoint

    connector = _connector({"alert_severity_max": 2})
    captured_queries = []

    async def snow_get(path, params=None):
        captured_queries.append(params["sysparm_query"])
        return {"result": [_alert(), _alert(updated="2026-07-31 09:30:00")]}

    with patch.object(connector, "_snow_get", side_effect=snow_get):
        result = await connector.fetch_changes(
            "em_alert", "servicenow_table", Checkpoint(data={})
        )

    # One rollup event, not one per alert.
    assert len(result.events) == 1
    assert result.events[0].object_type == "em_alert_rollup"
    # Severity filter present in BOTH ^NQ branches.
    query = captured_queries[0]
    first_branch, second_branch = query.split("^NQ")
    assert "^severity<=2" in first_branch
    assert "^severity<=2" in second_branch
    # Checkpoint advances on the RAW alert rows, not the rollup events.
    assert result.new_checkpoint.data["last_updated"] == "2026-07-31 09:30:00"


@pytest.mark.asyncio
async def test_fetch_changes_severity_config_falls_back_when_invalid():
    connector = _connector({"alert_severity_max": "everything"})
    assert connector._table_extra_query("em_alert") == "^severity<=3"
    assert connector._table_extra_query("incident") == ""


@pytest.mark.asyncio
async def test_hydrate_thread_rollup_guard_skips_api():
    connector = _connector()
    with patch.object(connector, "_snow_get", AsyncMock()) as snow:
        thread = await connector.hydrate_thread(
            f"em_alert_rollup:{GW_CI}:2026-07-31"
        )
    assert thread.messages == []
    assert thread.metadata == {"rollup": True}
    snow.assert_not_awaited()


# --- reference enrichment ---------------------------------------------------


def test_alert_incident_refs_validated_and_never_case_link_keys():
    from contextedge.services.correlation_service import extract_case_link_candidates
    from contextedge.services.servicenow_reference_service import (
        extract_alert_incident_references,
    )

    payload = {
        "record_type": "em_alert_rollup",
        "alert_incidents": [INC_A, "not-a-sys-id", INC_A, INC_B],
        "cmdb_ci": {"value": GW_CI},
    }
    assert extract_alert_incident_references(payload) == [INC_A, INC_B]
    assert extract_alert_incident_references({"alert_incidents": "junk"}) == []

    candidates = extract_case_link_candidates(
        source_type="servicenow",
        raw_object=SimpleNamespace(external_id=f"em_alert_rollup:{GW_CI}:2026-07-31"),
        raw_payload=payload,
    )
    assert ("servicenow", INC_A) not in candidates  # mass-merge guard
    assert ("servicenow", GW_CI) not in candidates


@pytest.mark.asyncio
async def test_rollup_processing_creates_preceded_incident_edges():
    from contextedge.services.servicenow_reference_service import (
        process_servicenow_references,
    )

    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None, source_id=uuid4())
    incident_evidence_id = uuid4()

    async def resolve(db, tid, sys_id):
        return incident_evidence_id if sys_id == INC_A else None

    with (
        patch(
            "contextedge.services.servicenow_reference_service._resolve_evidence_for_sys_id",
            side_effect=resolve,
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        counts = await process_servicenow_references(
            SimpleNamespace(),
            tenant_id,
            evidence,
            {"alert_incidents": [INC_A, INC_B]},
        )

    assert counts["alert_incident_edges"] == 1
    assert counts["unresolved_refs"] == 1
    args = edge_mock.await_args_list[0].args
    assert args[2:7] == (
        "evidence",
        evidence.id,
        "evidence",
        incident_evidence_id,
        "preceded_incident",
    )


@pytest.mark.asyncio
async def test_reverse_heal_skips_synthetic_external_ids():
    from contextedge.services.servicenow_reference_service import (
        heal_reverse_references,
    )

    db = SimpleNamespace(execute=AsyncMock())
    healed = await heal_reverse_references(
        db, uuid4(), SimpleNamespace(id=uuid4()), f"em_alert_rollup:{GW_CI}:2026-07-31"
    )
    assert healed == 0
    db.execute.assert_not_awaited()


def test_rollup_event_window_uses_both_time_fields():
    """The window closes at the latest LAST event time — not the latest
    initial time (pass-1 regression guard)."""
    (event,) = rollup_alert_events(
        [
            _alert(initial="2026-07-31 08:05:00", last="2026-07-31 08:45:00"),
            _alert(initial="2026-07-31 07:50:00", last="2026-07-31 09:20:00"),
        ]
    )
    assert event.content["first_event_time"] == "2026-07-31 07:50:00"
    assert event.content["last_event_time"] == "2026-07-31 09:20:00"


def test_alert_incident_refs_junk_does_not_mask_valid_refs_beyond_cap():
    from contextedge.services.servicenow_reference_service import (
        MAX_ALERT_INCIDENT_REFS,
        extract_alert_incident_references,
    )

    junk_padded = ["junk"] * MAX_ALERT_INCIDENT_REFS + [INC_A]
    assert extract_alert_incident_references({"alert_incidents": junk_padded}) == [INC_A]
