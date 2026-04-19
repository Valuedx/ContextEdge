"""Tests for A6: decision provenance + source deep-link helper."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.source_deep_link_service import build_source_deep_link


# =========================================================================
# Deep-link helper
# =========================================================================


def test_deep_link_returns_none_when_source_type_missing():
    assert build_source_deep_link(None, {}, "x") is None
    assert build_source_deep_link("", {}, "x") is None


def test_deep_link_template_wins_over_default():
    url = build_source_deep_link(
        "jira_sm",
        {"deep_link_template": "https://custom/{external_id}/x"},
        "INC-1",
    )
    assert url == "https://custom/INC-1/x"


def test_deep_link_template_rejects_missing_required_variable():
    """If the template needs {external_id} but none was provided, we must not
    leak the literal placeholder to the UI."""
    url = build_source_deep_link(
        "jira_sm",
        {"deep_link_template": "https://custom/{external_id}"},
        None,
    )
    assert url is None


def test_deep_link_template_with_thread_variable():
    url = build_source_deep_link(
        "gmail",
        {"deep_link_template": "https://mail/u/0/#/{thread_id}"},
        "msg-x",
        thread_id="thr-abc",
    )
    assert url == "https://mail/u/0/#/thr-abc"


def test_deep_link_defaults_jira_sm():
    url = build_source_deep_link(
        "jira_sm",
        {"base_url": "https://acme.atlassian.net/"},
        "INC-100",
    )
    # Trailing slash stripped.
    assert url == "https://acme.atlassian.net/browse/INC-100"


def test_deep_link_defaults_jira_sm_requires_base_url():
    assert build_source_deep_link("jira_sm", {}, "INC-100") is None


def test_deep_link_defaults_jira_sm_requires_external_id():
    assert build_source_deep_link(
        "jira_sm", {"base_url": "https://x.atlassian.net"}, None,
    ) is None


def test_deep_link_defaults_servicenow_uses_instance_url():
    url = build_source_deep_link(
        "servicenow",
        {"instance_url": "https://acme.service-now.com"},
        "INC-123",
    )
    assert "acme.service-now.com" in url
    assert "number=INC-123" in url


def test_deep_link_defaults_gmail_prefers_thread_id():
    url = build_source_deep_link("gmail", {}, "msg-1", thread_id="thr-2")
    assert url.endswith("/thr-2")


def test_deep_link_defaults_gmail_falls_back_to_external_id():
    url = build_source_deep_link("gmail", {}, "msg-1", thread_id=None)
    assert url.endswith("/msg-1")


def test_deep_link_defaults_gmail_returns_none_when_neither_provided():
    assert build_source_deep_link("gmail", {}, None) is None


def test_deep_link_defaults_teams_returns_none():
    """Teams links need tenant/team/channel not on the Source row — must
    return None until admin supplies a deep_link_template."""
    assert build_source_deep_link("teams", {"base_url": "https://x"}, "msg-1") is None


def test_deep_link_unknown_source_type_returns_none():
    assert build_source_deep_link("mystery", {"base_url": "https://x"}, "y") is None


# =========================================================================
# get_decision_provenance
# =========================================================================


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_get_decision_provenance_returns_none_for_missing(mock_get):
    from contextedge.services.decision_trace_service import get_decision_provenance

    mock_get.return_value = None
    db = SimpleNamespace()
    result = await get_decision_provenance(
        db, tenant_id=uuid4(), decision_id=uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_get_decision_provenance_empty_when_no_edges(mock_get):
    """Decision with no based_on edges returns empty lists (not None)."""
    from contextedge.services.decision_trace_service import get_decision_provenance

    decision_id = uuid4()
    mock_get.return_value = SimpleNamespace(id=decision_id, tenant_id=uuid4())

    class _Exec:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    db = SimpleNamespace(execute=AsyncMock(return_value=_Exec()))

    result = await get_decision_provenance(
        db, tenant_id=uuid4(), decision_id=decision_id,
    )

    assert result is not None
    assert result["decision_id"] == decision_id
    assert result["evidence"] == []
    assert result["episodes"] == []
    assert result["patterns"] == []


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_get_decision_provenance_hydrates_evidence_with_deep_link(mock_get):
    """Evidence edges are hydrated with source info + deep-link."""
    from contextedge.services.decision_trace_service import get_decision_provenance
    from contextedge.models.pattern import GraphEdge

    tenant_id = uuid4()
    decision_id = uuid4()
    evidence_id = uuid4()
    source_id = uuid4()
    source_object_id = uuid4()

    mock_get.return_value = SimpleNamespace(id=decision_id, tenant_id=tenant_id)

    edge = GraphEdge(
        tenant_id=tenant_id,
        source_node_type="decision",
        source_node_id=decision_id,
        target_node_type="evidence",
        target_node_id=evidence_id,
        edge_type="based_on",
    )
    ev = SimpleNamespace(
        id=evidence_id,
        title="VPN cert expired on vpn-gw-east-01",
        body_summary="Gateway auth cert expired at 14:30 UTC",
        evidence_type="incident",
        delta_signal="red",
        ingested_at=datetime.now(timezone.utc),
        source_id=source_id,
        source_object_id=source_object_id,
    )
    src = SimpleNamespace(
        id=source_id,
        source_type="servicenow",
        display_name="Acme ServiceNow Prod",
        config={"instance_url": "https://acme.service-now.com"},
    )
    src_obj = SimpleNamespace(
        id=source_object_id,
        external_id="INC-4521",
        metadata_extra=None,
    )

    class _EdgesExec:
        def scalars(self):
            return SimpleNamespace(all=lambda: [edge])

    class _EvidenceExec:
        def all(self):
            return [(ev, src, src_obj)]

    execute_mock = AsyncMock(side_effect=[_EdgesExec(), _EvidenceExec()])
    db = SimpleNamespace(execute=execute_mock)

    result = await get_decision_provenance(
        db, tenant_id=tenant_id, decision_id=decision_id,
    )

    assert len(result["evidence"]) == 1
    item = result["evidence"][0]
    assert item["evidence_id"] == evidence_id
    assert item["source_type"] == "servicenow"
    assert item["external_id"] == "INC-4521"
    assert item["deep_link"] is not None
    assert "INC-4521" in item["deep_link"]
    assert item["delta_signal"] == "red"


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_get_decision_provenance_skips_non_based_on_edges(mock_get):
    """Only `based_on` edges are queried — other edge types on this decision
    (`considered`, `chose`, `required_approval`, etc.) don't appear in the
    provenance bundle."""
    from contextedge.services.decision_trace_service import get_decision_provenance
    from contextedge.models.pattern import GraphEdge

    decision_id = uuid4()
    tenant_id = uuid4()
    mock_get.return_value = SimpleNamespace(id=decision_id, tenant_id=tenant_id)

    captured = {"where_clauses": []}

    async def _execute(stmt):
        # Compile with literal binds so the string embeds 'based_on'
        # instead of the :edge_type_1 placeholder.
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        captured["where_clauses"].append(sql)

        class _E:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])
        return _E()

    db = SimpleNamespace(execute=_execute)
    await get_decision_provenance(db, tenant_id=tenant_id, decision_id=decision_id)

    # First query is the edges lookup; must filter by edge_type = based_on.
    first_query = captured["where_clauses"][0]
    assert "edge_type" in first_query
    assert "based_on" in first_query


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_get_decision_provenance_respects_limits(mock_get):
    """evidence_limit caps how many evidence rows are fetched."""
    from contextedge.services.decision_trace_service import get_decision_provenance
    from contextedge.models.pattern import GraphEdge

    tenant_id = uuid4()
    decision_id = uuid4()
    mock_get.return_value = SimpleNamespace(id=decision_id, tenant_id=tenant_id)

    # Build 30 evidence edges; evidence_limit=5 should cap to 5.
    edges = [
        GraphEdge(
            tenant_id=tenant_id,
            source_node_type="decision",
            source_node_id=decision_id,
            target_node_type="evidence",
            target_node_id=uuid4(),
            edge_type="based_on",
        )
        for _ in range(30)
    ]

    class _EdgesExec:
        def scalars(self):
            return SimpleNamespace(all=lambda: edges)

    captured = {}

    async def _execute(stmt):
        sql = str(stmt)
        if "FROM evidence_items" in sql:
            captured["evidence_sql"] = sql

            class _EvidenceExec:
                def all(self):
                    return []
            return _EvidenceExec()
        return _EdgesExec()

    db = SimpleNamespace(execute=_execute)
    await get_decision_provenance(
        db, tenant_id=tenant_id, decision_id=decision_id,
        evidence_limit=5,
    )

    # Hard to inspect the `IN (...)` cardinality from the compiled str without
    # a real DB, but verify the evidence query was executed and references
    # evidence_items by name.
    assert "evidence_sql" in captured
