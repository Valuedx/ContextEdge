"""The canonical relationship vocabulary for ``graph_edges`` (F2).

``GraphEdge.edge_type`` is a free-text column written from the 26 modules that
import the builder, and the only vocabulary that existed was
``MAF_RELATIONSHIP_TYPES`` — a *read-side* allowlist. A typo at a write site
therefore produced a real, queryable edge that the projection silently dropped:
the graph knew something the agent could never see, and nothing failed.

This module is the write-side half. Every edge type the builder will accept is
declared here, and ``require_registered`` is called by ``add_edge`` /
``ensure_edge`` / ``close_edge`` / ``replace_edge``. Closing is validated too —
``close_edge`` with a typo silently closes nothing, which is the harder bug to
notice.

Adding a relationship type is deliberately two decisions, not one:

1. Register it here, in the group that describes what it means.
2. Decide whether the agent should traverse it — add it to
   ``MAF_RELATIONSHIP_TYPES`` in ``graph/agent/profiles.py``, or give it an
   entry in ``PROJECTION_EXCLUSIONS`` saying why not. The reason is data, not
   a comment: ``tests/test_edge_type_registry.py`` requires one.

Excluding a type from the projection is a normal, common answer. Budget is
finite, and a hub relation with 40-70 edges per handful of tickets spends it on
fan-out instead of on topology the agent can reason over.
"""

from __future__ import annotations


class UnknownEdgeType(ValueError):
    """An edge type that is not in the canonical vocabulary."""


# --- Evidence, identity and correlation -----------------------------------
_EVIDENCE = frozenset(
    {
        "affects_ci",           # evidence -> CI entity (every ticket connector)
        "assigned_to_group",    # evidence -> assignment group entity
        "mentions_identity",    # evidence -> canonical identity
        "references_identity",  # pattern/playbook -> canonical identity
        "exhibits",             # evidence -> error signature (D1 fingerprinting)
        "has_signature",        # episode <-> issue signature (D2)
        "tagged_with",          # evidence -> tag entity (Zoho)
        "documents",            # evidence -> knowledge category (Zoho KB)
        "related_ticket",       # deliberately untyped cross-ticket reference
        "asserted_in",          # claim/assertion -> evidence
        "supported_by",         # claim/hypothesis -> supporting evidence
        "supported_by_claim",   # decision -> claim
        "contradicted_by",      # claim -> contradicting evidence
        "weakened_by",          # claim -> weakening evidence
        "contradicts",          # evidence <-> evidence
    }
)

# --- Operational topology (CMDB + inference) ------------------------------
_TOPOLOGY = frozenset(
    {
        "depends_on",
        "runs_on",
        "hosted_on",
        "contains",
        "uses",
        "connected_to",
        "related_to",           # CMDB catch-all; unknown semantics
        "co_fails_with",        # inferred co-occurrence (>=3 distinct cases)
        "proposed_depends_on",  # agent-proposed, reviewable, never authored fact
        "instance_of",          # entity -> entity class (B1)
        "subclass_of",          # entity class -> parent class (B1)
        "affects",              # generic impact edge
        "applies_to",           # fix pattern -> workflow entity
        "governs",              # policy -> scope
        "involves_user",        # case -> user entity
        "targets_workflow",     # case -> workflow entity
        "tracks_request",       # case -> request entity
        "runs_on_agent",        # case -> agent machine entity
    }
)

# --- Incident causality (ServiceNow / Jira reference enrichment) ----------
_CAUSALITY = frozenset(
    {
        "related_problem",
        "caused_by_change",
        "remediated_by_change",
        "child_of_incident",
        "preceded_incident",    # alert rollup -> promoted incident
    }
)

# --- Learning: episodes, patterns, playbooks, fixes -----------------------
_LEARNING = frozenset(
    {
        "clusters",             # pattern -> episode membership
        "belongs_to",           # episode -> pattern
        "derived_from",         # playbook/pattern -> source
        "based_on",             # decision -> evidence/episode/pattern
        "aggregated_by",        # signature -> pattern
        "addresses",            # fix pattern -> error signature
        "recommends",           # fix pattern -> playbook
        # Pattern-enrichment scaffolding: one node per trigger / entity term /
        # observed error / root cause, each edged to the pattern
        # (``persist_pattern_enrichment_edges``).
        "trigger_of",
        "involved_in",
        "discovered_in",
        "causes",
        "superseded_by",        # versioned entity -> its replacement
        "validated_fix",
        "invalidated_fix",
        "partially_validated_fix",  # a half-fix is not a validation
    }
)

# --- Decision, governance and execution -----------------------------------
_DECISION = frozenset(
    {
        "records_decision",     # evidence -> actor identity
        "records_action_on",    # evidence -> target identity
        "considered",           # decision -> option
        "chose",                # decision -> selected option
        "followed_by",          # decision -> child decision
        "resulted_in",          # decision -> outcome
        "applied_policy",       # decision -> policy
        "required_approval",    # decision -> approval request
        "requires_approval",    # execution run -> approval request
        "approved_by",          # approval request -> user
        "denied_by",            # approval request -> user
        "modified_by",          # approval request -> user
        "has_execution",        # case -> execution run
        "executes",             # execution run -> playbook
        "executed_playbook",    # case -> playbook
        "execution_outcome",    # execution run -> playbook outcome
    }
)

EDGE_TYPES: frozenset[str] = _EVIDENCE | _TOPOLOGY | _CAUSALITY | _LEARNING | _DECISION

# Registered, written, and deliberately NOT traversable by maf.v1. Each entry
# is the argument for the exclusion — the projection has a finite budget and
# spending it is a decision, so it gets recorded like one.
PROJECTION_EXCLUSIONS: dict[str, str] = {
    "mentions_identity": (
        "measured 40-70 edges per handful of tickets; identity hubs would eat "
        "the budget affects_ci spends on topology the agent can reason over"
    ),
    "references_identity": (
        "same hub fan-out as mentions_identity, from the pattern/playbook side"
    ),
    "related_to": (
        "the CMDB catch-all: unknown semantics with hub fan-out is exactly what "
        "the traversal budget dies on"
    ),
    "related_ticket": (
        "SapphireIMS relates tickets without saying how; guessing caused_by_change "
        "would poison change-risk scoring"
    ),
    "proposed_depends_on": (
        "agent-discovered topology is a reviewable proposal, never authored fact — "
        "projecting it would let the agent read back its own guess as evidence"
    ),
    "instance_of": (
        "the class ladder is consumed by fix_applicability_service, not by graph "
        "traversal; projecting it would add a hop per CI for no agent-visible gain"
    ),
    "subclass_of": "the other half of the class ladder; same argument as instance_of",
    "clusters": (
        "the pattern/episode subgraph endpoint synthesises this relation from "
        "PatternEvidenceLink rows, so the projection would double-count it"
    ),
    "executed_playbook": "the projection reaches the playbook via has_execution -> executes",
    "execution_outcome": "the projection carries the outcome on resulted_in instead",
    "tagged_with": "Zoho tags are a folksonomy, not operational topology",
    "documents": "knowledge-taxonomy edge with no agent consumer yet",
    # NOTE: ``affects`` is deliberately NOT here — maf.v1 traverses it.
    "trigger_of": "enrichment scaffolding for pattern search, not a reasoning hop",
    "involved_in": "enrichment scaffolding; the same argument as trigger_of",
    "discovered_in": "enrichment scaffolding; the same argument as trigger_of",
    "causes": (
        "enrichment scaffolding — a root-cause *string* node, not a causal claim "
        "between real entities, which is what caused_by_change carries"
    ),
}


def require_registered(edge_type: str) -> str:
    """Return *edge_type*, or raise if it is not in the vocabulary.

    Fails loudly rather than writing the edge: an unregistered type is a typo
    or an undeclared relationship, and both are worse discovered at write time
    than as a silently unprojected row weeks later.
    """
    if edge_type not in EDGE_TYPES:
        raise UnknownEdgeType(
            f"Unregistered edge_type {edge_type!r}. Add it to "
            "contextedge.graph.edge_types.EDGE_TYPES in the group that describes "
            "what it means, then either allow it in MAF_RELATIONSHIP_TYPES or "
            "record why not in PROJECTION_EXCLUSIONS."
        )
    return edge_type
