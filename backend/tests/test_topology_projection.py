"""C1/C2: CMDB topology edges and criticality facts in maf.v1."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from contextedge.graph.agent.hydrators import hydrate_node
from contextedge.graph.agent.profiles import MAF_RELATIONSHIP_TYPES


def test_topology_edges_are_projectable_but_not_the_catchall():
    """cmdb_topology_service has cached these entity<->entity edges since
    it shipped; the blast-radius walk needs them projectable.
    related_to (unmapped semantics, hub fan-out) stays excluded."""
    for edge in ("depends_on", "runs_on", "hosted_on", "contains", "uses", "connected_to"):
        assert edge in MAF_RELATIONSHIP_TYPES, edge
    assert "related_to" not in MAF_RELATIONSHIP_TYPES


def _entity(attributes):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="payment-db-primary",
        entity_type="database",
        environment="production",
        business_unit=None,
        data_domain=None,
        is_active=True,
        last_synced_at=None,
        confidence=None,
        attributes=attributes,
        created_at=None,
        updated_at=None,
    )


def test_entity_facts_carry_criticality_and_owner():
    node = hydrate_node(
        "entity",
        _entity({"criticality": "1 - most critical", "support_group": "Database", "ci_class": "cmdb_ci_database"}),
    )
    assert node.facts["criticality"] == "1 - most critical"
    assert node.facts["support_group"] == "Database"
    assert node.facts["ci_class"] == "cmdb_ci_database"


def test_entity_facts_never_leak_the_raw_attributes_blob():
    node = hydrate_node(
        "entity",
        _entity({"_inventory_snapshot": {"browser": "119"}, "criticality": "2 - somewhat critical"}),
    )
    assert node.facts["criticality"] == "2 - somewhat critical"
    assert "_inventory_snapshot" not in node.facts
    assert not any(k.startswith("_") for k in node.facts)


def test_entity_facts_survive_missing_attributes():
    node = hydrate_node("entity", _entity(None))
    assert "criticality" not in node.facts
