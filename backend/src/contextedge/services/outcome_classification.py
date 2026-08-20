"""Turn free-text episode outcomes into the bounded vocabulary the ledger needs.

`PatternEvidence.outcome` accepts `success | partial | failure | unknown` and is
NULL on all 1,551 rows of the live corpus. The reason is not that the data is
missing — 10,247 of 15,260 episodes carry a `final_outcome` — but that it is
free text in **9,006 distinct phrasings**, so nothing could aggregate it.

That single unnormalized column is what stands between the system and the
question it exists to answer: *did this fix actually work?* Without it a pattern
can count how often it was cited and never how often it helped.

## Deterministic, not a model

Same rule as situation correlation: an outcome is a factual claim about what
happened, and a model's opinion is not evidence for one. Rules are ordered,
inspectable, and cheap enough to re-run over the whole corpus. Anything the
rules do not recognise becomes `unknown` — never a guess, because a wrong
outcome is worse than an absent one. An absent outcome is excluded from the
efficacy rate; a wrong one silently moves it.

## The ordering trap

`"unresolved"` contains `"resolved"`. A contains-check in the obvious order
classifies every failure in the corpus as a success and inflates every efficacy
number that follows — and the numbers would look entirely plausible. Failure
patterns are therefore tested first, and the test suite pins that ordering.

## `abandoned` is not `failure`

"Ticket closed due to lack of client response" is not a fix that did not work.
Nothing was tried and nothing was learned. Counting it as failure understates
efficacy; counting it as success overstates it. It maps to `unknown` and leaves
the rate alone, which is the only honest option.

Vocabulary derived from the measured distribution of the live corpus
(2026-08-21), not invented: the families below cover the phrasings that
actually occur, and coverage is reported rather than assumed.
"""

from __future__ import annotations

import re

SUCCESS = "success"
PARTIAL = "partial"
FAILURE = "failure"
UNKNOWN = "unknown"

OUTCOMES = (SUCCESS, PARTIAL, FAILURE, UNKNOWN)

# Ordered. First match wins, so the sequence is part of the definition.
#
# Each entry is (regex, outcome). Patterns are matched against the lowercased,
# whitespace-collapsed text.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # --- FAILURE first: "unresolved" contains "resolved" ------------------
    (re.compile(r"\bunresolved\b"), FAILURE),
    (re.compile(r"\bnot\s+resolved\b"), FAILURE),
    (re.compile(r"\bun-?fixed\b"), FAILURE),
    (re.compile(r"\bstill\s+(failing|broken|occurring|happening)\b"), FAILURE),
    (re.compile(r"\b(did\s*not|didn'?t|does\s*not|doesn'?t)\s+(work|help|resolve)"), FAILURE),
    (re.compile(r"\bno\s+resolution\b"), FAILURE),
    (re.compile(r"\bfailed\b"), FAILURE),
    (re.compile(r"\bescalat(ed|ion)\b"), FAILURE),
    # `\w+\s+` allows one intervening word: "under INTERNAL investigation"
    # was missed by strict adjacency, and that phrasing is common.
    (re.compile(r"\b(under|ongoing|pending|further)\s+(\w+\s+)?investigation\b"), FAILURE),
    (re.compile(r"\binvestigation\s+(ongoing|continues|requested|pending)\b"), FAILURE),
    (re.compile(r"\bstill\s+(open|pending)\b"), FAILURE),
    (re.compile(r"\bre-?opened\b"), FAILURE),
    # --- ABANDONED -> unknown. Closed is not fixed. -----------------------
    # Ahead of the success rules: "information provided, ticket closed"
    # would otherwise read as a resolution.
    (re.compile(r"\b(lack|absence)\s+of\s+(client|customer|user)\s+response\b"), UNKNOWN),
    (re.compile(r"\bno\s+(client|customer|user)\s+response\b"), UNKNOWN),
    (re.compile(r"\b(withdrawn|cancelled|canceled|duplicate)\b"), UNKNOWN),
    (re.compile(r"\bclosed\s+(without|due\s+to)\b"), UNKNOWN),
    (re.compile(r"\bno\s+(action|further\s+action)\s+(required|taken)\b"), UNKNOWN),
    (re.compile(r"\binformation\s+(provided|shared)\b"), UNKNOWN),
    # --- PARTIAL: service restored, cause survives ------------------------
    (re.compile(r"\bwork[\s-]?around\b"), PARTIAL),
    (re.compile(r"\btemporar(y|ily)\b"), PARTIAL),
    (re.compile(r"\bmitigat(ed|ion)\b"), PARTIAL),
    (re.compile(r"\bpartial(ly)?\b"), PARTIAL),
    (re.compile(r"\binterim\s+(fix|solution)\b"), PARTIAL),
    # --- SUCCESS ----------------------------------------------------------
    (re.compile(r"\bresolved\b"), SUCCESS),
    (re.compile(r"\bfixed\b"), SUCCESS),
    (re.compile(r"\b(renewed|renewal\s+(\w+\s+)?(approved|completed))\b"), SUCCESS),
    (re.compile(r"\b(enabled|unlocked|restored|reinstated|activated|reactivated)\b"), SUCCESS),
    # Bare "successfully" carries the claim on its own; the failure rules
    # above have already taken "did not work" and friends out of the running.
    (re.compile(r"\bsuccessfully\b"), SUCCESS),
    (re.compile(r"\b(completed|succeeded)\b"), SUCCESS),
    (re.compile(r"\b(granted|provisioned|installed|configured|reconfigured)\b"), SUCCESS),
    (re.compile(r"\b(reinstalled|upgraded|patched|replaced|migrated)\b"), SUCCESS),
    # Fulfilment of a request: the thing asked for was handed over. Distinct
    # from "information provided", which is caught above as unknown because
    # answering a question is not fixing anything.
    (
        re.compile(
            r"\b(driver|link|licen[sc]e|plugin|access|credentials?)\b"
            r"[^.]*\b(provided|shared|assigned|delivered|issued)\b"
        ),
        SUCCESS,
    ),
    (re.compile(r"\bissue\s+(is\s+)?(gone|cleared)\b"), SUCCESS),
    (re.compile(r"\bworking\s+(now|as\s+expected)\b"), SUCCESS),
    (re.compile(r"\bsolution\s+provided\b"), SUCCESS),
    # `closed` alone, last: a bare "ticket closed." says the case ended and
    # nothing about whether the fix worked.
    (re.compile(r"\b(ticket\s+)?closed\b"), UNKNOWN),
)

_WHITESPACE = re.compile(r"\s+")


def classify_outcome_detailed(text: str | None) -> tuple[str, bool]:
    """``(outcome, recognised)``.

    ``recognised`` separates two things that both come out as ``unknown`` and
    mean opposite things about the classifier. "Ticket closed due to lack of
    client response" is *deliberately* unknown — a rule matched and declined to
    call it, because nothing was tried. An unrecognised phrasing is a coverage
    gap. Reporting them as one number makes a declining classifier look like a
    failing one, and hides the gap that could actually be closed.
    """
    if not text:
        return UNKNOWN, False
    normalized = _WHITESPACE.sub(" ", str(text).strip().lower())
    if not normalized:
        return UNKNOWN, False
    for pattern, outcome in _RULES:
        if pattern.search(normalized):
            return outcome, True
    return UNKNOWN, False


def classify_outcome(text: str | None) -> str:
    """Map free-text episode outcome to the ledger vocabulary.

    Returns ``unknown`` for empty input and for anything unrecognised. Never
    raises: this runs over a whole corpus and one odd row must not stop it.
    """
    return classify_outcome_detailed(text)[0]


def support_role_for(outcome: str) -> str:
    """What a row with this outcome is offered as.

    A failure is evidence too, and recording it as ``supports_resolution``
    is how a pattern keeps recommending something that stopped working.
    Partial supports — service was restored. Unknown supports nothing and
    contradicts nothing; it is excluded from the rate rather than counted
    against it.
    """
    return "contradicts_resolution" if outcome == FAILURE else "supports_resolution"


def counts_toward_rate(outcome: str) -> bool:
    """Whether this outcome belongs in the efficacy denominator.

    ``unknown`` does not. Including it would let an unclassifiable corpus
    drive a success rate toward zero and read as a fix that stopped working.
    """
    return outcome in (SUCCESS, PARTIAL, FAILURE)
