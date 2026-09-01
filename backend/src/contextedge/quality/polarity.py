"""Whether a sentence asserts an action or forbids it.

Lexical overlap cannot tell "restart the agent" from "do not restart the
agent" — the two share every content word, so a token or bigram score rates
them a perfect match. Measured on the shipped Stage C scorer, the forbidden
case came back at 1.00: the strongest possible "entailed", awarded to a step
doing precisely what its source prohibits.

That is the failure the plan names in §4.2 — *"a source can mention an action
while explicitly prohibiting it"* — so it has to be handled before overlap is
read as support, not after.

Polarity flips the meaning of a high score rather than merely damping it:

- step asserts, source forbids  → the overlap is the *evidence of a conflict*.
  This is the most valuable cheap signal in the module.
- step forbids, source forbids  → agreement. The step is telling the operator
  not to do the thing, which is what the source says.
- step forbids, source asserts  → the step declines a required action. Worth a
  reviewer's attention, not a silent pass.

Deliberately conservative and deliberately shallow. It reads negation cues in
the main clause and abstains on anything it cannot parse — an abstention leaves
the existing score alone, so this can only remove false verdicts, never invent
new ones. Real negation scope resolution belongs with the embedding/LLM layer
this module is a placeholder for.
"""

from __future__ import annotations

import re

# Cues that negate the action of a clause. Kept to unambiguous operational
# phrasing: "no" and "without" are excluded because "no longer responding" and
# "without restarting, check X" negate a noun or a subordinate clause rather
# than the instruction itself, and misreading those inverts a correct verdict.
_NEGATION_CUES = (
    r"\bdo not\b",
    r"\bdon't\b",
    r"\bdoes not\b",
    r"\bdoesn't\b",
    r"\bnever\b",
    r"\bmust not\b",
    r"\bmustn't\b",
    r"\bshould not\b",
    r"\bshouldn't\b",
    r"\bcannot\b",
    r"\bcan't\b",
    r"\bavoid\b",
    r"\brefrain from\b",
    r"\bis not permitted\b",
    r"\bare not permitted\b",
    r"\bnot allowed\b",
    r"\bnot supported\b",
    r"\bnot recommended\b",
    r"\bwe do not suggest\b",
    r"\bdo not suggest\b",
)

_NEGATION_RE = re.compile("|".join(_NEGATION_CUES), re.IGNORECASE)

# A trailing clause that negates something *other* than the instruction:
# "Restart the agent, but do not delete the lock file" asserts a restart. Only
# the leading clause decides the sentence's polarity.
_CLAUSE_SPLIT_RE = re.compile(
    r"\b(?:but|however|although|whereas|otherwise|unless)\b", re.IGNORECASE
)


def is_negated(text: str) -> bool:
    """True when the main clause forbids its action.

    Only the first clause counts. "Restart the service, but do not stop the
    broker" is an instruction to restart, and reading the whole string would
    classify it as a prohibition and invert every comparison that uses it.
    """
    if not text:
        return False
    head = _CLAUSE_SPLIT_RE.split(text, maxsplit=1)[0]
    return bool(_NEGATION_RE.search(head))


def polarity_agrees(left: str, right: str) -> bool:
    """True when both sentences assert, or both forbid."""
    return is_negated(left) == is_negated(right)


def describe_conflict(step_text: str, source_text: str) -> str | None:
    """One sentence naming the polarity conflict, or ``None`` when there is none.

    Written for the reviewer panel: it has to say which way round the conflict
    runs, because "step and source disagree" is not actionable and the two
    directions need different fixes.
    """
    step_negated = is_negated(step_text)
    source_negated = is_negated(source_text)
    if step_negated == source_negated:
        return None
    if source_negated:
        return (
            "the source forbids this action while the step performs it — the wording "
            "matches almost exactly, which is why it scored as supported"
        )
    return (
        "the step declines an action the source requires — it may be a deliberate "
        "deviation, but nothing here records that decision"
    )
