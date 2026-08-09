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
        # Structured diagnostic index (roadmap D2): failing_component +
        # failure_mode + trigger_change in ~60 chars. Signature-first
        # entry (symptom -> signature -> episodes) is how an experienced
        # engineer thinks; until this line the 50+ populated signatures
        # were invisible to the agent.
        "issue_signature",
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
        # Written by every ticket connector's reference enrichment
        # (evidence -> configuration_item / assignment_group) and previously
        # invisible here: an agent could receive a CI entity as a seed and
        # never discover which incidents affected it or which team owns it —
        # the graph knew, the projection didn't. mentions_identity stays
        # deliberately EXCLUDED: it fans out at 40-70 edges per handful of
        # tickets (measured), and identity hubs would eat the budget that
        # affects_ci spends on topology an agent can actually reason over.
        "affects_ci",
        "assigned_to_group",
        # evidence -> error_signature, written deterministically by the D1
        # fingerprinting pass in error_signature_service. The signature node
        # type has been hydratable since the profile shipped; this makes the
        # edges that reach it traversable.
        "exhibits",
        # episode <-> issue_signature (roadmap D2): the hop that makes a
        # signature seed reach its episode history.
        "has_signature",
        # Instance-level causal topology written by ServiceNow reference
        # enrichment (evidence -> evidence) since it shipped — and never
        # projectable until now. caused_by_change IS the "which change
        # caused this incident" join the diagnosis loop pivots on;
        # child_of_incident is major-incident grouping (roadmap D3);
        # preceded_incident is the alert-rollup -> incident timeline.
        "related_problem",
        "caused_by_change",
        "remediated_by_change",
        "child_of_incident",
        "preceded_incident",
        # CI<->CI topology cached from cmdb_rel_ci by
        # cmdb_topology_service (roadmap C1) — the blast-radius walk
        # (incident -> CI -> dependent CIs -> their incidents/changes).
        # related_to (the unmapped catch-all) stays excluded: unknown
        # semantics with hub fan-out is exactly what budget dies on.
        "depends_on",
        "runs_on",
        "hosted_on",
        "contains",
        "uses",
        "connected_to",
        # Inferred, symmetric, deliberately weaker than authored
        # topology: co-occurrence across >=3 distinct cases
        # (dependency_inference_service). Carries shared_cases metadata
        # so the agent can weigh the inference.
        "co_fails_with",
        "derived_from",
        "contradicts",
        "aggregated_by",
        "addresses",
        "recommends",
        "validated_fix",
        "invalidated_fix",
        # A "partial" fix result is its own type — folding it into
        # validated_fix let half-fixes masquerade as full validation.
        "partially_validated_fix",
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
        # Between full validation (1.2) and neutral: partial evidence
        # helps ranking but must never outrank a fully validated fix.
        "partially_validated_fix": 1.05,
        "contradicted_by": 0.95,
        "invalidated_fix": 0.9,
        # Semantic episode seeds are only useful if the proven playbook two
        # hops behind them (episode -belongs_to-> pattern -derived_from->
        # playbook) survives the budget. At the default hop_decay 0.72 an
        # unboosted 2-hop playbook lands at ~0.39-0.47 relevance — last in
        # the projection and first to be truncated. 1.2 lifts the chain to
        # ~0.56-0.67 (hop factor is clamped at 1.0 in the selector, so
        # relevance still decays monotonically).
        "belongs_to": 1.2,
        "derived_from": 1.2,
        # Signature-first diagnosis: a matched signature's episodes are
        # the precedent the agent came for, and the causal change join
        # is the highest-value hop in the incident loop.
        "has_signature": 1.15,
        "caused_by_change": 1.2,
    },
    relationship_metadata={
        "co_fails_with": frozenset({"shared_cases", "origin"}),
        "approved_by": frozenset({"status"}),
        "denied_by": frozenset({"status", "reason_code"}),
        "modified_by": frozenset({"status", "reason_code"}),
        "resulted_in": frozenset({"result", "outcome"}),
        "validated_fix": frozenset({"result"}),
        "invalidated_fix": frozenset({"result"}),
        "partially_validated_fix": frozenset({"result"}),
    },
)

PROFILES = MappingProxyType({MAF_V1.name: MAF_V1})


def get_projection_profile(name: str) -> AgentGraphProjectionProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown graph projection profile: {name}") from exc
