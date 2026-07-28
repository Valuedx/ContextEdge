"""Server-controlled Context Graph projection profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from contextedge.graph.agent.contracts import AgentGraphBudget


@dataclass(frozen=True, slots=True)
class AgentGraphProjectionProfile:
    name: str
    node_types: frozenset[str]
    relationship_types: frozenset[str]
    default_budget: AgentGraphBudget
    maximum_budget: AgentGraphBudget
    hop_decay: float = 0.72
    relationship_weights: dict[str, float] = field(default_factory=dict)
    relationship_metadata: dict[str, frozenset[str]] = field(default_factory=dict)

    def clamp_budget(
        self,
        requested: AgentGraphBudget | None,
        requested_depth: int | None,
    ) -> AgentGraphBudget:
        value = requested or self.default_budget
        return AgentGraphBudget(
            max_nodes=min(value.max_nodes, self.maximum_budget.max_nodes),
            max_relationships=min(
                value.max_relationships,
                self.maximum_budget.max_relationships,
            ),
            max_depth=min(
                requested_depth if requested_depth is not None else value.max_depth,
                self.maximum_budget.max_depth,
            ),
            max_characters=min(
                value.max_characters,
                self.maximum_budget.max_characters,
            ),
        )

    def relationship_factor(self, relationship_type: str) -> float:
        return self.relationship_weights.get(relationship_type, 1.0)

    def filter_relationship_metadata(
        self,
        relationship_type: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        allowed = self.relationship_metadata.get(relationship_type, frozenset())
        if not metadata or not allowed:
            return {}
        return {key: metadata[key] for key in allowed if key in metadata}


MAF_NODE_TYPES = frozenset(
    {
        "session",
        "decision",
        "decision_option",
        "decision_outcome",
        "approval_request",
        "execution_run",
        "playbook",
        "pattern",
        "episode",
        "evidence",
        "identity",
        "entity",
        "user",
        "tenant_policy",
        "action_policy",
        "claim",
        "error_signature",
        "fix_pattern",
        "case_outcome",
    }
)

MAF_RELATIONSHIP_TYPES = frozenset(
    {
        "based_on",
        "supported_by",
        "contradicted_by",
        "weakened_by",
        "supported_by_claim",
        "records_decision",
        "records_action_on",
        "considered",
        "chose",
        "applied_policy",
        "required_approval",
        "resulted_in",
        "followed_by",
        "asserted_in",
        "has_execution",
        "executes",
        "requires_approval",
        "approved_by",
        "denied_by",
        "modified_by",
        "involves_user",
        "targets_workflow",
        "tracks_request",
        "runs_on_agent",
        "governs",
        "applies_to",
        "belongs_to",
        "affects",
        "derived_from",
        "contradicts",
        "aggregated_by",
        "addresses",
        "recommends",
        "validated_fix",
        "invalidated_fix",
        "superseded_by",
    }
)

MAF_V1 = AgentGraphProjectionProfile(
    name="maf.v1",
    node_types=MAF_NODE_TYPES,
    relationship_types=MAF_RELATIONSHIP_TYPES,
    default_budget=AgentGraphBudget(),
    maximum_budget=AgentGraphBudget(
        max_nodes=60,
        max_relationships=120,
        max_depth=3,
        max_characters=30_000,
    ),
    relationship_weights={
        "supported_by": 1.15,
        "supported_by_claim": 1.15,
        "chose": 1.1,
        "validated_fix": 1.2,
        "contradicted_by": 0.95,
        "invalidated_fix": 0.9,
    },
    relationship_metadata={
        "approved_by": frozenset({"status"}),
        "denied_by": frozenset({"status", "reason_code"}),
        "modified_by": frozenset({"status", "reason_code"}),
        "resulted_in": frozenset({"result", "outcome"}),
        "validated_fix": frozenset({"result"}),
        "invalidated_fix": frozenset({"result"}),
    },
)

PROFILES = MappingProxyType({MAF_V1.name: MAF_V1})


def get_projection_profile(name: str) -> AgentGraphProjectionProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown graph projection profile: {name}") from exc
