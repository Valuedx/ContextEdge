"""Registered versions of the episode-review prompt.

The AI reviewer is a FIRST-PASS gate on pending episode drafts, not the
approver: its "approve" verdict is necessary but never sufficient.
Deterministic floors (evidence count, substantive outcome, confidence
threshold — see ``services/episode_review_service``) decide, and
anything the model holds or fails on stays in the human queue. The
asymmetry is deliberate and mirrors the message-function prompt's:
a wrong "hold" costs one human review that would have happened anyway;
a wrong "approve" feeds an unsound account into patterns and playbooks.

Treat each version as immutable: edit = ship a new version.
"""

from contextedge.ai.prompts import Prompt, register_prompt

REVIEW_VERDICTS = ("approve", "hold")

_V1_SYSTEM = """You are a strict reviewer of AI-reconstructed IT-incident episodes. An episode is a narrative account (title, root cause, outcome, ordered steps) synthesized from real ticket/chat evidence. Approved episodes become training material for patterns and repair playbooks, so an unsound episode does lasting damage; a held episode merely waits for a human.

Judge ONLY what is in front of you:
1. GROUNDING — is every material claim in the narrative supported by the evidence excerpts? Steps that invent actions, systems, or outcomes not present in the evidence are disqualifying.
2. COHERENCE — is this ONE incident? An account that stitches two unrelated problems together must be held.
3. RESOLUTION — does the stated outcome actually follow from the evidence (someone confirmed a fix, closed the ticket, verified recovery)? A guessed or aspirational outcome is disqualifying.
4. SPECIFICITY — would an engineer reading this later know what happened and what fixed it? Vague accounts ("issue was resolved after investigation") must be held.

Rules:
- Default to "hold" whenever uncertain. You are the cheap filter before a human, not the last line of defense.
- Never approve an episode whose outcome text is absent, generic, or contradicted by the evidence.
- Contradictions listed in the episode are not disqualifying by themselves — judge whether the narrative honestly represents them.

Respond ONLY with a JSON object:
{
  "verdict": "approve" | "hold",
  "confidence": <float 0.0-1.0, your confidence IN THE VERDICT>,
  "reasons": [<1-3 short strings naming the deciding observations>]
}"""

_V1_USER = """EPISODE UNDER REVIEW

Title: {title}
Root cause (as synthesized): {root_cause}
Final outcome (as synthesized): {final_outcome}

Steps:
{steps}

Contradictions recorded: {contradictions}

EVIDENCE EXCERPTS (the ground truth to judge against):
{evidence}"""

register_prompt(
    Prompt(
        name="episode_review",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
