"""Relevance classifier for evidence items.

Lightweight first-pass classification to gate expensive downstream processing.
"""

from contextedge.ai.provider import llm_complete_json

RELEVANCE_PROMPT = """Classify the following operational evidence item for relevance to IT operations troubleshooting.

Evidence:
Title: {title}
Content: {body}
Source Type: {source_type}
Evidence Type: {evidence_type}

Classify into ONE of:
- "operational": Directly relevant to operational troubleshooting, incident response, or remediation
- "possibly_relevant": May contain useful operational context but not primary troubleshooting content
- "not_relevant": Social, administrative, off-topic, or non-operational content

Respond in JSON with keys: "classification", "confidence" (0-1), "reasoning" (one sentence).
"""


async def classify_relevance(
    title: str,
    body: str,
    source_type: str,
    evidence_type: str,
) -> dict:
    """Returns dict with classification, confidence, reasoning."""
    prompt = RELEVANCE_PROMPT.format(
        title=title or "",
        body=(body or "")[:2000],
        source_type=source_type,
        evidence_type=evidence_type,
    )
    result = await llm_complete_json(prompt, task="classification")
    return {
        "classification": result.get("classification", "not_relevant"),
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("reasoning", ""),
    }
