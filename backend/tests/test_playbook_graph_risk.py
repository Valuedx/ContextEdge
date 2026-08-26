"""Playbook nodes: risk-tier visibility uses RISK_RANK, not the old 4-tier map."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from contextedge.graph.agent.contracts import AgentGraphAccessScope
from contextedge.graph.agent.hydrators import node_is_visible


def _scope(tenant_id, cap="high"):
    return AgentGraphAccessScope(
        tenant_id=tenant_id,
        principal_id=uuid4(),
        principal_type="user",
        playbook_risk_cap=cap,
    )


def _playbook(tenant_id, *, risk_tier="medium", expired=False):
    return SimpleNamespace(
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        lifecycle_state="approved",
        current_version_id=uuid4(),
        expiry_at=(datetime.now(UTC) - timedelta(days=1)) if expired else None,
        risk_tier=risk_tier,
    )


def test_minimal_and_critical_are_visible_when_uncapped():
    tenant_id = uuid4()
    scope = _scope(tenant_id, cap=None)
    assert node_is_visible("playbook", _playbook(tenant_id, risk_tier="minimal"), scope, set())
    assert node_is_visible("playbook", _playbook(tenant_id, risk_tier="critical"), scope, set())
    assert node_is_visible("playbook", _playbook(tenant_id, risk_tier="restricted"), scope, set())


def test_critical_is_hidden_under_high_cap():
    """knowledge_manager / service accounts cap at high — critical stays out."""
    tenant_id = uuid4()
    scope = _scope(tenant_id, cap="high")
    assert node_is_visible("playbook", _playbook(tenant_id, risk_tier="high"), scope, set())
    assert not node_is_visible(
        "playbook", _playbook(tenant_id, risk_tier="critical"), scope, set()
    )


def test_restricted_is_hidden_under_high_cap():
    tenant_id = uuid4()
    scope = _scope(tenant_id, cap="high")
    assert not node_is_visible(
        "playbook", _playbook(tenant_id, risk_tier="restricted"), scope, set()
    )


def test_expired_playbook_stays_invisible():
    tenant_id = uuid4()
    assert not node_is_visible(
        "playbook",
        _playbook(tenant_id, expired=True),
        _scope(tenant_id, cap=None),
        set(),
    )


def test_approved_playbook_without_current_version_pointer_is_visible():
    tenant_id = uuid4()
    playbook = _playbook(tenant_id)
    playbook.current_version_id = None
    assert node_is_visible("playbook", playbook, _scope(tenant_id, cap=None), set())
