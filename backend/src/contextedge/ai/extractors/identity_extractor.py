"""Entity/identity extraction from evidence content.

Prompt text lives in ``contextedge.ai.prompts.identity`` (registry-
versioned, A/B-routable per tenant). Resolved via ``get_prompt`` on
each call so a tenant with an active variant routes to its version
automatically.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.fencing import fence_untrusted
from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json


async def extract_identities(
    content: str,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[dict]:
    """Extract operational entities from evidence text.

    Pass ``tenant_id`` + ``db`` when available so the call is
    instrumented into the admin cost dashboard and the resolved
    ``prompt_version`` lands in the ``llm.usage`` event.
    """
    if not content or len(content.strip()) < 10:
        return []

    prompt = get_prompt("identity", tenant_id)
    user = prompt.format_user(content=fence_untrusted(content[:4000]))
    result = await llm_complete_json(
        user,
        task="classification",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if not isinstance(result, dict):
        return []
    return result.get("entities", [])
