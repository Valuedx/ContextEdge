"""Registered versions of the playbook-candidate generation prompt."""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You are generating a living playbook for an operational troubleshooting pattern.

Generate a structured playbook candidate with:
1. Trigger conditions: when should this playbook be invoked
2. Steps with branching logic: diagnostic and remediation flow
3. Risk assessment
4. Rollback notes from negative knowledge
5. Confidence breakdown

Respond in JSON:
{{
  "title": "...",
  "description": "...",
  "risk_tier": "low" | "medium" | "high" | "critical",
  "trigger_conditions": {{
    "symptoms": ["..."],
    "entities": ["..."],
    "conditions": ["..."]
  }},
  "steps": [
    {{
      "order": 1,
      "type": "diagnostic" | "action" | "check" | "branch" | "escalation",
      "text": "...",
      "expected_outcome": "...",
      "on_failure": "...",
      "evidence_quality": "high" | "medium" | "low"
    }}
  ],
  "branching_logic": {{
    "decision_points": [
      {{
        "after_step": 1,
        "condition": "...",
        "if_true_goto": 2,
        "if_false_goto": 3
      }}
    ]
  }},
  "inputs": ["..."],
  "outputs": ["..."],
  "rollback_notes": "...",
  "playbook_confidence": 0.0-1.0,
  "execution_confidence_guidance": "..."
}}"""

_V1_USER = """Pattern Title: {pattern_title}
Pattern Description: {pattern_description}
Episode Count: {episode_count}

Episode Summaries:
{episode_summaries}

Negative Knowledge (steps that repeatedly fail):
{negative_knowledge}"""


register_prompt(
    Prompt(
        name="playbook",
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
_V2_SYSTEM = """You are generating a living playbook for an operational troubleshooting pattern.

Generate a structured playbook candidate with:
1. Trigger conditions: when should this playbook be invoked
2. Steps with branching logic: diagnostic and remediation flow
3. Risk assessment
4. Rollback notes from negative knowledge
5. Confidence breakdown

Respond in JSON:
{
  "title": "...",
  "description": "...",
  "risk_tier": "low" | "medium" | "high" | "critical",
  "trigger_conditions": {
    "symptoms": ["..."],
    "entities": ["..."],
    "conditions": ["..."]
  },
  "steps": [
    {
      "order": 1,
      "type": "diagnostic" | "action" | "check" | "branch" | "escalation",
      "text": "...",
      "expected_outcome": "...",
      "on_failure": "...",
      "evidence_quality": "high" | "medium" | "low"
    }
  ],
  "branching_logic": {
    "decision_points": [
      {
        "after_step": 1,
        "condition": "...",
        "if_true_goto": 2,
        "if_false_goto": 3
      }
    ]
  },
  "inputs": ["..."],
  "outputs": ["..."],
  "rollback_notes": "...",
  "playbook_confidence": 0.0-1.0,
  "execution_confidence_guidance": "..."
}"""

_V2_USER = _V1_USER


register_prompt(
    Prompt(
        name="playbook",
        version="v2",
        system=_V2_SYSTEM,
        user_template=_V2_USER,
    ),
    default=True,
)

