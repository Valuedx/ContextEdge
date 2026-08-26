"""Risk tier ordering for retrieval policy caps.

One vocabulary for every surface that filters playbooks by risk. The graph
hydrator used to keep a private four-tier map (``low/medium/high/restricted``)
that silently dropped ``minimal`` and ``critical``; runtime matching used this
five-tier map. Both now share ``RISK_RANK``. ``restricted`` is kept as a sixth
tier above ``critical`` so existing rows with that label stay the most
capped rather than collapsing to the unknown-tier default (medium).
"""

from __future__ import annotations

from typing import Protocol

PLAYBOOK_RISK_TIERS: tuple[str, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "critical",
    "restricted",
)

RISK_RANK: dict[str, int] = {
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
    "restricted": 5,
}


class _RiskPrincipal(Protocol):
    principal_type: str

    def has_role(self, role: str) -> bool: ...


def playbook_risk_rank(tier: str | None) -> int:
    return RISK_RANK.get((tier or "medium").lower().strip(), 2)


def risk_within_cap(tier: str | None, max_tier: str | None) -> bool:
    """If max_tier is None, all tiers allowed; otherwise tier must be at or below the cap."""
    if max_tier is None:
        return True
    return playbook_risk_rank(tier) <= playbook_risk_rank(max_tier)


def effective_max_risk_tier(user: _RiskPrincipal) -> str | None:
    """Caller-role risk cap. ``None`` means uncapped (admins).

    Shared by ``/runtime/match`` and the agent graph projection so an admin
    cannot be recommended a ``critical`` playbook their graph context omits.
    """
    if (
        user.has_role("platform_super_admin")
        or user.has_role("tenant_admin")
        or user.has_role("domain_admin")
    ):
        return None
    if user.has_role("knowledge_manager") or user.principal_type == "service_account":
        return "high"
    return "medium"
