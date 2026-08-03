"""CMDB topology hybrid (Phase 2): demand-driven cache, not a replica."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.cmdb_topology_service import (
    TOPOLOGY_EDGE_ORIGIN,
    cache_neighborhood,
    entity_is_stale,
    fetch_ci_neighborhood,
    lookup_topology,
    normalize_relationship_type,
)

GW_SYS_ID = "1" * 32   # vpn-gw-east-01
RADIUS_SYS_ID = "2" * 32  # radius-prod-01
CA_SYS_ID = "3" * 32   # cert-authority service
REL_A = "a" * 32
REL_B = "b" * 32


def _ref(sys_id):
    return {"value": sys_id, "link": f"https://acme.service-now.com/{sys_id}"}


class _FakeConnector:
    def __init__(self, rels, details):
        self.rels = rels
        self.details = details

    async def fetch_ci_relationships(self, sys_id):
        return self.rels

    async def fetch_ci_details(self, sys_ids):
        return [d for d in self.details if d["sys_id"] in sys_ids]


def _gateway_connector():
    return _FakeConnector(
        rels=[
            {
                "sys_id": REL_A,
                "parent": _ref(GW_SYS_ID),
                "child": _ref(RADIUS_SYS_ID),
                "type.name": "Depends on::Used by",
            },
            {
                "sys_id": REL_B,
                "parent": _ref(RADIUS_SYS_ID),
                "child": _ref(CA_SYS_ID),
                "type.name": "Depends on::Used by",
            },  # does not touch the gateway — must be ignored
            {
                "sys_id": "f" * 32,
                "parent": _ref(GW_SYS_ID),
                "child": _ref(GW_SYS_ID),
                "type.name": "Contains::Contained by",
            },  # self-loop — must be ignored
        ],
        details=[
            {"sys_id": GW_SYS_ID, "name": "vpn-gw-east-01", "sys_class_name": "cmdb_ci_netgear"},
            {"sys_id": RADIUS_SYS_ID, "name": "radius-prod-01", "sys_class_name": "cmdb_ci_server"},
        ],
    )


# --- normalization ----------------------------------------------------------


def test_relationship_types_normalize_on_parent_descriptor():
    assert normalize_relationship_type("Depends on::Used by") == "depends_on"
    assert normalize_relationship_type("Runs on::Runs") == "runs_on"
    assert normalize_relationship_type("Hosted on::Hosts") == "hosted_on"
    assert normalize_relationship_type("Exotic custom rel::Whatever") == "related_to"
    assert normalize_relationship_type(None) == "related_to"


def test_entity_staleness_ttl():
    now = datetime.now(UTC)
    assert entity_is_stale(SimpleNamespace(last_synced_at=None), now)
    assert entity_is_stale(SimpleNamespace(last_synced_at=now - timedelta(days=8)), now)
    assert not entity_is_stale(SimpleNamespace(last_synced_at=now - timedelta(days=1)), now)
    # naive timestamps (SQLite-ish test fixtures) must not crash
    assert not entity_is_stale(
        SimpleNamespace(last_synced_at=now.replace(tzinfo=None)), now
    )


# --- live fetch -------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_neighborhood_filters_to_center_and_parses_refs():
    neighborhood = await fetch_ci_neighborhood(_gateway_connector(), GW_SYS_ID)

    assert len(neighborhood["relationships"]) == 1
    rel = neighborhood["relationships"][0]
    assert rel["parent"] == GW_SYS_ID
    assert rel["child"] == RADIUS_SYS_ID
    assert rel["edge_type"] == "depends_on"
    assert neighborhood["ci_details"][RADIUS_SYS_ID]["name"] == "radius-prod-01"


# --- write-through cache ----------------------------------------------------


@pytest.mark.asyncio
async def test_cache_neighborhood_ensures_edges_and_closes_deleted_rels(monkeypatch):
    from unittest.mock import AsyncMock as _AM
    monkeypatch.setattr(
        "contextedge.services.entity_class_service.ensure_entity_class_edges",
        _AM(return_value=None),
    )
    tenant_id = uuid4()
    entities = {
        GW_SYS_ID: SimpleNamespace(id=uuid4(), last_synced_at=None),
        RADIUS_SYS_ID: SimpleNamespace(id=uuid4(), last_synced_at=None),
    }

    async def ensure_entity(db, tid, ref):
        return entities[ref.sys_id]

    # A previously cached edge whose relationship no longer exists
    # upstream, plus a Phase 1 evidence edge that must NOT be touched.
    deleted_rel_edge = SimpleNamespace(
        source_node_id=entities[GW_SYS_ID].id,
        target_node_id=uuid4(),
        edge_type="depends_on",
        metadata_extra={"origin": TOPOLOGY_EDGE_ORIGIN, "rel_sys_id": "9" * 32},
        valid_to=None,
    )
    foreign_edge = SimpleNamespace(
        source_node_id=uuid4(),
        target_node_id=entities[GW_SYS_ID].id,
        edge_type="depends_on",
        metadata_extra={"origin": "somewhere_else"},
        valid_to=None,
    )
    edge_result = Mock()
    edge_result.scalars.return_value.all.return_value = [deleted_rel_edge, foreign_edge]
    db = SimpleNamespace(execute=AsyncMock(return_value=edge_result), flush=AsyncMock())

    neighborhood = await fetch_ci_neighborhood(_gateway_connector(), GW_SYS_ID)
    with (
        patch(
            "contextedge.services.cmdb_topology_service._ensure_entity",
            side_effect=ensure_entity,
        ),
        patch(
            "contextedge.services.cmdb_topology_service.ensure_edge", AsyncMock()
        ) as edge_mock,
    ):
        counts = await cache_neighborhood(db, tenant_id, neighborhood)

    assert counts["edges_ensured"] == 1
    args = edge_mock.await_args_list[0].args
    assert args[2:7] == (
        "entity",
        entities[GW_SYS_ID].id,
        "entity",
        entities[RADIUS_SYS_ID].id,
        "depends_on",
    )
    kwargs = edge_mock.await_args_list[0].kwargs
    assert kwargs["metadata"]["origin"] == TOPOLOGY_EDGE_ORIGIN
    assert kwargs["metadata"]["rel_sys_id"] == REL_A

    assert counts["edges_closed"] == 1
    assert deleted_rel_edge.valid_to is not None  # upstream deletion end-dated
    assert foreign_edge.valid_to is None  # non-topology edge untouched
    assert entities[GW_SYS_ID].last_synced_at is not None  # TTL stamped


# --- lookup (tool / API entry) ----------------------------------------------


def _tool_db():
    return SimpleNamespace(begin_nested=Mock(return_value=_NestedTx()))


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_lookup_live_success_returns_neighbors_and_caches():
    tenant_id = uuid4()
    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(return_value=_gateway_connector()),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.cache_neighborhood",
            AsyncMock(return_value={"entities": 2, "edges_ensured": 1, "edges_closed": 0}),
        ) as cache_mock,
    ):
        result = await lookup_topology(_tool_db(), tenant_id, GW_SYS_ID)

    assert result["source"] == "live"
    assert result["center"]["name"] == "vpn-gw-east-01"
    (neighbor,) = result["neighbors"]
    assert neighbor["name"] == "radius-prod-01"
    assert neighbor["relationship"] == "depends_on"
    assert neighbor["center_role"] == "parent"  # the gateway depends ON radius
    cache_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_cache_write_failure_still_returns_live_data():
    """Live data in hand + broken cache write = live result flagged
    uncached, never the stale-cache fallback."""
    tenant_id = uuid4()
    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(return_value=_gateway_connector()),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.cache_neighborhood",
            AsyncMock(side_effect=RuntimeError("unique violation")),
        ),
    ):
        result = await lookup_topology(_tool_db(), tenant_id, GW_SYS_ID)

    assert result["source"] == "live"
    assert result["cache"] == {"cache_write_failed": True}
    assert result["neighbors"][0]["name"] == "radius-prod-01"


@pytest.mark.asyncio
async def test_lookup_falls_back_to_cache_marked_stale():
    tenant_id = uuid4()
    entity = SimpleNamespace(
        id=uuid4(),
        name="vpn-gw-east-01",
        external_id=GW_SYS_ID,
        external_system="servicenow",
        attributes={"ci_class": "cmdb_ci_netgear"},
        last_synced_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=entity),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(side_effect=ConnectionError("instance down")),
        ),
        patch(
            "contextedge.services.cmdb_topology_service._cached_topology",
            AsyncMock(return_value=[{"name": "radius-prod-01", "relationship": "depends_on"}]),
        ),
    ):
        result = await lookup_topology(SimpleNamespace(), tenant_id, "vpn-gw-east-01")

    assert result["source"] == "cache"
    assert result["stale"] is True
    assert result["as_of"] == "2026-07-01T00:00:00+00:00"
    assert result["neighbors"][0]["name"] == "radius-prod-01"


@pytest.mark.asyncio
async def test_lookup_unknown_name_and_unavailable_uncached():
    tenant_id = uuid4()
    with patch(
        "contextedge.services.cmdb_topology_service.resolve_ci_entity",
        AsyncMock(return_value=None),
    ):
        unknown = await lookup_topology(SimpleNamespace(), tenant_id, "no-such-host")
    assert unknown["error"]["code"] == "unknown_ci"

    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(side_effect=ConnectionError("down")),
        ),
    ):
        down = await lookup_topology(SimpleNamespace(), tenant_id, GW_SYS_ID)
    assert down["error"]["code"] == "servicenow_unavailable"


# --- warming ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_task_skips_fresh_entity_without_api_calls():
    from contextedge.workers.cmdb_tasks import _warm

    fresh = SimpleNamespace(last_synced_at=datetime.now(UTC))
    found = Mock()
    found.scalar_one_or_none.return_value = fresh
    db = SimpleNamespace(execute=AsyncMock(return_value=found))

    with patch(
        "contextedge.services.cmdb_topology_service.load_servicenow_connector",
        AsyncMock(),
    ) as loader:
        result = await _warm(db, uuid4(), uuid4(), GW_SYS_ID)

    assert result == {"status": "fresh"}
    loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_ci_surfaces_warm_candidate_from_reference_processing(monkeypatch):
    from unittest.mock import AsyncMock as _AM
    monkeypatch.setattr(
        "contextedge.services.entity_class_service.ensure_entity_class_edges",
        _AM(return_value=None),
    )
    from contextedge.services.servicenow_reference_service import (
        process_servicenow_references,
    )

    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None, source_id=uuid4())
    stale_entity = SimpleNamespace(id=uuid4(), last_synced_at=None)

    with (
        patch(
            "contextedge.services.servicenow_reference_service._ensure_entity",
            AsyncMock(return_value=stale_entity),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ),
    ):
        counts = await process_servicenow_references(
            SimpleNamespace(),
            tenant_id,
            evidence,
            {"cmdb_ci": {"value": GW_SYS_ID}, "cmdb_ci.name": "vpn-gw-east-01"},
        )

    assert counts["warm_candidates"] == [
        {"sys_id": GW_SYS_ID, "source_id": str(evidence.source_id)}
    ]


# --- MAF tool ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_maf_tool_passthrough_and_structured_errors():
    pytest.importorskip("agent_framework")
    from contextedge.integrations.maf.tools import CmdbTopologyTools

    class _Client:
        async def lookup(self, term):
            if term == "boom":
                raise RuntimeError("db exploded at /etc/secrets")
            return {"source": "live", "center": {"name": term}, "neighbors": []}

    toolset = CmdbTopologyTools(_Client())
    ok = await toolset.cmdb_topology.invoke(
        arguments={"ci": "vpn-gw-east-01"}, skip_parsing=True
    )
    assert ok["source"] == "live"

    err = await toolset.cmdb_topology.invoke(
        arguments={"ci": "boom"}, skip_parsing=True
    )
    assert err["error"]["code"] == "topology_unavailable"
    assert "secrets" not in str(err)  # no raw exception text leaks

    empty = await toolset.cmdb_topology.invoke(
        arguments={"ci": "   "}, skip_parsing=True
    )
    assert empty["error"]["code"] == "invalid_ci"


@pytest.mark.asyncio
async def test_lookup_serves_fresh_cache_without_api_calls():
    """A CI fetched minutes ago must not trigger new ServiceNow calls —
    agent loops on one CI stay off the instance."""
    tenant_id = uuid4()
    entity = SimpleNamespace(
        id=uuid4(),
        name="vpn-gw-east-01",
        external_id=GW_SYS_ID,
        external_system="servicenow",
        attributes={"ci_class": "cmdb_ci_netgear"},
        last_synced_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=entity),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(),
        ) as loader,
        patch(
            "contextedge.services.cmdb_topology_service._cached_topology",
            AsyncMock(return_value=[{"name": "radius-prod-01", "relationship": "depends_on"}]),
        ),
    ):
        result = await lookup_topology(_tool_db(), tenant_id, "vpn-gw-east-01")

    assert result["source"] == "cache"
    assert result["stale"] is False
    loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_task_rejects_unvalidated_sys_id():
    """Task args are an external boundary — junk must never reach a
    sysparm_query."""
    from contextedge.workers.cmdb_tasks import _warm

    db = SimpleNamespace(execute=AsyncMock())
    result = await _warm(db, uuid4(), uuid4(), "x^ORDERBYsys_id")
    assert result == {"status": "invalid_sys_id"}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_does_not_churn_edges_missing_rel_sys_id():
    """An edge cached without a rel sys_id must be left open, not closed
    and re-created on every refresh."""
    tenant_id = uuid4()
    center = SimpleNamespace(id=uuid4(), last_synced_at=None)

    unmatched_edge = SimpleNamespace(
        source_node_id=center.id,
        target_node_id=uuid4(),
        edge_type="depends_on",
        metadata_extra={"origin": TOPOLOGY_EDGE_ORIGIN, "rel_sys_id": ""},
        valid_to=None,
    )
    edge_result = Mock()
    edge_result.scalars.return_value.all.return_value = [unmatched_edge]
    db = SimpleNamespace(execute=AsyncMock(return_value=edge_result), flush=AsyncMock())

    with (
        patch(
            "contextedge.services.cmdb_topology_service._ensure_entity",
            AsyncMock(return_value=center),
        ),
        patch("contextedge.services.cmdb_topology_service.ensure_edge", AsyncMock()),
    ):
        counts = await cache_neighborhood(
            db, tenant_id, {"sys_id": GW_SYS_ID, "relationships": [], "ci_details": {}}
        )

    assert counts["edges_closed"] == 0
    assert unmatched_edge.valid_to is None


@pytest.mark.asyncio
async def test_fetch_details_requests_center_first():
    """Hub truncation must never cut the center CI's own details."""
    requested = []

    class _Recorder(_FakeConnector):
        async def fetch_ci_details(self, sys_ids):
            requested.extend(sys_ids)
            return []

    connector = _Recorder(rels=_gateway_connector().rels, details=[])
    await fetch_ci_neighborhood(connector, GW_SYS_ID)
    assert requested[0] == GW_SYS_ID


@pytest.mark.asyncio
async def test_unknown_sys_id_does_not_materialize_junk_entity():
    """A hallucinated-but-well-formed sys_id (ServiceNow returns nothing)
    must not create a hex-named entity stamped fresh."""
    db = SimpleNamespace(flush=AsyncMock())

    with patch(
        "contextedge.services.cmdb_topology_service._ensure_entity", AsyncMock()
    ) as ensure_mock:
        counts = await cache_neighborhood(
            db, uuid4(), {"sys_id": "9" * 32, "relationships": [], "ci_details": {}}
        )

    assert counts["skipped_unknown_ci"] is True
    ensure_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_lookup_flags_nonexistent_ci():
    empty_connector = _FakeConnector(rels=[], details=[])
    with (
        patch(
            "contextedge.services.cmdb_topology_service.resolve_ci_entity",
            AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.cmdb_topology_service.load_servicenow_connector",
            AsyncMock(return_value=empty_connector),
        ),
    ):
        result = await lookup_topology(_tool_db(), uuid4(), "9" * 32)

    assert result["source"] == "live"
    assert result["ci_found"] is False
    assert result["neighbors"] == []
    assert result["cache"]["skipped_unknown_ci"] is True


# --- HTTP client twin (D3) --------------------------------------------------


def test_http_topology_client_refuses_plain_http():
    from contextedge.integrations.maf.client import HttpCmdbTopologyClient

    with pytest.raises(ValueError, match="https"):
        HttpCmdbTopologyClient("http://contextedge.internal")
    # Local development opt-in works; https always works.
    HttpCmdbTopologyClient("http://localhost:8000", allow_insecure_http=True)
    HttpCmdbTopologyClient("https://contextedge.internal")


@pytest.mark.asyncio
async def test_http_topology_client_calls_endpoint_with_tokens():
    from unittest.mock import AsyncMock, Mock

    from contextedge.integrations.maf.client import HttpCmdbTopologyClient

    response = Mock()
    response.raise_for_status = Mock()
    response.json = Mock(return_value={"ci_found": True})
    fake_http = Mock()
    fake_http.get = AsyncMock(return_value=response)

    client = HttpCmdbTopologyClient(
        "https://contextedge.internal",
        bearer_token="jwt",
        service_token="svc",
        client=fake_http,
    )
    out = await client.lookup("vpn-gw-emea-03")

    assert out == {"ci_found": True}
    call = fake_http.get.await_args
    assert call.args[0].endswith("/api/v1/graph/cmdb-topology")
    assert call.kwargs["params"] == {"ci": "vpn-gw-emea-03"}
    assert call.kwargs["headers"]["Authorization"] == "Bearer jwt"
    assert call.kwargs["headers"]["X-Service-Token"] == "svc"
