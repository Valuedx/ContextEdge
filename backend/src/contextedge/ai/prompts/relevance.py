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
)

# v2 (roadmap A2): identical classification contract plus a compact
# operational summary, produced by the call that already reads every
# body — one field for ~50 extra output tokens instead of a second LLM
# pass. The summary lands in ``evidence_items.body_summary`` and from
# there into the maf.v1 evidence projection, replacing "no summary" for
# connector-ingested evidence. The classification instructions are
# byte-identical to v1 so label behaviour carries over, and the system
# block stays constant per version for provider prefix caching.
_V2_SYSTEM = _V1_SYSTEM.replace(
    """Respond ONLY with a JSON object matching this exact schema:
{
  "classification": "operational" | "possibly_relevant" | "not_relevant",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one-sentence justification>"
}""",
    """Respond ONLY with a JSON object matching this exact schema:
{
  "classification": "operational" | "possibly_relevant" | "not_relevant",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one-sentence justification>",
  "summary": "<operational summary, max 300 characters, or null>"
}

The summary is for a support engineer scanning a list: state the symptom, the affected component, and the action/outcome if present ("Agent fails to start: NullPointerException during registration; resolved by re-uploading web drivers via SysAdmin"). Use only facts from the content — never invent. For "not_relevant" items return null.""",
)

register_prompt(
    Prompt(
        name="relevance",
        version="v2",
        system=_V2_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
