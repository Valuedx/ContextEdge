"""Message-function classifier for conversational evidence (backlog A1).

One call per conversational evidence item, run inline in the normalize
worker AFTER the relevance gate (noise never spends a second LLM call).
The label is persisted on the evidence row and consumed
deterministically downstream â€” dissociation veto, correction
supersession, negative-evidence store. The classifier proposes;
deterministic policy disposes.

Prompt text lives in ``contextedge.ai.prompts.message_function``
(registry-versioned, A/B-routable per tenant, prefix-cache friendly).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
from contextedge.ai.prompts.message_function import MESSAGE_FUNCTIONS
from contextedge.ai.provider import llm_complete_json
from contextedge.ai.text_salience import salient_slice


async def classify_message_function(
    title: str,
    body: str,
    source_type: str,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    evidence_id: _uuid.UUID | str | None = None,
) -> dict:
    """Returns ``{function, confidence}``. An out-of-vocabulary label
    from the model degrades to ``unclassified`` â€” consumers treat that
    exactly like "no classifier available" and fall back to their
    deterministic floors."""
    prompt = get_prompt("message_function", tenant_id)
    user_prompt = prompt.format_user(
        title=title or "",
        source_type=source_type,
        body=salient_slice(body or "", 2000),
    )
    result = await llm_complete_json(
        user_prompt,
        task="classification",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        # F5: this call is ABOUT an evidence row that already exists, so the
        # usage event anchors to it — "what did classifying this message
        # cost?" stops needing a correlation-id join.
        subject_type="evidence_item" if evidence_id else None,
        subject_id=evidence_id,
    )
    function = result.get("function")
    if function not in MESSAGE_FUNCTIONS:
        function = "unclassified"
    try:
        confidence = min(max(float(result.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {"function": function, "confidence": confidence}
