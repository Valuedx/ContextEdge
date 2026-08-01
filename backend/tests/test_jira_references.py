"""Jira SM reference edges: issue links, components, kind-prefixed threads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.connectors.jira_sm.connector import (
    JiraSmConnector,
    issue_kind,
)
from contextedge.services.jira_reference_service import (
    extract_entity_references,
    extract_issue_references,
    heal_reverse_references,
    process_jira_references,
)


def _connector(config=None):
    return JiraSmConnector(
        config or {},
        {"base_url": "https://acme.atlassian.net", "email": "e", "api_token": "t"},
    )


def _issue(**kw):
    fields = {
        "summary": kw.get("summary", "Users cannot log in to the VPN"),
        "description": None,
        "status": {"name": "Open"},
        "priority": {"name": "High"},
        "issuetype": {"name": kw.get("issue_type", "Incident")},
        "updated": "2026-08-01T10:00:00.000+0000",
        "labels": ["vpn"],
        "resolution": None,
        "comment": {"total": 2},
        "issuelinks": kw.get("issuelinks", []),
        "components": kw.get("components", [{"id": "10100", "name": "VPN Gateway"}]),
    }
    if kw.get("parent_key"):
        fields["parent"] = {
            "key": kw["parent_key"],
            "fields": {"issuetype": {"name": "Incident"}},
        }
    fields.update(kw.get("extra_fields", {}))
    return {"key": kw.get("key", "ITOPS-101"), "fields": fields}


def _link(direction, description, key, linked_type, type_name="Causes"):
    """RAW Jira v3 issuelinks shape — connector-level tests only."""
    side = f"{direction}Issue"
    return {
        "type": {"name": type_name, "inward": description, "outward": description},
        side: {"key": key, "fields": {"issuetype": {"name": linked_type}}},
    }


def _slim(direction, description, key, linked_type, type_name="Causes"):
    """The connector's slimmed link shape — what evidence payloads carry
    and what the reference service consumes."""
    return {
        "direction": direction,
        "description": description,
        "type_name": type_name,
        "key": key,
        "issue_type": linked_type,
    }


# --- connector --------------------------------------------------------------


def test_issue_kind_normalizes_jsm_types():
    assert issue_kind("[System] Change") == "change_request"
    assert issue_kind("Incident") == "incident"
    assert issue_kind("Task") == "issue"
    assert issue_kind(None) == "issue"


def test_issue_event_carries_kind_prefix_links_and_components():
    connector = _connector()
    issue = _issue(
        issuelinks=[_link("inward", "is caused by", "ITOPS-90", "[System] Change")],
        parent_key="ITOPS-50",
    )
    event = connector._issue_event(issue, "ITOPS")

    assert event.thread_id == "incident:ITOPS-101"
    assert event.external_id == "ITOPS-101"
    assert event.content["record_kind"] == "incident"
    (link,) = event.content["issue_links"]
    assert link == {
        "direction": "inward",
        "description": "is caused by",
        "type_name": "Causes",
        "key": "ITOPS-90",
        "issue_type": "[System] Change",
    }
    assert event.content["components"] == [{"id": "10100", "name": "VPN Gateway"}]
    assert event.content["parent_key"] == "ITOPS-50"


def test_service_field_included_only_when_configured_and_prefixed():
    services = [{"id": "svc-1", "name": "VPN Service"}]
    issue = _issue(extra_fields={"customfield_10099": services})

    with_config = _connector({"service_field_id": "customfield_10099"})
    event = with_config._issue_event(issue, "ITOPS")
    assert event.content["affected_services"] == services
    assert "customfield_10099" in with_config._issue_fields_param()

    without = _connector()
    assert "affected_services" not in without._issue_event(issue, "ITOPS").content
    # Non-customfield config values must not reach the fields param.
    injected = _connector({"service_field_id": "summary,malicious"})
    assert "malicious" not in injected._issue_fields_param()


@pytest.mark.asyncio
async def test_hydrate_strips_kind_prefix_and_accepts_bare_keys():
    connector = _connector()
    requested = []

    async def jira_get(path, params=None):
        requested.append(path)
        return {"fields": {"summary": "s", "comment": {"comments": []}}}

    with patch.object(connector, "_jira_get", side_effect=jira_get):
        await connector.hydrate_thread("incident:ITOPS-101")
        await connector.hydrate_thread("ITOPS-102")  # pre-existing thread

    assert requested == ["/issue/ITOPS-101", "/issue/ITOPS-102"]


@pytest.mark.asyncio
async def test_fetch_changes_paginates_past_first_page():
    connector = _connector()
    pages = [
        {"issues": [_issue(key=f"ITOPS-{i}") for i in range(100)]},
        {"issues": [_issue(key="ITOPS-200", extra_fields={"updated": "2026-08-01T11:00:00.000+0000"})]},
    ]
    calls = []

    async def jira_get(path, params=None):
        calls.append(params)
        return pages[len(calls) - 1]

    from contextedge.connectors.base import Checkpoint

    with patch.object(connector, "_jira_get", side_effect=jira_get):
        result = await connector.fetch_changes(
            "ITOPS", "jira_project", Checkpoint(data={"last_updated": "2026-08-01T09:30:15.000+0000"})
        )

    assert len(result.events) == 101  # the old single-page fetch dropped page 2
    assert calls[0]["startAt"] == "0"
    assert calls[1]["startAt"] == "100"
    # JQL-safe minute cursor, rewound 30 min so timezone slop re-delivers
    # instead of skipping.
    assert '"2026-08-01 09:00"' in calls[0]["jql"]


# --- reference extraction ---------------------------------------------------


def test_issue_references_map_and_direction_rules():
    payload = {
        "record_kind": "incident",
        "issue_links": [
            _slim("inward", "is caused by", "ITOPS-90", "[System] Change"),
            _slim("inward", "is caused by", "ITOPS-91", "Task"),
            _slim("outward", "causes", "ITOPS-92", "Incident"),  # other side emits
            _slim("outward", "duplicates", "ITOPS-93", "Incident", "Duplicate"),
            _slim("inward", "is duplicated by", "ITOPS-94", "Incident", "Duplicate"),
            _slim("outward", "relates to", "ITOPS-95", "[System] Problem", "Relates"),
            _slim("outward", "relates to", "ITOPS-96", "Task", "Relates"),  # untyped noise
        ],
        "parent_key": "ITOPS-50",
    }
    refs = extract_issue_references(payload)
    assert refs == [
        ("caused_by_change", "ITOPS-90"),
        ("caused_by_issue", "ITOPS-91"),
        ("duplicate_of", "ITOPS-93"),
        ("related_problem", "ITOPS-95"),
        ("child_of_issue", "ITOPS-50"),
    ]


def test_issue_references_validate_keys_and_problem_side_rule():
    junk = {
        "record_kind": "incident",
        "issue_links": [_slim("inward", "is caused by", "not a key", "Change")],
        "parent_key": "also-not-A-KEY!",
    }
    assert extract_issue_references(junk) == []

    # The problem side must NOT emit related_problem for its own links.
    problem_side = {
        "record_kind": "problem",
        "issue_links": [_slim("outward", "relates to", "ITOPS-95", "[System] Problem")],
    }
    assert extract_issue_references(problem_side) == []


def test_entity_references_namespace_components_and_services():
    payload = {
        "key": "ITOPS-101",
        "components": [{"id": "10100", "name": "VPN Gateway"}, "junk"],
        "affected_services": [{"id": "svc-1", "name": "VPN Service"}],
    }
    component, service = extract_entity_references(payload)
    assert component.sys_id == "component:ITOPS:10100"
    assert component.name == "VPN Gateway"
    assert component.entity_type == "business_service"
    assert component.edge_type == "affects_ci"
    assert service.sys_id == "service:svc-1"
    assert service.attributes["source_kind"] == "jsm_service"


def test_case_link_candidates_include_linked_keys_not_components():
    from contextedge.services.correlation_service import extract_case_link_candidates

    payload = {
        "record_kind": "incident",
        "issue_links": [_slim("inward", "is caused by", "ITOPS-90", "[System] Change")],
        "components": [{"id": "10100", "name": "VPN Gateway"}],
        "key": "ITOPS-101",
    }
    candidates = extract_case_link_candidates(
        source_type="jira_sm",
        raw_object=SimpleNamespace(external_id="ITOPS-101"),
        raw_payload=payload,
    )
    assert ("jira_sm", "ITOPS-101") in candidates
    assert ("jira_sm", "ITOPS-90") in candidates
    assert not any("component" in c[1] for c in candidates)


# --- processing -------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_creates_typed_edges_and_jira_entities():
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    change_evidence_id = uuid4()

    async def resolve(db, tid, key):
        return change_evidence_id if key == "ITOPS-90" else None

    entity = SimpleNamespace(id=uuid4())
    payload = {
        "key": "ITOPS-101",
        "record_kind": "incident",
        "issue_links": [
            _slim("inward", "is caused by", "ITOPS-90", "[System] Change"),
            _slim("inward", "is caused by", "ITOPS-99", "[System] Change"),  # not ingested
        ],
        "components": [{"id": "10100", "name": "VPN Gateway"}],
    }

    with (
        patch(
            "contextedge.services.jira_reference_service._resolve_evidence_for_issue_key",
            side_effect=resolve,
        ),
        patch(
            "contextedge.services.jira_reference_service._ensure_entity",
            AsyncMock(return_value=entity),
        ) as ensure_entity_mock,
        patch(
            "contextedge.services.jira_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        counts = await process_jira_references(SimpleNamespace(), tenant_id, evidence, payload)

    assert counts["task_edges"] == 1
    assert counts["unresolved_refs"] == 1
    assert counts["entity_edges"] == 1
    assert ensure_entity_mock.await_args.kwargs["external_system"] == "jira_sm"
    typed = edge_mock.await_args_list[0].args
    assert typed[2:7] == (
        "evidence",
        evidence.id,
        "evidence",
        change_evidence_id,
        "caused_by_change",  # change-risk counts this edge for Jira too
    )


@pytest.mark.asyncio
async def test_reverse_heal_guards_invalid_own_key():
    db = SimpleNamespace(execute=AsyncMock())
    healed = await heal_reverse_references(
        db, uuid4(), SimpleNamespace(id=uuid4()), "not a key"
    )
    assert healed == 0
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_topology_lookup_refuses_non_servicenow_entities():
    """Name resolution finds Jira components, but live topology must
    never push a Jira id into a ServiceNow query."""
    from contextedge.services.cmdb_topology_service import lookup_topology

    entity = SimpleNamespace(
        id=uuid4(),
        name="VPN Gateway",
        external_id="component:ITOPS:10100",
        external_system="jira_sm",
        attributes={},
        last_synced_at=None,
    )
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=entity),
    ):
        result = await lookup_topology(SimpleNamespace(), uuid4(), "VPN Gateway")
    assert result["error"]["code"] == "topology_unsupported_for_source"


# --- parity fixes: retry + resolves mapping ---------------------------------


@pytest.mark.asyncio
async def test_jira_get_retries_429_with_retry_after():
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
        patch("contextedge.connectors.jira_sm.connector.httpx.AsyncClient", return_value=_Client()),
        patch("contextedge.connectors.jira_sm.connector.asyncio.sleep", AsyncMock()) as sleep_mock,
    ):
        result = await connector._jira_get("/myself")

    assert result == {"ok": True}
    assert len(attempts) == 2  # one 429, one success
    sleep_mock.assert_awaited_once()


def test_resolves_link_types_config_merges_with_default():
    from contextedge.services.jira_reference_service import resolves_link_types

    assert resolves_link_types(None) == frozenset({"resolves"})
    merged = resolves_link_types({"resolves_link_names": ["Fixes", "  ", 42, "Remediates"]})
    assert merged == frozenset({"resolves", "fixes", "remediates"})


def test_resolves_link_emits_remediated_by_change_for_changes_only():
    from contextedge.services.jira_reference_service import (
        extract_issue_references,
        resolves_link_types,
    )

    resolves = resolves_link_types({"resolves_link_names": ["Fixes"]})
    payload = {
        "record_kind": "incident",
        "issue_links": [
            _slim("inward", "is resolved by", "ITOPS-90", "[System] Change", "Resolves"),
            _slim("inward", "is fixed by", "ITOPS-91", "[System] Change", "Fixes"),
            _slim("inward", "is resolved by", "ITOPS-92", "Task", "Resolves"),  # not a Change
            _slim("outward", "resolves", "ITOPS-93", "Incident", "Resolves"),  # change side: skip
        ],
    }
    refs = extract_issue_references(payload, resolves)
    assert ("remediated_by_change", "ITOPS-90") in refs
    assert ("remediated_by_change", "ITOPS-91") in refs
    assert not any(key == "ITOPS-92" for _t, key in refs)
    assert not any(key == "ITOPS-93" for _t, key in refs)


def test_resolves_keys_become_case_link_candidates():
    from contextedge.services.correlation_service import extract_case_link_candidates

    payload = {
        "record_kind": "incident",
        "issue_links": [
            _slim("inward", "is fixed by", "ITOPS-91", "[System] Change", "Fixes"),
        ],
    }
    candidates = extract_case_link_candidates(
        source_type="jira_sm",
        raw_object=SimpleNamespace(external_id="ITOPS-101"),
        raw_payload=payload,
        source_config={"resolves_link_names": ["Fixes"]},
    )
    assert ("jira_sm", "ITOPS-91") in candidates


def test_resolves_config_cannot_hijack_builtin_semantics():
    from contextedge.services.jira_reference_service import resolves_link_types

    hijack = resolves_link_types({"resolves_link_names": ["Causes", "Duplicate", "Fixes"]})
    assert "causes" not in hijack
    assert "duplicate" not in hijack
    assert "fixes" in hijack
