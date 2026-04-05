"""Episode reconstruction from correlated evidence."""

from contextedge.ai.provider import llm_complete_json

EPISODE_PROMPT = """You are analyzing operational evidence from an IT troubleshooting case. Reconstruct a structured episode from the following evidence items.

Evidence items (ordered by time):
{evidence_text}

Reconstruct the troubleshooting episode as a structured sequence. For each step, identify:
1. step_type: one of "complaint", "diagnostic", "hypothesis", "action", "observation", "failed_step", "remediation", "escalation", "outcome"
2. text: what happened in this step
3. observation: what was observed after this step (if applicable)
4. result_state: "success", "failure", "inconclusive", "unknown"
5. confidence: 0.0-1.0 how confident you are this step actually occurred based on evidence

Also extract:
- title: concise episode title
- root_cause_summary: root cause if identified (null if unclear)
- final_outcome: final resolution (null if unresolved)
- overall_confidence: 0.0-1.0 overall extraction confidence

Respond in JSON:
{{
  "title": "...",
  "root_cause_summary": "..." or null,
  "final_outcome": "..." or null,
  "overall_confidence": 0.0-1.0,
  "steps": [
    {{
      "step_order": 1,
      "step_type": "...",
      "text": "...",
      "observation": "..." or null,
      "result_state": "...",
      "failed_flag": false,
      "successful_flag": false,
      "confidence": 0.0-1.0
    }}
  ]
}}

Preserve failed steps and uncertainty. Do not collapse ambiguity into polished summaries.
"""


async def reconstruct_episode(evidence_items: list[dict]) -> dict:
    """Reconstruct a structured episode from evidence items.

    Args:
        evidence_items: list of dicts with keys: title, body, source_type, timestamp, evidence_id

    Returns:
        Structured episode dict with title, steps, root_cause, outcome, confidence
    """
    evidence_text = ""
    for i, item in enumerate(evidence_items):
        evidence_text += f"\n--- Evidence {i + 1} ---\n"
        evidence_text += f"Source: {item.get('source_type', 'unknown')}\n"
        if item.get("timestamp"):
            evidence_text += f"Time: {item['timestamp']}\n"
        if item.get("title"):
            evidence_text += f"Title: {item['title']}\n"
        evidence_text += f"Content: {(item.get('body', '') or '')[:2000]}\n"

    prompt = EPISODE_PROMPT.format(evidence_text=evidence_text)
    result = await llm_complete_json(prompt, task="extraction")

    for step in result.get("steps", []):
        step.setdefault("failed_flag", step.get("result_state") == "failure")
        step.setdefault("successful_flag", step.get("result_state") == "success")
        step.setdefault("confidence", 0.5)

    return result
