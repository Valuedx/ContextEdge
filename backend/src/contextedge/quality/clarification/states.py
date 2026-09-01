"""The vocabulary of the clarification loop: round states, question states,
obligations, answer sources, and the rule that decides when a round is done.

Two decisions carry the weight here.

**Mandatory is policy, not a model opinion.** ``enforce_obligation`` overrides
whatever the question generator proposed whenever the underlying gap came from a
blocking finding. A model that can mark its own blockers optional makes the
mandatory/optional distinction decorative, and the first reviewer who notices
will stop reading the badges.

**"Answered" excludes skipped for mandatory and includes it for optional.** That
asymmetry is the entire user-facing contract of the feature, so it lives in one
function (``mandatory_outstanding``) rather than being re-derived at each call
site.
"""

from __future__ import annotations

from typing import Any

# --- round states -----------------------------------------------------------

ROUND_OPEN = "open"
ROUND_ANSWERED = "answered"
ROUND_APPLIED = "applied"
ROUND_SATISFIED = "satisfied"
ROUND_EXHAUSTED = "exhausted"
ROUND_ABANDONED = "abandoned"

ROUND_STATUSES: tuple[str, ...] = (
    ROUND_OPEN,
    ROUND_ANSWERED,
    ROUND_APPLIED,
    ROUND_SATISFIED,
    ROUND_EXHAUSTED,
    ROUND_ABANDONED,
)

# Rounds that are still the playbook's current conversation. The partial unique
# index in migration 0095 permits exactly one of these per playbook.
LIVE_ROUND_STATUSES: frozenset[str] = frozenset({ROUND_OPEN, ROUND_ANSWERED})

# Rounds nothing further will happen to. Written out rather than "not live" so a
# future seventh status has to be classified deliberately.
TERMINAL_ROUND_STATUSES: frozenset[str] = frozenset(
    {ROUND_SATISFIED, ROUND_EXHAUSTED, ROUND_ABANDONED}
)


# --- question states --------------------------------------------------------

Q_OPEN = "open"
Q_ANSWERED = "answered"
Q_SKIPPED = "skipped"
Q_RESOLVED_KB = "resolved_from_kb"
Q_RESOLVED_CONTEXT = "resolved_from_context"
Q_WITHDRAWN = "withdrawn"

QUESTION_STATUSES: tuple[str, ...] = (
    Q_OPEN,
    Q_ANSWERED,
    Q_SKIPPED,
    Q_RESOLVED_KB,
    Q_RESOLVED_CONTEXT,
    Q_WITHDRAWN,
)

# States in which a question no longer needs a person. ``skipped`` is here on
# purpose and is only ever reachable for an optional question — see
# ``mandatory_outstanding``.
SETTLED_QUESTION_STATUSES: frozenset[str] = frozenset(
    {Q_ANSWERED, Q_SKIPPED, Q_RESOLVED_KB, Q_RESOLVED_CONTEXT, Q_WITHDRAWN}
)

# States that carry a usable answer into the revision prompt. ``skipped`` is
# absent: a skipped optional question tells the model nothing, and feeding it
# "(skipped)" invites the model to treat the omission as a stated fact.
ANSWER_BEARING_STATUSES: frozenset[str] = frozenset(
    {Q_ANSWERED, Q_RESOLVED_KB, Q_RESOLVED_CONTEXT}
)


# --- obligations and answer sources -----------------------------------------

MANDATORY = "mandatory"
OPTIONAL = "optional"
OBLIGATIONS: tuple[str, ...] = (MANDATORY, OPTIONAL)

ANSWER_HUMAN = "human"
ANSWER_KB = "kb"
ANSWER_CONTEXT = "context"
ANSWER_CARRIED = "carried"
ANSWER_SOURCES: tuple[str, ...] = (ANSWER_HUMAN, ANSWER_KB, ANSWER_CONTEXT, ANSWER_CARRIED)

ANSWER_KIND_TEXT = "text"
ANSWER_KIND_CHOICE = "choice"
ANSWER_KIND_BOOLEAN = "boolean"
ANSWER_KIND_LIST = "list"
ANSWER_KINDS: tuple[str, ...] = (
    ANSWER_KIND_TEXT,
    ANSWER_KIND_CHOICE,
    ANSWER_KIND_BOOLEAN,
    ANSWER_KIND_LIST,
)

GAP_ORIGIN_FINDING = "finding"
GAP_ORIGIN_CONTRACT = "contract"
GAP_ORIGIN_GATE = "gate"
GAP_ORIGIN_STRUCTURE = "structure"
GAP_ORIGINS: tuple[str, ...] = (
    GAP_ORIGIN_FINDING,
    GAP_ORIGIN_CONTRACT,
    GAP_ORIGIN_GATE,
    GAP_ORIGIN_STRUCTURE,
)

KB_OK = "ok"
KB_NO_RESULTS = "no_results"
KB_FAILED = "retrieval_failed"


def enforce_obligation(proposed: str | None, *, blocking: bool) -> str:
    """The obligation a question actually carries.

    ``blocking`` comes from the gap, not from the model: a gap raised by a
    ``critical`` or ``major`` finding, or by a hard pre-generation gate, is
    mandatory whatever the generator proposed. Everything else takes the
    model's proposal and defaults to optional, because the safe default when
    nobody decided is the one that does not demand a person's time.
    """
    if blocking:
        return MANDATORY
    return MANDATORY if proposed == MANDATORY else OPTIONAL


def mandatory_outstanding(questions: list[Any]) -> list[Any]:
    """Mandatory questions still waiting on a person.

    A mandatory question cannot be satisfied by skipping it — that is what
    mandatory means, and the answer-recording path refuses the skip rather than
    filtering it out here, so the refusal is visible to the caller instead of
    silently doing nothing.
    """
    return [
        question
        for question in questions
        if getattr(question, "obligation", OPTIONAL) == MANDATORY
        and getattr(question, "status", Q_OPEN) not in SETTLED_QUESTION_STATUSES
    ]


def round_is_answerable(questions: list[Any]) -> bool:
    """True when every mandatory question has been settled.

    Optional questions may remain open forever; they never hold up an apply.
    """
    return not mandatory_outstanding(questions)
