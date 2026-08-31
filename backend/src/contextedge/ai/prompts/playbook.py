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
    # Default moved to v6 below on the 2026-08-19 A/B; the registry allows
    # exactly one default per prompt name.
)


# v6 — sequencing, step economy, and readable language (2026-08-19).
# Directive from playbook review: steps must be logically correct,
# meaningfully sequenced, and free of padding, in plain friendly prose.
# v5 constrains what a step may CLAIM (grounding, citations, verbatim
# commands) but not how the procedure reads as a whole: nothing stops a
# generation whose steps are individually grounded yet out of causal
# order, near-duplicated, or written in committee English. Rules 10-12
# constrain the procedure itself.
#
# A/B verdict (2026-08-19, 6 patterns, judge + 8 patterns structural —
# evals/playbook_prompt_ab.py, datasets/playbook_prompt_ab_2026-08-19.json):
# v6 won on economy (6.3 -> 5.5 steps at 62 -> 61 refs: tighter, not
# thinner), grounding (0.79 -> 0.94), and language (4.67 -> 5.0), with
# rollback 6/6 and latency unchanged on both.
#
# Rule 10's sequencing half did NOT hold up: on the deterministic branch
# audit both versions produced valid control flow on 5 of 8 patterns, and
# v6 emitted MORE defect occurrences (6 vs 3). Prompting cannot make a
# model reliably emit consistent branch targets, which is why
# ``sanitize_branching_logic`` enforces that structurally instead. Rule 10
# is kept for its ordering guidance, not credited for branch validity.
_V6_SYSTEM = _V5_SYSTEM.replace(
    """   Place best-practice steps at their natural position in the execution
   sequence. Prefer grounded steps whenever the sources support them;
   add best-practice steps ONLY where their absence would leave the
   playbook incomplete or unsafe — not to pad it.""",
    """   Place best-practice steps at their natural position in the execution
   sequence. Prefer grounded steps whenever the sources support them;
   add best-practice steps ONLY where their absence would leave the
   playbook incomplete or unsafe — not to pad it.
10. Sequence by causality, not by category. Diagnose before you change
    anything; verify after every change; escalate or roll back last.
    Each step's "expected_outcome" is the gate for the next step — if
    reordering two steps would not break the procedure, ask whether one
    of them is doing any work. Every "decision_points" entry must
    reference step orders that exist, and a branch must land on a step
    that makes sense given the condition.
11. Produce the MINIMAL COMPLETE set of steps. Merge actions that are
    always executed together into one step. Do not emit a step that
    neither changes the system, narrows the diagnosis, nor reduces
    risk — "review the logs", "document findings", "monitor the
    situation" are filler unless a supplied source requires them for
    this specific pattern. No near-duplicate steps. The test: a
    reviewer should be unable to delete any step without losing
    coverage or safety.
12. Write step text in plain, friendly language for a tired on-call
    engineer: short imperative sentences, second person, concrete nouns
    ("restart the RADIUS service on vpn-gw-01", not "initiate a service
    restart procedure"). No corporate filler — never "leverage",
    "utilize", "ensure alignment", "as appropriate". Expand an acronym
    at first use unless it is universally known (DNS, VPN, SSL).""",
)

register_prompt(
    Prompt(
        name="playbook",
        version="v6",
        system=_V6_SYSTEM,
        user_template=_V3_USER,
    ),
)


# v7 - stronger use of AutomationEdge KB action guidance.
#
# v6 received approved knowledge, but the retriever could still surface a
# descriptive section while the model wrote a generic support-engineering
# action. The retrieval layer now labels ACTION / PREREQUISITE / VALIDATION /
# ROLLBACK sections; v7 makes those labels binding for generation. A generated
# playbook must either include the KB-required item with a citation, or surface
# why it cannot be used as a conflict/mismatch for reviewer attention.
_V7_SYSTEM = _V6_SYSTEM + """
13. Treat labelled KB sections as a coverage checklist:
    - ACTION: include the product-specific action as a playbook step with
      the KB source_ref, preserving exact AutomationEdge component names,
      plugin names, paths, settings, commands, and UI labels from the source.
    - PREREQUISITE: include it before the dependent action, or add a
      requires_review conflict if observed practice skipped it.
    - VALIDATION: include it as an explicit check after the related action;
      do not hide it inside expected_outcome when the KB states a separate
      validation activity.
    - ROLLBACK: include it in rollback_notes or as a final rollback step when
      the KB gives concrete rollback work.
14. Before finalizing, compare the steps against every supplied KB ACTION,
    PREREQUISITE, VALIDATION, and ROLLBACK section. If any required item is
    missing, add it or record a conflict explaining why the reviewer must
    decide. A product-specific KB instruction should not disappear just
    because no episode performed it.
15. Prefer AutomationEdge product language from KB over generic phrasing.
    Name the exact product area or plugin when the KB supplies it. Use generic
    best-practice wording only when neither KB nor episodes identify the
    product-specific action."""

register_prompt(
    Prompt(
        name="playbook",
        version="v7",
        system=_V7_SYSTEM,
        user_template=_V3_USER,
    ),
)


# v8 — product-version labels on KB (2026-08-31).
# Retrieval is embedding-nearest, then stored KB version (source_facets /
# applicability) re-ranks. A different-release article is still fetched —
# it is often the full procedure that still applies — and the prompt must
# make the generator name that gap on the step, not hide it in source_refs.
_V8_SYSTEM = _V7_SYSTEM + """
16. When a supplied KB is labelled PRODUCT VERSION MISMATCH, still use the
    full procedure. In every step that follows that article, say so in the
    step text itself: "Based on KB for AutomationEdge <kb-version> (this
    ticket is <ticket-version>): ..." Do not hide the version gap in
    source_refs only — an engineer running the playbook must see it on
    the step. When the KB matches the ticket version, do not add that
    caveat. When the KB states no version, treat it as version-agnostic."""

register_prompt(
    Prompt(
        name="playbook",
        version="v8",
        system=_V8_SYSTEM,
        user_template=_V3_USER,
    ),
)


# v9 — mail-thread solutions under each episode, used together with KB.
# v8 still only received episode title / root cause / outcome, so a
# working fix written in the ticket email thread never reached generation
# unless it had been compressed into those two fields. Observed steps and
# thread excerpts now sit under [ep-N]; the model must use them and KB.
_V9_SYSTEM = _V8_SYSTEM + """
17. Use BOTH sources. Approved KB is what should be done. Under each
    [ep-N], "Observed steps (from mail thread)" and "Mail-thread solution"
    are what actually resolved the ticket. Include that working solution
    as grounded playbook steps citing the episode. Do not generate from
    KB alone when an episode records a working mail-thread solution, and
    do not ignore a KB safeguard the mail thread skipped — record a
    conflict if they disagree."""

register_prompt(
    Prompt(
        name="playbook",
        version="v9",
        system=_V9_SYSTEM,
        user_template=_V3_USER,
    ),
    default=True,
)
