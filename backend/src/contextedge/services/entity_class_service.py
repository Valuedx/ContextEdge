"""Entity → class-taxonomy wiring (backlog B1).

Maps a connector's raw CI class to the canonical taxonomy and
materializes the graph edges applicability traversal needs:

- ``entity ──instance_of──> entity_class``
- ``entity_class ──subclass_of──> entity_class`` (the chain up to the
  root, written per tenant on first use so a tenant's graph is
  self-contained)

Unknown classes degrade to ``configuration_item`` — exactly mirroring
the entity_type fallback that already exists, so a CI class the map has
never seen behaves as today plus a root-class edge.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.entity import Entity
from contextedge.models.entity_class import EntityClass

logger = structlog.get_logger()

FALLBACK_CLASS_KEY = "configuration_item"

# Conservative ServiceNow ``sys_class_name`` → canonical class map.
# Only classes whose meaning is unambiguous are mapped; everything else
# falls back. Extending the map is additive and safe — entities are
# re-linked on their next reference (ensure_edge is idempotent).
SERVICENOW_CLASS_TO_CANONICAL = {
    "cmdb_ci_computer": "endpoint",
    "cmdb_ci_pc_hardware": "endpoint",
    "cmdb_ci_server": "server",
    "cmdb_ci_win_server": "server",
    "cmdb_ci_linux_server": "server",
    "cmdb_ci_unix_server": "server",
    "cmdb_ci_db_mssql_server": "database_server",
    "cmdb_ci_db_ora_listener": "database_server",
    "cmdb_ci_database": "database",
    "cmdb_ci_db_instance": "database",
    "cmdb_ci_appl": "application",
    "cmdb_ci_service": "business_service",
    "cmdb_ci_service_auto": "business_service",
    "cmdb_ci_netgear": "network_device",
    "cmdb_ci_ip_router": "network_device",
    "cmdb_ci_ip_switch": "network_device",
    "cmdb_ci_firewall_network": "network_device",
    "cmdb_ci_lb_bigip": "network_device",
}

# Subclass chains are short (root ≤ 4 hops in the seed); the bound only
# guards against a future cyclic mis-seed.
MAX_CHAIN_DEPTH = 8


def canonical_class_for(ci_class: str | None) -> str:
    return SERVICENOW_CLASS_TO_CANONICAL.get(ci_class or "", FALLBACK_CLASS_KEY)


async def ensure_entity_class_edges(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: Entity,
    ci_class: str | None,
) -> str | None:
    """Link an entity to its canonical class and materialize the
    subclass chain for this tenant. Returns the canonical key, or None
    when the taxonomy is absent (pre-0042 database) — in which case the
    entity simply has no class edges, i.e. today's behavior."""
    key = canonical_class_for(ci_class)
    entity_class = (
        await db.execute(
            select(EntityClass).where(EntityClass.canonical_key == key)
        )
    ).scalar_one_or_none()
    if entity_class is None:
        logger.warning(
            "entity_class.missing_taxonomy_row",
            canonical_key=key,
        )
        return None

    await ensure_edge(
        db,
        tenant_id,
        "entity",
        entity.id,
        "entity_class",
        entity_class.id,
        "instance_of",
    )

    current = entity_class
    for _ in range(MAX_CHAIN_DEPTH):
        if current.parent_class_id is None:
            break
        parent = await db.get(EntityClass, current.parent_class_id)
        if parent is None:
            break
        await ensure_edge(
            db,
            tenant_id,
            "entity_class",
            current.id,
            "entity_class",
            parent.id,
            "subclass_of",
        )
        current = parent
    return key
