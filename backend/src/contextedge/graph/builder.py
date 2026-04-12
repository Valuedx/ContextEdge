"""Context graph builder using PostgreSQL adjacency tables."""

import uuid

from sqlalchemy import select
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


async def ensure_edge(
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
    existing = (
        await db.execute(
            select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.source_node_type == source_type,
                GraphEdge.source_node_id == source_id,
                GraphEdge.target_node_type == target_type,
                GraphEdge.target_node_id == target_id,
                GraphEdge.edge_type == edge_type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await add_edge(
        db,
        tenant_id,
        source_type,
        source_id,
        target_type,
        target_id,
        edge_type,
        weight=weight,
        metadata=metadata,
    )


async def link_node_to_identities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    node_type: str,
    node_id: uuid.UUID,
    identity_ids: list[uuid.UUID],
    *,
    edge_type: str = "mentions_identity",
    weight: float = 1.0,
    metadata: dict | None = None,
) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    seen: set[uuid.UUID] = set()
    for identity_id in identity_ids:
        if identity_id in seen:
            continue
        seen.add(identity_id)
        edges.append(
            await ensure_edge(
                db,
                tenant_id,
                node_type,
                node_id,
                "identity",
                identity_id,
                edge_type,
                weight=weight,
                metadata=metadata,
            )
        )
    return edges


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
        edges.append(await ensure_edge(
            db, tenant_id,
            "episode", episode_id,
            "pattern", pattern_id,
            "belongs_to",
        ))

    edges.extend(
        await link_node_to_identities(
            db,
            tenant_id,
            "episode",
            episode_id,
            entity_ids,
            edge_type="affects",
        )
    )

    return edges


async def add_contradicts_edge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    playbook_id: uuid.UUID,
    evidence_id: uuid.UUID,
    *,
    metadata: dict | None = None,
) -> GraphEdge:
    existing = (
        await db.execute(
            select(GraphEdge).where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.source_node_type == "playbook",
                GraphEdge.source_node_id == playbook_id,
                GraphEdge.target_node_type == "evidence",
                GraphEdge.target_node_id == evidence_id,
                GraphEdge.edge_type == "contradicts",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    return await add_edge(
        db,
        tenant_id,
        "playbook",
        playbook_id,
        "evidence",
        evidence_id,
        "contradicts",
        metadata=metadata,
    )
