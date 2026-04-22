"""Relevance classifier for evidence items.

Lightweight first-pass classification to gate expensive downstream
processing (embedding, identity/decision extraction). Runs inline in the
normalize worker before embedding, so items the classifier marks
``not_relevant`` with high confidence skip the costly fan-out entirely.

Prompt is split into a stable ``SYSTEM_PROMPT`` (instructions + schema)
and a dynamic user message (the evidence content). This lets OpenAI's
automatic prefix cache and Anthropic's ``cache_control: {"type": "ephemeral"}``
header both kick in — the system block is identical across all
classification calls, so after the first warm-up call per worker the
instruction tokens hit the cache at 10-25% of normal cost.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.provider import llm_complete_json

# Static — stays identical across all classification calls so it caches.
SYSTEM_PROMPT = """You classify operational-evidence items for an IT operations memory platform.

You receive a single evidence item and return a JSON classification.

Valid classifications:
- "operational": Directly relevant to operational troubleshooting, incident response, or remediation.
- "possibly_relevant": May contain useful operational context but is not primary troubleshooting content.
- "not_relevant": Social, administrative, off-topic, or non-operational content (marketing, calendar invites, newsletters, personal chat).

Respond ONLY with a JSON object matching this exact schema:
{
  "classification": "operational" | "possibly_relevant" | "not_relevant",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one-sentence justification>"
}

Be conservative: only mark "operational" when the content clearly describes an incident, error, failure, outage, or remediation. Ambiguous content goes to "possibly_relevant". Default to "not_relevant" when unsure."""


# Dynamic — one per call.
USER_PROMPT_TEMPLATE = """Classify this evidence item:

Title: {title}
Source Type: {source_type}
Evidence Type: {evidence_type}
Content:
{body}"""


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
    admin cost dashboard captures per-tenant spend.
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=title or "",
        source_type=source_type,
        evidence_type=evidence_type,
        body=(body or "")[:2000],
    )
    result = await llm_complete_json(
        user_prompt,
        task="classification",
        system_prompt=SYSTEM_PROMPT,
        tenant_id=tenant_id,
        db=db,
    )
    return {
        "classification": result.get("classification", "not_relevant"),
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("reasoning", ""),
    }
