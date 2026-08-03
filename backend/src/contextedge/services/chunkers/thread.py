"""Chunker for Gmail and Teams thread-message evidences.

Like tickets, thread messages arrive as one-event-per-message via the
hydration worker (``workers/hydration_tasks.py``). Each Gmail reply
and each Teams message is its own ``EvidenceItem`` with body text =
that single message. The thread spine is reconstructed at query time
through ``Thread.evidence_items``.

What this chunker does over the fallback splitter:

1. **Quote-stripping for Gmail.** Email replies typically inline the
   prior conversation as ``> quoted`` lines or an ``On <date>,
   <person> wrote:`` block. Embedding the quote inflates similarity
   garbage — a quoted "AUTH_CERT_EXPIRED" makes the reply look 99%
   similar to the original alert when the reply itself is just
   ``thanks!``. We strip quotes before splitting and store
   ``metadata.replies_to_excerpt`` so the chunk still indicates it's
   a reply without indexing the quoted text twice.

2. **Author + timestamp metadata.** ``from`` (Gmail) / ``author``
   (Teams) populates ``metadata.author``. Source-event timestamp
   populates ``metadata.ts``. The reranker uses these for both
   personalization (Sarah owns App-X → favour Sarah's messages) and
   recency weighting.

3. **chunk_kind = "message".** Distinguishes thread chunks from
   ticket-body chunks at the search-rollup layer.

Teams messages tend to be short (often <500 chars), which means most
of them produce a single chunk and the splitter is a no-op. The
metadata enrichment is still the meaningful contribution there.
"""

from __future__ import annotations

import re
from dataclasses import replace

from contextedge.services.chunkers.base import ChunkSpec
from contextedge.services.chunkers.fallback import FallbackChunker

# Common quoted-reply patterns. Greedy from the first match to the end
# of the body — once a reply quote starts, everything after it is
# either the prior conversation or a signature, neither of which we
# want to embed at this chunk's identity.
_QUOTE_LEADER_PATTERNS = (
    # "On Mon, May 1, 2026 at 09:42, Jane Doe <jane@…> wrote:"
    re.compile(
        r"^On\s+.{0,200}?\s+wrote:\s*$",
        re.MULTILINE,
    ),
    # Outlook-style: "From: Jane Doe <…>\nSent: …\nTo: …\nSubject: …"
    re.compile(
        r"^From:\s+.+?\nSent:\s+",
        re.MULTILINE | re.DOTALL,
    ),
    # Gmail mobile: "---------- Forwarded message ---------"
    re.compile(
        r"^-{3,}\s*(Forwarded message|Original Message)\s*-{3,}",
        re.MULTILINE | re.IGNORECASE,
    ),
)

# Lines that begin with ``>`` (one or more) are quoted-reply text.
_QUOTED_LINE = re.compile(r"^\s*>+", re.MULTILINE)


class ThreadChunker:
    """Quote-stripping wrapper around the fallback splitter."""

    name = "thread"
    version = 1

    def __init__(self) -> None:
        self._fallback = FallbackChunker()

    def chunk(
        self,
        *,
        title: str | None,
        body: str | None,
        payload: dict,
    ) -> list[ChunkSpec]:
        cleaned, replies_to_excerpt = _strip_quoted_reply(body or "")
        chunks = self._fallback.chunk(
            title=title, body=cleaned, payload=payload,
        )
        if not chunks:
            return []

        meta_overlay: dict[str, object] = {}
        author = (
            (payload or {}).get("from")
            or (payload or {}).get("author")
            or (payload or {}).get("sender")
        )
        if author:
            meta_overlay["author"] = author
        ts = (payload or {}).get("timestamp") or (payload or {}).get("created_at")
        if ts:
            meta_overlay["ts"] = ts
        if replies_to_excerpt:
            # Truncate hard — this is a hint, not a body. 200 chars is
            # enough to identify the parent message without paying for
            # its embedding budget.
            meta_overlay["replies_to_excerpt"] = replies_to_excerpt[:200]

        out: list[ChunkSpec] = []
        for c in chunks:
            merged = {**meta_overlay, **c.metadata}
            out.append(replace(c, chunk_kind="message", metadata=merged))
        return out


def _strip_quoted_reply(text: str) -> tuple[str, str | None]:
    """Remove quoted-reply tail from an email message body.

    Returns ``(stripped_text, quoted_excerpt_or_None)``. The excerpt
    is the first ~200 chars of what was stripped, suitable for a
    ``replies_to_excerpt`` metadata hint.

    Stripping rule: find the *earliest* match across all quote
    leaders + the first line that starts with ``>``. Everything from
    that point to end-of-string is treated as quoted history.
    """
    if not text:
        return text, None

    earliest = len(text)

    for pat in _QUOTE_LEADER_PATTERNS:
        m = pat.search(text)
        if m and m.start() < earliest:
            earliest = m.start()

    quote_line_match = _QUOTED_LINE.search(text)
    if quote_line_match and quote_line_match.start() < earliest:
        # Walk backward to the start of the line to keep the leader
        # whitespace contiguous with the quoted block.
        line_start = text.rfind("\n", 0, quote_line_match.start())
        earliest = (line_start + 1) if line_start >= 0 else quote_line_match.start()

    if earliest >= len(text):
        return text, None

    stripped = text[:earliest].rstrip()
    excerpt = text[earliest:].strip()
    if not stripped:
        # Whole body was a quote (forwarded message with no
        # commentary). Keep the original — better to embed a quote
        # than nothing.
        return text, None
    return stripped, excerpt or None
