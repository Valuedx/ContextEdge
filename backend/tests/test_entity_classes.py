"""B1 entity class taxonomy: mapping, fallback, edge materialization,
seed integrity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.entity_class_service import (
    FALLBACK_CLASS_KEY,
    SERVICENOW_CLASS_TO_CANONICAL,
    canonical_class_for,
    ensure_entity_class_edges,
)


def test_known_classes_map_and_unknown_falls_back():
    assert canonical_class_for("cmdb_ci_computer") == "endpoint"
    assert canonical_class_for("cmdb_ci_win_server") == "server"
    assert canonical_class_for("cmdb_ci_ip_router") == "network_device"
    # Unknown classes must degrade to current behavior: the root class.
    assert canonical_class_for("cmdb_ci_toaster") == FALLBACK_CLASS_KEY
    assert canonical_class_for(None) == FALLBACK_CLASS_KEY
    assert canonical_class_for("") == FALLBACK_CLASS_KEY


def test_seed_hierarchy_is_consistent():
    """Every mapped canonical key exists in the migration seed, every
    parent reference resolves, deterministic ids match uuid5, and the
    tree is acyclic up to the root."""
    import importlib.util
    import uuid as uuid_mod
    from pathlib import Path

    path = next(
        (Path(__file__).parent.parent / "alembic" / "versions").glob(
            "0042_entity_classes*.py"
        )
    )
    spec = importlib.util.spec_from_file_location("migration_0042", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    keys = {row[0] for row in mig._SEED}
    for canonical in set(SERVICENOW_CLASS_TO_CANONICAL.values()):
        assert canonical in keys
    assert FALLBACK_CLASS_KEY in keys

    parents = {row[0]: row[2] for row in mig._SEED}
    for key, parent in parents.items():
        assert parent is None or parent in keys
        # Walk to root, bounded.
        seen = set()
        cursor = key
        while parents.get(cursor) is not None:
            assert cursor not in seen, f"cycle at {cursor}"
            seen.add(cursor)
            cursor = parents[cursor]
        assert cursor == "configuration_item" or parents[cursor] is None

    for key, expected in mig._IDS.items():
        assert (
            str(uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, f"contextedge:entity_class:{key}"))
            == expected
        )


@pytest.mark.asyncio
async def test_edges_materialize_instance_and_chain():
    tenant_id = uuid4()
    entity = SimpleNamespace(id=uuid4())
    laptop = SimpleNamespace(
        id=uuid4(), canonical_key="laptop", parent_class_id=uuid4()
    )
    portable = SimpleNamespace(
        id=laptop.parent_class_id, canonical_key="portable_endpoint",
        parent_class_id=uuid4(),
    )
    endpoint = SimpleNamespace(
        id=portable.parent_class_id, canonical_key="endpoint", parent_class_id=None
    )
    by_id = {portable.id: portable, endpoint.id: endpoint}

    async def execute(stmt):
        result = Mock()
        result.scalar_one_or_none.return_value = laptop
        return result

    async def get(model, pk):
        return by_id.get(pk)

    edges = []

    async def fake_ensure_edge(db, tid, st, sid, tt, tid2, edge_type, **kw):
        edges.append((st, sid, tt, tid2, edge_type))

    db = SimpleNamespace(execute=execute, get=AsyncMock(side_effect=get))
    with patch(
        "contextedge.services.entity_class_service.ensure_edge",
        side_effect=fake_ensure_edge,
    ):
        key = await ensure_entity_class_edges(db, tenant_id, entity, "whatever")

    # "whatever" maps to the fallback key; the fake returns the laptop
    # row regardless so the 3-level chain materialization is exercised.
    assert key == FALLBACK_CLASS_KEY
    assert edges[0] == ("entity", entity.id, "entity_class", laptop.id, "instance_of")
    assert ("entity_class", laptop.id, "entity_class", portable.id, "subclass_of") in edges
    assert ("entity_class", portable.id, "entity_class", endpoint.id, "subclass_of") in edges
    assert len(edges) == 3


@pytest.mark.asyncio
async def test_missing_taxonomy_is_logged_noop():
    """Pre-0042 database: no class rows — entity keeps today's behavior."""
    tenant_id = uuid4()

    async def execute(stmt):
        result = Mock()
        result.scalar_one_or_none.return_value = None
        return result

    edges = []
    with patch(
        "contextedge.services.entity_class_service.ensure_edge",
        side_effect=AsyncMock(),
    ) as edge_mock:
        key = await ensure_entity_class_edges(
            SimpleNamespace(execute=execute), tenant_id, SimpleNamespace(id=uuid4()), "cmdb_ci_computer"
        )
    assert key is None
    assert not edge_mock.called
