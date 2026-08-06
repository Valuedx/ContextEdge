"""Salience-aware slicing for model-bound text (diagnosis roadmap A1).

Every ``[:N]`` head-slice in the pipeline spends its budget on whatever
sits at the top of the text — and in conversational bodies the top is
the newest reply's greeting and scheduling chatter, while the technical
substance sits thousands of characters below. Measured failure (roadmap
F4): a 109k-char thread whose first substantive line started at char
4,330 was classified ``not_relevant`` from its first 2,000 chars, and a
complete, reusable resolution was silently discarded.

``salient_slice(text, n)`` replaces head-truncation at those call
sites. It is deterministic (no LLM):

1. Under-budget text passes through UNTOUCHED — the p50 evidence body is
   254 chars and must not be rewritten by a salience pass.
2. Over-budget text is segmented (email-header boundaries, blank-line
   paragraphs), boilerplate lines are dropped, segments are scored by
   technical-token density, and the top-scoring segments are re-emitted
   in ORIGINAL order until the budget is spent. A slice of the head
   segment is always kept: the problem statement usually leads, and a
   slice that loses the question while keeping the answer is as bad as
   the reverse.
"""

from __future__ import annotations

import re

# Lines that carry no operational meaning on their own. Anchored to line
# starts; conversational sentences that merely contain "thanks" survive.
_BOILERPLATE_RE = re.compile(
    r"^(?:(?:hi|hello|dear|greetings)\b|good\s+(?:morning|afternoon|evening)|"
    r"thanks?\b|thank\s+you|warm\s+regards|kind\s+regards|best\s+regards|"
    r"regards\b|cheers\b|sincerely\b|"
    r"from:|sent:|to:|cc:|subject:|date:|"
    r">|-{3,}|_{3,}|={3,}|\*{3,}|"
    r"disclaimer\b|confidential(?:ity)?\b|this\s+e?-?mail)",
    re.IGNORECASE,
)

# Boundaries that start a new message inside a fused thread.
_MESSAGE_BOUNDARY_RE = re.compile(
    r"^(?:from:|on\s.+wrote:|-{3,}\s*(?:original|forwarded)\s+message)",
    re.IGNORECASE,
)

# Technical vocabulary that marks a line as operationally substantive.
_TECH_WORD_RE = re.compile(
    r"\b(?:error|errors|fail(?:ed|ure|s|ing)?|exception|timeout|timed\s+out|"
    r"crash(?:ed|es)?|refused|denied|unable|cannot|exhausted|missing|"
    r"config(?:uration)?|install(?:ed|ation)?|upgrade[ds]?|update[ds]?|patch(?:ed)?|"
    r"version|log[s]?|server|service|agent|plugin|driver[s]?|workflow|job[s]?|"
    r"restart(?:ed)?|reboot(?:ed)?|resolv(?:e|ed|ing)|fix(?:ed|es)?|"
    r"disk|memory|cpu|port|certificate|license|queue|database|connection)\b",
    re.IGNORECASE,
)
# Code-shaped tokens: paths, dotted identifiers, snake_case,
# UPPER_CONSTANTS (underscore required — bare uppercase words are
# addresses and legal shouting, not code), hex, version numbers.
_CODE_TOKEN_RE = re.compile(
    r"(?:[\w-]+\.[\w.-]+\w|\w+_\w+|\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b|"
    r"\b0x[0-9a-fA-F]+\b|\b\d+\.\d+(?:\.\d+)?\b|[\\/][\w\\/.-]{4,})"
)

HEAD_RESERVE_FRACTION = 0.25


# Redaction placeholders and angle-bracketed addresses look like code
# tokens ([REDACTED:EMAIL] is an UPPER_CONSTANT match) — a wrapped CC
# list scored 51 on the real F4 thread and starved the actual resolution.
_SCORE_NOISE_RE = re.compile(r"\[REDACTED:[A-Z_]+\]|<[^>\n]{0,120}>")
_EMAILISH_RE = re.compile(r"\[REDACTED:EMAIL\]|@[\w.-]+\.\w{2,}")


def _score_segment(lines: list[str]) -> float:
    """Absolute substance, not density: a long technical paragraph must
    outrank a short one, and both must outrank scheduling chatter of any
    length (which scores ~0 on both counters)."""
    text = _SCORE_NOISE_RE.sub(" ", " ".join(lines))
    return len(_TECH_WORD_RE.findall(text)) + len(_CODE_TOKEN_RE.findall(text))


def _segments(text: str) -> list[list[str]]:
    """Split into paragraph/message segments with boilerplate lines and
    blank lines removed. Segment order is document order. A segment that
    repeats verbatim is boilerplate BY DEFINITION — signature blocks,
    postal addresses, and disclaimers recur under every reply in a fused
    thread — so only the first occurrence survives."""
    segments: list[list[str]] = []
    seen: set[str] = set()
    current: list[str] = []

    def _close() -> None:
        nonlocal current
        if current:
            key = "\n".join(current)
            if key not in seen:
                seen.add(key)
                segments.append(current)
            current = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _MESSAGE_BOUNDARY_RE.match(line):
            _close()
            continue
        if _BOILERPLATE_RE.match(line):
            continue
        # Wrapped recipient lists: `To:`/`Cc:` covers only the first
        # line; continuations are bare `Name <addr>; Name <addr>;` runs.
        if len(_EMAILISH_RE.findall(line)) >= 2:
            continue
        # Link-menu footers ("| Home | Submit a Ticket | Knowledge Base")
        # score on their URLs while carrying nothing.
        if line.count("|") >= 2:
            continue
        current.append(line)
    _close()
    return segments


def _is_fused_thread(text: str) -> bool:
    """Two or more message boundaries = a fused conversation. There the
    'head' is the NEWEST reply — greetings and scheduling — not a problem
    statement, so it earns no reserved budget."""
    count = 0
    for raw in text.splitlines():
        if _MESSAGE_BOUNDARY_RE.match(raw.strip()):
            count += 1
            if count >= 2:
                return True
    return False


def salient_slice(text: str, budget: int) -> str:
    """Return at most ``budget`` characters of the most substantive text.

    Under-budget input is returned verbatim (byte-identical) so short
    evidence — the common case — is never rewritten.
    """
    if text is None:
        return ""
    if len(text) <= budget:
        return text

    segments = _segments(text)
    if not segments:
        return text[:budget]

    rendered = ["\n".join(seg) for seg in segments]

    # Allocation per segment index. In a single-message document the head
    # keeps a guaranteed slice (the problem statement usually leads, even
    # when its wording is not technical enough to win on score); in a
    # fused thread the head is the newest reply's chatter and competes on
    # score like everything else.
    alloc: dict[int, int] = {}
    spent = 0
    scored_from = 0
    if not _is_fused_thread(text):
        head_reserve = int(budget * HEAD_RESERVE_FRACTION)
        alloc[0] = min(len(rendered[0]), head_reserve)
        spent = alloc[0]
        scored_from = 1

    order = sorted(
        range(scored_from, len(segments)),
        key=lambda i: _score_segment(segments[i]),
        reverse=True,
    )
    for i in order:
        if _score_segment(segments[i]) <= 0:
            break
        remaining = budget - spent - 1
        if remaining < 40:
            break
        # The winning segment gets truncated to fit rather than skipped —
        # 200 chars of the resolution beat 0 chars of it.
        take = min(len(rendered[i]), remaining)
        alloc[i] = max(alloc.get(i, 0), take)
        spent += take + 1

    if not alloc:
        # Nothing scored and no head guarantee (all-chatter thread):
        # degrade to a head slice of the de-boilerplated text.
        return "\n".join(rendered)[:budget]

    # Any leftover budget tops up the head segment past its reserve.
    leftover = budget - spent
    if 0 in alloc and leftover > 0:
        alloc[0] = min(len(rendered[0]), alloc[0] + leftover)

    return "\n".join(rendered[i][: alloc[i]] for i in sorted(alloc))[:budget]
