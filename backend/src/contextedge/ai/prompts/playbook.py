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
)


# v4 — three changes, each from reviewing 37 generated playbooks against the
# sources they were generated from:
#
# 1. Verbatim commands. Only 25 of 223 generated steps contained anything an
#    engineer could actually run; the sources often held the literal command
#    and the generator paraphrased it into "test basic network connectivity
#    (e.g., ping, traceroute)" — the reader is handed back the work they
#    asked to have done.
# 2. No prompt labels in step text. Persisted steps read "as indicated by
#    KB-1" — a label that exists only inside this prompt. After persistence
#    it resolves to nothing. source_refs carries the citation; prose must
#    stand on its own.
# 3. An unsourced step must say what would verify it. "evidence_quality":
#    "low" told a reviewer a step was inference but not what to check;
#    81 of 223 steps carried the flag and nothing else.
_V4_SYSTEM = _V3_SYSTEM.replace(
    """5. A section marked "read from an image" is a model's paraphrase, not
   the SOP's exact wording. Cite it, but do not quote it as verbatim
   policy.""",
    """5. A section marked "read from an image" is a model's paraphrase, not
   the SOP's exact wording. Cite it, but do not quote it as verbatim
   policy.
6. When a supplied source contains a literal command, path, config key,
   or flag, reproduce it EXACTLY in the step text — do not paraphrase
   "openssl s_client -connect host:443" into "use an SSL testing tool".
   Never compose a command that appears in no source: describe the goal
   and mark the step "evidence_quality": "low" instead. A wrong literal
   command is worse than none.
7. The labels kb-N and ep-N exist only inside this prompt. They belong
   in "source_refs" and NOWHERE in prose: never write "as per KB-1" in
   "text", "on_failure", or "conflicts" fields. Once stored, those
   labels resolve to nothing a reader can open.
8. A step with empty source_refs must state, inside "expected_outcome",
   what observable result would confirm the step was right — a reviewer
   deciding whether to approve your inference needs something to check,
   not just a low-evidence flag.""",
)

register_prompt(
    Prompt(
        name="playbook",
        version="v4",
        system=_V4_SYSTEM,
        user_template=_V3_USER,
    ),
)


# v5 — grounded vs best-practice step taxonomy. v4's rule 8 told an
# unsourced step to state its verification; v5 makes the distinction a
# first-class, filterable contract. The generator ENFORCES the tags
# structurally after citation cleaning (a step with no surviving
# source_refs is best_practice no matter what the model claims), so
# these instructions shape intent and completeness, not trust.
_V5_SYSTEM = _V4_SYSTEM.replace(
    """8. A step with empty source_refs must state, inside "expected_outcome",
   what observable result would confirm the step was right — a reviewer
   deciding whether to approve your inference needs something to check,
   not just a low-evidence flag.""",
    """8. A step with empty source_refs must state, inside "expected_outcome",
   what observable result would confirm the step was right — a reviewer
   deciding whether to approve your inference needs something to check,
   not just a low-evidence flag.
9. Every step is either GROUNDED or BEST-PRACTICE — never an untagged
   blend:
   - Grounded: explicitly supported by a supplied ticket, KB article,
     SOP, log, or episode. It MUST cite that support in "source_refs".
     Do not infer missing operational actions and present them as
     grounded.
   - Best practice: an important operational step no source states but
     an experienced support engineer would expect — prerequisite
     validation, backup/rollback preparation, checksum or antivirus
     verification, security validation, version compatibility checks,
     file integrity verification, logging and audit documentation,
     customer communication checkpoints, post-deployment validation,
     health checks, risk mitigation, cleanup, documentation updates,
     lessons learned. Tag it exactly:
       "grounding_status": "non_grounded",
       "step_classification": "best_practice",
       "confidence": "best_practice",
       "reason": "Generated from industry/support engineering best practices; not explicitly present in the source."
   Place best-practice steps at their natural position in the execution
   sequence. Prefer grounded steps whenever the sources support them;
   add best-practice steps ONLY where their absence would leave the
   playbook incomplete or unsafe — not to pad it.""",
)

register_prompt(
    Prompt(
        name="playbook",
        version="v5",
        system=_V5_SYSTEM,
        user_template=_V3_USER,
    ),
    default=True,
)
