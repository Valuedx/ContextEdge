"""Context graph builder using PostgreSQL adjacency tables."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge


async def add_edge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    edge_type: str,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> GraphEdge:
    """Add an edge to the context graph."""
    edge = GraphEdge(
        tenant_id=tenant_id,
        source_node_type=source_type,
        source_node_id=source_id,
        target_node_type=target_type,
        target_node_id=target_id,
        edge_type=edge_type,
        weight=weight,
        metadata_extra=metadata,
    )
    db.add(edge)
    await db.flush()
    return edge


async def build_episode_graph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    episode_id: uuid.UUID,
    pattern_id: uuid.UUID | None,
    entity_ids: list[uuid.UUID],
) -> list[GraphEdge]:
    """Build graph edges from an episode to its related entities and patterns."""
    edges = []

    if pattern_id:
        edges.append(await add_edge(
            db, tenant_id,
            "episode", episode_id,
            "pattern", pattern_id,
            "belongs_to",
        ))

    for eid in entity_ids:
        edges.append(await add_edge(
            db, tenant_id,
            "episode", episode_id,
            "identity", eid,
            "affects",
        ))

    return edges
