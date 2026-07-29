"""Graph query service for pattern/context graph traversal."""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.temporal import edge_valid_at
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
    as_of: datetime | None = None,
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
                edge_valid_at(as_of),
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
    as_of: datetime | None = None,
) -> dict:
    """Get the subgraph around a pattern including episodes, evidence, entities, and playbooks (up to 2 hops)."""
    from contextedge.models.pattern import Pattern, PatternEvidenceLink, GraphEdge
    from contextedge.models.episode import Episode
    from contextedge.models.evidence import EvidenceItem

    pattern_res = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == tenant_id)
    )
    pattern = pattern_res.scalar_one_or_none()
    if not pattern:
        return {"nodes": [], "edges": []}

    nodes: dict[str, dict] = {}
    edge_list: list[dict] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()

    def add_node(ntype: str, nid: str, title: str | None = None):
        key = f"{ntype}:{nid}"
        if key not in nodes:
            nodes[key] = {"type": ntype, "id": nid, "title": title}
        elif title and not nodes[key].get("title"):
            nodes[key]["title"] = title

    add_node("pattern", str(pattern_id), pattern.title)

    # 1. 2-Hop BFS Traversal on GraphEdge
    frontier: list[tuple[str, uuid.UUID]] = [("pattern", pattern_id)]
    visited_refs: set[tuple[str, uuid.UUID]] = {("pattern", pattern_id)}

    for _depth in range(1, 3):
        if not frontier:
            break
        next_frontier: list[tuple[str, uuid.UUID]] = []
        for f_type, f_id in frontier:
            q = select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                edge_valid_at(as_of),
                or_(
                    (GraphEdge.source_node_type == f_type) & (GraphEdge.source_node_id == f_id),
                    (GraphEdge.target_node_type == f_type) & (GraphEdge.target_node_id == f_id),
                ),
            )
            if domain_id is not None:
                q = q.where(
                    (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
                )
            edges_result = await db.execute(q)
            edges = edges_result.scalars().all()

            for e in edges:
                source_key = f"{e.source_node_type}:{e.source_node_id}"
                target_key = f"{e.target_node_type}:{e.target_node_id}"
                edge_key = (source_key, target_key, e.edge_type)

                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    label = (e.metadata_extra or {}).get("label")
                    add_node(e.source_node_type, str(e.source_node_id), label)
                    add_node(e.target_node_type, str(e.target_node_id), label)
                    edge_list.append({
                        "source": source_key,
                        "target": target_key,
                        "type": e.edge_type,
                        "weight": e.weight,
                    })

                neighbor = (
                    (e.target_node_type, e.target_node_id)
                    if (e.source_node_id == f_id and e.source_node_type == f_type)
                    else (e.source_node_type, e.source_node_id)
                )
                if neighbor not in visited_refs:
                    visited_refs.add(neighbor)
                    next_frontier.append(neighbor)

        frontier = next_frontier

    # 2. Also query relational PatternEvidenceLink to ensure linked Episodes & Evidence are included
    pel_res = await db.execute(
        select(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pattern_id)
    )
    pel_links = pel_res.scalars().all()
    for link in pel_links:
        if link.episode_id:
            ep_res = await db.execute(
                select(Episode).where(Episode.id == link.episode_id, Episode.tenant_id == tenant_id)
            )
            ep = ep_res.scalar_one_or_none()
            if ep:
                d_str = ep.created_at.strftime("%b %d, %Y") if ep.created_at else ""
                base_t = ep.title or ep.root_cause_summary or f"Episode {str(link.episode_id)[:8]}"
                ep_title = f"{base_t} [{d_str}]" if d_str else base_t
            else:
                ep_title = f"Episode {str(link.episode_id)[:8]}"
            add_node("episode", str(link.episode_id), ep_title)
            edge_key = (f"pattern:{pattern_id}", f"episode:{link.episode_id}", "clusters")
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                edge_list.append({
                    "source": f"pattern:{pattern_id}",
                    "target": f"episode:{link.episode_id}",
                    "type": "clusters",
                    "weight": link.weight,
                })

        if link.evidence_id:
            ev_res = await db.execute(
                select(EvidenceItem).where(EvidenceItem.id == link.evidence_id, EvidenceItem.tenant_id == tenant_id)
            )
            ev = ev_res.scalar_one_or_none()
            if ev:
                d_str = ev.ingested_at.strftime("%b %d, %Y") if ev.ingested_at else ""
                base_t = ev.title or f"Evidence {str(link.evidence_id)[:8]}"
                ev_title = f"{base_t} [{d_str}]" if d_str else base_t
            else:
                ev_title = f"Evidence {str(link.evidence_id)[:8]}"
            add_node("evidence", str(link.evidence_id), ev_title)
            source_parent = f"episode:{link.episode_id}" if link.episode_id else f"pattern:{pattern_id}"
            edge_key = (source_parent, f"evidence:{link.evidence_id}", "derived_from")
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                edge_list.append({
                    "source": source_parent,
                    "target": f"evidence:{link.evidence_id}",
                    "type": "derived_from",
                    "weight": link.weight,
                })

    # 3. Post-process Episode & Evidence nodes to populate exact dates in titles
    for key, n in nodes.items():
        ntype = n["type"]
        nid_str = n["id"]
        try:
            nid_uuid = uuid.UUID(nid_str)
        except Exception:
            continue

        if ntype == "episode":
            ep_res = await db.execute(select(Episode).where(Episode.id == nid_uuid))
            ep_obj = ep_res.scalar_one_or_none()
            if ep_obj and ep_obj.created_at:
                d_str = ep_obj.created_at.strftime("%b %d, %Y")
                base_title = ep_obj.title or ep_obj.root_cause_summary or f"Episode {nid_str[:8]}"
                n["title"] = f"{base_title} ({d_str})"
        elif ntype == "evidence":
            ev_res = await db.execute(select(EvidenceItem).where(EvidenceItem.id == nid_uuid))
            ev_obj = ev_res.scalar_one_or_none()
            if ev_obj and ev_obj.ingested_at:
                d_str = ev_obj.ingested_at.strftime("%b %d, %Y")
                base_title = ev_obj.title or f"Evidence {nid_str[:8]}"
                n["title"] = f"{base_title} ({d_str})"

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
    as_of: datetime | None = None,
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
                edge_valid_at(as_of),
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
                neighbor = (
                    (e.target_node_type, e.target_node_id)
                    if (e.source_node_id == f_id and e.source_node_type == f_type)
                    else (e.source_node_type, e.source_node_id)
                )
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
    as_of: datetime | None = None,
) -> dict:
    """Return aggregate edge-type and node-type counts for the tenant."""
    q = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        edge_valid_at(as_of),
    )
    if domain_id is not None:
        q = q.where(
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        )
    result = await db.execute(q)
    edges = result.scalars().all()

    edge_type_counts: dict[str, int] = {}
    node_type_counts: dict[str, set[str]] = {}

    for e in edges:
        edge_type_counts[e.edge_type] = edge_type_counts.get(e.edge_type, 0) + 1

        if e.source_node_type not in node_type_counts:
            node_type_counts[e.source_node_type] = set()
        node_type_counts[e.source_node_type].add(str(e.source_node_id))

        if e.target_node_type not in node_type_counts:
            node_type_counts[e.target_node_type] = set()
        node_type_counts[e.target_node_type].add(str(e.target_node_id))

    return {
        "total_edges": len(edges),
        "edge_types": edge_type_counts,
        "node_types": {k: len(v) for k, v in node_type_counts.items()},
    }
