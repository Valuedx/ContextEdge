"""Graph query service for pattern/context graph traversal."""

import uuid

from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge

MAX_TRAVERSAL_DEPTH = 3


async def get_neighbors(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    node_type: str,
    node_id: uuid.UUID,
    edge_type: str | None = None,
    max_depth: int = 1,
    domain_id: uuid.UUID | None = None,
) -> list[dict]:
    """Get neighboring nodes in the graph using iterative BFS up to *max_depth* hops."""
    max_depth = min(max(1, max_depth), MAX_TRAVERSAL_DEPTH)
    visited: set[tuple[str, uuid.UUID]] = {(node_type, node_id)}
    frontier: list[tuple[str, uuid.UUID]] = [(node_type, node_id)]
    results: list[dict] = []

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        next_frontier: list[tuple[str, uuid.UUID]] = []
        for f_type, f_id in frontier:
            q = select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                or_(
                    (GraphEdge.source_node_type == f_type) & (GraphEdge.source_node_id == f_id),
                    (GraphEdge.target_node_type == f_type) & (GraphEdge.target_node_id == f_id),
                ),
            )
            if edge_type:
                q = q.where(GraphEdge.edge_type == edge_type)
            if domain_id is not None:
                q = q.where(
                    (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
                )

            result = await db.execute(q)
            edges = result.scalars().all()

            for edge in edges:
                if edge.source_node_id == f_id and edge.source_node_type == f_type:
                    neighbor = (edge.target_node_type, edge.target_node_id)
                    direction = "outgoing"
                else:
                    neighbor = (edge.source_node_type, edge.source_node_id)
                    direction = "incoming"

                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_frontier.append(neighbor)
                results.append({
                    "node_type": neighbor[0],
                    "node_id": str(neighbor[1]),
                    "edge_type": edge.edge_type,
                    "weight": edge.weight,
                    "direction": direction,
                    "depth": depth,
                })
        frontier = next_frontier

    return results


async def get_pattern_subgraph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
) -> dict:
    """Get the subgraph around a pattern including episodes, entities, and playbooks."""
    from contextedge.models.pattern import Pattern

    pattern_res = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == tenant_id)
    )
    pattern = pattern_res.scalar_one_or_none()
    if not pattern:
        return {"nodes": [], "edges": []}

    q = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        or_(
            GraphEdge.source_node_id == pattern_id,
            GraphEdge.target_node_id == pattern_id,
        ),
    )
    if domain_id is not None:
        q = q.where(
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        )
    edges_result = await db.execute(q)
    edges = edges_result.scalars().all()

    nodes: dict[str, dict] = {}

    def add_node(ntype: str, nid: str, title: str | None = None):
        key = f"{ntype}:{nid}"
        if key not in nodes:
            nodes[key] = {"type": ntype, "id": nid, "title": title}
        elif title and not nodes[key].get("title"):
            nodes[key]["title"] = title

    add_node("pattern", str(pattern_id), pattern.title)

    edge_list = []
    for e in edges:
        label = (e.metadata_extra or {}).get("label")
        add_node(e.source_node_type, str(e.source_node_id), label)
        add_node(e.target_node_type, str(e.target_node_id), label)
        edge_list.append({
            "source": f"{e.source_node_type}:{e.source_node_id}",
            "target": f"{e.target_node_type}:{e.target_node_id}",
            "type": e.edge_type,
            "weight": e.weight,
        })

    return {
        "nodes": list(nodes.values()),
        "edges": edge_list,
    }


async def get_entity_subgraph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    node_type: str,
    node_id: uuid.UUID,
    max_depth: int = 1,
    domain_id: uuid.UUID | None = None,
) -> dict:
    """Get the subgraph around any entity using BFS traversal."""
    max_depth = min(max(1, max_depth), MAX_TRAVERSAL_DEPTH)
    nodes: dict[str, dict] = {}
    edge_list: list[dict] = []

    def add_node(ntype: str, nid: str, label: str | None = None):
        key = f"{ntype}:{nid}"
        if key not in nodes:
            nodes[key] = {"type": ntype, "id": nid, "title": label}
        elif label and not nodes[key].get("title"):
            nodes[key]["title"] = label

    add_node(node_type, str(node_id))
    visited: set[tuple[str, uuid.UUID]] = {(node_type, node_id)}
    frontier: list[tuple[str, uuid.UUID]] = [(node_type, node_id)]
    seen_edges: set[uuid.UUID] = set()

    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: list[tuple[str, uuid.UUID]] = []
        for f_type, f_id in frontier:
            q = select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                or_(
                    (GraphEdge.source_node_type == f_type) & (GraphEdge.source_node_id == f_id),
                    (GraphEdge.target_node_type == f_type) & (GraphEdge.target_node_id == f_id),
                ),
            )
            if domain_id is not None:
                q = q.where(
                    (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
                )
            result = await db.execute(q)
            for e in result.scalars().all():
                if e.id in seen_edges:
                    continue
                seen_edges.add(e.id)
                label = (e.metadata_extra or {}).get("label")
                add_node(e.source_node_type, str(e.source_node_id), label)
                add_node(e.target_node_type, str(e.target_node_id), label)
                edge_list.append({
                    "source": f"{e.source_node_type}:{e.source_node_id}",
                    "target": f"{e.target_node_type}:{e.target_node_id}",
                    "type": e.edge_type,
                    "weight": e.weight,
                })
                for neighbor in [
                    (e.target_node_type, e.target_node_id),
                    (e.source_node_type, e.source_node_id),
                ]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
        frontier = next_frontier

    return {
        "nodes": list(nodes.values()),
        "edges": edge_list,
    }


async def get_graph_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
) -> dict:
    """Return aggregate edge and node statistics for the tenant."""
    from sqlalchemy import union_all, literal_column

    domain_filter = []
    if domain_id is not None:
        domain_filter = [
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        ]

    base = select(
        GraphEdge.edge_type,
        func.count().label("count"),
    ).where(GraphEdge.tenant_id == tenant_id, *domain_filter)
    base = base.group_by(GraphEdge.edge_type)
    result = await db.execute(base)
    edge_type_counts = {row.edge_type: row.count for row in result.all()}

    source_q = select(
        GraphEdge.source_node_type.label("node_type"),
        GraphEdge.source_node_id.label("node_id"),
    ).where(GraphEdge.tenant_id == tenant_id, *domain_filter)
    target_q = select(
        GraphEdge.target_node_type.label("node_type"),
        GraphEdge.target_node_id.label("node_id"),
    ).where(GraphEdge.tenant_id == tenant_id, *domain_filter)

    all_nodes = union_all(source_q, target_q).subquery("all_nodes")
    node_stats_q = select(
        all_nodes.c.node_type,
        func.count(func.distinct(all_nodes.c.node_id)).label("count"),
    ).group_by(all_nodes.c.node_type)
    node_result = await db.execute(node_stats_q)
    node_type_counts = {row.node_type: row.count for row in node_result.all()}

    total_edges = sum(edge_type_counts.values())

    return {
        "total_edges": total_edges,
        "edge_type_counts": edge_type_counts,
        "node_type_counts": node_type_counts,
    }
