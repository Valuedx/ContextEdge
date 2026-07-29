from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.graph.builder import (
    _enrichment_node_id,
    add_contradicts_edge,
    add_edge,
    build_episode_graph,
    ensure_edge,
    link_node_to_identities,
    persist_pattern_enrichment_edges,
)
from contextedge.models.pattern import GraphEdge


from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql.dml import Insert as _PgInsert


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _make_db(side_effects=None):
    """Fake AsyncSession: SELECT results come from *side_effects* in order;
    ensure_edge's ON CONFLICT INSERT ... RETURNING is echoed back as a
    GraphEdge built from the statement's bound values (what the DB would
    return on a successful insert)."""
    added: list = []
    select_results = list(side_effects or [])

    async def _execute(stmt):
        if isinstance(stmt, _PgInsert):
            params = stmt.compile(dialect=postgresql.dialect()).params
            return _ScalarOneOrNoneResult(GraphEdge(**dict(params)))
        return select_results.pop(0)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )
    return db, added


@pytest.mark.asyncio
async def test_add_edge_creates_graph_edge_with_domain_id():
    db, added = _make_db()
    tenant_id = uuid4()
    domain_id = uuid4()

    edge = await add_edge(
        db, tenant_id,
        "episode", uuid4(),
        "pattern", uuid4(),
        "belongs_to",
        weight=1.0,
        domain_id=domain_id,
    )

    assert isinstance(edge, GraphEdge)
    assert edge.domain_id == domain_id
    assert edge.tenant_id == tenant_id
    assert edge.edge_type == "belongs_to"
    assert len(added) == 1
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_edge_domain_id_defaults_to_none():
    db, added = _make_db()
    edge = await add_edge(
        db, uuid4(),
        "playbook", uuid4(),
        "identity", uuid4(),
        "references_identity",
    )
    assert edge.domain_id is None


@pytest.mark.asyncio
async def test_ensure_edge_idempotent():
    tenant_id = uuid4()
    existing_edge = GraphEdge(
        tenant_id=tenant_id,
        source_node_type="episode",
        source_node_id=uuid4(),
        target_node_type="pattern",
        target_node_id=uuid4(),
        edge_type="belongs_to",
        weight=1.0,
    )
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(existing_edge)])

    result = await ensure_edge(
        db, tenant_id,
        existing_edge.source_node_type, existing_edge.source_node_id,
        existing_edge.target_node_type, existing_edge.target_node_id,
        existing_edge.edge_type,
    )

    assert result is existing_edge
    assert len(added) == 0


@pytest.mark.asyncio
async def test_ensure_edge_creates_when_not_found():
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    domain_id = uuid4()

    edge = await ensure_edge(
        db, tenant_id,
        "episode", uuid4(),
        "pattern", uuid4(),
        "belongs_to",
        domain_id=domain_id,
    )

    assert isinstance(edge, GraphEdge)
    assert edge.domain_id == domain_id
    assert edge.edge_type == "belongs_to"
    # Miss path inserts via ON CONFLICT DO NOTHING, not session.add().
    assert added == []
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_link_node_to_identities_deduplicates():
    identity_a = uuid4()
    identity_b = uuid4()
    db, added = _make_db(
        side_effects=[
            _ScalarOneOrNoneResult(None),
            _ScalarOneOrNoneResult(None),
        ]
    )

    edges = await link_node_to_identities(
        db, uuid4(),
        "episode", uuid4(),
        [identity_a, identity_b, identity_a],
        edge_type="affects",
        domain_id=uuid4(),
    )

    assert len(edges) == 2
    target_ids = {e.target_node_id for e in edges}
    assert target_ids == {identity_a, identity_b}


@pytest.mark.asyncio
async def test_build_episode_graph_creates_belongs_to_and_affects():
    tenant_id = uuid4()
    episode_id = uuid4()
    pattern_id = uuid4()
    identity_id = uuid4()
    domain_id = uuid4()

    db, added = _make_db(
        side_effects=[
            _ScalarOneOrNoneResult(None),  # ensure_edge for belongs_to
            _ScalarOneOrNoneResult(None),  # ensure_edge for affects
        ]
    )

    edges = await build_episode_graph(
        db, tenant_id, episode_id, pattern_id, [identity_id],
        domain_id=domain_id,
    )

    assert len(edges) == 2
    edge_types = {e.edge_type for e in edges}
    assert "belongs_to" in edge_types
    assert "affects" in edge_types
    for edge in edges:
        assert edge.domain_id == domain_id


@pytest.mark.asyncio
async def test_add_contradicts_edge_idempotent():
    tenant_id = uuid4()
    existing = GraphEdge(
        tenant_id=tenant_id,
        source_node_type="playbook",
        source_node_id=uuid4(),
        target_node_type="evidence",
        target_node_id=uuid4(),
        edge_type="contradicts",
    )
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(existing)])

    result = await add_contradicts_edge(
        db, tenant_id, existing.source_node_id, existing.target_node_id,
    )

    assert result is existing
    assert len(added) == 0


@pytest.mark.asyncio
async def test_persist_pattern_enrichment_edges():
    tenant_id = uuid4()
    pattern_id = uuid4()
    domain_id = uuid4()
    db, added = _make_db(
        side_effects=[
            _ScalarOneOrNoneResult(None),  # trigger 1
            _ScalarOneOrNoneResult(None),  # entity 1
            _ScalarOneOrNoneResult(None),  # entity 2
            _ScalarOneOrNoneResult(None),  # error 1
            _ScalarOneOrNoneResult(None),  # root_cause 1
        ]
    )

    edges = await persist_pattern_enrichment_edges(
        db, tenant_id, pattern_id, domain_id,
        trigger_conditions=["high cpu"],
        core_entities=["gateway", "load balancer"],
        observed_errors=["connection timeout"],
        root_causes=["misconfigured pool"],
    )

    assert len(edges) == 5
    edge_types = [e.edge_type for e in edges]
    assert "trigger_of" in edge_types
    assert "involved_in" in edge_types
    assert "discovered_in" in edge_types
    assert "causes" in edge_types
    for edge in edges:
        assert edge.weight == 1.5
        assert edge.domain_id == domain_id
        assert edge.target_node_type == "pattern"
        assert edge.target_node_id == pattern_id


@pytest.mark.asyncio
async def test_persist_pattern_enrichment_edges_skips_none_lists():
    db, added = _make_db()

    edges = await persist_pattern_enrichment_edges(
        db, uuid4(), uuid4(), None,
        trigger_conditions=None,
        core_entities=None,
        observed_errors=None,
        root_causes=None,
    )

    assert len(edges) == 0
    assert len(added) == 0


def test_enrichment_node_id_deterministic():
    pattern_id = uuid4()
    id_a = _enrichment_node_id(pattern_id, "trigger", "high cpu")
    id_b = _enrichment_node_id(pattern_id, "trigger", "high cpu")
    id_c = _enrichment_node_id(pattern_id, "trigger", "low memory")
    assert id_a == id_b
    assert id_a != id_c
