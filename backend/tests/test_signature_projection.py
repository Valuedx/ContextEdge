"""D2/D3: issue signatures and causal reference edges in maf.v1.

Validated live before these pins: the query "workflow failing with SSL
handshake error in REST client plugin after TLS change" seeded
rest_client_plugin/ssl_handshake_failure_missing_tls_version
(episode_count=2) into the projection with a has_signature edge to its
episode.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from contextedge.graph.agent.hydrators import NODE_MODELS, hydrate_node
from contextedge.graph.agent.profiles import (
    MAF_NODE_TYPES,
    MAF_RELATIONSHIP_TYPES,
)


def test_issue_signature_is_projectable():
    assert "issue_signature" in MAF_NODE_TYPES
    assert "issue_signature" in NODE_MODELS
    assert "has_signature" in MAF_RELATIONSHIP_TYPES


def test_causal_reference_edges_are_projectable():
    """The ServiceNow reference enrichment has written these
    evidence -> evidence edges since it shipped; the diagnosis loop
    (which change caused this incident, which problem explains it,
    which major incident is this part of) is built on them."""
    for edge in (
        "related_problem",
        "caused_by_change",
        "remediated_by_change",
        "child_of_incident",
        "preceded_incident",
    ):
        assert edge in MAF_RELATIONSHIP_TYPES, edge


def test_signature_hydration_reads_like_a_diagnosis_sheet():
    sig = SimpleNamespace(
        id=uuid.uuid4(),
        failing_component="rest_client_plugin",
        affected_capability="api_connectivity",
        failure_mode="ssl_handshake_failure_missing_tls_version",
        trigger_change="policy_change",
        environment="production",
        scope="single_device",
        episode_count=2,
        created_at=None,
        updated_at=None,
    )
    node = hydrate_node("issue_signature", sig)
    assert node.label == "rest client plugin: ssl handshake failure missing tls version"
    assert node.facts["trigger_change"] == "policy_change"
    assert node.facts["episode_count"] == 2


def test_signature_hydration_survives_missing_component():
    sig = SimpleNamespace(
        id=uuid.uuid4(),
        failing_component=None,
        affected_capability="email_delivery",
        failure_mode="disk_full",
        trigger_change=None,
        environment=None,
        scope=None,
        episode_count=1,
        created_at=None,
        updated_at=None,
    )
    node = hydrate_node("issue_signature", sig)
    assert node.label == "email delivery: disk full"
