"""Episode reconstruction from correlated evidence."""

import re
import structlog
from contextedge.ai.provider import llm_complete_json

logger = structlog.get_logger()

EPISODE_PROMPT = """You are an expert IT Operations Analyst. Your mission is to reconstruct high-fidelity, structured troubleshooting "episodes" from a sequence of evidence items (mostly emails).

### MISSION OBJECTIVE (CRITICAL)
- DO NOT SUMMARIZE. The user needs to see the EXACT sequence of events.
- EVERY evidence item provided MUST correspond to AT LEAST one step in the final episode.
- If there are 5 emails, I expect 5 or more steps in the output.
- COLLAPSING multiple emails into one step is a FAILURE and will result in data loss.

### Grouping Strategy:
1. A SINGLE EPISODE should encompass the entire lifecycle of an incident.
2. If multiple evidence items share the same conversation thread (subject/thread_id), they MUST be in the same episode.
3. Separate unrelated incidents (e.g., SSO login vs Password reset) into different episodes.

### Step-by-Step Extraction:
- THREAD PROGRESSION: Emails are ordered by time. Maintain this order exactly.
- NO CONFLATION: Even if two emails are short (e.g., "Thanks" or "Checking"), they represent distinct heartbeat events in the incident and MUST be their own steps.
- ATOMIZE: Every question asked, every diagnostic run, and every outcome reported is its own step.

### Reasoning & Analysis Requirement:
In the "analysis" field of the JSON output, you MUST provide a mapping like this:
"Evidence 1 (ID: uuid) -> Step 1 (Complaint). Evidence 2 (ID: uuid) -> Step 2 (Diagnostic). ..."
This forces you to account for every item.

Evidence items (ordered by time):
{evidence_text}

### Output Format:
Respond ONLY in JSON matching this structure:
{{
  "analysis": "A detailed item-by-id mapping string as described above.",
  "episodes": [
    {{
      "title": "Clear, descriptive incident title",
      "root_cause_summary": "...",
      "final_outcome": "...",
      "overall_confidence": 0.0-1.0,
      "steps": [
        {{
          "step_order": 1,
          "step_type": "complaint|diagnostic|hypothesis|action|observation|remediation|outcome",
          "text": "Detailed, factual description of exactly what happened in THIS specific interaction.",
          "observation": "Direct results or logs seen during this step.",
          "result_state": "success|failure|unknown",
          "failed_flag": false,
          "successful_flag": false,
          "extraction_confidence": 0.0-1.0,
          "evidence_refs": ["uuid-of-the-evidence-item"]
        }}
      ]
    }}
  ]
}}
"""


def _clean_email_body(text: str) -> str:
    """Remove redundant quoted text and common email signatures to focus the AI."""
    if not text:
        return ""
    # Remove lines starting with >
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        if line.strip().startswith(">"):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


async def reconstruct_episode(evidence_items: list[dict]) -> list[dict]:
    """Reconstruct structured episodes from evidence items.

    Args:
        evidence_items: list of dicts with keys: title, body, source_type, timestamp, evidence_id

    Returns:
        List of structured episode dicts.
    """
    evidence_text = ""
    for i, item in enumerate(evidence_items):
        evidence_text += f"\n<evidence_item id=\"{item.get('evidence_id', 'unknown')}\">\n"
        evidence_text += f"Source: {item.get('source_type', 'unknown')}\n"
        if item.get("timestamp"):
            evidence_text += f"Time: {item['timestamp']}\n"
        if item.get("title"):
            evidence_text += f"Title: {item['title']}\n"
        if item.get("thread_id"):
            evidence_text += f"Thread Correlation ID: {item['thread_id']}\n"
        
        # Clean body to remove excessive noise if needed, but preserve content
        raw_body = (item.get('body', '') or '').strip()
        body = _clean_email_body(raw_body) or raw_body[:1000] # Fallback to raw if cleaning is too aggressive
        evidence_text += f"Content:\n{body[:8000]}\n"
        evidence_text += "</evidence_item>\n"

    prompt = EPISODE_PROMPT.format(evidence_text=evidence_text)
    
    logger.info(
        "episode_extraction.calling_llm",
        evidence_count=len(evidence_items),
        total_chars=len(evidence_text)
    )
    
    result = await llm_complete_json(prompt, task="extraction")
    
    if isinstance(result, list):
        episodes = result
    else:
        episodes = result.get("episodes", [])
        if not episodes and "title" in result:
            # Fallback for old single-episode format if LLM ignores the new list instruction
            episodes = [result]

    total_steps = 0
    for ep in episodes:
        steps = ep.get("steps", [])
        total_steps += len(steps)
        for step in steps:
            step.setdefault("failed_flag", step.get("result_state") == "failure")
            step.setdefault("successful_flag", step.get("result_state") == "success")
            step.setdefault("confidence", 0.5)

    logger.info(
        "episode_extraction.completed",
        episode_count=len(episodes),
        total_steps=total_steps
    )

    return episodes
