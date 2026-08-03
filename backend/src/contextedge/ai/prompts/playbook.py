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
    # Default moved to v3 below. The registry allows exactly one default
    # per prompt name, so promoting a version means moving this flag —
    # the v2 *text* above is untouched, which is what the immutability
    # convention protects (eval baselines, historical llm.usage).
)



# v3 (2026-08): adds approved KB/SOP content as a distinct input, with
# step-level citations and — the substantive change — an explicit
# instruction to SURFACE disagreement between the documented procedure
# and observed practice rather than silently choosing one.
#
# v2 saw only what engineers did. A playbook generated from VPN incidents
# reproduced observed practice and dropped whatever the approved SOP
# required but nobody happened to perform:
#
#     SOP:       stop service -> back up certificate -> renew -> restart
#     Episodes:  engineer renewed the certificate and restarted
#     v2 output: renew -> restart          (the backup step is gone)
#
# Resolving the conflict automatically in either direction is wrong.
# Preferring the SOP ignores that 15 verified runs did something else;
# preferring practice quietly deletes a safeguard. Both belong in front
# of the reviewer, which is what `conflicts` and per-step `source_refs`
# are for.
#
# New version, not an edit — v2 stays immutable for eval baselines and
# historical llm.usage accuracy.
_V3_SYSTEM = """You are generating a living playbook for an operational troubleshooting pattern.

You receive three kinds of input, and they carry different authority:

- APPROVED KNOWLEDGE (KB articles and SOPs) — what the organisation says
  SHOULD be done. Normative. It may be outdated, incomplete, or written
  for a different environment, but it is the documented procedure.
- EPISODES — what engineers ACTUALLY did, and whether it worked.
  Empirical. Verified outcomes are strong evidence a step works; they
  are not evidence that an undocumented shortcut is approved.
- NEGATIVE KNOWLEDGE — steps that repeatedly failed.

Rules:

1. Include steps the approved knowledge requires even when no episode
   performed them. A backup or approval step missing from observed
   practice is the most common and most costly omission.
2. Where knowledge and practice DISAGREE, do not silently choose.
   Record the disagreement in "conflicts" and keep the documented step,
   marking it "requires_review". A reviewer decides; you surface.
3. Cite sources per step in "source_refs": [kb-N] for knowledge,
   [ep-N] for episodes. A step with neither is your inference — mark
   "evidence_quality": "low".
4. Never invent a normative source. If no knowledge was supplied, say so
   through empty source_refs rather than attributing a step to an SOP.
5. A section marked "read from an image" is a model's paraphrase, not
   the SOP's exact wording. Cite it, but do not quote it as verbatim
   policy.

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
      "evidence_quality": "high" | "medium" | "low",
      "source_refs": ["kb-1", "ep-2"],
      "status": "ok" | "requires_review"
    }
  ],
  "conflicts": [
    {
      "topic": "...",
      "documented": "what the approved knowledge requires",
      "observed": "what episodes actually did",
      "recommendation": "what a reviewer should check",
      "source_refs": ["kb-1", "ep-3"]
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

_V3_USER = """Pattern Title: {pattern_title}
Pattern Description: {pattern_description}
Episode Count: {episode_count}

APPROVED KNOWLEDGE (normative — what should be done):
{knowledge_sources}

EPISODES (empirical — what was actually done):
{episode_summaries}

NEGATIVE KNOWLEDGE (steps that repeatedly fail):
{negative_knowledge}"""


register_prompt(
    Prompt(
        name="playbook",
        version="v3",
        system=_V3_SYSTEM,
        user_template=_V3_USER,
    ),
    default=True,
)
