"""Registered versions of the episode-reconstruction prompt.

Splits the original inline ``EPISODE_PROMPT`` (from
``ai/extractors/episode_extractor.py``) into a stable system block —
instructions + schema, identical across all calls — and a dynamic
user template that carries only the evidence. This lets OpenAI's
automatic prefix cache and Anthropic's ephemeral cache both hit on
the instruction tokens, cutting per-call cost for the biggest LLM
call in the pipeline.

Treat ``v1`` as immutable. Edit = ship a new version; historical
``llm.usage`` events stay accurate.
"""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You are analyzing operational evidence from an IT troubleshooting case.
Your goal is to reconstruct one or more structured episodes from the providing evidence.

Logic for Grouping:
1. Combine evidence into a SINGLE episode IF AND ONLY IF they share the same context or category (e.g., cascading service failures, shared infrastructure logs, a shared root cause).
2. Separate evidence into MULTIPLE episodes if they represent distinct, unrelated operational problems (e.g., a "Billing Service crash" is distinct from a "VPN authentication failure" unless they are explicitly linked by a shared root cause).

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

Preserve failed steps and uncertainty. Do not collapse ambiguity into polished summaries."""

_V1_USER = """Evidence items (ordered by time):
{evidence_text}"""


register_prompt(
    Prompt(
        name="episode",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
