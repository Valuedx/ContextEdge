"""Decision/action extraction from evidence content.

Prompt text lives in ``contextedge.ai.prompts.decision`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.fencing import fence_untrusted
from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json
from contextedge.ai.text_salience import salient_slice


async def extract_decisions(
    content: str,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[dict]:
    """Extract operational decisions and actions from evidence text."""
    if not content or len(content.strip()) < 10:
        return []

    prompt = get_prompt("decision", tenant_id)
    user = prompt.format_user(content=fence_untrusted(salient_slice(content, 4000)))
    result = await llm_complete_json(
        user,
        task="classification",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    return result.get("decisions", [])
