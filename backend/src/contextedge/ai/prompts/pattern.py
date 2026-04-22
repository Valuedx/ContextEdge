"""Registered versions of the pattern-synthesis prompt."""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You are an expert site reliability engineer. You analyze one or more troubleshooting episodes to identify a recurring Operational Pattern.

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
}}"""

_V1_USER = """Incident Episodes:
{episodes_text}"""


register_prompt(
    Prompt(
        name="pattern",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
