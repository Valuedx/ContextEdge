"""The capability declaration has to stay true, or coverage lies confidently.

``source_capabilities`` promises what each connector can reach. A promise that
drifts from the code is worse than no promise: coverage would report
``unsupported`` for a relation a connector happily emits, and an agent would
stop asking a question that had an answer.

Record kinds cannot drift -- they are read from ``evidence_typing``. Relations
can, because there is no single structure to read them from, so they are
cross-checked here against the reference services that actually emit them.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from contextedge.services import (
    jira_reference_service,
    sapphireims_reference_service,
    servicenow_reference_service,
    zoho_desk_reference_service,
)
from contextedge.services.source_capabilities import (
    CANONICAL_RELATIONS,
    CAPABILITIES,
    capability_for,
    object_types_for,
    record_kinds_for,
    supports_record_kind,
    supports_relation,
)

# source_type -> the module whose reference service emits its relations.
REFERENCE_SERVICES = {
    "servicenow": servicenow_reference_service,
    "jira_sm": jira_reference_service,
    "zoho_desk": zoho_desk_reference_service,
    "sapphireims": sapphireims_reference_service,
}


def _code_strings(module) -> set[str]:
    """String literals in executable code, excluding docstrings.

    Docstrings are excluded on purpose: several of these modules discuss
    relations they do *not* emit -- Jira's header explains that its
    ``caused_by_change`` is "the same edge type ServiceNow emits" -- so
    matching on prose would let one module's commentary satisfy another
    module's declaration.
    """
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    }


@pytest.mark.parametrize("source_type", sorted(REFERENCE_SERVICES))
def test_declared_relations_are_actually_emitted(source_type):
    """Every declared relation appears in the service that would emit it.

    Catches the optimistic direction: claiming a capability the code cannot
    deliver, which makes coverage report `empty` where the truth is that
    nothing will ever populate it.
    """
    declared = capability_for(source_type).relations
    emitted = _code_strings(REFERENCE_SERVICES[source_type])
    missing = declared - emitted
    assert not missing, (
        f"{source_type} declares {sorted(missing)} but its reference service "
        f"never mentions them in code"
    )


@pytest.mark.parametrize("source_type", sorted(REFERENCE_SERVICES))
def test_emitted_relations_are_declared(source_type):
    """And the pessimistic direction: a connector that gains a relation
    without updating its declaration keeps being reported as unable to
    supply it, so the capability silently never gets used."""
    declared = capability_for(source_type).relations
    emitted = _code_strings(REFERENCE_SERVICES[source_type]) & CANONICAL_RELATIONS
    undeclared = emitted - declared
    assert not undeclared, (
        f"{source_type} emits {sorted(undeclared)} but does not declare them"
    )


def test_every_declared_relation_is_canonical():
    """A typo in a declaration is a relation that matches nothing, forever,
    without erroring. The closed set turns that into a test failure."""
    for source_type, capability in CAPABILITIES.items():
        stray = capability.relations - CANONICAL_RELATIONS
        assert not stray, f"{source_type} declares non-canonical {sorted(stray)}"


def test_record_kinds_are_derived_not_declared():
    """The whole point of reading evidence_typing: a connector that learns a
    new object type gains the capability without anyone editing this module."""
    assert "change" in record_kinds_for("servicenow")
    assert "problem" in record_kinds_for("servicenow")
    assert "alert" in record_kinds_for("servicenow")
    # A help desk is not an ITSM suite, and coverage depends on knowing it.
    assert "change" not in record_kinds_for("zoho_desk")
    assert "problem" not in record_kinds_for("zoho_desk")
    assert "kb_article" in record_kinds_for("zoho_desk")


def test_object_types_resolve_back_to_the_table_that_supplies_them():
    """Coverage needs this to tell 'the connector can supply changes' from
    'this source is syncing the table changes come from'."""
    assert object_types_for("servicenow", "change") == frozenset({"change_request"})
    assert object_types_for("servicenow", "alert") == frozenset(
        {"em_alert", "em_alert_rollup"}
    )
    assert object_types_for("zoho_desk", "change") == frozenset()


def test_unknown_connector_supplies_nothing():
    """The safe reading. An unknown source must not be assumed capable, or
    coverage reports `empty` for a connector that cannot answer at all."""
    capability = capability_for("some_future_itsm")
    assert capability.relations == frozenset()
    assert capability.topology is False
    assert not supports_relation("some_future_itsm", "caused_by_change")
    assert not supports_record_kind("some_future_itsm", "change")


def test_only_servicenow_claims_topology():
    """Blast radius needs CI-to-CI edges, and `cmdb_rel_ci` is the only
    source of them today. If this ever fails, a second CMDB arrived and the
    topology facet's detail text needs revisiting."""
    with_topology = {st for st, c in CAPABILITIES.items() if c.topology}
    assert with_topology == {"servicenow"}


def test_every_registered_connector_has_a_declaration():
    """A connector with no entry defaults to 'supplies nothing', which is safe
    but silent. Registering one should force the question to be answered."""
    from contextedge.connectors import registry

    # The registry populates lazily, so a bare read returns an empty map and
    # this test would pass by finding nothing to check.
    registry._register_connectors()

    undeclared = set(registry.CONNECTOR_CLASSES) - set(CAPABILITIES)
    assert not undeclared, (
        f"connectors registered but not declared in source_capabilities: "
        f"{sorted(undeclared)}"
    )
