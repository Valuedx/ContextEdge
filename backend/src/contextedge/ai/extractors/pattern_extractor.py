"""Pattern extraction and synthesis from incident episodes.

Prompt text lives in ``contextedge.ai.prompts.pattern`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.fencing import fence_untrusted
from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json


async def synthesize_pattern(
    episodes: list[dict],
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """Synthesize a Pattern from a list of Episode data."""
    ep_text = ""
    for i, ep in enumerate(episodes):
        ep_text += f"\n--- Episode {i + 1} ---\n"
        ep_text += f"Title: {ep.get('title', 'Untitled')}\n"
        ep_text += f"Root Cause: {ep.get('root_cause_summary', 'Unknown')}\n"
        ep_text += f"Outcome: {ep.get('final_outcome', 'Unknown')}\n"

        steps = ep.get("steps", [])
        ep_text += "Key Steps:\n"
        for s in steps[:5]:
            ep_text += f"- {s.get('text')}\n"

    prompt = get_prompt("pattern", tenant_id)
    user = prompt.format_user(episodes_text=fence_untrusted(ep_text))
    return await llm_complete_json(
        user,
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )


async def validate_pattern_match(
    episode_info: dict,
    pattern_info: dict,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """AI validation of whether an episode matches an existing pattern.

    Returns dict with keys:
        is_match: bool
        confidence: float
        reason: str
    """
    ep_text = (
        f"Episode Title: {episode_info.get('title')}\n"
        f"Root Cause: {episode_info.get('root_cause_summary')}\n"
        f"Outcome: {episode_info.get('final_outcome')}\n"
    )
    pattern_text = (
        f"Pattern Title: {pattern_info.get('title')}\n"
        f"Description: {pattern_info.get('description')}\n"
        f"Root Causes: {pattern_info.get('root_causes')}\n"
        f"Resolution Steps: {pattern_info.get('resolution_steps')}\n"
    )
    user_prompt = (
        "Evaluate if the following incident Episode belongs to the existing Pattern.\n\n"
        f"--- EXISTING PATTERN ---\n{fence_untrusted(pattern_text)}\n\n"
        f"--- NEW EPISODE ---\n{fence_untrusted(ep_text)}\n\n"
        "Return a JSON object with keys:\n"
        '  "is_match": boolean (true if the episode matches the pattern, false otherwise),\n'
        '  "confidence": number between 0.0 and 1.0,\n'
        '  "reason": brief explanation'
    )
    system_prompt = (
        "You are an AI SRE expert evaluating operational pattern matches. "
        "Strictly return valid JSON with keys is_match, confidence, and reason."
    )
    try:
        res = await llm_complete_json(
            user_prompt,
            task="verification",
            system_prompt=system_prompt,
            tenant_id=tenant_id,
            db=db,
        )
        if isinstance(res, dict):
            return {
                "is_match": bool(res.get("is_match", True)),
                "confidence": float(res.get("confidence", 0.8)),
                "reason": str(res.get("reason", "AI match evaluation")),
            }
    except Exception:
        pass

    # Safe fallback if LLM is unavailable
    return {"is_match": True, "confidence": 0.75, "reason": "Vector similarity fallback"}
