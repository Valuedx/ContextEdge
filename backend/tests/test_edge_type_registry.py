"""Guard: the write vocabulary and the read allowlist cannot drift (F2).

Before this, ``GraphEdge.edge_type`` was free text written from the 26 modules
that import the builder, and the only vocabulary was ``MAF_RELATIONSHIP_TYPES``
— a read-side allowlist. A typo at a write site produced a real, queryable edge
the projection silently dropped. Nothing failed; the graph just knew something
the agent could never see.

Three checks, each a different way the two halves can drift:

1. Every edge type literal at a builder call site is registered. Static, so it
   catches an unregistered type before the writer is ever exercised.
2. Every registered type is either projected by maf.v1 or has a written reason
   for not being. Excluding a type is normal — budget is finite — but it is a
   decision, and decisions get recorded.
3. The allowlist cannot name a type the registry does not know.

The static scan is a net, not a proof: types assembled from constant tables
(``TASK_REFERENCE_EDGE_TYPES``, ``REL_PARENT_EDGE_TYPES``, the materializer's
maps) are invisible to it. ``require_registered`` in the builder is the
backstop, and it is how ``involved_in`` — a literal inside a tuple, missed by
this scan — was caught when F2 first ran the suite.
"""

from __future__ import annotations

import ast
import functools
import pathlib

import pytest

from contextedge.graph.agent.profiles import MAF_RELATIONSHIP_TYPES
from contextedge.graph.edge_types import (
    EDGE_TYPES,
    PROJECTION_EXCLUSIONS,
    UnknownEdgeType,
    require_registered,
)

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "contextedge"

# helper name -> positional index of the edge_type argument. ``_edge`` is the
# materializer's bound wrapper, so it has one fewer positional than the
# module-level helpers.
_HELPERS = {"add_edge": 6, "ensure_edge": 6, "close_edge": 6, "replace_edge": 6, "_edge": 5}


@functools.lru_cache(maxsize=1)
def _literal_edge_types() -> frozenset[tuple[str, str, int]]:
    """``(edge_type, file, line)`` for every literal passed to a builder helper."""
    found: set[tuple[str, str, int]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if not source.strip():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - the suite would not import either
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in _HELPERS:
                continue
            index = _HELPERS[name]
            arg = next((kw.value for kw in node.keywords if kw.arg == "edge_type"), None)
            if arg is None and len(node.args) > index:
                arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add((arg.value, path.name, node.lineno))
    return frozenset(found)


def test_the_scan_finds_call_sites_at_all():
    """A guard on the guard: an AST scan that silently matches nothing would
    make every other assertion here vacuously true."""
    literals = _literal_edge_types()
    assert len(literals) > 30, f"expected many builder call sites, found {len(literals)}"


def test_every_written_edge_type_is_registered():
    unregistered = sorted(
        f"{value!r} at {file}:{line}"
        for value, file, line in _literal_edge_types()
        if value not in EDGE_TYPES
    )
    assert not unregistered, (
        "These edge types are written but not in contextedge.graph.edge_types.EDGE_TYPES:\n  "
        + "\n  ".join(unregistered)
    )


def test_every_registered_type_is_projected_or_excluded_with_a_reason():
    unaccounted = sorted(
        EDGE_TYPES - set(MAF_RELATIONSHIP_TYPES) - set(PROJECTION_EXCLUSIONS)
    )
    assert not unaccounted, (
        "These edge types are registered but neither traversable by maf.v1 nor "
        "excluded with a reason. Add them to MAF_RELATIONSHIP_TYPES, or say why "
        "the agent should not walk them in PROJECTION_EXCLUSIONS:\n  "
        + "\n  ".join(unaccounted)
    )


def test_exclusions_are_reasons_not_placeholders():
    for edge_type, reason in PROJECTION_EXCLUSIONS.items():
        assert edge_type in EDGE_TYPES, f"{edge_type!r} is excluded but not registered"
        assert edge_type not in MAF_RELATIONSHIP_TYPES, (
            f"{edge_type!r} is both projected and excluded"
        )
        assert len(reason.strip()) > 25, f"{edge_type!r} needs a real reason: {reason!r}"


def test_projection_cannot_allow_an_unregistered_type():
    unknown = sorted(set(MAF_RELATIONSHIP_TYPES) - EDGE_TYPES)
    assert not unknown, (
        "maf.v1 allows edge types the write registry does not know — the "
        "projection would be advertising a hop nothing can create:\n  "
        + "\n  ".join(unknown)
    )


def test_require_registered_accepts_known_and_rejects_unknown():
    assert require_registered("affects_ci") == "affects_ci"
    with pytest.raises(UnknownEdgeType, match="Unregistered edge_type"):
        require_registered("afects_ci")


@pytest.mark.asyncio
async def test_builder_refuses_an_unregistered_edge_type():
    """The backstop that catches what the static scan cannot see."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from contextedge.graph.builder import add_edge, close_edge, ensure_edge

    db = SimpleNamespace(add=lambda _obj: None, flush=AsyncMock(), execute=AsyncMock())
    tenant_id = uuid.uuid4()
    args = (db, tenant_id, "evidence", uuid.uuid4(), "entity", uuid.uuid4(), "afects_ci")

    for helper in (add_edge, ensure_edge, close_edge):
        with pytest.raises(UnknownEdgeType):
            await helper(*args)
    # …and the write never reached the session.
    db.flush.assert_not_awaited()
    db.execute.assert_not_awaited()
