"""Context graph builder using PostgreSQL adjacency tables."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.pattern import GraphEdge

ENRICHMENT_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


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
    domain_id: uuid.UUID | None = None,
    confidence: float | None = None,
    valid_from: datetime | None = None,
) -> GraphEdge:
    """Add an edge to the context graph."""
    edge = GraphEdge(
        tenant_id=tenant_id,
        domain_id=domain_id,
        source_node_type=source_type,
        source_node_id=source_id,
        target_node_type=target_type,
        target_node_id=target_id,
        edge_type=edge_type,
        weight=weight,
        metadata_extra=metadata,
        confidence=confidence,
        valid_from=valid_from or datetime.now(UTC),
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
    domain_id: uuid.UUID | None = None,
) -> GraphEdge:
    q = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == source_type,
        GraphEdge.source_node_id == source_id,
        GraphEdge.target_node_type == target_type,
        GraphEdge.target_node_id == target_id,
        GraphEdge.edge_type == edge_type,
        GraphEdge.valid_to.is_(None),
    )
    if domain_id is not None:
        q = q.where(GraphEdge.domain_id == domain_id)
    else:
        q = q.where(GraphEdge.domain_id.is_(None))
    existing = (await db.execute(q)).scalar_one_or_none()
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
        domain_id=domain_id,
    )


async def close_edge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    edge_type: str,
    *,
    domain_id: uuid.UUID | None = None,
    valid_to: datetime | None = None,
) -> int:
    """Close the current version of a logical relationship."""
    closed_at = valid_to or datetime.now(UTC)
    q = (
        update(GraphEdge)
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.source_node_type == source_type,
            GraphEdge.source_node_id == source_id,
            GraphEdge.target_node_type == target_type,
            GraphEdge.target_node_id == target_id,
            GraphEdge.edge_type == edge_type,
            GraphEdge.valid_to.is_(None),
        )
        .values(valid_to=closed_at)
    )
    if domain_id is None:
        q = q.where(GraphEdge.domain_id.is_(None))
    else:
        q = q.where(GraphEdge.domain_id == domain_id)
    result = await db.execute(q)
    return int(result.rowcount or 0)


async def replace_edge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    edge_type: str,
    *,
    weight: float = 1.0,
    metadata: dict | None = None,
    domain_id: uuid.UUID | None = None,
    confidence: float | None = None,
    changed_at: datetime | None = None,
) -> GraphEdge:
    """Close the active relationship and insert its next temporal version."""
    timestamp = changed_at or datetime.now(UTC)
    await close_edge(
        db,
        tenant_id,
        source_type,
        source_id,
        target_type,
        target_id,
        edge_type,
        domain_id=domain_id,
        valid_to=timestamp,
    )
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
        domain_id=domain_id,
        confidence=confidence,
        valid_from=timestamp,
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
    domain_id: uuid.UUID | None = None,
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
                domain_id=domain_id,
            )
        )
    return edges


async def build_episode_graph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    episode_id: uuid.UUID,
    pattern_id: uuid.UUID | None,
    entity_ids: list[uuid.UUID],
    domain_id: uuid.UUID | None = None,
) -> list[GraphEdge]:
    """Build graph edges from an episode to its related entities and patterns."""
    edges = []

    if pattern_id:
        edges.append(await ensure_edge(
            db, tenant_id,
            "episode", episode_id,
            "pattern", pattern_id,
            "belongs_to",
            domain_id=domain_id,
        ))

    edges.extend(
        await link_node_to_identities(
            db,
            tenant_id,
            "episode",
            episode_id,
            entity_ids,
            edge_type="affects",
            domain_id=domain_id,
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
    domain_id: uuid.UUID | None = None,
) -> GraphEdge:
    q = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "playbook",
        GraphEdge.source_node_id == playbook_id,
        GraphEdge.target_node_type == "evidence",
        GraphEdge.target_node_id == evidence_id,
        GraphEdge.edge_type == "contradicts",
    )
    if domain_id is not None:
        q = q.where(GraphEdge.domain_id == domain_id)
    else:
        q = q.where(GraphEdge.domain_id.is_(None))
    existing = (await db.execute(q)).scalar_one_or_none()
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
        domain_id=domain_id,
    )


async def link_decision_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    evidence_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "evidence", evidence_id, "based_on",
        weight=weight, metadata=metadata, domain_id=domain_id,
    )


async def link_decision_episode(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    episode_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "episode", episode_id, "based_on",
        weight=weight, metadata=metadata, domain_id=domain_id,
    )


async def link_decision_pattern(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    pattern_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "pattern", pattern_id, "based_on",
        weight=weight, metadata=metadata, domain_id=domain_id,
    )


async def link_decision_option(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    option_id: uuid.UUID,
    selected: bool,
    domain_id: uuid.UUID | None = None,
    *,
    metadata: dict | None = None,
) -> list[GraphEdge]:
    """Create CONSIDERED edge for all options, plus CHOSE for selected."""
    edges = [
        await ensure_edge(
            db, tenant_id, "decision", decision_id,
            "decision_option", option_id, "considered",
            metadata=metadata, domain_id=domain_id,
        )
    ]
    if selected:
        edges.append(
            await ensure_edge(
                db, tenant_id, "decision", decision_id,
                "decision_option", option_id, "chose",
                metadata=metadata, domain_id=domain_id,
            )
        )
    return edges


async def link_decision_policy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    policy_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "tenant_policy", policy_id, "applied_policy",
        metadata=metadata, domain_id=domain_id,
    )


async def link_decision_approval(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    approval_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "approval_request", approval_id, "required_approval",
        metadata=metadata, domain_id=domain_id,
    )


async def link_decision_outcome(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    outcome_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", decision_id,
        "decision_outcome", outcome_id, "resulted_in",
        metadata=metadata, domain_id=domain_id,
    )


async def link_decision_chain(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    metadata: dict | None = None,
) -> GraphEdge:
    return await ensure_edge(
        db, tenant_id, "decision", parent_id,
        "decision", child_id, "followed_by",
        metadata=metadata, domain_id=domain_id,
    )


def _enrichment_node_id(pattern_id: uuid.UUID, node_type: str, value: str) -> uuid.UUID:
    """Deterministic UUID for virtual enrichment nodes so edges are idempotent."""
    return uuid.uuid5(ENRICHMENT_NAMESPACE, f"{pattern_id}:{node_type}:{value}")


async def persist_pattern_enrichment_edges(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    trigger_conditions: list[str] | None,
    core_entities: list[str] | None,
    observed_errors: list[str] | None,
    root_causes: list[str] | None,
) -> list[GraphEdge]:
    """Persist pattern enrichment metadata as real graph edges.

    Creates typed edges from enrichment nodes (triggers, entities, errors,
    root causes) to the pattern so they participate in traversal and ranking.
    """
    mappings: list[tuple[str, list[str] | None, str]] = [
        ("trigger", trigger_conditions, "trigger_of"),
        ("entity_term", core_entities, "involved_in"),
        ("error", observed_errors, "discovered_in"),
        ("root_cause", root_causes, "causes"),
    ]
    edges: list[GraphEdge] = []
    for node_type, items, edge_type in mappings:
        if not items:
            continue
        for value in items:
            node_id = _enrichment_node_id(pattern_id, node_type, value)
            edges.append(
                await ensure_edge(
                    db,
                    tenant_id,
                    node_type,
                    node_id,
                    "pattern",
                    pattern_id,
                    edge_type,
                    weight=1.5,
                    metadata={"label": value},
                    domain_id=domain_id,
                )
            )
    return edges
