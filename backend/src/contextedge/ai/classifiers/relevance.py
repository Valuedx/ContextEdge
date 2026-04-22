"""Relevance classifier for evidence items.

Lightweight first-pass classification to gate expensive downstream processing.
"""

from contextedge.ai.provider import llm_complete_json

RELEVANCE_PROMPT = """You are an enterprise IT operations classifier.

Your task is to analyze an email thread and determine whether it contains operationally relevant incident or troubleshooting information.

Classify the email into one of the following categories:
1. OPERATIONAL_INCIDENT (login issues, outages, errors, failures, tickets)
2. POSSIBLY_RELEVANT (partial signals, unclear issue, needs more context)
3. NOT_RELEVANT (HR emails, newsletters, approvals, casual communication)

Strict rules:
- Focus ONLY on operational IT issues (SSO, VPN, application errors, outages, alerts, infra issues)
- Ignore greetings, signatures, and noise
- Detect intent (complaint, escalation, troubleshooting)

Also extract:
- issue_type
- affected_system
- user_impact
- urgency_level (low, medium, high, critical)

Evidence Title: {title}
Source Type: {source_type}
Content (Thread snippet):
{body}

Return JSON only:
{{
"classification": "",
"confidence": 0-1,
"issue_type": "",
"affected_system": "",
"user_impact": "",
"urgency_level": ""
}}
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
        "classification": result.get("classification", "not_relevant").upper(),
        "confidence": float(result.get("confidence", 0.5)),
        "issue_type": result.get("issue_type"),
        "affected_system": result.get("affected_system"),
        "user_impact": result.get("user_impact"),
        "urgency_level": result.get("urgency_level"),
    }
