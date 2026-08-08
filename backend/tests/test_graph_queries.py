from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.graph.queries import (
    get_entity_subgraph,
    get_graph_stats,
    get_neighbors,
    get_pattern_subgraph,
)
from contextedge.models.pattern import GraphEdge


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _edge(tenant_id, src_type, src_id, tgt_type, tgt_id, edge_type, weight=1.0, domain_id=None, metadata=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=domain_id,
        source_node_type=src_type,
        source_node_id=src_id,
        target_node_type=tgt_type,
        target_node_id=tgt_id,
        edge_type=edge_type,
        weight=weight,
        metadata_extra=metadata,
    )


# ---- get_neighbors ----

@pytest.mark.asyncio
async def test_get_neighbors_depth_1():
    tenant_id = uuid4()
    origin_id = uuid4()
    neighbor_id = uuid4()

    e = _edge(tenant_id, "pattern", origin_id, "episode", neighbor_id, "belongs_to")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([e])),
    )

    result = await get_neighbors(db, tenant_id, "pattern", origin_id)

    assert len(result) == 1
    assert result[0]["node_type"] == "episode"
    assert result[0]["node_id"] == str(neighbor_id)
    assert result[0]["depth"] == 1
    assert result[0]["direction"] == "outgoing"


@pytest.mark.asyncio
async def test_get_neighbors_depth_2():
    """BFS should reach hop-2 neighbors via the hop-1 frontier."""
    tenant_id = uuid4()
    origin = uuid4()
    hop1 = uuid4()
    hop2 = uuid4()

    e1 = _edge(tenant_id, "pattern", origin, "episode", hop1, "belongs_to")
    e2 = _edge(tenant_id, "episode", hop1, "identity", hop2, "affects")

    call_count = 0

    async def _execute(q):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _ScalarsResult([e1])
        elif call_count == 2:
            return _ScalarsResult([e2])
        return _ScalarsResult([])

    db = SimpleNamespace(execute=_execute)

    result = await get_neighbors(db, tenant_id, "pattern", origin, max_depth=2)

    assert len(result) == 2
    types_at_depth = {(r["node_type"], r["depth"]) for r in result}
    assert ("episode", 1) in types_at_depth
    assert ("identity", 2) in types_at_depth


@pytest.mark.asyncio
async def test_get_neighbors_caps_at_max_depth_3():
    """max_depth > 3 is clamped to 3."""
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([])),
    )

    result = await get_neighbors(db, tenant_id, "pattern", uuid4(), max_depth=10)
    assert result == []
    assert db.execute.await_count <= 3


@pytest.mark.asyncio
async def test_get_neighbors_with_domain_filter():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([])),
    )
    domain_id = uuid4()

    result = await get_neighbors(
        db, tenant_id, "pattern", uuid4(),
        domain_id=domain_id,
    )

    assert result == []
    db.execute.assert_awaited_once()


# ---- get_pattern_subgraph ----

def _pel(pattern_id, episode_id=None, evidence_id=None, weight=1.0):
    return SimpleNamespace(
        id=uuid4(),
        pattern_id=pattern_id,
        episode_id=episode_id,
        evidence_id=evidence_id,
        link_type="clusters",
        weight=weight,
    )


@pytest.mark.asyncio
async def test_get_pattern_subgraph_returns_persisted_edges():
    """After enrichment edges are persisted, subgraph should return them without virtual nodes.

    Query order: pattern lookup, one batched edge query per depth (2), the
    PatternEvidenceLink merge, then batched title decoration for any
    episode/evidence nodes.
    """
    tenant_id = uuid4()
    pattern_id = uuid4()
    trigger_node_id = uuid4()

    pattern = SimpleNamespace(id=pattern_id, title="VPN issue", tenant_id=tenant_id)
    e = _edge(
        tenant_id, "trigger", trigger_node_id, "pattern", pattern_id, "trigger_of",
        weight=1.5, metadata={"label": "high cpu"},
    )

    call_count = 0

    async def _execute(q):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _ScalarOneOrNoneResult(pattern)
        if call_count == 2:
            return _ScalarsResult([e])
        # depth-2 batch, PEL merge, decoration: nothing further
        return _ScalarsResult([])

    db = SimpleNamespace(execute=_execute)

    result = await get_pattern_subgraph(db, tenant_id, pattern_id)

    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    node_types = {n["type"] for n in result["nodes"]}
    assert "pattern" in node_types
    assert "trigger" in node_types
    assert result["edges"][0]["type"] == "trigger_of"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_get_pattern_subgraph_merges_evidence_links():
    """Relational PatternEvidenceLink rows appear as clusters/derived_from edges."""
    tenant_id = uuid4()
    pattern_id = uuid4()
    episode_id = uuid4()
    evidence_id = uuid4()

    pattern = SimpleNamespace(id=pattern_id, title="VPN issue", tenant_id=tenant_id)
    link = _pel(pattern_id, episode_id=episode_id, evidence_id=evidence_id)

    call_count = 0

    async def _execute(q):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _ScalarOneOrNoneResult(pattern)
        if call_count == 3:  # PEL merge (call 2 is depth-1 BFS; empty ends traversal)
            return _ScalarsResult([link])
        return _ScalarsResult([])

    db = SimpleNamespace(execute=_execute)

    result = await get_pattern_subgraph(db, tenant_id, pattern_id)

    edge_types = {e["type"] for e in result["edges"]}
    assert edge_types == {"clusters", "derived_from"}
    node_types = {n["type"] for n in result["nodes"]}
    assert node_types == {"pattern", "episode", "evidence"}
    # Rows missing from the tenant-filtered decoration fetch get fallback titles.
    titles = {n["type"]: n["title"] for n in result["nodes"]}
    assert titles["episode"].startswith("Episode ")
    assert titles["evidence"].startswith("Evidence ")


@pytest.mark.asyncio
async def test_get_pattern_subgraph_as_of_skips_evidence_link_merge():
    """PEL has no validity window, so point-in-time queries must not merge it."""
    from datetime import UTC, datetime

    tenant_id = uuid4()
    pattern_id = uuid4()
    pattern = SimpleNamespace(id=pattern_id, title="VPN issue", tenant_id=tenant_id)

    calls = []

    async def _execute(q):
        calls.append(q)
        if len(calls) == 1:
            return _ScalarOneOrNoneResult(pattern)
        return _ScalarsResult([])

    db = SimpleNamespace(execute=_execute)

    result = await get_pattern_subgraph(
        db, tenant_id, pattern_id, as_of=datetime(2026, 1, 1, tzinfo=UTC)
    )

    # The guarantee is the ABSENCE of the PatternEvidenceLink merge under
    # as_of (PEL has no validity window — merging would leak present-day
    # links into a historical view). The 2026-08-07 node-inspector work
    # added an unconditional title-decoration query, so the old exact
    # query-count pin (==2) no longer describes the invariant.
    assert not any("pattern_evidence_links" in str(c) for c in calls)
    assert result["edges"] == []


@pytest.mark.asyncio
async def test_get_pattern_subgraph_not_found():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(None)),
    )

    result = await get_pattern_subgraph(db, tenant_id, uuid4())

    assert result == {"nodes": [], "edges": [], "truncated": False}


# ---- get_entity_subgraph ----

@pytest.mark.asyncio
async def test_get_entity_subgraph_returns_nodes_and_edges():
    tenant_id = uuid4()
    playbook_id = uuid4()
    pattern_id = uuid4()

    e = _edge(tenant_id, "playbook", playbook_id, "pattern", pattern_id, "derived_from")

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([e])),
    )

    result = await get_entity_subgraph(db, tenant_id, "playbook", playbook_id)

    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    node_types = {n["type"] for n in result["nodes"]}
    assert "playbook" in node_types
    assert "pattern" in node_types


@pytest.mark.asyncio
async def test_get_entity_subgraph_empty():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([])),
    )

    result = await get_entity_subgraph(db, tenant_id, "episode", uuid4())

    assert len(result["nodes"]) == 1  # just the origin node
    assert len(result["edges"]) == 0


# ---- get_graph_stats ----

class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


@pytest.mark.asyncio
async def test_get_graph_stats_response_contract():
    """Keys are a frontend contract: GraphStatsResponse in types/graph.ts."""
    tenant_id = uuid4()

    edge_rows = [
        SimpleNamespace(edge_type="belongs_to", count=3),
        SimpleNamespace(edge_type="trigger_of", count=2),
    ]
    node_rows = [
        SimpleNamespace(node_type="pattern", count=2),
        SimpleNamespace(node_type="episode", count=4),
    ]
    results = iter([_RowsResult(edge_rows), _RowsResult(node_rows)])

    async def _execute(q):
        return next(results)

    db = SimpleNamespace(execute=_execute)

    stats = await get_graph_stats(db, tenant_id)

    assert stats["total_edges"] == 5
    assert stats["edge_type_counts"] == {"belongs_to": 3, "trigger_of": 2}
    assert stats["node_type_counts"] == {"pattern": 2, "episode": 4}
