from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphBudget,
    AgentGraphRequest,
    GraphEdgeRecord,
    GraphNodeRef,
    HydratedGraphNode,
    RankedGraphSeed,
)
from contextedge.graph.agent.hydrators import hydrate_node, node_is_visible
from contextedge.graph.agent.profiles import MAF_V1, get_projection_profile
from contextedge.graph.agent.selector import AgentGraphSelector
from contextedge.graph.temporal import normalize_graph_as_of
from contextedge.models.base import Base


class FakeRepository:
    def __init__(self):
        self.session_id = uuid4()
        self.claim_id = uuid4()
        self.evidence_id = uuid4()
        self.hidden_id = uuid4()
        self.nodes = {
            f"session:{self.session_id}": self._node("session", self.session_id, "Case"),
            f"claim:{self.claim_id}": self._node("claim", self.claim_id, "Root cause"),
            f"evidence:{self.evidence_id}": self._node(
                "evidence", self.evidence_id, "Incident summary"
            ),
        }

    @staticmethod
    def _node(node_type, node_id, label):
        return HydratedGraphNode(
            ref=GraphNodeRef(type=node_type, id=node_id),
            label=label,
            summary=None,
            facts={},
            confidence=0.9,
            freshness=None,
            created_at=None,
            updated_at=None,
        )

    async def resolve_seeds(self, request, scope):
        del request, scope
        return [
            RankedGraphSeed(
                ref=GraphNodeRef(type="session", id=self.session_id),
                relevance=1.0,
            )
        ]

    async def load_edges(self, frontier, scope, as_of):
        del scope, as_of
        keys = {node.key for node in frontier}
        if f"session:{self.session_id}" in keys:
            return [
                GraphEdgeRecord(
                    source=GraphNodeRef(type="claim", id=self.claim_id),
                    target=GraphNodeRef(type="session", id=self.session_id),
                    type="asserted_in",
                    weight=1.0,
                    confidence=0.9,
                ),
                GraphEdgeRecord(
                    source=GraphNodeRef(type="session", id=self.session_id),
                    target=GraphNodeRef(type="evidence", id=self.hidden_id),
                    type="based_on",
                    weight=1.0,
                    confidence=1.0,
                ),
            ]
        if f"claim:{self.claim_id}" in keys:
            return [
                GraphEdgeRecord(
                    source=GraphNodeRef(type="claim", id=self.claim_id),
                    target=GraphNodeRef(type="evidence", id=self.evidence_id),
                    type="supported_by",
                    weight=1.0,
                    confidence=0.9,
                )
            ]
        return []

    async def hydrate_nodes(self, nodes, scope):
        del scope
        return {node.key: self.nodes[node.key] for node in nodes if node.key in self.nodes}


def test_maf_profile_clamps_server_maximums():
    budget = MAF_V1.clamp_budget(
        AgentGraphBudget(
            max_nodes=100,
            max_relationships=250,
            max_depth=3,
            max_characters=50_000,
        ),
        requested_depth=3,
    )
    assert budget.max_nodes == 60
    assert budget.max_relationships == 120
    assert budget.max_characters == 30_000
    assert budget.max_depth == 3
    assert MAF_V1.type_reservations["playbook"] == 2


def test_unknown_projection_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown graph projection profile"):
        get_projection_profile("unknown")


def test_selector_preserves_paths_and_never_traverses_hidden_nodes():
    repository = FakeRepository()
    scope = AgentGraphAccessScope(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        principal_type="user",
    )
    subset = asyncio.run(
        AgentGraphSelector().select(
            repository,
            AgentGraphRequest(query="root cause", max_depth=2),
            scope,
            MAF_V1,
        )
    )
    keys = {node.key for node in subset.nodes}
    assert f"session:{repository.session_id}" in keys
    assert f"claim:{repository.claim_id}" in keys
    assert f"evidence:{repository.evidence_id}" in keys
    assert f"evidence:{repository.hidden_id}" not in keys
    assert all(
        relationship.source in keys and relationship.target in keys
        for relationship in subset.relationships
    )


def test_selector_reports_budget_truncation():
    repository = FakeRepository()
    scope = AgentGraphAccessScope(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        principal_type="user",
    )
    subset = asyncio.run(
        AgentGraphSelector().select(
            repository,
            AgentGraphRequest(
                query="root cause",
                budget=AgentGraphBudget(
                    max_nodes=1,
                    max_relationships=1,
                    max_depth=2,
                    max_characters=1_000,
                ),
            ),
            scope,
            MAF_V1,
        )
    )
    assert subset.truncated is True
    assert "max_nodes" in subset.truncation_reasons
    assert subset.usage.nodes == 1


def test_selector_reserves_characters_for_relationships():
    """A node-rich selection must not spend the whole character budget:
    without a reserve the projection degrades to a flat, edge-less node
    list (observed live: 24,927/25,000 characters on nodes, 0 of 80
    relationship slots used)."""
    repository = FakeRepository()
    repository.nodes = {
        key: FakeRepository._node(
            node.ref.type, node.ref.id, node.label + " " + "x" * 1200
        )
        for key, node in repository.nodes.items()
    }
    scope = AgentGraphAccessScope(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        principal_type="user",
    )

    def run(budget=None):
        return asyncio.run(
            AgentGraphSelector().select(
                repository,
                AgentGraphRequest(query="root cause", max_depth=2, budget=budget),
                scope,
                MAF_V1,
            )
        )

    generous = run()
    assert generous.usage.relationships >= 1  # precondition for the probe
    node_chars = sum(len(n.model_dump_json()) for n in generous.nodes)
    rel_chars = len(generous.relationships[0].model_dump_json())

    # Sized so every node fits but the first relationship would not: the
    # exact live failure shape. The reserve must trade a tail node for
    # keeping the topology.
    subset = run(
        AgentGraphBudget(
            max_nodes=24,
            max_relationships=48,
            max_depth=2,
            max_characters=node_chars + rel_chars - 1,
        )
    )
    assert subset.usage.relationships >= 1
    assert subset.usage.nodes >= 2
    assert "max_characters" in subset.truncation_reasons


def test_selector_reserves_playbook_slots_before_relevance_fill():
    """G4.1: a hub of evidence must not push playbooks off the budget."""
    repository = FakeRepository()
    extra = [uuid4() for _ in range(8)]
    playbook_a, playbook_b = uuid4(), uuid4()
    for eid in extra:
        repository.nodes[f"evidence:{eid}"] = FakeRepository._node(
            "evidence", eid, "hub evidence"
        )
    repository.nodes[f"playbook:{playbook_a}"] = FakeRepository._node(
        "playbook", playbook_a, "Playbook A"
    )
    repository.nodes[f"playbook:{playbook_b}"] = FakeRepository._node(
        "playbook", playbook_b, "Playbook B"
    )

    orig_load = repository.load_edges

    async def load_edges(frontier, scope, as_of):
        edges = list(await orig_load(frontier, scope, as_of))
        keys = {node.key for node in frontier}
        if f"session:{repository.session_id}" in keys:
            for eid in extra:
                edges.append(
                    GraphEdgeRecord(
                        source=GraphNodeRef(type="session", id=repository.session_id),
                        target=GraphNodeRef(type="evidence", id=eid),
                        type="based_on",
                        weight=1.0,
                        confidence=1.0,
                    )
                )
            edges.append(
                GraphEdgeRecord(
                    source=GraphNodeRef(type="session", id=repository.session_id),
                    target=GraphNodeRef(type="playbook", id=playbook_a),
                    type="derived_from",
                    weight=0.2,
                    confidence=0.5,
                )
            )
            edges.append(
                GraphEdgeRecord(
                    source=GraphNodeRef(type="session", id=repository.session_id),
                    target=GraphNodeRef(type="playbook", id=playbook_b),
                    type="derived_from",
                    weight=0.2,
                    confidence=0.5,
                )
            )
        return edges

    repository.load_edges = load_edges  # type: ignore[method-assign]
    scope = AgentGraphAccessScope(
        tenant_id=uuid4(),
        principal_id=uuid4(),
        principal_type="user",
    )
    subset = asyncio.run(
        AgentGraphSelector().select(
            repository,
            AgentGraphRequest(
                query="root cause",
                max_depth=2,
                budget=AgentGraphBudget(
                    max_nodes=6,
                    max_relationships=40,
                    max_depth=2,
                    max_characters=50_000,
                ),
            ),
            scope,
            MAF_V1,
        )
    )
    types = {node.type for node in subset.nodes}
    assert "playbook" in types


def test_as_of_requires_offset_and_rejects_future_values():
    with pytest.raises(HTTPException):
        normalize_graph_as_of(datetime.now())
    with pytest.raises(HTTPException):
        normalize_graph_as_of(datetime.now(UTC) + timedelta(hours=1))
    value = datetime.now(UTC) - timedelta(minutes=1)
    assert normalize_graph_as_of(value) == value


def test_hardened_models_are_registered():
    assert "decision_claims" in Base.metadata.tables
    assert "decision_action_policies" in Base.metadata.tables
    assert "case_outcome_fix_patterns" in Base.metadata.tables
    graph_edges = Base.metadata.tables["graph_edges"]
    assert graph_edges.c.valid_from.nullable is False
    assert {constraint.name for constraint in graph_edges.constraints} >= {
        "ck_graph_edges_weight_nonnegative",
        "ck_graph_edges_confidence_range",
        "ck_graph_edges_valid_window",
    }


def test_generated_search_vectors_are_read_only_in_orm():
    evidence_column = Base.metadata.tables["evidence_items"].c.search_tsvector
    playbook_column = Base.metadata.tables["playbooks"].c.search_tsvector

    assert evidence_column.computed is not None
    assert playbook_column.computed is not None


def test_pending_ai_decisions_are_invisible_until_reviewed():
    """Agent output must not launder itself into agent input: a pending
    AI-authored diagnosis stays out of projection until review or a
    recorded outcome moves it past pending."""
    tenant_id = uuid4()
    scope = AgentGraphAccessScope(
        tenant_id=tenant_id,
        principal_id=uuid4(),
        principal_type="user",
    )
    decision = SimpleNamespace(
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        status="pending",
        actor_type="ai",
    )
    assert node_is_visible("decision", decision, scope, set()) is False
    decision.status = "completed"  # outcome recorded
    assert node_is_visible("decision", decision, scope, set()) is True
    decision.status = "pending"
    decision.actor_type = "human"  # human pending decisions stay visible
    assert node_is_visible("decision", decision, scope, set()) is True
    decision.status = "superseded"
    assert node_is_visible("decision", decision, scope, set()) is False


def test_sensitive_nodes_fail_closed_and_user_projection_omits_email():
    tenant_id = uuid4()
    scope = AgentGraphAccessScope(
        tenant_id=tenant_id,
        principal_id=uuid4(),
        principal_type="user",
    )
    evidence = SimpleNamespace(
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        sensitivity_label="legal_hold",
        redaction_status=None,
        access_policy_id=None,
    )
    assert node_is_visible("evidence", evidence, scope, set()) is False
    evidence.sensitivity_label = None
    evidence.redaction_status = "pending"
    assert node_is_visible("evidence", evidence, scope, set()) is False

    claim = SimpleNamespace(
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        validation_status="unverified",
    )
    assert node_is_visible("claim", claim, scope, set()) is False
    claim.tenant_id = uuid4()
    claim.validation_status = "human_validated"
    assert node_is_visible("claim", claim, scope, set()) is False

    user = SimpleNamespace(
        id=uuid4(),
        display_name="Operations Reviewer",
        email="private@example.com",
        status="active",
        created_at=None,
        updated_at=None,
    )
    projected = hydrate_node("user", user)
    assert projected.label == "Operations Reviewer"
    assert projected.facts == {"status": "active"}
    assert "private@example.com" not in str(projected)


def test_agent_request_rejects_naive_as_of():
    with pytest.raises(ValueError, match="timezone"):
        AgentGraphRequest(as_of=datetime.now())


def test_episode_facts_render_bounded_contradictions():
    """C6: an agent consuming an episode sees that its sources
    disagreed — bounded to 3 entries with truncated claims."""
    from types import SimpleNamespace as NS

    episode = NS(
        id=uuid4(),
        title="VPN outage",
        root_cause_summary="Expired certificate",
        final_outcome="Renewed",
        status="approved",
        reviewer_state="approved",
        extraction_confidence=0.9,
        contradictions=[
            {
                "topic": "what fixed it",
                "accounts": [
                    {"evidence_id": "a", "claim": "close notes: cert renewed"},
                    {"evidence_id": "b", "claim": "teams: auth service rolled back"},
                ],
            },
            "garbage",
        ]
        + [{"topic": f"noise {i}", "accounts": [{"claim": "x"}]} for i in range(5)],
    )
    projected = hydrate_node("episode", episode)
    rendered = projected.facts["contradictions"]
    assert len(rendered) <= 3
    assert rendered[0]["topic"] == "what fixed it"
    assert "cert renewed" in rendered[0]["accounts"][0]


def test_episode_without_contradictions_has_no_block():
    from types import SimpleNamespace as NS

    episode = NS(
        id=uuid4(),
        title="VPN outage",
        root_cause_summary="Expired certificate",
        final_outcome=None,
        status="approved",
        reviewer_state="approved",
        extraction_confidence=0.9,
        contradictions=None,
    )
    projected = hydrate_node("episode", episode)
    assert "contradictions" not in projected.facts
