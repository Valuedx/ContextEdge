"""Chunker for attachment evidences (runbooks, post-mortems, logs).

Attachments are the high-variance source: a 200 KB markdown
post-mortem, a 5 MB log file, a stack trace inside a code review.
The fallback recursive splitter does fine on flat prose but loses
boundary structure that is exactly what makes attachments useful — a
heading hierarchy, a per-event log boundary, a per-symbol code
boundary.

The chunker dispatches on a *content kind* sniffed from payload
metadata (mime type, filename) and the leading bytes of the body:

| Kind detected | Strategy | Chunk kind | parent_section |
| --- | --- | --- | --- |
| ``markdown``  | split on heading boundaries (`#`/`##`/...); chunks ~300–500 tokens | ``heading_section`` | breadcrumb of heading path |
| ``log_jsonl`` | one chunk per JSON-line; window N consecutive lines if individual lines are tiny | ``log_event`` | timestamp range |
| ``log_plain`` | split on log-event boundaries: timestamp prefix or `WARN`/`ERROR`/`INFO` markers | ``log_event`` | timestamp prefix |
| anything else | delegate to ``FallbackChunker`` | ``body`` | none |

Code attachments via tree-sitter are deferred — see
``codewiki/CHUNKING_DESIGN.md`` §"What's not in this design".

Detection rules favour false-fallback over false-positive: if we're
not confident the input is markdown / log / etc., we fall through to
the recursive splitter. Recursive splitting is always *correct* on
prose; aggressive heading detection on a non-markdown body would
produce nonsense chunks.
"""

from __future__ import annotations

import re
from dataclasses import replace

from contextedge.services.chunkers.base import ChunkSpec, Chunker
from contextedge.services.chunkers.fallback import (
    CHUNK_TARGET_CHARS,
    FallbackChunker,
)


# Markdown heading: 1-6 ``#`` at line start + space + text.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# JSON-line: line starts with ``{`` and ends with ``}``. Cheap pre-check
# before attempting json.loads — we don't care if it's *valid* JSON,
# only whether the per-line shape suggests JSON-lines.
_JSONL_LINE = re.compile(r"^\s*\{.*\}\s*$")

# Common log timestamp leaders. We don't try to be exhaustive — these
# cover ISO-8601, syslog, and the `[YYYY-MM-DD HH:MM:SS]` bracketed
# variant that journald / Docker / many app loggers use. Anything not
# matched falls through to the recursive splitter.
_LOG_TS_PATTERNS = (
    # 2026-05-08T14:32:15.123Z  or  2026-05-08 14:32:15
    re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", re.MULTILINE),
    # May  8 14:32:15  (syslog)
    re.compile(
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}",
        re.MULTILINE,
    ),
    # [2026-05-08 14:32:15]
    re.compile(r"^\[\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", re.MULTILINE),
)


# Heuristic confidence thresholds. ``MIN_LOG_TS_HITS`` is the minimum
# number of timestamp matches in the first 4 KB of the body before we
# trust the input is a log file. Below this, recursive splitting wins.
MIN_LOG_TS_HITS = 5
MIN_JSONL_LINE_RATIO = 0.7
SNIFF_BYTES = 4096


class AttachmentChunker:
    """Dispatches to per-kind splitter; falls back to recursive splitter."""

    name = "attachment"
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
        if not (body or "").strip():
            return []

        kind = _sniff_kind(body or "", payload or {})

        if kind == "markdown":
            return _chunk_markdown(title=title, body=body or "")
        if kind == "log_jsonl":
            return _chunk_log_jsonl(title=title, body=body or "")
        if kind == "log_plain":
            return _chunk_log_plain(title=title, body=body or "")

        # Default: recursive splitter. Stamp metadata so observability
        # can see how often we fell through.
        chunks = self._fallback.chunk(title=title, body=body, payload=payload)
        return [
            replace(c, metadata={**c.metadata, "attachment_kind": "prose"})
            for c in chunks
        ]


def _sniff_kind(body: str, payload: dict) -> str:
    """Return one of ``markdown`` | ``log_jsonl`` | ``log_plain`` | ``prose``.

    Resolution order: payload metadata first (mime type, filename
    extension), then content sniff over the first ``SNIFF_BYTES``.
    """
    mime = (payload.get("mime_type") or payload.get("content_type") or "").lower()
    filename = (payload.get("filename") or payload.get("file_name") or "").lower()

    if "markdown" in mime or filename.endswith((".md", ".markdown")):
        return "markdown"
    if filename.endswith((".jsonl", ".ndjson")) or "ndjson" in mime:
        return "log_jsonl"
    if filename.endswith((".log", ".syslog")) or "log" in mime:
        # Could be JSONL-formatted logs; sniff body to decide.
        head = body[:SNIFF_BYTES]
        if _looks_like_jsonl(head):
            return "log_jsonl"
        return "log_plain"

    head = body[:SNIFF_BYTES]
    if _looks_like_markdown(head):
        return "markdown"
    if _looks_like_jsonl(head):
        return "log_jsonl"
    if _looks_like_plain_log(head):
        return "log_plain"
    return "prose"


def _looks_like_markdown(head: str) -> bool:
    """At least 2 heading-like lines in the first sniff window."""
    return len(_MD_HEADING.findall(head)) >= 2


def _looks_like_jsonl(head: str) -> bool:
    """Most non-blank lines look like JSON objects."""
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    hits = sum(1 for ln in lines if _JSONL_LINE.match(ln))
    return (hits / len(lines)) >= MIN_JSONL_LINE_RATIO


def _looks_like_plain_log(head: str) -> bool:
    """Enough timestamp-led lines to call this a log file."""
    total_hits = sum(len(p.findall(head)) for p in _LOG_TS_PATTERNS)
    return total_hits >= MIN_LOG_TS_HITS


def _chunk_markdown(*, title: str | None, body: str) -> list[ChunkSpec]:
    """Split markdown on heading boundaries with section breadcrumbs."""
    headings = list(_MD_HEADING.finditer(body))
    if not headings:
        # Markdown with no headings — defer to recursive splitter.
        return _fallback_with_kind(title=title, body=body, kind="prose")

    chunks: list[ChunkSpec] = []
    breadcrumb: list[str] = []
    last_end = 0

    # Emit the pre-heading prelude (if any) as its own chunk so
    # frontmatter / lede paragraphs stay searchable.
    first = headings[0]
    if first.start() > 0:
        prelude = body[: first.start()].strip()
        if prelude:
            chunks.append(
                ChunkSpec(
                    text=_compose(title, prelude),
                    chunk_kind="heading_section",
                    char_offset_start=0,
                    char_offset_end=first.start(),
                    parent_section=None,
                    metadata={"attachment_kind": "markdown"},
                )
            )

    for i, h in enumerate(headings):
        level = len(h.group(1))
        heading_text = h.group(2).strip()
        # Trim breadcrumb to the parent of this level.
        breadcrumb = breadcrumb[: max(0, level - 1)]
        breadcrumb.append(heading_text)
        section_path = " > ".join(breadcrumb) if breadcrumb else heading_text

        section_start = h.end()
        section_end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        section_body = body[section_start:section_end].strip()
        if not section_body:
            continue
        # If the section is oversized, sub-split it on paragraph
        # boundaries inside this heading rather than letting it become
        # one giant chunk.
        for sub_start, sub_end, sub_text in _maybe_split(section_body):
            chunks.append(
                ChunkSpec(
                    text=sub_text,
                    chunk_kind="heading_section",
                    char_offset_start=section_start + sub_start,
                    char_offset_end=section_start + sub_end,
                    parent_section=section_path,
                    metadata={
                        "attachment_kind": "markdown",
                        "heading_level": level,
                    },
                )
            )
        last_end = section_end

    # Trailing content past the last heading match (rare).
    if last_end < len(body):
        tail = body[last_end:].strip()
        if tail:
            chunks.append(
                ChunkSpec(
                    text=tail,
                    chunk_kind="heading_section",
                    char_offset_start=last_end,
                    char_offset_end=len(body),
                    parent_section=" > ".join(breadcrumb) if breadcrumb else None,
                    metadata={"attachment_kind": "markdown"},
                )
            )
    return chunks


def _chunk_log_jsonl(*, title: str | None, body: str) -> list[ChunkSpec]:
    """One chunk per JSON-line; small lines are windowed together.

    The windowing rule keeps each chunk close to the size budget so
    we don't pay an embedding call per line of a 100K-line log.
    """
    lines = body.splitlines(keepends=True)
    chunks: list[ChunkSpec] = []
    buffer: list[str] = []
    buffer_chars = 0
    buffer_start = 0
    cursor = 0

    def flush():
        nonlocal buffer, buffer_chars, buffer_start
        if buffer:
            chunks.append(
                ChunkSpec(
                    text="".join(buffer).rstrip(),
                    chunk_kind="log_event",
                    char_offset_start=buffer_start,
                    char_offset_end=buffer_start + buffer_chars,
                    metadata={"attachment_kind": "log_jsonl"},
                )
            )
            buffer = []
            buffer_chars = 0

    for line in lines:
        if not buffer:
            buffer_start = cursor
        buffer.append(line)
        buffer_chars += len(line)
        cursor += len(line)
        if buffer_chars >= CHUNK_TARGET_CHARS:
            flush()
    flush()
    return chunks


def _chunk_log_plain(*, title: str | None, body: str) -> list[ChunkSpec]:
    """Split plain-text logs at timestamp boundaries.

    The boundaries are anchors — content from one timestamp to the
    next-but-one belongs to that event. Stack traces (which span
    several lines without their own timestamp) stay attached to the
    timestamp that introduced them.
    """
    boundaries = sorted(
        {m.start() for pat in _LOG_TS_PATTERNS for m in pat.finditer(body)}
    )
    if not boundaries:
        return _fallback_with_kind(title=title, body=body, kind="log_plain_unstructured")

    boundaries.append(len(body))
    chunks: list[ChunkSpec] = []
    buffer_start = boundaries[0]
    buffer_end = buffer_start

    # Group consecutive small events into one chunk under the size budget.
    cursor = 0
    while cursor < len(boundaries) - 1:
        start = boundaries[cursor]
        end = boundaries[cursor + 1]
        size = end - buffer_start
        if size >= CHUNK_TARGET_CHARS or end == boundaries[-1]:
            text = body[buffer_start:end].rstrip()
            if text:
                chunks.append(
                    ChunkSpec(
                        text=text,
                        chunk_kind="log_event",
                        char_offset_start=buffer_start,
                        char_offset_end=end,
                        metadata={"attachment_kind": "log_plain"},
                    )
                )
            buffer_start = end
        cursor += 1
    return chunks


def _maybe_split(text: str) -> list[tuple[int, int, str]]:
    """If the text fits, return it as one span; else hand to the splitter."""
    if len(text) <= CHUNK_TARGET_CHARS:
        return [(0, len(text), text)]
    from contextedge.services.chunkers.fallback import split_for_overlap

    return split_for_overlap(text)


def _compose(title: str | None, body: str) -> str:
    if title and title.strip():
        return f"{title.strip()}\n\n{body}"
    return body


def _fallback_with_kind(*, title: str | None, body: str, kind: str) -> list[ChunkSpec]:
    fb = FallbackChunker()
    chunks = fb.chunk(title=title, body=body, payload={})
    return [
        replace(c, metadata={**c.metadata, "attachment_kind": kind})
        for c in chunks
    ]
