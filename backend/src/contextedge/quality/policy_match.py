"""Policy rule matching for validators (no DB dependency)."""

from __future__ import annotations

from typing import Any

from contextedge.quality.claim_match import overlap_ratio


def rule_matches_action(rule: dict[str, Any], action_text: str) -> bool:
    needle = (rule.get("normalized_action") or "").strip()
    if not needle or not action_text.strip():
        return False
    return overlap_ratio(needle, action_text) >= 0.55
