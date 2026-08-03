"""Registered versions of the decision/action-extraction prompt."""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """Extract operational decisions and actions from the provided evidence content.
A decision or action is anything a person explicitly did or chose to do — there is no
fixed category list. Use a short, descriptive label that fits the action.

Common examples of decision_type labels (use these when they fit, but invent a
more accurate label whenever none of these match):
  approval, denial, remediation, escalation, configuration_change, restart,
  access_grant, access_revoke, rollback, deployment, migration, workaround,
  investigation, scheduling, delegation, policy_change, communication, purchase,
  maintenance, decommission

Respond in JSON with key "decisions" containing a list of objects:
{{"decisions": [{{"decision_type": "short_label_for_the_action", "actor": "person who decided or acted", "target": "system, service, or resource acted upon", "action": "concise description of what was done", "context": "brief surrounding context"}}]}}

Only extract clearly stated decisions or actions. Do not fabricate.
If no decisions or actions are found, return {{"decisions": []}}."""

_V1_USER = """Content:
{content}"""


register_prompt(
    Prompt(
        name="decision",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
)


# v2 (2026-08): fixes the doubled-brace bug — ``system`` is never
# .format()ed, so v1's {{ }} reached the model as literal double
# braces (malformed JSON example). Same text otherwise. New version,
# not an edit — v1 stays immutable for eval baselines and historical
# llm.usage accuracy.
_V2_SYSTEM = """Extract operational decisions and actions from the provided evidence content.
A decision or action is anything a person explicitly did or chose to do — there is no
fixed category list. Use a short, descriptive label that fits the action.

Common examples of decision_type labels (use these when they fit, but invent a
more accurate label whenever none of these match):
  approval, denial, remediation, escalation, configuration_change, restart,
  access_grant, access_revoke, rollback, deployment, migration, workaround,
  investigation, scheduling, delegation, policy_change, communication, purchase,
  maintenance, decommission

Respond in JSON with key "decisions" containing a list of objects:
{"decisions": [{"decision_type": "short_label_for_the_action", "actor": "person who decided or acted", "target": "system, service, or resource acted upon", "action": "concise description of what was done", "context": "brief surrounding context"}]}

Only extract clearly stated decisions or actions. Do not fabricate.
If no decisions or actions are found, return {"decisions": []}."""

_V2_USER = _V1_USER


register_prompt(
    Prompt(
        name="decision",
        version="v2",
        system=_V2_SYSTEM,
        user_template=_V2_USER,
    ),
    default=True,
)

