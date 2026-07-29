"""SQLAlchemy repository for bounded agent graph traversal and hydration."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy import case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphRequest,
    GraphEdgeRecord,
    GraphNodeRef,
    HydratedGraphNode,
    RankedGraphSeed,
)
from contextedge.graph.agent.hydrators import NODE_MODELS, hydrate_node, node_is_visible
from contextedge.graph.temporal import edge_valid_at
from contextedge.models.entity import Entity
from contextedge.models.episode import CanonicalIdentity
from contextedge.models.pattern import GraphEdge, Pattern
from contextedge.models.playbook import Playbook
from contextedge.search.access_control import resolve_excluded_access_policy_ids


class AgentGraphRepository(Protocol):
    async def resolve_seeds(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
    ) -> list[RankedGraphSeed]: ...

    async def load_edges(
        self,
        frontier: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
        as_of: datetime | None,
    ) -> list[GraphEdgeRecord]: ...

    async def hydrate_nodes(
        self,
        nodes: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
    ) -> dict[str, HydratedGraphNode]: ...


class SQLAlchemyAgentGraphRepository:
    # Per-frontier-node edge cap: a hub node (an entity referenced by tens of
    # thousands of sessions) must not turn one traversal hop into a
    # tens-of-thousands-row fetch. The selector re-ranks and budgets after
    # this, so keeping the strongest edges per node is lossless in practice.
    EDGES_PER_FRONTIER_NODE = 200
    # Absolute backstop per load_edges call regardless of frontier size.
    MAX_EDGES_PER_HOP = 5_000

    def __init__(self, db: AsyncSession):
        self.db = db

    def _domain_predicate(self, column, scope: AgentGraphAccessScope):
        if scope.domain_id is not None:
            return or_(column.is_(None), column == scope.domain_id)
        if scope.allowed_domain_ids is not None:
            return or_(column.is_(None), column.in_(scope.allowed_domain_ids))
        return None

    async def resolve_seeds(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
    ) -> list[RankedGraphSeed]:
        seeds = [
            RankedGraphSeed(ref=seed, relevance=1.0, reason="explicit")
            for seed in request.seeds
        ]
        if request.session_id is not None:
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="session", id=request.session_id),
                    relevance=1.0,
                    reason="session",
                )
            )

        query = request.query.strip()
        if query:
            playbook_q = (
                select(Playbook.id)
                .where(
                    Playbook.tenant_id == scope.tenant_id,
                    Playbook.lifecycle_state == "approved",
                    or_(
                        Playbook.title.icontains(query, autoescape=True),
                        Playbook.description.icontains(query, autoescape=True),
                    ),
                )
                .limit(3)
            )
            pattern_q = (
                select(Pattern.id)
                .where(
                    Pattern.tenant_id == scope.tenant_id,
                    Pattern.active_flag.is_(True),
                    or_(
                        Pattern.title.icontains(query, autoescape=True),
                        Pattern.description.icontains(query, autoescape=True),
                    ),
                )
                .limit(3)
            )
            playbook_domain = self._domain_predicate(Playbook.domain_id, scope)
            pattern_domain = self._domain_predicate(Pattern.domain_id, scope)
            if playbook_domain is not None:
                playbook_q = playbook_q.where(playbook_domain)
            if pattern_domain is not None:
                pattern_q = pattern_q.where(pattern_domain)
            for row in (await self.db.execute(playbook_q)).scalars().all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="playbook", id=row),
                        relevance=0.9,
                        reason="query",
                    )
                )
            for row in (await self.db.execute(pattern_q)).scalars().all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="pattern", id=row),
                        relevance=0.85,
                        reason="query",
                    )
                )

        for term in request.entities[:10]:
            entity_q = (
                select(Entity.id)
                .where(
                    Entity.tenant_id == scope.tenant_id,
                    Entity.is_active.is_(True),
                    Entity.name.icontains(term, autoescape=True),
                )
                .limit(3)
            )
            identity_q = (
                select(CanonicalIdentity.id)
                .where(
                    CanonicalIdentity.tenant_id == scope.tenant_id,
                    CanonicalIdentity.is_active.is_(True),
                    CanonicalIdentity.canonical_name.icontains(
                        term,
                        autoescape=True,
                    ),
                )
                .limit(3)
            )
            entity_domain = self._domain_predicate(Entity.domain_id, scope)
            if entity_domain is not None:
                entity_q = entity_q.where(entity_domain)
            for row in (await self.db.execute(entity_q)).scalars().all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="entity", id=row),
                        relevance=0.9,
                        reason="entity",
                    )
                )
            for row in (await self.db.execute(identity_q)).scalars().all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="identity", id=row),
                        relevance=0.85,
                        reason="entity",
                    )
                )

        deduplicated: dict[str, RankedGraphSeed] = {}
        for seed in seeds:
            current = deduplicated.get(seed.ref.key)
            if current is None or seed.relevance > current.relevance:
                deduplicated[seed.ref.key] = seed
        return sorted(
            deduplicated.values(),
            key=lambda item: (-item.relevance, item.ref.type, str(item.ref.id)),
        )[:20]

    async def load_edges(
        self,
        frontier: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
        as_of: datetime | None,
    ) -> list[GraphEdgeRecord]:
        if not frontier:
            return []
        pairs = [(node.type, node.id) for node in frontier]
        source_matches = tuple_(
            GraphEdge.source_node_type,
            GraphEdge.source_node_id,
        ).in_(pairs)
        target_matches = tuple_(
            GraphEdge.target_node_type,
            GraphEdge.target_node_id,
        ).in_(pairs)

        # Rank edges per matched frontier endpoint and keep only the
        # strongest EDGES_PER_FRONTIER_NODE of each, so one hub node cannot
        # make a hop unbounded. Edges matching on both endpoints partition
        # under their source key, which is fine — they are counted once.
        partition_key = case(
            (
                source_matches,
                func.concat(GraphEdge.source_node_type, ":", GraphEdge.source_node_id),
            ),
            else_=func.concat(GraphEdge.target_node_type, ":", GraphEdge.target_node_id),
        )
        rank = (
            func.row_number()
            .over(
                partition_by=partition_key,
                order_by=(GraphEdge.weight.desc(), GraphEdge.id),
            )
            .label("frontier_rank")
        )
        inner = select(GraphEdge, rank).where(
            GraphEdge.tenant_id == scope.tenant_id,
            edge_valid_at(as_of),
            or_(source_matches, target_matches),
        )
        domain_predicate = self._domain_predicate(GraphEdge.domain_id, scope)
        if domain_predicate is not None:
            inner = inner.where(domain_predicate)
        subq = inner.subquery()
        ranked_edge = aliased(GraphEdge, subq)
        query = (
            select(ranked_edge)
            .where(subq.c.frontier_rank <= self.EDGES_PER_FRONTIER_NODE)
            # Deterministic survivors when the absolute cap bites.
            .order_by(subq.c.weight.desc(), subq.c.id)
            .limit(self.MAX_EDGES_PER_HOP)
        )

        rows = (await self.db.execute(query)).scalars().all()
        return [
            GraphEdgeRecord(
                source=GraphNodeRef(type=row.source_node_type, id=row.source_node_id),
                target=GraphNodeRef(type=row.target_node_type, id=row.target_node_id),
                type=row.edge_type,
                weight=float(row.weight),
                confidence=float(row.confidence) if row.confidence is not None else None,
                metadata=row.metadata_extra or {},
            )
            for row in rows
        ]

    async def hydrate_nodes(
        self,
        nodes: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
    ) -> dict[str, HydratedGraphNode]:
        grouped: dict[str, list[GraphNodeRef]] = defaultdict(list)
        for node in nodes:
            if node.type in NODE_MODELS:
                grouped[node.type].append(node)

        excluded = await resolve_excluded_access_policy_ids(
            self.db,
            scope.tenant_id,
            list(scope.roles),
        )
        excluded_evidence_policy_ids = set(excluded or [])
        hydrated: dict[str, HydratedGraphNode] = {}

        for node_type, refs in grouped.items():
            model = NODE_MODELS[node_type]
            query = select(model).where(
                model.id.in_([ref.id for ref in refs]),
                model.tenant_id == scope.tenant_id,
            )
            rows = (await self.db.execute(query)).scalars().all()
            for row in rows:
                if not node_is_visible(
                    node_type,
                    row,
                    scope,
                    excluded_evidence_policy_ids,
                ):
                    continue
                node = hydrate_node(node_type, row)
                hydrated[node.ref.key] = node
        return hydrated
