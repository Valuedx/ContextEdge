"""Relevance classifier for evidence items.

Lightweight first-pass classification to gate expensive downstream
processing (embedding, identity/decision extraction). Runs inline in the
normalize worker before embedding, so items the classifier marks
``not_relevant`` with high confidence skip the costly fan-out entirely.

Prompt text lives in ``contextedge.ai.prompts.relevance`` so it can be
versioned and A/B-tested per tenant (W10-12.2). The system block is
identical across all calls for a given version, so OpenAI's automatic
prefix cache and Anthropic's ``cache_control: {"type": "ephemeral"}``
header both kick in and the instruction tokens hit the cache at
10-25% of normal cost after the first warm-up call per worker.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json
from contextedge.ai.text_salience import salient_slice

# Re-exported for legacy importers. New code should go through
# ``get_prompt("relevance", tenant_id)`` so per-tenant variants route
# correctly.
SYSTEM_PROMPT = get_prompt("relevance").system
USER_PROMPT_TEMPLATE = get_prompt("relevance").user_template


async def classify_relevance(
    title: str,
    body: str,
    source_type: str,
    evidence_type: str,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """Returns ``{classification, confidence, reasoning}``.

    When ``tenant_id`` is passed the call is instrumented (Prometheus
    counters, structured log, operational event). Callers running inside
    a DB-backed worker should pass both ``tenant_id`` and ``db`` so the
    admin cost dashboard captures per-tenant spend and the resolved
    ``prompt_version`` lands in the ``llm.usage`` event for per-version
    quality / cost analysis.
    """
    prompt = get_prompt("relevance", tenant_id)
    user_prompt = prompt.format_user(
        title=title or "",
        source_type=source_type,
        evidence_type=evidence_type,
        # Salience-aware, not head-first: a fused thread's first 2,000
        # chars are the newest reply's greetings, and classifying those
        # once discarded a complete resolution (roadmap F4).
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
    )
    summary = result.get("summary")
    return {
        "classification": result.get("classification", "not_relevant"),
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("reasoning", ""),
        # v2+ prompts return an operational summary; v1 (and models that
        # drop the field) yield None. Non-string junk degrades to None.
        "summary": summary.strip()[:300] if isinstance(summary, str) and summary.strip() else None,
        # v3+ claims — strict-structure, lenient-vocabulary: unknown
        # types and malformed entries drop silently, never crash the
        # gate call.
        "claims": _parse_claims(result.get("claims")),
    }


_CLAIM_TYPES = frozenset(
    {"symptom", "probable_root_cause", "recommended_action", "failed_step", "user_impact"}
)


def _parse_claims(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    claims: list[dict] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        claim_type = item.get("type")
        if not isinstance(text, str) or not text.strip():
            continue
        if claim_type not in _CLAIM_TYPES:
            continue
        try:
            confidence = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5
        claims.append(
            {
                "type": claim_type,
                "text": " ".join(text.split())[:300],
                "confidence": confidence,
            }
        )
    return claims
