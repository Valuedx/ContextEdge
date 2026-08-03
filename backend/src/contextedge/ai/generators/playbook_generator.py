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
    knowledge_sources: list[Any] | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """Generate a playbook candidate from pattern analysis.

    ``knowledge_sources`` are the approved KB/SOP documents retrieved for
    this pattern. They are passed as a *distinct* input rather than
    folded into the episode summaries, because the prompt has to treat
    them differently: knowledge is normative (what should be done) and
    episodes are empirical (what was done). Merging them would erase the
    distinction the reviewer needs to adjudicate a disagreement.

    Episodes are labelled ``[ep-N]`` so steps can cite them the same way
    they cite ``[kb-N]`` knowledge sections.
    """
    summaries_text = ""
    for i, ep in enumerate(episode_summaries[:10]):
        summaries_text += f"\n[ep-{i + 1}] {ep.get('title', 'Untitled')}"
        if ep.get("root_cause"):
            summaries_text += f"\n   Root cause: {ep['root_cause']}"
        if ep.get("outcome"):
            summaries_text += f"\n   Outcome: {ep['outcome']}"

    neg_text = (
        "\n".join(f"- {nk}" for nk in negative_knowledge[:20])
        if negative_knowledge
        else "None identified"
    )

    from contextedge.services.knowledge_retrieval_service import (
        format_knowledge_block,
    )

    knowledge_text = format_knowledge_block(knowledge_sources or [])

    prompt = get_prompt("playbook", tenant_id)
    format_kwargs: dict[str, Any] = {
        "pattern_title": pattern_title,
        "pattern_description": pattern_description or "",
        "episode_count": episode_count,
        "episode_summaries": summaries_text,
        "negative_knowledge": neg_text,
    }
    # Older prompt versions have no knowledge slot; a tenant pinned to v1
    # or v2 via variant routing must keep working rather than raising on
    # an unexpected format key.
    if "{knowledge_sources}" in prompt.user_template:
        format_kwargs["knowledge_sources"] = knowledge_text

    user = prompt.format_user(**format_kwargs)
    return await llm_complete_json(
        user,
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
