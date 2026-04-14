from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.graph.queries import get_entity_subgraph, get_neighbors, get_pattern_subgraph
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

@pytest.mark.asyncio
async def test_get_pattern_subgraph_returns_persisted_edges():
    """After enrichment edges are persisted, subgraph should return them without virtual nodes."""
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
        return _ScalarsResult([e])

    db = SimpleNamespace(execute=_execute)

    result = await get_pattern_subgraph(db, tenant_id, pattern_id)

    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    node_types = {n["type"] for n in result["nodes"]}
    assert "pattern" in node_types
    assert "trigger" in node_types
    assert result["edges"][0]["type"] == "trigger_of"


@pytest.mark.asyncio
async def test_get_pattern_subgraph_not_found():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(None)),
    )

    result = await get_pattern_subgraph(db, tenant_id, uuid4())

    assert result == {"nodes": [], "edges": []}


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
