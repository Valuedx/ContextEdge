from uuid import UUID

from fastapi import APIRouter, Query

from contextedge.deps import AuthUser, DbSession
from contextedge.graph.queries import get_entity_subgraph, get_graph_stats, get_neighbors

router = APIRouter()


@router.get("/neighbors")
async def graph_neighbors(
    db: DbSession,
    user: AuthUser,
    node_type: str = Query(..., description="Type of the origin node (e.g. playbook, pattern, episode)"),
    node_id: UUID = Query(..., description="ID of the origin node"),
    edge_type: str | None = Query(None, description="Filter by edge type"),
    max_depth: int = Query(1, ge=1, le=3, description="BFS traversal depth (1-3)"),
    domain_id: UUID | None = Query(None, description="Scope to a domain (includes domain-less edges)"),
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
    )


@router.get("/subgraph/{entity_type}/{entity_id}")
async def graph_subgraph(
    entity_type: str,
    entity_id: UUID,
    db: DbSession,
    user: AuthUser,
    max_depth: int = Query(1, ge=1, le=3),
    domain_id: UUID | None = Query(None),
):
    """Return the subgraph around any entity as nodes + edges suitable for visualization."""
    return await get_entity_subgraph(
        db,
        tenant_id=user.tenant_id,
        node_type=entity_type,
        node_id=entity_id,
        max_depth=max_depth,
        domain_id=domain_id,
    )


@router.get("/stats")
async def graph_stats(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = Query(None),
):
    """Return aggregate edge-type and node-type counts for the tenant."""
    return await get_graph_stats(db, tenant_id=user.tenant_id, domain_id=domain_id)
