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
