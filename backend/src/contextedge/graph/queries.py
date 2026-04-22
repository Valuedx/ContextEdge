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

    # Also fetch 2nd-hop edges (episode→identity, etc.) but limit to specific types to avoid explosion
    episode_ids: list[uuid.UUID] = []
    for e in edges:
        if e.source_node_type == "episode":
            episode_ids.append(e.source_node_id)
        elif e.target_node_type == "episode":
            episode_ids.append(e.target_node_id)

    second_hop_edges: list = []
    if episode_ids:
        # Only pull high-value 2nd-hop relations (like identities affected)
        q2 = select(GraphEdge).where(
            GraphEdge.tenant_id == tenant_id,
            or_(
                GraphEdge.source_node_id.in_(episode_ids),
                GraphEdge.target_node_id.in_(episode_ids),
            ),
            GraphEdge.edge_type.in_(["mentions_identity", "affects", "references_identity", "derived_from"])
        ).limit(20) # Global cap on secondary expansion
        
        if domain_id is not None:
            q2 = q2.where(
                (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
            )
        second_hop_result = await db.execute(q2)
        second_hop_edges = second_hop_result.scalars().all()

    all_edges = list(edges) + [e for e in second_hop_edges if e.id not in {x.id for x in edges}]

    nodes: dict[str, dict] = {}

    def add_node(ntype: str, nid: str, title: str | None = None):
        key = f"{ntype}:{nid}"
        if key not in nodes:
            nodes[key] = {"type": ntype, "id": nid, "title": title}
        elif title and not nodes[key].get("title"):
            nodes[key]["title"] = title

    add_node("pattern", str(pattern_id), pattern.title)

    # Enrichment node types that store their label in edge metadata
    ENRICHMENT_TYPES = {"trigger", "entity", "error", "root_cause"}

    edge_list = []
    for e in all_edges:
        meta_label = (e.metadata_extra or {}).get("label")
        # Enrichment nodes (source) carry their name in edge metadata
        if e.source_node_type in ENRICHMENT_TYPES:
            add_node(e.source_node_type, str(e.source_node_id), meta_label)
        else:
            add_node(e.source_node_type, str(e.source_node_id))
        # Target is rarely an enrichment node but handle both anyway
        if e.target_node_type in ENRICHMENT_TYPES:
            add_node(e.target_node_type, str(e.target_node_id), meta_label)
        else:
            add_node(e.target_node_type, str(e.target_node_id))

        edge_list.append({
            "source": f"{e.source_node_type}:{e.source_node_id}",
            "target": f"{e.target_node_type}:{e.target_node_id}",
            "type": e.edge_type,
            "weight": e.weight,
        })

    # Fetch real titles for DB-backed node types
    from contextedge.models.episode import Episode
    from contextedge.models.evidence import EvidenceItem

    ep_ids = [
        uuid.UUID(v["id"]) for v in nodes.values()
        if v["type"] == "episode" and not v.get("title")
    ]
    if ep_ids:
        ep_res = await db.execute(
            select(Episode.id, Episode.title).where(Episode.id.in_(ep_ids))
        )
        for row in ep_res.all():
            key = f"episode:{row.id}"
            if key in nodes:
                nodes[key]["title"] = row.title

    ev_ids = [
        uuid.UUID(v["id"]) for v in nodes.values()
        if v["type"] == "evidence" and not v.get("title")
    ]
    if ev_ids:
        ev_res = await db.execute(
            select(EvidenceItem.id, EvidenceItem.title).where(EvidenceItem.id.in_(ev_ids))
        )
        for row in ev_res.all():
            key = f"evidence:{row.id}"
            if key in nodes:
                nodes[key]["title"] = row.title

    # Identity Pruning: Only show shared or significant identities to avoid clutter
    id_counts: dict[str, int] = {}
    for e in all_edges:
        if e.source_node_type == "identity":
            id_counts[e.source_node_id] = id_counts.get(str(e.source_node_id), 0) + 1
        if e.target_node_type == "identity":
            id_counts[str(e.target_node_id)] = id_counts.get(str(e.target_node_id), 0) + 1

    # Fetch identity names
    try:
        from contextedge.models.episode import CanonicalIdentity
        all_id_keys = [k for k, v in nodes.items() if v["type"] == "identity"]
        id_ids = [uuid.UUID(k.split(":")[1]) for k in all_id_keys]
        
        if id_ids:
            # Join with canonical_identities to get real names
            id_res = await db.execute(
                select(CanonicalIdentity.id, CanonicalIdentity.canonical_name)
                .where(CanonicalIdentity.id.in_(id_ids))
            )
            for row in id_res.all():
                key = f"identity:{row.id}"
                if key in nodes:
                    nodes[key]["title"] = row.canonical_name

        # 2. Pruning: Keep only those with titles AND limit to top 8 by frequency
        sorted_ids = sorted(
            [k for k in all_id_keys if nodes[k].get("title")],
            key=lambda k: id_counts.get(k.split(":")[1], 0),
            reverse=True
        )[:8]

        # 3. Deduplication: Merge identities with nearly identical names
        merged_ids: dict[str, str] = {} # original_key -> canonical_key
        name_map: dict[str, str] = {}   # normalized_name -> canonical_key
        
        # Sort for deterministic canonical keys
        for nid in sorted(sorted_ids, key=len):
            title = nodes[nid].get("title", "").lower()
            norm = "".join(c for c in title if c.isalnum())
            if not norm: continue
            
            if norm in name_map:
                merged_ids[nid] = name_map[norm]
            else:
                name_map[norm] = nid
                merged_ids[nid] = nid

        # Final set of nodes to keep
        final_id_set = set(name_map.values())
        
        # Remove pruned or merged identities from nodes
        for k in all_id_keys:
            if k not in final_id_set:
                del nodes[k]
        
        # Remap edges to point to canonical nodes
        edge_list = [
            {
                **e,
                "source": merged_ids.get(e["source"], e["source"]),
                "target": merged_ids.get(e["target"], e["target"])
            }
            for e in edge_list 
            if not (e["source"].startswith("identity:") and e["source"] not in merged_ids and e["source"] not in final_id_set)
            and not (e["target"].startswith("identity:") and e["target"] not in merged_ids and e["target"] not in final_id_set)
        ]
        
        # Deduplicate resulting edges
        seen_edges = set()
        final_edges = []
        for e in edge_list:
            ekey = f"{e['source']}-{e['target']}-{e['type']}"
            if ekey not in seen_edges:
                seen_edges.add(ekey)
                final_edges.append(e)
        edge_list = final_edges

    except Exception as e:
        import structlog
        structlog.get_logger().error("identity_resolution_failed", error=str(e))

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


async def get_decision_subgraph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    max_depth: int = 2,
    domain_id: uuid.UUID | None = None,
) -> dict:
    """Get the subgraph around a decision including evidence, options, outcomes, and policies."""
    return await get_entity_subgraph(
        db, tenant_id, "decision", decision_id, max_depth=max_depth, domain_id=domain_id,
    )


async def get_decision_effectiveness(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_filters: dict | None = None,
) -> dict:
    """Aggregate outcome success/failure counts for a decision type.

    Delegates to the service-layer implementation to keep query logic
    co-located with the ORM models it depends on.
    """
    from contextedge.services.decision_trace_service import (
        get_decision_effectiveness as _svc_effectiveness,
    )

    return await _svc_effectiveness(
        db, tenant_id=tenant_id, decision_type=decision_type,
        context_filters=context_filters,
    )


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
