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
    from contextedge.models.pattern import Pattern
    
    # 1. Fetch the pattern to get enriched data
    pattern_res = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == tenant_id)
    )
    pattern = pattern_res.scalar_one_or_none()
    if not pattern:
        return {"nodes": [], "edges": []}

    # 2. Fetch explicit graph edges
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

    nodes = {} # type: dict[str, dict]
    
    def add_node(ntype: str, nid: str, title: str | None = None):
        key = f"{ntype}:{nid}"
        if key not in nodes:
            nodes[key] = {"type": ntype, "id": nid, "title": title}

    add_node("pattern", str(pattern_id), pattern.title)
    
    edge_list = []
    for e in edges:
        add_node(e.source_node_type, str(e.source_node_id))
        add_node(e.target_node_type, str(e.target_node_id))
        edge_list.append({
            "source": f"{e.source_node_type}:{e.source_node_id}",
            "target": f"{e.target_node_type}:{e.target_node_id}",
            "type": e.edge_type,
            "weight": e.weight,
        })

    # 3. Add virtual nodes from enriched metadata
    virtual_mappings = [
        ("trigger", pattern.trigger_conditions, "trigger_of"),
        ("entity", pattern.core_entities, "involved_in"),
        ("error", pattern.observed_errors, "discovered_in"),
        ("root_cause", pattern.root_causes, "causes"),
    ]

    for ntype, items, etype in virtual_mappings:
        if items:
            for item in items:
                # Use item string as ID for virtual nodes
                node_id = f"v-{ntype}-{item}"
                add_node(ntype, node_id, item)
                edge_list.append({
                    "source": f"{ntype}:{node_id}",
                    "target": f"pattern:{pattern_id}",
                    "type": etype,
                    "weight": 1.5, # Stronger weight for core pattern data
                })

    return {
        "nodes": list(nodes.values()),
        "edges": edge_list,
    }
