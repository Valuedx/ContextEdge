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
)

# v2 (2026-08): adds per-episode and per-step ``evidence_refs`` — the
# provenance the persistence layer was already prepared to read but the
# prompt never requested (P0 of the correlation/episode review). Also
# fixes the doubled-brace bug from v1: ``system`` is never .format()ed,
# so v1's {{ }} reached the model as literal double braces (malformed
# JSON example). New version, not an edit — v1 stays immutable so
# historical llm.usage events and eval baselines stay accurate.
_V2_SYSTEM = """You are analyzing operational evidence from an IT troubleshooting case.
Your goal is to reconstruct one or more structured episodes from the provided evidence.

Each evidence item is labelled with a reference id like [ev-3]. Use these ids to ground
your output: every episode and every step must cite the evidence it is based on.

Logic for Grouping:
1. Combine evidence into a SINGLE episode IF AND ONLY IF they share the same context or category (e.g., cascading service failures, shared infrastructure logs, a shared root cause).
2. Separate evidence into MULTIPLE episodes if they represent distinct, unrelated operational problems (e.g., a "Billing Service crash" is distinct from a "VPN authentication failure" unless they are explicitly linked by a shared root cause).
3. Assign each evidence reference to the episode(s) it actually supports. An evidence item that supports none of the episodes should appear in no episode's evidence_refs.

For each episode, identify a structured sequence of steps:
1. step_type: one of "complaint", "diagnostic", "hypothesis", "action", "observation", "failed_step", "remediation", "escalation", "outcome"
2. text: what happened in this step
3. observation: what was observed after this step (if applicable)
4. result_state: "success", "failure", "inconclusive", "unknown"
   NOTE: "failure" should ONLY be used when a troubleshooting ACTION or ATTEMPTED DIAGNOSTIC failed to execute (e.g., "Tried to ping but timed out").
   Do NOT mark complaints or diagnostics as "failure" just because they report a system crash. Reporting a crash is a "success".
5. confidence: 0.0-1.0 how confident you are this step actually occurred based on evidence
6. evidence_refs: the [ev-N] ids this step is grounded in (at least one where possible)

For each episode also extract:
- title: concise episode title
- root_cause_summary: root cause if identified (null if unclear)
- final_outcome: final resolution (null if unresolved)
- overall_confidence: 0.0-1.0 overall extraction confidence
- evidence_refs: ALL [ev-N] ids belonging to this episode

Respond in JSON with a list of episodes:
{
  "episodes": [
    {
      "title": "...",
      "root_cause_summary": "..." or null,
      "final_outcome": "..." or null,
      "overall_confidence": 0.0-1.0,
      "evidence_refs": ["ev-1", "ev-2"],
      "steps": [
        {
          "step_order": 1,
          "step_type": "...",
          "text": "...",
          "observation": "..." or null,
          "result_state": "...",
          "failed_flag": false,
          "successful_flag": false,
          "confidence": 0.0-1.0,
          "evidence_refs": ["ev-2"]
        }
      ]
    }
  ]
}

Preserve failed steps and uncertainty. Do not collapse ambiguity into polished summaries.
Preserve contradictions between sources; do not merge them into one unsupported conclusion."""

_V2_USER = """Evidence items (ordered by time):
{evidence_text}"""


register_prompt(
    Prompt(
        name="episode",
        version="v2",
        system=_V2_SYSTEM,
        user_template=_V2_USER,
    ),
    default=True,
)
