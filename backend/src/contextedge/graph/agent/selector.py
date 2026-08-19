"""Deterministic, connected, budget-aware Context Graph selection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphNode,
    AgentGraphProvenance,
    AgentGraphRelationship,
    AgentGraphRequest,
    AgentGraphSubset,
    AgentGraphUsage,
    GraphEdgeRecord,
)
from contextedge.graph.agent.profiles import AgentGraphProjectionProfile
from contextedge.graph.agent.repository import AgentGraphRepository


class AgentGraphSelector:
    # Hydrating a candidate costs a row fetch plus visibility checks; only
    # budget.max_nodes of them can survive selection, so hydrating more than
    # a small multiple is pure waste on hub-heavy graphs.
    MAX_HYDRATION_FACTOR = 5

    async def select(
        self,
        repository: AgentGraphRepository,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
        profile: AgentGraphProjectionProfile,
    ) -> AgentGraphSubset:
        budget = profile.clamp_budget(request.budget, request.max_depth)
        seed_candidates = await repository.resolve_seeds(request, scope)
        seed_candidates = [
            seed for seed in seed_candidates if seed.ref.type in profile.node_types
        ]
        seed_hydrated = await repository.hydrate_nodes(
            [seed.ref for seed in seed_candidates],
            scope,
        )
        seeds = [seed for seed in seed_candidates if seed.ref.key in seed_hydrated]
        scores = {seed.ref.key: seed.relevance for seed in seeds}
        hydrated = dict(seed_hydrated)
        parent: dict[str, str] = {}
        frontier = [seed.ref for seed in seeds]
        seen = set(scores)
        edge_records: dict[tuple[str, str, str], GraphEdgeRecord] = {}

        for _depth in range(1, budget.max_depth + 1):
            if not frontier:
                break
            rows = await repository.load_edges(frontier, scope, request.as_of)
            eligible = [
                edge
                for edge in rows
                if edge.type in profile.relationship_types
                and edge.source.type in profile.node_types
                and edge.target.type in profile.node_types
            ]
            candidate_refs = {}
            frontier_keys = {node.key for node in frontier}
            for edge in eligible:
                if edge.source.key in frontier_keys:
                    candidate_refs[edge.target.key] = edge.target
                if edge.target.key in frontier_keys:
                    candidate_refs[edge.source.key] = edge.source

            hydration_cap = budget.max_nodes * self.MAX_HYDRATION_FACTOR
            if len(candidate_refs) > hydration_cap:
                best_weight: dict[str, float] = {}
                for edge in eligible:
                    for key in (edge.source.key, edge.target.key):
                        if key in candidate_refs:
                            best_weight[key] = max(best_weight.get(key, 0.0), edge.weight)
                kept = sorted(
                    candidate_refs,
                    key=lambda key: (-best_weight.get(key, 0.0), key),
                )[:hydration_cap]
                candidate_refs = {key: candidate_refs[key] for key in kept}

            candidate_hydrated = await repository.hydrate_nodes(
                list(candidate_refs.values()),
                scope,
            )
            hydrated.update(candidate_hydrated)

            next_frontier = []
            for edge in eligible:
                if edge.source.key not in hydrated or edge.target.key not in hydrated:
                    continue
                edge_key = (edge.source.key, edge.target.key, edge.type)
                edge_records[edge_key] = edge
                orientations = (
                    (edge.source.key, edge.target.key, edge.target),
                    (edge.target.key, edge.source.key, edge.source),
                )
                for current_key, neighbor_key, neighbor_ref in orientations:
                    if current_key not in frontier_keys or neighbor_key not in hydrated:
                        continue
                    # The per-hop factor is clamped to 1.0 so relevance decays
                    # monotonically: heavy edges (weight 1.5 enrichment) and
                    # relationship boosts must not amplify scores above the
                    # parent's, or multi-hop paths through boosted edges
                    # outrank the seeds themselves.
                    hop_factor = min(
                        profile.hop_decay
                        * max(edge.weight, 0.0)
                        * (edge.confidence if edge.confidence is not None else 1.0)
                        * profile.relationship_factor(edge.type),
                        1.0,
                    )
                    candidate_score = scores.get(current_key, 0.0) * hop_factor
                    if candidate_score > scores.get(neighbor_key, -1.0):
                        scores[neighbor_key] = candidate_score
                        parent[neighbor_key] = current_key
                    if neighbor_key not in seen:
                        seen.add(neighbor_key)
                        next_frontier.append(neighbor_ref)
            frontier = sorted(
                next_frontier,
                key=lambda node: (node.type, str(node.id)),
            )

        ordered_keys = sorted(
            hydrated,
            key=lambda key: (
                -scores.get(key, 0.0),
                hydrated[key].ref.type,
                str(hydrated[key].ref.id),
            ),
        )
        selected: dict[str, AgentGraphNode] = {}
        character_count = 0
        truncation_reasons: list[str] = []

        # Node admission may not spend the whole character budget when there
        # are edges to emit: without a reserve, a node-rich selection leaves
        # zero characters for relationships and the "graph" degrades to a
        # flat list. A tenth of the budget is ~8 relationship JSONs at the
        # default budget. Relationships still draw from the FULL budget, so
        # the reserve is only ever idle if no selected pair is connected.
        node_character_budget = budget.max_characters
        if edge_records:
            node_character_budget -= budget.max_characters // 10

        def chain_for(key: str) -> list[str]:
            chain = [key]
            while chain[-1] in parent:
                ancestor = parent[chain[-1]]
                if ancestor in chain:
                    break
                chain.append(ancestor)
            chain.reverse()
            return chain

        for key in ordered_keys:
            pending = [item for item in chain_for(key) if item not in selected]
            if len(selected) + len(pending) > budget.max_nodes:
                if "max_nodes" not in truncation_reasons:
                    truncation_reasons.append("max_nodes")
                continue
            additions: list[tuple[str, AgentGraphNode, int]] = []
            for item in pending:
                raw = hydrated[item]
                node = AgentGraphNode(
                    key=raw.ref.key,
                    type=raw.ref.type,
                    id=raw.ref.id,
                    label=raw.label,
                    summary=raw.summary,
                    facts=raw.facts,
                    confidence=raw.confidence,
                    freshness=raw.freshness,
                    relevance=round(scores.get(item, 0.0), 6),
                    provenance=AgentGraphProvenance(
                        source_type=raw.ref.type,
                        created_at=raw.created_at,
                        updated_at=raw.updated_at,
                    ),
                )
                additions.append((item, node, len(node.model_dump_json())))
            addition_chars = sum(item[2] for item in additions)
            if character_count + addition_chars > node_character_budget:
                if "max_characters" not in truncation_reasons:
                    truncation_reasons.append("max_characters")
                continue
            for item, node, size in additions:
                selected[item] = node
                character_count += size

        relationships: list[AgentGraphRelationship] = []
        ordered_edges = sorted(
            edge_records.values(),
            key=lambda edge: (
                -min(scores.get(edge.source.key, 0.0), scores.get(edge.target.key, 0.0)),
                edge.type,
                edge.source.key,
                edge.target.key,
            ),
        )
        for edge in ordered_edges:
            if edge.source.key not in selected or edge.target.key not in selected:
                continue
            if len(relationships) >= budget.max_relationships:
                if "max_relationships" not in truncation_reasons:
                    truncation_reasons.append("max_relationships")
                break
            relevance = min(
                scores.get(edge.source.key, 0.0),
                scores.get(edge.target.key, 0.0),
            )
            relationship = AgentGraphRelationship(
                source=edge.source.key,
                target=edge.target.key,
                type=edge.type,
                direction="outgoing",
                weight=edge.weight,
                confidence=edge.confidence,
                relevance=round(relevance, 6),
                metadata=profile.filter_relationship_metadata(
                    edge.type,
                    edge.metadata,
                ),
            )
            size = len(relationship.model_dump_json())
            if character_count + size > budget.max_characters:
                if "max_characters" not in truncation_reasons:
                    truncation_reasons.append("max_characters")
                break
            relationships.append(relationship)
            character_count += size

        warnings: list[str] = []
        if not seeds:
            warnings.append("No authorized graph seeds were resolved.")
        if request.as_of is not None:
            warnings.append(
                "Relationship topology is point-in-time; node facts reflect current state."
            )

        return AgentGraphSubset(
            profile=profile.name,
            projection_id=uuid4(),
            generated_at=datetime.now(UTC),
            query=request.query,
            seeds=[seed.ref for seed in seeds],
            nodes=list(selected.values()),
            relationships=relationships,
            budget=budget,
            usage=AgentGraphUsage(
                nodes=len(selected),
                relationships=len(relationships),
                characters=character_count,
            ),
            truncated=bool(truncation_reasons),
            truncation_reasons=truncation_reasons,
            warnings=warnings,
        )
