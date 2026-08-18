"""Episode-review classifier: the AI first-pass over pending drafts.

The classifier proposes; deterministic policy disposes (the
message-function convention). Every malformed shape — unknown verdict,
missing keys, non-finite or boolean confidence, provider failure — fails
CLOSED to ``hold`` with confidence 0.0, because a wrong hold costs one
human review that was going to happen anyway, while a wrong approve
feeds patterns and playbooks.

Prompt text lives in ``contextedge.ai.prompts.episode_review``
(registry-versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import math
import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
from contextedge.ai.prompts.episode_review import REVIEW_VERDICTS
from contextedge.ai.provider import llm_complete_json

HELD = {"verdict": "hold", "confidence": 0.0, "reasons": ["reviewer_unavailable"]}


def _parse(result: Any) -> dict:
    """Strict structure, lenient vocabulary — and fail-closed throughout."""
    if not isinstance(result, dict):
        return dict(HELD)
    verdict = result.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        return {"verdict": "hold", "confidence": 0.0, "reasons": ["invalid_verdict"]}
    raw_confidence = result.get("confidence")
    if isinstance(raw_confidence, bool):
        confidence = 0.0
    else:
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)
    reasons = [
        str(reason)[:200]
        for reason in (result.get("reasons") or [])
        if isinstance(reason, str | int | float)
    ][:3]
    return {"verdict": verdict, "confidence": confidence, "reasons": reasons}


async def review_episode_llm(
    *,
    title: str,
    root_cause: str | None,
    final_outcome: str | None,
    steps_text: str,
    contradictions_text: str,
    evidence_text: str,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    episode_id: _uuid.UUID | str | None = None,
) -> dict:
    """Returns ``{verdict, confidence, reasons, prompt_version}``."""
    prompt = get_prompt("episode_review", tenant_id)
    user_prompt = prompt.format_user(
        title=title or "",
        root_cause=(root_cause or "(none)")[:1500],
        final_outcome=(final_outcome or "(none)")[:1500],
        steps=steps_text or "(none)",
        contradictions=contradictions_text or "(none)",
        evidence=evidence_text or "(none)",
    )
    try:
        result = await llm_complete_json(
            user_prompt,
            task="classification",
            system_prompt=prompt.system,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            subject_type="episode" if episode_id else None,
            subject_id=episode_id,
        )
    except Exception:
        # Provider failure holds the draft for THIS sweep but must stay
        # retryable: the caller sees transient_failure and persists
        # nothing, so the next sweep picks the draft up again. Stamping
        # the outage onto the row turned a one-hour provider blip into
        # a permanent "never AI-reviewed" for a whole batch.
        parsed = dict(HELD)
        parsed["transient_failure"] = True
    else:
        parsed = _parse(result)
    parsed["prompt_version"] = prompt.version
    return parsed
