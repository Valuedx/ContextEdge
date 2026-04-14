"""Decision/action extraction from evidence content."""

from contextedge.ai.provider import llm_complete_json

DECISION_PROMPT = """Extract operational decisions and actions from the following evidence content.
A decision or action is anything a person explicitly did or chose to do — there is no
fixed category list. Use a short, descriptive label that fits the action.

Content:
{content}

Common examples of decision_type labels (use these when they fit, but invent a
more accurate label whenever none of these match):
  approval, denial, remediation, escalation, configuration_change, restart,
  access_grant, access_revoke, rollback, deployment, migration, workaround,
  investigation, scheduling, delegation, policy_change, communication, purchase,
  maintenance, decommission

Respond in JSON with key "decisions" containing a list of objects:
{{"decisions": [{{"decision_type": "short_label_for_the_action", "actor": "person who decided or acted", "target": "system, service, or resource acted upon", "action": "concise description of what was done", "context": "brief surrounding context"}}]}}

Only extract clearly stated decisions or actions. Do not fabricate.
If no decisions or actions are found, return {{"decisions": []}}.
"""


async def extract_decisions(content: str) -> list[dict]:
    """Extract operational decisions and actions from evidence text."""
    if not content or len(content.strip()) < 10:
        return []

    prompt = DECISION_PROMPT.format(content=content[:4000])
    result = await llm_complete_json(prompt, task="classification")
    if isinstance(result, list):
        return result
    return result.get("decisions", [])
