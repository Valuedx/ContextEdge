"""CMDB topology as a demand-driven cache, not a replica (Phase 2 hybrid).

ContextEdge does NOT bulk-sync the CMDB. ServiceNow stays the system of
record; this module fetches a CI's ±1-hop neighborhood live
(``cmdb_rel_ci`` + ``cmdb_ci``) and write-through-caches it into the two
tables that already exist — ``entities`` (natural key
``(entity_type, external_system, external_id)``, shared with the Phase 1
reference enrichment so ticket-referenced CIs and topology-fetched CIs
are one row) and ``graph_edges`` (typed entity→entity edges,
parent -[edge_type]-> child).

Freshness is TTL-based instead of sync-based: ``Entity.last_synced_at``
stamps each cached center; a re-fetch closes edges that disappeared
upstream via ``GraphEdge.valid_to`` (never hard-deletes — old incidents
legitimately reference retired topology). When ServiceNow is unreachable,
``lookup_topology`` falls back to the cached neighborhood, explicitly
marked stale with its as-of stamp — degraded, never silently wrong.

Consumers:
- the ``cmdb_topology`` MAF tool and the ``/graph/cmdb-topology`` API
  (live, authoritative, cache-warming as a side effect);
- the ``evaluation.warm_cmdb_topology`` worker task, dispatched after
  correlation when a ticket references a CI whose cache is stale — so the
  agent projection can traverse the operational working set without any
  runtime round-trip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.entity import Entity
from contextedge.models.pattern import GraphEdge
from contextedge.models.source import Source
from contextedge.services.servicenow_reference_service import (
    CI_CLASS_ENTITY_TYPES,
    EntityReference,
    _display,
    _ensure_entity,
    _ref_sys_id,
    extract_ci_traits,
)

logger = structlog.get_logger()

TOPOLOGY_TTL = timedelta(days=7)
# Lookups within this window serve the cache instead of re-fetching —
# an agent asking about the same CI in a loop must not hammer the
# instance (two API calls per live lookup).
FRESH_SERVE_WINDOW = timedelta(minutes=5)
TOPOLOGY_EDGE_ORIGIN = "servicenow_cmdb"

# cmdb_rel_ci type labels are "Parent descriptor::Child descriptor"
# ("Depends on::Used by" = parent depends on child). Normalized on the
# parent descriptor; edges always run parent -[edge_type]-> child.
REL_PARENT_EDGE_TYPES = {
    "depends on": "depends_on",
    "runs on": "runs_on",
    "hosted on": "hosted_on",
    "contains": "contains",
    "uses": "uses",
    "connects to": "connected_to",
}
TOPOLOGY_EDGE_TYPES = tuple(sorted({*REL_PARENT_EDGE_TYPES.values(), "related_to"}))


def normalize_relationship_type(label: str | None) -> str:
    parent_descriptor = (label or "").split("::", 1)[0].strip().lower()
    return REL_PARENT_EDGE_TYPES.get(parent_descriptor, "related_to")


def entity_is_stale(entity: Entity, now: datetime | None = None) -> bool:
    if entity.last_synced_at is None:
        return True
    now = now or datetime.now(UTC)
    last = entity.last_synced_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last) > TOPOLOGY_TTL


async def fetch_ci_neighborhood(connector, sys_id: str) -> dict:
    """Live ±1-hop fetch: relationship rows touching the CI, plus name /
    class details for the CI and every neighbor (two API calls total)."""
    relationships: list[dict] = []
    neighbor_ids: set[str] = set()
    for row in await connector.fetch_ci_relationships(sys_id):
        parent = _ref_sys_id(row.get("parent"))
        child = _ref_sys_id(row.get("child"))
        if parent is None or child is None or sys_id not in (parent, child):
            continue
        if parent == child:
            continue
        label = _display(row.get("type.name")) or ""
        relationships.append(
            {
                "rel_sys_id": row.get("sys_id") or "",
                "parent": parent,
                "child": child,
                "label": label,
                "edge_type": normalize_relationship_type(label),
            }
        )
        neighbor_ids.add(child if parent == sys_id else parent)

    # Center first: on a hub CI the detail fetch is truncated downstream,
    # and losing the center's own name/class is the worst possible cut.
    detail_ids = [sys_id] + sorted(neighbor_ids - {sys_id})
    details: dict[str, dict] = {}
    for item in await connector.fetch_ci_details(detail_ids):
        item_sys_id = _ref_sys_id(item.get("sys_id"))
        if item_sys_id is not None:
            details[item_sys_id] = item
    return {"sys_id": sys_id, "relationships": relationships, "ci_details": details}


async def cache_neighborhood(
    db: AsyncSession, tenant_id: uuid.UUID, neighborhood: dict
) -> dict:
    """Write-through: upsert CI entities, ensure parent→child edges, close
    topology edges that disappeared upstream, stamp the center's TTL."""
    now = datetime.now(UTC)
    center_sys_id = neighborhood["sys_id"]
    details = neighborhood["ci_details"]
    relationships = neighborhood["relationships"]

    # A sys_id ServiceNow knows nothing about (no relationships AND no CI
    # detail row) must not materialize a junk entity — an agent passing a
    # hallucinated-but-well-formed sys_id would otherwise pollute the
    # entity table with hex-named rows stamped fresh.
    if not relationships and center_sys_id not in details:
        return {"entities": 0, "edges_ensured": 0, "edges_closed": 0, "skipped_unknown_ci": True}

    all_sys_ids = {center_sys_id}
    for rel in relationships:
        all_sys_ids.add(rel["parent"])
        all_sys_ids.add(rel["child"])

    entities_by_sys_id: dict[str, Entity] = {}
    for sid in sorted(all_sys_ids):
        detail = details.get(sid, {})
        ci_class = _display(detail.get("sys_class_name")) or ""
        # C2: criticality + owning group ride into entity attributes and
        # from there into the agent projection — blast radius without
        # criticality cannot be prioritized, and remediation risk on a
        # Tier-1 CI cannot be assessed.
        attributes = {"ci_class": ci_class} if ci_class else {}
        criticality = _display(detail.get("busines_criticality"))
        if criticality:
            attributes["criticality"] = criticality
        support_group = _display(detail.get("support_group.name"))
        if support_group:
            attributes["support_group"] = support_group
        entities_by_sys_id[sid] = await _ensure_entity(
            db,
            tenant_id,
            EntityReference(
                sys_id=sid,
                name=_display(detail.get("name")) or sid,
                entity_type=CI_CLASS_ENTITY_TYPES.get(ci_class, "configuration_item"),
                edge_type="",
                attributes=attributes,
                traits=extract_ci_traits(detail, prefix=""),
            ),
        )
        # B1: cached topology CIs join the class taxonomy too.
        from contextedge.services.entity_class_service import (
            ensure_entity_class_edges,
        )

        await ensure_entity_class_edges(
            db, tenant_id, entities_by_sys_id[sid], ci_class or None
        )

    seen_rel_ids: set[str] = set()
    edges_ensured = 0
    for rel in relationships:
        source = entities_by_sys_id[rel["parent"]]
        target = entities_by_sys_id[rel["child"]]
        await ensure_edge(
            db,
            tenant_id,
            "entity",
            source.id,
            "entity",
            target.id,
            rel["edge_type"],
            metadata={
                "origin": TOPOLOGY_EDGE_ORIGIN,
                "rel_sys_id": rel["rel_sys_id"],
                "label": rel["label"],
            },
        )
        if rel["rel_sys_id"]:
            seen_rel_ids.add(rel["rel_sys_id"])
        edges_ensured += 1

    # Close cached topology edges touching the center that the live fetch
    # no longer returned — the relationship was deleted upstream. valid_to
    # end-dates them; history referencing old topology stays intact.
    center = entities_by_sys_id[center_sys_id]
    edges_closed = 0
    stale_edges = (
        await db.execute(
            select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.valid_to.is_(None),
                GraphEdge.edge_type.in_(TOPOLOGY_EDGE_TYPES),
                or_(
                    (GraphEdge.source_node_type == "entity")
                    & (GraphEdge.source_node_id == center.id),
                    (GraphEdge.target_node_type == "entity")
                    & (GraphEdge.target_node_id == center.id),
                ),
            )
        )
    ).scalars().all()
    for edge in stale_edges:
        meta = edge.metadata_extra or {}
        if meta.get("origin") != TOPOLOGY_EDGE_ORIGIN:
            continue
        rel_sys_id = meta.get("rel_sys_id")
        # Only close edges we can positively match to a now-absent
        # relationship. An edge cached without a rel sys_id would
        # otherwise be closed and re-created on every refresh — churn,
        # not correctness.
        if not rel_sys_id or rel_sys_id in seen_rel_ids:
            continue
        edge.valid_to = now
        edges_closed += 1

    center.last_synced_at = now
    await db.flush()
    return {
        "entities": len(entities_by_sys_id),
        "edges_ensured": edges_ensured,
        "edges_closed": edges_closed,
    }


async def resolve_ci_entity(
    db: AsyncSession, tenant_id: uuid.UUID, term: str
) -> Entity | None:
    """A CI by sys_id or by exact (case-insensitive) display name."""
    sys_id = _ref_sys_id(term)
    if sys_id is not None:
        return (
            await db.execute(
                select(Entity)
                .where(
                    Entity.tenant_id == tenant_id,
                    Entity.external_system == "servicenow",
                    Entity.external_id == sys_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    # Name lookup is deliberately system-agnostic: Jira components and
    # JSM services (external_system "jira_sm") are CI-like anchors too,
    # and change-risk assessment must find them by name. The sys_id path
    # above stays servicenow-scoped — 32-hex ids are a ServiceNow shape.
    return (
        await db.execute(
            select(Entity)
            .where(
                Entity.tenant_id == tenant_id,
                func.lower(Entity.name) == term.strip().lower(),
            )
            .order_by(Entity.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


async def resolve_ci_entity_checked(
    db: AsyncSession, tenant_id: uuid.UUID, term: str
) -> tuple[Entity | None, bool]:
    """``(entity, ambiguous)`` — for WRITE paths that must refuse to
    guess. ``resolve_ci_entity`` above keeps its oldest-match behavior
    for read-only assessments (change risk, fix applicability), where a
    best-effort answer beats none; a write against the wrong same-named
    CI is how topology rots, so writers use this instead."""
    sys_id = _ref_sys_id(term)
    if sys_id is not None:
        return await resolve_ci_entity(db, tenant_id, term), False
    matches = (
        (
            await db.execute(
                select(Entity)
                .where(
                    Entity.tenant_id == tenant_id,
                    func.lower(Entity.name) == term.strip().lower(),
                )
                .order_by(Entity.created_at)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(matches) > 1:
        return None, True
    return (matches[0] if matches else None), False


async def load_servicenow_connector(
    db: AsyncSession, tenant_id: uuid.UUID, source_id: uuid.UUID | None = None
):
    """Connector for the tenant's active ServiceNow source (or a specific
    one). Reuses the sync worker's credential-decrypting factory."""
    from contextedge.services.sync_worker_service import _load_connector

    q = select(Source).where(
        Source.tenant_id == tenant_id,
        Source.source_type == "servicenow",
        Source.is_active.is_(True),
    )
    if source_id is not None:
        q = q.where(Source.id == source_id)
    # Deterministic pick for multi-instance tenants: oldest source wins.
    source = (
        await db.execute(q.order_by(Source.created_at).limit(1))
    ).scalars().first()
    if source is None:
        raise ValueError("no_active_servicenow_source")
    return await _load_connector(db, source)


def _cached_neighbor_payload(entity: Entity, edge: GraphEdge, other: Entity | None) -> dict:
    center_is_parent = edge.source_node_id == entity.id
    return {
        "name": other.name if other is not None else None,
        "sys_id": other.external_id if other is not None else None,
        "ci_class": (other.attributes or {}).get("ci_class") if other is not None else None,
        "relationship": edge.edge_type,
        "center_role": "parent" if center_is_parent else "child",
    }


# Response-payload ceiling for cached lookups: 2× the live truncation
# bound, since refreshes can accrete beyond one fetch's 200 relationships.
CACHED_NEIGHBORS_MAX = 400


async def _cached_topology(
    db: AsyncSession, tenant_id: uuid.UUID, entity: Entity
) -> list[dict]:
    edges = (
        await db.execute(
            select(GraphEdge)
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.valid_to.is_(None),
                GraphEdge.edge_type.in_(TOPOLOGY_EDGE_TYPES),
                or_(
                    (GraphEdge.source_node_type == "entity")
                    & (GraphEdge.source_node_id == entity.id),
                    (GraphEdge.target_node_type == "entity")
                    & (GraphEdge.target_node_id == entity.id),
                ),
            )
            .limit(CACHED_NEIGHBORS_MAX)
        )
    ).scalars().all()
    topology_edges = [
        e for e in edges if (e.metadata_extra or {}).get("origin") == TOPOLOGY_EDGE_ORIGIN
    ]
    other_ids = {
        e.target_node_id if e.source_node_id == entity.id else e.source_node_id
        for e in topology_edges
    }
    others: dict[uuid.UUID, Entity] = {}
    if other_ids:
        rows = (
            await db.execute(
                select(Entity).where(
                    Entity.tenant_id == tenant_id, Entity.id.in_(tuple(other_ids))
                )
            )
        ).scalars().all()
        others = {row.id: row for row in rows}
    return [
        _cached_neighbor_payload(
            entity,
            edge,
            others.get(
                edge.target_node_id
                if edge.source_node_id == entity.id
                else edge.source_node_id
            ),
        )
        for edge in topology_edges
    ]


async def lookup_topology(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    term: str,
    source_id: uuid.UUID | None = None,
) -> dict:
    """Live CI neighborhood with write-through caching; cached fallback
    (explicitly marked stale) when ServiceNow is unreachable."""
    term = (term or "").strip()
    if not term:
        return {"error": {"code": "invalid_ci", "message": "Provide a CI name or sys_id."}}

    entity = await resolve_ci_entity(db, tenant_id, term)
    if entity is not None and entity.external_system not in (None, "servicenow"):
        # Name resolution is system-agnostic (Jira components resolve
        # too), but live topology is a ServiceNow capability — a Jira
        # entity's id must never reach a sysparm_query.
        return {
            "error": {
                "code": "topology_unsupported_for_source",
                "message": (
                    f"'{entity.name}' is a {entity.external_system} entity; "
                    "CMDB topology is only available for ServiceNow CIs."
                ),
            }
        }
    sys_id = entity.external_id if entity is not None else _ref_sys_id(term)
    if sys_id is None:
        return {
            "error": {
                "code": "unknown_ci",
                "message": (
                    f"No CI named '{term}' is known yet. Pass its 32-hex "
                    "ServiceNow sys_id to fetch it directly."
                ),
            }
        }

    # Just-fetched CIs serve straight from cache — honest (as_of included)
    # and keeps repeated agent lookups off the instance.
    if entity is not None and entity.last_synced_at is not None:
        last = entity.last_synced_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (datetime.now(UTC) - last) <= FRESH_SERVE_WINDOW:
            return {
                "source": "cache",
                "ci_found": True,
                "stale": False,
                "as_of": entity.last_synced_at.isoformat(),
                "center": {
                    "name": entity.name,
                    "sys_id": entity.external_id,
                    "ci_class": (entity.attributes or {}).get("ci_class"),
                },
                "neighbors": await _cached_topology(db, tenant_id, entity),
            }

    # Failure domain 1 — ServiceNow I/O. No DB writes have happened yet, so
    # the session is clean for the cached fallback.
    try:
        connector = await load_servicenow_connector(db, tenant_id, source_id)
        neighborhood = await fetch_ci_neighborhood(connector, sys_id)
    except Exception as exc:
        logger.warning(
            "cmdb_topology.live_fetch_failed",
            tenant_id=str(tenant_id),
            sys_id=sys_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if entity is None:
            return {
                "error": {
                    "code": "servicenow_unavailable",
                    "message": "ServiceNow is unreachable and this CI has no cached topology yet.",
                }
            }
        neighbors = await _cached_topology(db, tenant_id, entity)
        return {
            "source": "cache",
            "ci_found": True,
            "stale": True,
            "as_of": entity.last_synced_at.isoformat() if entity.last_synced_at else None,
            "center": {
                "name": entity.name,
                "sys_id": entity.external_id,
                "ci_class": (entity.attributes or {}).get("ci_class"),
            },
            "neighbors": neighbors,
        }

    # Failure domain 2 — the write-through. Live data is already in hand:
    # a cache failure must degrade to "live result, not cached", never to
    # the stale-cache fallback (and never poison the session — SAVEPOINT).
    counts: dict
    try:
        async with db.begin_nested():
            counts = await cache_neighborhood(db, tenant_id, neighborhood)
    except Exception as exc:
        logger.warning(
            "cmdb_topology.cache_write_failed",
            tenant_id=str(tenant_id),
            sys_id=sys_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        counts = {"cache_write_failed": True}

    center_detail = neighborhood["ci_details"].get(sys_id, {})
    neighbors = []
    for rel in neighborhood["relationships"]:
        other_sys_id = rel["child"] if rel["parent"] == sys_id else rel["parent"]
        other_detail = neighborhood["ci_details"].get(other_sys_id, {})
        neighbors.append(
            {
                "name": _display(other_detail.get("name")) or other_sys_id,
                "sys_id": other_sys_id,
                "ci_class": _display(other_detail.get("sys_class_name")),
                "relationship": rel["edge_type"],
                "center_role": "parent" if rel["parent"] == sys_id else "child",
            }
        )
    return {
        "source": "live",
        # False when ServiceNow returned neither a detail row nor any
        # relationship for this sys_id — "does not exist" rather than
        # "exists but isolated".
        "ci_found": bool(neighborhood["relationships"]) or sys_id in neighborhood["ci_details"],
        "center": {
            "name": _display(center_detail.get("name"))
            or (entity.name if entity is not None else sys_id),
            "sys_id": sys_id,
            "ci_class": _display(center_detail.get("sys_class_name")),
        },
        "neighbors": neighbors,
        "cache": counts,
    }
