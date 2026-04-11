"""Episode reconstruction from correlated evidence."""

from contextedge.ai.provider import llm_complete_json

EPISODE_PROMPT = """You are analyzing operational evidence from an IT troubleshooting case. 
Your goal is to reconstruct one or more structured episodes from the providing evidence.

Logic for Grouping:
1. Combine evidence into a SINGLE episode IF AND ONLY IF they share the same context or category (e.g., cascading service failures, shared infrastructure logs, a shared root cause).
2. Separate evidence into MULTIPLE episodes if they represent distinct, unrelated operational problems (e.g., a "Billing Service crash" is distinct from a "VPN authentication failure" unless they are explicitly linked by a shared root cause).

Evidence items (ordered by time):
{evidence_text}

For each episode, identify a structured sequence of steps:
1. step_type: one of "complaint", "diagnostic", "hypothesis", "action", "observation", "failed_step", "remediation", "escalation", "outcome"
2. text: what happened in this step
3. observation: what was observed after this step (if applicable)
4. result_state: "success", "failure", "inconclusive", "unknown"
   NOTE: "failure" should ONLY be used when a troubleshooting ACTION or ATTEMPTED DIAGNOSTIC failed to execute (e.g., "Tried to ping but timed out"). 
   Do NOT mark complaints or diagnostics as "failure" just because they report a system crash. Reporting a crash is a "success".
5. confidence: 0.0-1.0 how confident you are this step actually occurred based on evidence

For each episode also extract:
- title: concise episode title
- root_cause_summary: root cause if identified (null if unclear)
- final_outcome: final resolution (null if unresolved)
- overall_confidence: 0.0-1.0 overall extraction confidence

Respond in JSON with a list of episodes:
{{
  "episodes": [
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
  ]
}}

Preserve failed steps and uncertainty. Do not collapse ambiguity into polished summaries.
"""


async def reconstruct_episode(evidence_items: list[dict]) -> list[dict]:
    """Reconstruct structured episodes from evidence items.

    Args:
        evidence_items: list of dicts with keys: title, body, source_type, timestamp, evidence_id

    Returns:
        List of structured episode dicts.
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

    episodes = result.get("episodes", [])
    if not episodes and "title" in result:
        # Fallback for old single-episode format if LLM ignores the new list instruction
        episodes = [result]

    for ep in episodes:
        for step in ep.get("steps", []):
            step.setdefault("failed_flag", step.get("result_state") == "failure")
            step.setdefault("successful_flag", step.get("result_state") == "success")
            step.setdefault("confidence", 0.5)

    return episodes
