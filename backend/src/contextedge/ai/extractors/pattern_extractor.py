"""Pattern extraction and synthesis from incident episodes."""

from contextedge.ai.provider import llm_complete_json

PATTERN_PROMPT = """You are an expert site reliability engineer. You are analyzing one or more troubleshooting episodes to identify a recurring Operational Pattern.

Incident Episodes:
{episodes_text}

Analyze these incidents and synthesize a high-level "Knowledge Pattern".
A pattern represents a category of issues that can be solved with a common playbook.

Extract:
1. title: A concise, professional name for the recurring pattern (e.g., "Post-Deployment Application Crash Due to Dependency Mismatch")
2. description: A clear explanation of what this pattern represents and its typical root causes.
3. trigger_conditions: A list of common observations or states that trigger this issue (e.g., "Application crash immediately after deployment").
4. core_entities: Specific components, services, or patches involved (e.g., ["billing-service", "patch-v2.3.1"]).
5. observed_errors: Specific error strings or log messages (e.g., ["NullPointerException"]).
6. root_causes: The underlying technical reason for the failure (e.g., ["Dependency mismatch in deployed patch"]).
7. resolution_steps: A sequence of logical steps to resolve the issue.
8. evidence_summary: A count of supporting evidence types (e.g., {{"tickets": 1, "logs": 2, "slack_threads": 1, "emails": 1}}).
9. pattern_type: Usually "recurring_issue" or "configuration_drift"
10. confidence: 0.0-1.0 how confident you are that these episodes form a coherent pattern.

Respond in JSON:
{{
  "title": "...",
  "description": "...",
  "trigger_conditions": ["..."],
  "core_entities": ["..."],
  "observed_errors": ["..."],
  "root_causes": ["..."],
  "resolution_steps": ["..."],
  "evidence_summary": {{
    "tickets": 0,
    "logs": 0,
    "slack_threads": 0,
    "emails": 0
  }},
  "pattern_type": "...",
  "confidence": 0.0-1.0
}}
"""

async def synthesize_pattern(episodes: list[dict]) -> dict:
    """Synthesize a Pattern from a list of Episode data."""
    ep_text = ""
    for i, ep in enumerate(episodes):
        ep_text += f"\n--- Episode {i + 1} ---\n"
        ep_text += f"Title: {ep.get('title', 'Untitled')}\n"
        ep_text += f"Root Cause: {ep.get('root_cause_summary', 'Unknown')}\n"
        ep_text += f"Outcome: {ep.get('final_outcome', 'Unknown')}\n"
        
        steps = ep.get("steps", [])
        ep_text += "Key Steps:\n"
        for s in steps[:5]:
            ep_text += f"- {s.get('text')}\n"

    prompt = PATTERN_PROMPT.format(episodes_text=ep_text)
    return await llm_complete_json(prompt, task="extraction")
