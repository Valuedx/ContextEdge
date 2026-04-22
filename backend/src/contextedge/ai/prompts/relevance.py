"""Registered versions of the evidence-relevance classifier prompt.

``v1`` is the text that shipped inline in
``contextedge.ai.classifiers.relevance`` before this registry existed.
It is the current default. A future ``v2`` can register here and be
flipped on per-tenant via ``tenant_prompt_variants_json`` before being
promoted to default. Treat each version as immutable: edit = ship a
new version, never mutate a released one, so historical
``llm.usage`` events stay accurate.
"""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You classify operational-evidence items for an IT operations memory platform.

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

_V1_USER = """Classify this evidence item:

Title: {title}
Source Type: {source_type}
Evidence Type: {evidence_type}
Content:
{body}"""


register_prompt(
    Prompt(
        name="relevance",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
