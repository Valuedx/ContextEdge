from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from contextedge.deps import AuthUser, DbSession
from contextedge.graph.agent.contracts import AgentGraphRequest, AgentGraphSubset
from contextedge.graph.agent.service import (
    AgentGraphProjectionService,
    build_agent_graph_scope,
)
from contextedge.graph.queries import get_entity_subgraph, get_graph_stats, get_neighbors
from contextedge.graph.temporal import normalize_graph_as_of

router = APIRouter()


@router.post("/agent-subsets", response_model=AgentGraphSubset)
async def create_agent_graph_subset(
    body: AgentGraphRequest,
    db: DbSession,
    user: AuthUser,
):
    """Return a ranked, bounded, authorization-filtered agent graph projection."""
    effective = body.model_copy(update={"as_of": normalize_graph_as_of(body.as_of)})
    scope = await build_agent_graph_scope(db, user, effective.domain_id)
    return await AgentGraphProjectionService(db).project(
        effective,
        scope,
        invocation_mode="api",
    )


@router.get("/cmdb-topology")
async def cmdb_topology(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="CI display name (e.g. vpn-gw-east-01) or 32-hex sys_id.",
    ),
):
    """Live ±1-hop CMDB neighborhood for a CI, write-through cached into
    entities / graph_edges; falls back to the cached view (marked stale)
    when ServiceNow is unreachable."""
    from contextedge.services.cmdb_topology_service import lookup_topology

    return await lookup_topology(db, user.tenant_id, ci)


@router.get("/fix-applicability")
async def fix_applicability(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(..., min_length=1, max_length=500),
):
    """Deterministic fix-applicability assessment for a CI: which known
    fixes validate against its recorded traits, at which level of the
    7-level ladder, and whether review is required (B4)."""
    user.require_role("knowledge_manager")
    from contextedge.services.cmdb_topology_service import resolve_ci_entity
    from contextedge.services.fix_applicability_service import (
        assess_fix_applicability,
    )

    entity = await resolve_ci_entity(db, user.tenant_id, ci)
    if entity is None:
        raise HTTPException(status_code=404, detail="CI not found")
    return await assess_fix_applicability(db, user.tenant_id, entity)


@router.get("/change-risk")
async def change_risk(
    db: DbSession,
    user: AuthUser,
    ci: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="CI display name (e.g. vpn-gw-east-01) or 32-hex sys_id.",
    ),
    window_days: int = Query(180, ge=1, le=730),
):
    """Deterministic change-risk profile for a CI from operational history:
    change→incident blame rate, incident pressure, alert activity, and
    cached blast radius — every factor explained."""
    from contextedge.services.change_risk_service import assess_change_risk

    return await assess_change_risk(db, user.tenant_id, ci, window_days=window_days)


@router.get("/neighbors")
async def graph_neighbors(
    db: DbSession,
    user: AuthUser,
    node_type: str = Query(
        ...,
        description="Type of the origin node (e.g. playbook, pattern, episode)",
    ),
    node_id: UUID = Query(..., description="ID of the origin node"),
    edge_type: str | None = Query(None, description="Filter by edge type"),
    max_depth: int = Query(1, ge=1, le=3, description="BFS traversal depth (1-3)"),
    domain_id: UUID | None = Query(
        None,
        description="Scope to a domain (includes domain-less edges)",
    ),
    as_of: datetime | None = Query(None, description="Point-in-time traversal timestamp"),
):
    """Return neighboring nodes reachable via graph edges up to *max_depth* hops."""
    return await get_neighbors(
        db,
        tenant_id=user.tenant_id,
        node_type=node_type,
        node_id=node_id,
        edge_type=edge_type,
        max_depth=max_depth,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )


@router.get("/subgraph/{entity_type}/{entity_id}")
async def graph_subgraph(
    entity_type: str,
    entity_id: UUID,
    db: DbSession,
    user: AuthUser,
    max_depth: int = Query(1, ge=1, le=3),
    domain_id: UUID | None = Query(None),
    as_of: datetime | None = Query(None),
):
    """Return the subgraph around any entity as nodes + edges suitable for visualization."""
    return await get_entity_subgraph(
        db,
        tenant_id=user.tenant_id,
        node_type=entity_type,
        node_id=entity_id,
        max_depth=max_depth,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )


@router.get("/stats")
async def graph_stats(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None),
    as_of: datetime | None = Query(None),
):
    """Return aggregate edge-type and node-type counts for the tenant."""
    return await get_graph_stats(
        db,
        tenant_id=user.tenant_id,
        domain_id=domain_id,
        as_of=normalize_graph_as_of(as_of),
    )
