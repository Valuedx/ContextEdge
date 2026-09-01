"""Policy rule matching for validators (no DB dependency)."""

from __future__ import annotations

from typing import Any

from contextedge.quality.claim_match import overlap_ratio, tokens


def rule_matches_action(rule: dict[str, Any], action_text: str) -> bool:
    """Whether a policy rule's normalized action appears in step text.

    Short rules (few tokens) require every rule token to appear in the step —
    overlap ratio alone false-positives on partial matches like
    ``change``+``file`` without ``jar``.
    """
    needle = (rule.get("normalized_action") or "").strip()
    if not needle or not action_text.strip():
        return False

    rule_toks = tokens(needle)
    action_toks = tokens(action_text)
    if not rule_toks or not action_toks:
        return False

    if len(rule_toks) <= 4:
        return rule_toks <= action_toks

    return overlap_ratio(needle, action_text) >= 0.55
