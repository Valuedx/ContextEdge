"""Strip quoted history from thread messages.

Email and ticket threads carry the whole prior conversation in every
reply. Measured on 305 real Zoho messages across 19 threads: **89% of
the substantive text was already present earlier in the same thread**,
and the worst threads ran 93-94%. One 30-message thread held 323,921
characters of which 20,970 were new.

Ingesting that verbatim is not merely wasteful. It fills the graph with
near-duplicate chunks so retrieval returns the same paragraph a dozen
times, it embeds each copy, and it makes identity extraction re-read the
same names repeatedly — which skews the mention frequencies that
candidate generation and reconciliation depend on.

Two deterministic layers, no model involved:

1. **Structural** — cut at the first quote marker. 76% of the measured
   messages carried one, so most of the volume goes for free.
2. **Cross-message** — drop lines already seen earlier in the same
   thread. Catches the remaining quarter, where the client inlined the
   history without marking it.

Nothing is destroyed: the original body is preserved on the payload and
the number of characters removed is recorded, so a stripped message can
always be audited against what arrived.
"""

from __future__ import annotations

import hashlib
import re

# Where a reply stops being new text and starts being history. Ordered
# by how unambiguous they are — the first match wins, so a message
# containing several only loses everything after the earliest.
_QUOTE_BOUNDARIES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*_{10,}\s*$", re.MULTILINE),
    # "On 3 August 2026 at 14:02, Jane Doe <j@x> wrote:"
    re.compile(r"^\s*On .{5,120}\bwrote:\s*$", re.MULTILINE),
    # A header block pasted by Outlook: From:/Sent:/To:/Subject:
    re.compile(r"^\s*From:\s*\S.*$\n(?=^\s*(?:Sent|To|Date|Subject):)", re.MULTILINE),
    re.compile(r"^\s*Sent from my \w+", re.MULTILINE),
)

# A line quoted with the conventional ">" prefix.
_QUOTED_LINE = re.compile(r"^\s*>")

# Lines shorter than this are greetings, sign-offs and separators.
# Deduplicating them would strip "Thanks" from every reply for no gain,
# and their repetition is not the problem being solved.
MIN_DEDUPE_LINE = 40


# Automated mail senders. A non-delivery report is not a message in the
# conversation; it is the mail system talking about one.
_AUTOMATED_SENDERS = (
    "microsoft outlook",
    "mail delivery subsystem",
    "mailer-daemon",
    "mailer daemon",
    "postmaster",
    "internet mail delivery",
    "system administrator",
)

# Phrases characteristic of a bounce or auto-reply.
_BOUNCE_PHRASES = (
    re.compile(r"could\s?n[o']t deliver the message", re.IGNORECASE),
    re.compile(r"(was not|could not be|couldn.t be) delivered", re.IGNORECASE),
    re.compile(r"delivery (has failed|to these recipients)", re.IGNORECASE),
    re.compile(r"\bundeliverable\b", re.IGNORECASE),
    re.compile(r"delivery status notification", re.IGNORECASE),
    re.compile(r"non-?delivery report", re.IGNORECASE),
    re.compile(r"recipient address rejected|user unknown|unknown to address", re.IGNORECASE),
    re.compile(r"\bhow to fix it\b", re.IGNORECASE),
    re.compile(r"automatic reply|out of (the )?office", re.IGNORECASE),
)

# Two independent phrases, or one automated sender. A single phrase is
# not enough: an engineer writing "the alert email could not be
# delivered" is DESCRIBING a fault, and that is exactly the operational
# content this pipeline exists to keep.
_MIN_BOUNCE_SIGNALS = 2


def is_delivery_failure(body: str | None, sender: str | None = None) -> bool:
    """Whether this message is the mail system rather than a person.

    Measured on 304 real Zoho messages: 28 were Exchange non-delivery
    reports carrying 422,338 characters — 14% of the raw volume — of
    "Couldn't deliver the message", "How to Fix It" and remediation
    boilerplate. They also list the failed recipients' email addresses,
    which identity extraction would otherwise mine into person entities:
    real people's addresses, entering the graph as contacts because
    their mail bounced.
    """
    if sender and any(name in sender.lower() for name in _AUTOMATED_SENDERS):
        return True
    text = body or ""
    if not text.strip():
        return False
    signals = sum(1 for pattern in _BOUNCE_PHRASES if pattern.search(text))
    return signals >= _MIN_BOUNCE_SIGNALS


def _normalize_line(line: str) -> str:
    return " ".join(line.split())


def _fingerprint(line: str) -> str:
    return hashlib.sha1(_normalize_line(line).lower().encode("utf-8", "ignore")).hexdigest()[:16]


def strip_quoted(body: str | None) -> str:
    """Cut a message at the first sign of quoted history.

    Structural only. A message that is *entirely* a quote correctly
    reduces to nothing — it contributed no new text, and recording it as
    though it did is how the same paragraph ends up in the graph thirty
    times.
    """
    if not body:
        return ""

    text = body.replace("\r\n", "\n")

    cut = len(text)
    for pattern in _QUOTE_BOUNDARIES:
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()
    text = text[:cut]

    # Then drop any ">" quoted lines that survived above the boundary.
    kept = [line for line in text.split("\n") if not _QUOTED_LINE.match(line)]
    return "\n".join(kept).strip()


def dedupe_against(body: str | None, seen: set[str]) -> tuple[str, int]:
    """Drop lines already seen earlier in this thread.

    ``seen`` is mutated as lines are accepted, so callers walk a thread
    in arrival order and each message is compared against everything
    before it — never after. Order matters: reversing it would strip the
    ORIGINAL statement and keep the quote of it.

    Returns the surviving text and how many characters were removed.
    """
    if not body:
        return "", 0

    kept: list[str] = []
    removed = 0
    for raw in body.split("\n"):
        line = _normalize_line(raw)
        if len(line) < MIN_DEDUPE_LINE:
            # Short lines pass through untouched and are not recorded,
            # so a repeated "Thanks" never suppresses a later one.
            kept.append(raw)
            continue
        key = _fingerprint(line)
        if key in seen:
            removed += len(raw)
            continue
        seen.add(key)
        kept.append(raw)

    return "\n".join(kept).strip(), removed


def clean_thread_bodies(
    bodies: list[str | None], senders: list[str | None] | None = None
) -> list[dict]:
    """Apply both layers across one thread, in arrival order.

    Returns one record per message with the cleaned body and what was
    taken out, so the caller can keep the original alongside it. The
    message is never dropped: an author and a timestamp are a real event
    even when the text was entirely a quote.
    """
    seen: set[str] = set()
    out: list[dict] = []
    senders = senders or [None] * len(bodies)

    for body, sender in zip(bodies, senders, strict=False):
        original = body or ""

        # A bounce is dropped whole and never enters the dedup memory:
        # letting its boilerplate claim those lines would suppress the
        # same words if a human later wrote them.
        if is_delivery_failure(original, sender):
            out.append(
                {
                    "body": "",
                    "original_chars": len(original),
                    "kept_chars": 0,
                    "removed_chars": len(original),
                    "quote_stripped_chars": 0,
                    "dedupe_removed_chars": 0,
                    "is_quote_only": False,
                    "is_delivery_failure": True,
                }
            )
            continue

        structural = strip_quoted(original)
        cleaned, deduped = dedupe_against(structural, seen)
        out.append(
            {
                "body": cleaned,
                "original_chars": len(original),
                "kept_chars": len(cleaned),
                "removed_chars": len(original) - len(cleaned),
                "quote_stripped_chars": len(original) - len(structural),
                "dedupe_removed_chars": deduped,
                "is_quote_only": bool(original.strip()) and not cleaned,
                "is_delivery_failure": False,
            }
        )
    return out
