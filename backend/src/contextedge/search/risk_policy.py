"""Risk tier ordering for retrieval policy caps."""

RISK_RANK: dict[str, int] = {
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def playbook_risk_rank(tier: str | None) -> int:
    return RISK_RANK.get((tier or "medium").lower().strip(), 2)


def risk_within_cap(tier: str | None, max_tier: str | None) -> bool:
    """If max_tier is None, all tiers allowed; otherwise tier must be at or below the cap."""
    if max_tier is None:
        return True
    return playbook_risk_rank(tier) <= playbook_risk_rank(max_tier)
