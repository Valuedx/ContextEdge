"""Unified playbook risk vocabulary (N2 / N11)."""

from types import SimpleNamespace
from uuid import uuid4

from contextedge.search.risk_policy import (
    PLAYBOOK_RISK_TIERS,
    RISK_RANK,
    effective_max_risk_tier,
    playbook_risk_rank,
    risk_within_cap,
)


class _User:
    def __init__(self, roles=(), principal_type="user"):
        self.roles = tuple(roles)
        self.principal_type = principal_type

    def has_role(self, role: str) -> bool:
        return role in self.roles


def test_restricted_ranks_above_critical():
    assert playbook_risk_rank("restricted") > playbook_risk_rank("critical")
    assert playbook_risk_rank("minimal") < playbook_risk_rank("low")
    assert set(RISK_RANK) == set(PLAYBOOK_RISK_TIERS)


def test_unknown_tier_defaults_to_medium_not_exclude():
    assert playbook_risk_rank("not-a-tier") == playbook_risk_rank("medium")


def test_critical_and_minimal_are_within_uncapped_admin():
    assert risk_within_cap("critical", None) is True
    assert risk_within_cap("minimal", None) is True
    assert risk_within_cap("restricted", None) is True


def test_critical_is_hidden_by_high_cap_visible_when_uncapped():
    assert risk_within_cap("critical", "high") is False
    assert risk_within_cap("critical", None) is True
    assert risk_within_cap("high", "high") is True


def test_admins_are_uncapped_matching_runtime_match():
    for role in ("platform_super_admin", "tenant_admin", "domain_admin"):
        assert effective_max_risk_tier(_User(roles=(role,))) is None


def test_knowledge_manager_and_service_account_cap_at_high():
    assert effective_max_risk_tier(_User(roles=("knowledge_manager",))) == "high"
    assert effective_max_risk_tier(_User(principal_type="service_account")) == "high"


def test_analyst_caps_at_medium():
    assert effective_max_risk_tier(_User(roles=("analyst",))) == "medium"
    assert effective_max_risk_tier(_User()) == "medium"


def test_graph_and_runtime_share_the_same_function():
    from contextedge.api.v1.runtime import _effective_max_risk_tier
    from contextedge.graph.agent.service import _risk_cap

    user = SimpleNamespace(
        principal_type="user",
        has_role=lambda role: role == "tenant_admin",
        tenant_id=uuid4(),
        user_id=uuid4(),
        roles=("tenant_admin",),
        workspace_ids=[],
        allowed_domain_ids=None,
    )
    assert _risk_cap(user) is None
    assert _effective_max_risk_tier(user) is None
