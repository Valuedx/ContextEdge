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
    # An attribution line, in the several shapes clients actually emit:
    #
    #   On 3 August 2026 at 14:02, Jane Doe <j@x> wrote:
    #   ---- on Mon, 27 Jul 2026 23:58:50 +0530 "Jane Doe"<j@x> wrote ----
    #
    # The first form alone — capital "On", mandatory colon — was what this
    # matched originally, and it missed the dashed lowercase variant on 127
    # of 285 already-"cleaned" messages: 43,136 characters, a quarter of the
    # corpus, still carrying the conversation it was supposed to have
    # removed. It was found by diffing against email_reply_parser, which
    # matched the shape we did not.
    #
    # Anchoring on a line that STARTS with "on" (after optional dashes) and
    # ENDS with "wrote" is what keeps it safe: prose mentioning what someone
    # wrote does not begin with "on", and "As discussed on Monday, the
    # engineer wrote" fails the start anchor.
    re.compile(
        r"^\s*-*\s*on\b.{5,160}?\bwrote\b\s*[-:]*\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
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
    re.compile(
        r"rejected (the|your) email|access denied policy|mail server .* rejected",
        re.IGNORECASE,
    ),
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


# --- trailing boilerplate: signatures and legal disclaimers ------------------
#
# Measured on 285 real cleaned messages (172,680 chars, already stripped of
# quoted history): 79% carried a sign-off line, and 26% of what remained was
# signature blocks and legal disclaimers. The same corporate disclaimer
# appeared 19 times and a marketing footer 16 times.
#
# Cross-message dedup does not remove these. It keeps the FIRST occurrence in
# each thread and suppresses later ones, so every thread retains one full copy
# — and a signature is attached to a person, so it seeds identity extraction
# with the sender's name, title, phone number and employer on every thread.
#
# All patterns are phrasing-based, never organisation-based. A rule listing
# one customer's company name would silently do nothing for every other
# tenant while appearing to work.

# Legal boilerplate. These are always trailing — no message resumes technical
# content after "if you have received this in error" — so the first match cuts
# to the end.
_DISCLAIMER = re.compile(
    r"^.{0,120}?("
    r"this (e-?mail|message|communication|transmission)\b.{0,120}?"
    r"(confidential|privileged|intended (solely|only|for))"
    r"|if you (are not the|have received this).{0,80}(intended|in error)"
    r"|any (unauthorised|unauthorized) (use|review|disclosure|copying)"
    r"|the (contents|content) of this (e-?mail|message).{0,60}confidential"
    r"|disclaimer\s*:"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# A sign-off on its own line. Anchored to the whole line: "Thanks" ending a
# line is a sign-off, "Thanks for confirming the port was open" is content.
_SIGNOFF = re.compile(
    r"^\s*(thanks?\s*(and|&|,)?\s*regards?|best regards?|kind regards?"
    r"|warm regards?|regards?|sincerely|cheers|thanks?|thank you|br)"
    r"[\s,!.]*$",
    re.IGNORECASE | re.MULTILINE,
)

# What a signature block contains that prose does not.
_CONTACT = re.compile(
    r"(^\s*(mobile|mob|cell|phone|tel|direct|desk|ext|email|e-?mail|web)\b[:.\s|]"
    r"|https?://|www\.|@[\w.-]+\.\w{2,}|\+\d[\d\s()-]{7,})",
    re.IGNORECASE | re.MULTILINE,
)

# A signature is short. Beyond this the tail is being treated as prose and
# left alone, because the cost of eating a paragraph of diagnosis is far
# higher than the cost of keeping a name and a phone number.
MAX_SIGNATURE_CHARS = 600
MAX_SIGNATURE_LINE = 60


def _looks_like_signature(tail: str) -> bool:
    """Whether the text after a sign-off is a signature rather than content.

    The conservative half of this feature. "Thanks," is regularly followed by
    more substance — "Thanks, and one more thing: the agent is still down" —
    so matching a sign-off is never on its own a reason to cut.
    """
    lines = [line for line in (raw.strip() for raw in tail.split("\n")) if line]
    if not lines or len(tail) > MAX_SIGNATURE_CHARS:
        return False

    long_lines = [line for line in lines if len(line) > MAX_SIGNATURE_LINE]
    # More than one full-length line is a paragraph, not a signature block.
    if len(long_lines) > 1:
        return False

    # Contact details are decisive — a phone number or a URL under a
    # sign-off is a signature whatever else is around it.
    if _CONTACT.search(tail):
        return True

    # Without them, a signature is a few SHORT lines: name, title, company.
    # Requiring every line to be short is what keeps "Thanks" followed by
    # one more paragraph of diagnosis from being read as a sign-off block —
    # that paragraph is a single long line, and counting lines alone would
    # have cut it.
    return not long_lines and len(lines) <= 4


# A trailing block of links — "Follow us", portal menus, a row of
# pipe-separated destinations. Measured at 11.4% of what survived the
# signature and disclaimer rules, the same footer appearing 116 times.
#
# Recognised by shape rather than by destination: a run of trailing lines
# that are blank, short, or links, containing at least one URL. Matching on
# the domains themselves would be the hardcoding this must avoid.
_URLISH = re.compile(r"(https?://|www\.)", re.IGNORECASE)
MAX_LINK_BLOCK_CHARS = 800
MAX_LINK_BLOCK_LINE = 60

# Two links make a menu; one makes a sentence. A closing line pointing at
# a KB article, a build or a log location is the answer, and a rule keyed
# on "contains a link" would delete exactly that.
MIN_LINK_BLOCK_URLS = 2


def _strip_trailing_link_block(body: str) -> str:
    """Drop a trailing run of link/short lines, if it is a link menu."""
    lines = body.split("\n")
    cut = len(lines)
    while cut > 0:
        candidate = lines[cut - 1].strip()
        if not candidate or _URLISH.search(candidate) or len(candidate) <= MAX_LINK_BLOCK_LINE:
            cut -= 1
        else:
            break

    block = "\n".join(lines[cut:])
    if len(block) > MAX_LINK_BLOCK_CHARS:
        return body
    if len(_URLISH.findall(block)) < MIN_LINK_BLOCK_URLS:
        return body
    return "\n".join(lines[:cut]).rstrip()


def strip_trailing_boilerplate(body: str | None) -> str:
    """Remove a legal disclaimer and/or a signature block from the end.

    The last sign-off is used, not the first: a message may thank someone
    mid-conversation and continue, and only the final one plausibly begins
    the signature.

    Never empties a message that had content — a body reduced to nothing
    leaves the evidence unnameable, and a message consisting solely of
    "Thanks, Dana" is still a real event with a real author.
    """
    if not body:
        return ""

    original = body
    match = _DISCLAIMER.search(body)
    if match:
        body = body[: match.start()].rstrip()

    signoffs = list(_SIGNOFF.finditer(body))
    if signoffs:
        start = signoffs[-1].start()
        if _looks_like_signature(body[start:]):
            body = body[:start].rstrip()

    # Last, so it sees what the sign-off cut exposed: the link row usually
    # sits below the signature, and removing the signature first is what
    # leaves it trailing.
    body = _strip_trailing_link_block(body)

    return body.strip() or original.strip()


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

        # Boilerplate is removed BEFORE the dedup memory sees it. A
        # signature suppressed as a duplicate would still have claimed its
        # lines in `seen`, so the same words written later by a person
        # would be dropped as a repeat of a footer.
        structural = strip_trailing_boilerplate(strip_quoted(original))
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
