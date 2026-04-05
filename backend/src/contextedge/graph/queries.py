"""Graph query service for pattern/context graph traversal."""

import uuid

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge


async def get_neighbors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    node_type: str,
    node_id: uuid.UUID,
    edge_type: str | None = None,
    max_depth: int = 1,
) -> list[dict]:
    """Get neighboring nodes in the graph."""
    q = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        or_(
            (GraphEdge.source_node_type == node_type) & (GraphEdge.source_node_id == node_id),
            (GraphEdge.target_node_type == node_type) & (GraphEdge.target_node_id == node_id),
        ),
    )
    if edge_type:
        q = q.where(GraphEdge.edge_type == edge_type)

    result = await db.execute(q)
    edges = result.scalars().all()

    neighbors = []
    for edge in edges:
        if edge.source_node_id == node_id:
            neighbors.append({
                "node_type": edge.target_node_type,
                "node_id": str(edge.target_node_id),
                "edge_type": edge.edge_type,
                "weight": edge.weight,
                "direction": "outgoing",
            })
        else:
            neighbors.append({
                "node_type": edge.source_node_type,
                "node_id": str(edge.source_node_id),
                "edge_type": edge.edge_type,
                "weight": edge.weight,
                "direction": "incoming",
            })
    return neighbors


async def get_pattern_subgraph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
) -> dict:
    """Get the subgraph around a pattern including episodes, entities, and playbooks."""
    edges_result = await db.execute(
        select(GraphEdge).where(
            GraphEdge.tenant_id == tenant_id,
            or_(
                GraphEdge.source_node_id == pattern_id,
                GraphEdge.target_node_id == pattern_id,
            ),
        )
    )
    edges = edges_result.scalars().all()

    nodes = set()
    nodes.add(("pattern", str(pattern_id)))
    edge_list = []
    for e in edges:
        nodes.add((e.source_node_type, str(e.source_node_id)))
        nodes.add((e.target_node_type, str(e.target_node_id)))
        edge_list.append({
            "source": f"{e.source_node_type}:{e.source_node_id}",
            "target": f"{e.target_node_type}:{e.target_node_id}",
            "type": e.edge_type,
            "weight": e.weight,
        })

    return {
        "nodes": [{"type": t, "id": i} for t, i in nodes],
        "edges": edge_list,
    }
