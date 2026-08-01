"""SapphireIMS connector (config-mapped contract) + reference enrichment."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.connectors.base import Checkpoint
from contextedge.connectors.sapphireims.connector import (
    SapphireIMSConnector,
    parse_sapphire_datetime,
)
from contextedge.services.sapphireims_reference_service import (
    extract_entity_references,
    extract_ticket_references,
    process_sapphireims_references,
)


def _connector(config=None, credentials=None):
    return SapphireIMSConnector(
        config or {},
        {
            "base_url": "https://itsm.acme.example",
            "api_key": "k",
            "auth_token": "t",
            **(credentials or {}),
        },
    )


def _ticket(**kw):
    return {
        "ticket_id": kw.get("ticket_id", "INC-4021"),
        "subject": kw.get("subject", "Users cannot log in to the VPN"),
        "description": "RADIUS timeouts reported by the field team",
        "ticket_type": kw.get("ticket_type", "Incident"),
        "status": "Open",
        "priority": "P1",
        "modified_time": kw.get("modified_time", "2026-08-01 12:00:00"),
        "service_name": "VPN Service",
        "asset_name": "vpn-gw-east-01",
        "related_tickets": kw.get("related_tickets", ["CHG-880", "PRB-12"]),
    }


# --- connector mapping ------------------------------------------------------


def test_headers_use_documented_auth_model_with_overrides():
    default = _connector()._headers()
    assert default["apikey"] == "k"
    assert default["authtoken"] == "t"

    custom = _connector(
        credentials={
            "api_key_header": "X-Api-Key",
            "auth_token_header": "X-Auth-Token",
            "submitted_by": "contextedge",
        }
    )._headers()
    assert custom["X-Api-Key"] == "k"
    assert custom["X-Auth-Token"] == "t"
    assert custom["submittedBy"] == "contextedge"


def test_items_extraction_tolerates_common_shapes():
    connector = _connector()
    assert connector._items({"data": [{"a": 1}, "junk"]}) == [{"a": 1}]
    assert connector._items({"records": [{"b": 2}]}) == [{"b": 2}]
    assert connector._items([{"c": 3}]) == [{"c": 3}]
    assert connector._items({"unrelated": 1}) == []
    custom = _connector({"api": {"items_key": "ticketList"}})
    assert custom._items({"ticketList": [{"d": 4}]}) == [{"d": 4}]


def test_ticket_event_normalizes_content_and_kind_prefix():
    event = _connector()._ticket_event(_ticket(), "ACME-IT")
    assert event.external_id == "INC-4021"
    assert event.thread_id == "incident:INC-4021"
    assert event.content["record_kind"] == "incident"
    assert event.content["summary"] == "Users cannot log in to the VPN"
    assert event.content["ci_name"] == "vpn-gw-east-01"
    assert event.content["related_tickets"] == ["CHG-880", "PRB-12"]
    assert event.timestamp.hour == 12

    # Instance-specific field names remap through config.
    remapped = _connector(
        {"fields": {"id": "TicketNo", "type": "Module"}}
    )._ticket_event({"TicketNo": 991, "Module": "Change"}, "ACME-IT")
    assert remapped.external_id == "991"
    assert remapped.thread_id == "change_request:991"

    assert _connector()._ticket_event({"subject": "no id"}, "P") is None


def test_related_tickets_accept_list_string_and_scalar():
    connector = _connector()
    as_string = connector._ticket_event(
        _ticket(related_tickets="CHG-880, PRB-12"), "P"
    )
    assert as_string.content["related_tickets"] == ["CHG-880", "PRB-12"]
    as_scalar = connector._ticket_event(_ticket(related_tickets=4021), "P")
    assert as_scalar.content["related_tickets"] == ["4021"]


def test_datetime_parsing_tolerates_instance_formats():
    assert parse_sapphire_datetime("2026-08-01T12:00:00Z").hour == 12
    assert parse_sapphire_datetime("2026-08-01 12:00:00").hour == 12
    assert parse_sapphire_datetime(1785585600).year == 2026  # epoch seconds
    assert parse_sapphire_datetime(1785585600000).year == 2026  # epoch millis
    assert parse_sapphire_datetime("someday") is None
    assert parse_sapphire_datetime(None) is None


@pytest.mark.asyncio
async def test_fetch_changes_paginates_with_configured_params():
    connector = _connector({"api": {"updated_since_param": "since"}})
    pages = [
        {"data": [_ticket(ticket_id=f"INC-{i}") for i in range(100)]},
        {"data": [_ticket(ticket_id="INC-999", modified_time="2026-08-01 13:00:00")]},
    ]
    calls = []

    async def get(path, params=None):
        calls.append(params)
        return pages[len(calls) - 1]

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "ACME-IT", "sapphireims_project", Checkpoint(data={"last_updated": "2026-08-01 09:00:00"})
        )

    assert len(result.events) == 101
    assert calls[0]["since"] == "2026-08-01 09:00:00"
    assert calls[0]["project"] == "ACME-IT"
    assert calls[1]["page"] == "2"
    assert result.new_checkpoint.data["last_updated"] == "2026-08-01 13:00:00"


@pytest.mark.asyncio
async def test_retry_handles_429_with_retry_after():
    connector = _connector()
    attempts = []

    class _Resp:
        def __init__(self, status, body=None):
            self.status_code = status
            self.headers = {"Retry-After": "0"} if status == 429 else {}
            self._body = body or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    async def fake_get(url, headers=None, params=None):
        attempts.append(url)
        return _Resp(429) if len(attempts) == 1 else _Resp(200, {"ok": True})

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(get=fake_get)

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "contextedge.connectors.sapphireims.connector.httpx.AsyncClient",
            return_value=_Client(),
        ),
        patch(
            "contextedge.connectors.sapphireims.connector.asyncio.sleep", AsyncMock()
        ),
    ):
        assert await connector._get("/probe") == {"ok": True}
    assert len(attempts) == 2


def test_registry_and_schema_accept_sapphireims():
    from contextedge.connectors.registry import get_connector
    from contextedge.schemas.source import SourceCreate

    connector = get_connector(
        "sapphireims", {}, {"base_url": "https://x", "api_key": "k", "auth_token": "t"}
    )
    assert isinstance(connector, SapphireIMSConnector)
    assert SourceCreate.model_fields["source_type"].metadata  # pattern present


# --- reference enrichment ---------------------------------------------------


def test_ticket_reference_validation_dedupe_and_cap():
    payload = {"related_tickets": ["CHG-880", "CHG-880", "bad id!", "PRB-12"]}
    assert extract_ticket_references(payload) == ["CHG-880", "PRB-12"]
    assert extract_ticket_references({"related_tickets": "not-a-list"}) == []


def test_entity_references_from_ci_and_service_names():
    ci, service = extract_entity_references(
        {"ci_name": "vpn-gw-east-01", "service_name": "VPN Service"}
    )
    assert ci.sys_id == "ci:vpn-gw-east-01"
    assert ci.entity_type == "configuration_item"
    assert service.sys_id == "service:vpn service"
    assert service.entity_type == "business_service"
    assert extract_entity_references({"ci_name": "  "}) == []


def test_case_link_candidates_include_related_ids_not_names():
    from contextedge.services.correlation_service import extract_case_link_candidates

    candidates = extract_case_link_candidates(
        source_type="sapphireims",
        raw_object=SimpleNamespace(external_id="INC-4021"),
        raw_payload=_ticket(),
    )
    assert ("sapphireims", "INC-4021") in candidates
    assert ("sapphireims", "CHG-880") in candidates
    assert not any("vpn" in c[1].lower() for c in candidates)


@pytest.mark.asyncio
async def test_process_creates_related_edges_and_namespaced_entities():
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    linked_evidence_id = uuid4()

    async def resolve(db, tid, ticket_id):
        return linked_evidence_id if ticket_id == "CHG-880" else None

    entity = SimpleNamespace(id=uuid4())
    with (
        patch(
            "contextedge.services.sapphireims_reference_service._resolve_evidence_for_ticket_id",
            side_effect=resolve,
        ),
        patch(
            "contextedge.services.sapphireims_reference_service._ensure_entity",
            AsyncMock(return_value=entity),
        ) as ensure_entity_mock,
        patch(
            "contextedge.services.sapphireims_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        # The service consumes the connector-NORMALIZED payload shape.
        normalized = {
            "ticket_id": "INC-4021",
            "record_kind": "incident",
            "related_tickets": ["CHG-880", "PRB-12"],
            "ci_name": "vpn-gw-east-01",
            "service_name": "VPN Service",
        }
        counts = await process_sapphireims_references(
            SimpleNamespace(), tenant_id, evidence, normalized
        )

    assert counts["task_edges"] == 1
    assert counts["unresolved_refs"] == 1  # PRB-12 not ingested yet
    assert counts["entity_edges"] == 2  # CI + service
    assert ensure_entity_mock.await_args.kwargs["external_system"] == "sapphireims"
    assert edge_mock.await_args_list[0].args[6] == "related_ticket"


# --- configuration probe (D4) -----------------------------------------------


@pytest.mark.asyncio
async def test_probe_reports_endpoints_fields_and_type_coverage():
    conn = SapphireIMSConnector(
        {
            "projects": ["ITOPS"],
            "fields": {"type": "ticketType"},
            "type_kind_map": {"incident": "incident"},
        },
        {"base_url": "https://sapphire.local", "api_key": "k", "auth_token": "t"},
    )
    sample = {
        "items": [
            {
                "ticketId": "4021",
                "subject": "VPN down",
                "ticketType": "Incident",
                "status": "Open",
            },
            {
                "ticketId": "4022",
                "subject": "New laptop",
                "ticketType": "ServiceRequest",
            },
        ]
    }

    async def fake_get(path, params=None):
        return sample

    conn._get = fake_get
    report = await conn.probe_configuration()

    assert report["endpoints"]["probe_path"]["ok"] is True
    assert report["endpoints"]["tickets:ITOPS"]["ok"] is True
    assert report["sample_rows"] == 2
    assert report["fields"]["type"]["mapped_to"] == "ticketType"
    assert report["fields"]["type"]["present_in_samples"] is True
    assert report["type_kind_coverage"]["incident"] == "incident"
    assert "unmapped" in report["type_kind_coverage"]["servicerequest"]


@pytest.mark.asyncio
async def test_probe_records_endpoint_failures_not_raises():
    conn = SapphireIMSConnector(
        {"projects": ["ITOPS"]},
        {"base_url": "https://sapphire.local", "api_key": "k", "auth_token": "t"},
    )

    async def fail_get(path, params=None):
        raise RuntimeError("boom")

    conn._get = fail_get
    report = await conn.probe_configuration()
    assert report["endpoints"]["probe_path"]["ok"] is False
    assert report["endpoints"]["tickets:ITOPS"]["ok"] is False
    assert report["sample_rows"] == 0
