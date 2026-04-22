"""Playbook candidate generation from pattern clusters.

Prompt text lives in ``contextedge.ai.prompts.playbook`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json


async def generate_playbook_candidate(
    pattern_title: str,
    pattern_description: str,
    episode_count: int,
    episode_summaries: list[dict],
    negative_knowledge: list[str],
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """Generate a playbook candidate from pattern analysis."""
    summaries_text = ""
    for i, ep in enumerate(episode_summaries[:10]):
        summaries_text += f"\n{i + 1}. {ep.get('title', 'Untitled')}"
        if ep.get("root_cause"):
            summaries_text += f"\n   Root cause: {ep['root_cause']}"
        if ep.get("outcome"):
            summaries_text += f"\n   Outcome: {ep['outcome']}"

    neg_text = (
        "\n".join(f"- {nk}" for nk in negative_knowledge[:20])
        if negative_knowledge
        else "None identified"
    )

    prompt = get_prompt("playbook", tenant_id)
    user = prompt.format_user(
        pattern_title=pattern_title,
        pattern_description=pattern_description or "",
        episode_count=episode_count,
        episode_summaries=summaries_text,
        negative_knowledge=neg_text,
    )
    return await llm_complete_json(
        user,
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
