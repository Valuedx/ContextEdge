"""Graph query service for pattern/context graph traversal."""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.temporal import edge_valid_at
from contextedge.models.pattern import GraphEdge

MAX_TRAVERSAL_DEPTH = 3

# Budget for the pattern-subgraph visualization payload. The UI renders the
# full response with no virtualization, so the server must bound it.
MAX_SUBGRAPH_NODES = 250
MAX_SUBGRAPH_EDGES = 500


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
    """Get the subgraph around a pattern including episodes, evidence,
    entities, and playbooks (up to 2 hops)."""
    from contextedge.models.episode import Episode
    from contextedge.models.evidence import EvidenceItem
    from contextedge.models.pattern import GraphEdge, Pattern, PatternEvidenceLink

    pattern_res = await db.execute(
        select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == tenant_id)
    )
    pattern = pattern_res.scalar_one_or_none()
    if not pattern:
        return {"nodes": [], "edges": [], "truncated": False}

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

    truncated = False

    def _budget_left() -> bool:
        return len(nodes) < MAX_SUBGRAPH_NODES and len(edge_list) < MAX_SUBGRAPH_EDGES

    # 1. 2-hop BFS on GraphEdge — one batched query per depth, bounded by the
    # subgraph budget so a hub pattern cannot produce an unbounded payload.
    frontier: list[tuple[str, uuid.UUID]] = [("pattern", pattern_id)]
    visited_refs: set[tuple[str, uuid.UUID]] = {("pattern", pattern_id)}

    for _depth in range(1, 3):
        if not frontier:
            break
        if not _budget_left():
            # Budget exhausted with unexplored frontier = dropped depth.
            truncated = True
            break
        frontier_clauses = [
            ((GraphEdge.source_node_type == f_type) & (GraphEdge.source_node_id == f_id))
            | ((GraphEdge.target_node_type == f_type) & (GraphEdge.target_node_id == f_id))
            for f_type, f_id in frontier
        ]
        q = (
            select(GraphEdge)
            .where(
                GraphEdge.tenant_id == tenant_id,
                edge_valid_at(as_of),
                or_(*frontier_clauses),
            )
            # Deterministic survivors when the cap bites: strongest first.
            .order_by(GraphEdge.weight.desc(), GraphEdge.id)
            .limit(MAX_SUBGRAPH_EDGES + 1)
        )
        if domain_id is not None:
            q = q.where(
                (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
            )
        edges_result = await db.execute(q)
        edges = edges_result.scalars().all()

        frontier_set = set(frontier)
        next_frontier: list[tuple[str, uuid.UUID]] = []
        for e in edges:
            if not _budget_left():
                truncated = True
                break
            source_key = f"{e.source_node_type}:{e.source_node_id}"
            target_key = f"{e.target_node_type}:{e.target_node_id}"
            edge_key = (source_key, target_key, e.edge_type)

            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                # Edge labels describe the enrichment node (the SOURCE of
                # trigger_of/involved_in/discovered_in/causes edges) — never
                # title the target with it, or a pattern first reached via a
                # labeled edge inherits its trigger text as a name.
                label = (e.metadata_extra or {}).get("label")
                add_node(e.source_node_type, str(e.source_node_id), label)
                add_node(e.target_node_type, str(e.target_node_id), None)
                edge_list.append({
                    "source": source_key,
                    "target": target_key,
                    "type": e.edge_type,
                    "weight": e.weight,
                })

            src_ref = (e.source_node_type, e.source_node_id)
            tgt_ref = (e.target_node_type, e.target_node_id)
            neighbor = tgt_ref if src_ref in frontier_set else src_ref
            if neighbor not in visited_refs:
                visited_refs.add(neighbor)
                next_frontier.append(neighbor)

        frontier = next_frontier

    # 2. Merge relational PatternEvidenceLink rows so linked episodes and
    # evidence appear even before graph edges are materialized. Skipped for
    # point-in-time queries: PEL has no validity window, so merging it would
    # leak present-day links into a historical view. Node titles are filled by
    # the tenant-filtered decoration pass below.
    if as_of is None:
        pel_res = await db.execute(
            select(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pattern_id)
        )
        pel_links = pel_res.scalars().all()
        for link in pel_links:
            if not _budget_left():
                truncated = True
                break
            if link.episode_id:
                add_node("episode", str(link.episode_id))
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
                add_node("evidence", str(link.evidence_id))
                source_parent = (
                    f"episode:{link.episode_id}" if link.episode_id else f"pattern:{pattern_id}"
                )
                edge_key = (source_parent, f"evidence:{link.evidence_id}", "derived_from")
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    edge_list.append({
                        "source": source_parent,
                        "target": f"evidence:{link.evidence_id}",
                        "type": "derived_from",
                        "weight": link.weight,
                    })

    # 3. Decorate node titles across all types (episodes, evidence, identities,
    # entities, playbooks, patterns).
    from contextedge.models.entity import Entity
    from contextedge.models.episode import CanonicalIdentity
    from contextedge.models.playbook import Playbook

    episode_ids: list[uuid.UUID] = []
    evidence_ids: list[uuid.UUID] = []
    identity_ids: list[uuid.UUID] = []
    entity_ids: list[uuid.UUID] = []
    playbook_ids: list[uuid.UUID] = []
    pattern_ids: list[uuid.UUID] = []

    for n in nodes.values():
        try:
            nid_uuid = uuid.UUID(n["id"])
        except (ValueError, AttributeError, TypeError):
            continue
        ntype = n["type"]
        if ntype == "episode":
            episode_ids.append(nid_uuid)
        elif ntype == "evidence":
            evidence_ids.append(nid_uuid)
        elif ntype == "identity":
            identity_ids.append(nid_uuid)
        elif ntype == "entity":
            entity_ids.append(nid_uuid)
        elif ntype == "playbook":
            playbook_ids.append(nid_uuid)
        elif ntype == "pattern":
            pattern_ids.append(nid_uuid)

    ep_by_id: dict[str, object] = {}
    if episode_ids:
        ep_res = await db.execute(
            select(Episode).where(
                Episode.id.in_(episode_ids), Episode.tenant_id == tenant_id
            )
        )
        ep_by_id = {str(ep.id): ep for ep in ep_res.scalars().all()}

    ev_by_id: dict[str, object] = {}
    if evidence_ids:
        ev_res = await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.id.in_(evidence_ids), EvidenceItem.tenant_id == tenant_id
            )
        )
        ev_by_id = {str(ev.id): ev for ev in ev_res.scalars().all()}

    ident_by_id: dict[str, object] = {}
    if identity_ids:
        ident_res = await db.execute(
            select(CanonicalIdentity).where(
                CanonicalIdentity.id.in_(identity_ids),
                CanonicalIdentity.tenant_id == tenant_id,
            )
        )
        ident_by_id = {str(ident.id): ident for ident in ident_res.scalars().all()}

    ent_by_id: dict[str, object] = {}
    if entity_ids:
        ent_res = await db.execute(
            select(Entity).where(
                Entity.id.in_(entity_ids), Entity.tenant_id == tenant_id
            )
        )
        ent_by_id = {str(ent.id): ent for ent in ent_res.scalars().all()}

    pb_by_id: dict[str, object] = {}
    if playbook_ids:
        pb_res = await db.execute(
            select(Playbook).where(
                Playbook.id.in_(playbook_ids), Playbook.tenant_id == tenant_id
            )
        )
        pb_by_id = {str(pb.id): pb for pb in pb_res.scalars().all()}

    pat_by_id: dict[str, object] = {}
    if pattern_ids:
        pat_res = await db.execute(
            select(Pattern).where(
                Pattern.id.in_(pattern_ids), Pattern.tenant_id == tenant_id
            )
        )
        pat_by_id = {str(pat.id): pat for pat in pat_res.scalars().all()}

    for n in nodes.values():
        nid_str = n["id"]
        ntype = n["type"]
        if ntype == "episode":
            ep_obj = ep_by_id.get(nid_str)
            if ep_obj:
                base_title = ep_obj.title or ep_obj.root_cause_summary or f"Episode {nid_str[:8]}"
                if ep_obj.created_at:
                    n["title"] = f"{base_title} ({ep_obj.created_at.strftime('%b %d, %Y')})"
                else:
                    n["title"] = base_title
            elif not n.get("title"):
                n["title"] = f"Episode {nid_str[:8]}"
        elif ntype == "evidence":
            ev_obj = ev_by_id.get(nid_str)
            if ev_obj:
                base_title = ev_obj.title or f"Evidence {nid_str[:8]}"
                if ev_obj.ingested_at:
                    n["title"] = f"{base_title} ({ev_obj.ingested_at.strftime('%b %d, %Y')})"
                else:
                    n["title"] = base_title
            elif not n.get("title"):
                n["title"] = f"Evidence {nid_str[:8]}"
        elif ntype == "identity":
            ident_obj = ident_by_id.get(nid_str)
            if ident_obj:
                n["title"] = f"{ident_obj.canonical_name} ({ident_obj.entity_type})"
            elif not n.get("title"):
                n["title"] = f"Identity {nid_str[:8]}"
        elif ntype == "entity":
            ent_obj = ent_by_id.get(nid_str)
            if ent_obj:
                n["title"] = f"{ent_obj.name} ({ent_obj.entity_type})"
            elif not n.get("title"):
                n["title"] = f"Entity {nid_str[:8]}"
        elif ntype == "playbook":
            pb_obj = pb_by_id.get(nid_str)
            if pb_obj:
                n["title"] = pb_obj.title
            elif not n.get("title"):
                n["title"] = f"Playbook {nid_str[:8]}"
        elif ntype == "pattern":
            pat_obj = pat_by_id.get(nid_str)
            if pat_obj:
                n["title"] = pat_obj.title
            elif not n.get("title"):
                n["title"] = f"Pattern {nid_str[:8]}"

    return {
        "nodes": list(nodes.values()),
        "edges": edge_list,
        "truncated": truncated,
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
                # Labels describe the enrichment (source) node only — see
                # the same rule in get_pattern_subgraph.
                label = (e.metadata_extra or {}).get("label")
                add_node(e.source_node_type, str(e.source_node_id), label)
                add_node(e.target_node_type, str(e.target_node_id), None)
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

    # Decorate node titles across all types (episodes, evidence, identities,
    # entities, playbooks, patterns).
    from contextedge.models.entity import Entity
    from contextedge.models.episode import CanonicalIdentity, Episode
    from contextedge.models.evidence import EvidenceItem
    from contextedge.models.pattern import Pattern
    from contextedge.models.playbook import Playbook

    episode_ids: list[uuid.UUID] = []
    evidence_ids: list[uuid.UUID] = []
    identity_ids: list[uuid.UUID] = []
    entity_ids: list[uuid.UUID] = []
    playbook_ids: list[uuid.UUID] = []
    pattern_ids: list[uuid.UUID] = []

    for n in nodes.values():
        try:
            nid_uuid = uuid.UUID(n["id"])
        except (ValueError, AttributeError, TypeError):
            continue
        ntype = n["type"]
        if ntype == "episode":
            episode_ids.append(nid_uuid)
        elif ntype == "evidence":
            evidence_ids.append(nid_uuid)
        elif ntype == "identity":
            identity_ids.append(nid_uuid)
        elif ntype == "entity":
            entity_ids.append(nid_uuid)
        elif ntype == "playbook":
            playbook_ids.append(nid_uuid)
        elif ntype == "pattern":
            pattern_ids.append(nid_uuid)

    ep_by_id: dict[str, object] = {}
    if episode_ids:
        ep_res = await db.execute(
            select(Episode).where(
                Episode.id.in_(episode_ids), Episode.tenant_id == tenant_id
            )
        )
        ep_by_id = {str(ep.id): ep for ep in ep_res.scalars().all()}

    ev_by_id: dict[str, object] = {}
    if evidence_ids:
        ev_res = await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.id.in_(evidence_ids), EvidenceItem.tenant_id == tenant_id
            )
        )
        ev_by_id = {str(ev.id): ev for ev in ev_res.scalars().all()}

    ident_by_id: dict[str, object] = {}
    if identity_ids:
        ident_res = await db.execute(
            select(CanonicalIdentity).where(
                CanonicalIdentity.id.in_(identity_ids),
                CanonicalIdentity.tenant_id == tenant_id,
            )
        )
        ident_by_id = {str(ident.id): ident for ident in ident_res.scalars().all()}

    ent_by_id: dict[str, object] = {}
    if entity_ids:
        ent_res = await db.execute(
            select(Entity).where(
                Entity.id.in_(entity_ids), Entity.tenant_id == tenant_id
            )
        )
        ent_by_id = {str(ent.id): ent for ent in ent_res.scalars().all()}

    pb_by_id: dict[str, object] = {}
    if playbook_ids:
        pb_res = await db.execute(
            select(Playbook).where(
                Playbook.id.in_(playbook_ids), Playbook.tenant_id == tenant_id
            )
        )
        pb_by_id = {str(pb.id): pb for pb in pb_res.scalars().all()}

    pat_by_id: dict[str, object] = {}
    if pattern_ids:
        pat_res = await db.execute(
            select(Pattern).where(
                Pattern.id.in_(pattern_ids), Pattern.tenant_id == tenant_id
            )
        )
        pat_by_id = {str(pat.id): pat for pat in pat_res.scalars().all()}

    for n in nodes.values():
        nid_str = n["id"]
        ntype = n["type"]
        if ntype == "episode":
            ep_obj = ep_by_id.get(nid_str)
            if ep_obj:
                base_title = ep_obj.title or ep_obj.root_cause_summary or f"Episode {nid_str[:8]}"
                if ep_obj.created_at:
                    n["title"] = f"{base_title} ({ep_obj.created_at.strftime('%b %d, %Y')})"
                else:
                    n["title"] = base_title
            elif not n.get("title"):
                n["title"] = f"Episode {nid_str[:8]}"
        elif ntype == "evidence":
            ev_obj = ev_by_id.get(nid_str)
            if ev_obj:
                base_title = ev_obj.title or f"Evidence {nid_str[:8]}"
                if ev_obj.ingested_at:
                    n["title"] = f"{base_title} ({ev_obj.ingested_at.strftime('%b %d, %Y')})"
                else:
                    n["title"] = base_title
            elif not n.get("title"):
                n["title"] = f"Evidence {nid_str[:8]}"
        elif ntype == "identity":
            ident_obj = ident_by_id.get(nid_str)
            if ident_obj:
                n["title"] = f"{ident_obj.canonical_name} ({ident_obj.entity_type})"
            elif not n.get("title"):
                n["title"] = f"Identity {nid_str[:8]}"
        elif ntype == "entity":
            ent_obj = ent_by_id.get(nid_str)
            if ent_obj:
                n["title"] = f"{ent_obj.name} ({ent_obj.entity_type})"
            elif not n.get("title"):
                n["title"] = f"Entity {nid_str[:8]}"
        elif ntype == "playbook":
            pb_obj = pb_by_id.get(nid_str)
            if pb_obj:
                n["title"] = pb_obj.title
            elif not n.get("title"):
                n["title"] = f"Playbook {nid_str[:8]}"
        elif ntype == "pattern":
            pat_obj = pat_by_id.get(nid_str)
            if pat_obj:
                n["title"] = pat_obj.title
            elif not n.get("title"):
                n["title"] = f"Pattern {nid_str[:8]}"

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
    """Return aggregate edge and node statistics for the tenant.

    Aggregation stays in SQL — the edge table is the largest graph structure
    and loading it into Python scales linearly with tenant size. The
    ``edge_type_counts`` / ``node_type_counts`` keys are a frontend contract
    (``GraphStatsResponse`` in ``frontend/src/lib/types/graph.ts``).
    """
    from sqlalchemy import union_all

    domain_filter = [edge_valid_at(as_of)]
    if domain_id is not None:
        domain_filter.append(
            (GraphEdge.domain_id == domain_id) | GraphEdge.domain_id.is_(None)
        )

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
