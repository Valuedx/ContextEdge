"""Blueprint gaps 2+4: propose_dependency governance, monitoring index."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from contextedge.graph.agent.profiles import MAF_RELATIONSHIP_TYPES
from contextedge.services import dependency_inference_service as svc


def test_proposed_edges_are_invisible_to_the_agent():
    """Agent-discovered topology must NOT be traversable until reviewed —
    proposed_depends_on stays OUT of the maf.v1 allowlist by design."""
    assert "proposed_depends_on" not in MAF_RELATIONSHIP_TYPES


@pytest.mark.asyncio
async def test_monitoring_sources_merge_into_entity_attributes():
    ci = uuid.uuid4()
    entity = SimpleNamespace(
        tenant_id=uuid.uuid4(), attributes={"monitoring_sources": ["splunk"]}
    )
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [(ci, ["em_alert", "splunk"])]
    db.execute = AsyncMock(return_value=rows)
    db.get = AsyncMock(return_value=entity)
    db.flush = AsyncMock()
    entity.tenant_id = tenant = uuid.uuid4()
    updated = await svc.index_monitoring_sources(db, tenant)
    assert updated == 1
    assert entity.attributes["monitoring_sources"] == ["em_alert", "splunk"]


@pytest.mark.asyncio
async def test_unchanged_coverage_writes_nothing():
    ci = uuid.uuid4()
    tenant = uuid.uuid4()
    entity = SimpleNamespace(tenant_id=tenant, attributes={"monitoring_sources": ["splunk"]})
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [(ci, ["splunk"])]
    db.execute = AsyncMock(return_value=rows)
    db.get = AsyncMock(return_value=entity)
    db.flush = AsyncMock()
    assert await svc.index_monitoring_sources(db, tenant) == 0
    db.flush.assert_not_awaited()


def test_monitoring_sources_project_as_entity_fact():
    from contextedge.graph.agent.hydrators import hydrate_node

    node = hydrate_node(
        "entity",
        SimpleNamespace(
            id=uuid.uuid4(), name="sqlprod01", entity_type="database",
            environment=None, business_unit=None, data_domain=None,
            is_active=True, last_synced_at=None, confidence=None,
            attributes={"monitoring_sources": ["datadog", "splunk"]},
            created_at=None, updated_at=None,
        ),
    )
    assert node.facts["monitoring_sources"] == ["datadog", "splunk"]
