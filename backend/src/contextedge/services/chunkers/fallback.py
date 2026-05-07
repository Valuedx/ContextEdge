"""Recursive prose splitter — the workhorse chunker.

Used directly when no source-specific chunker matches, and used as a
delegate by the source-specific chunkers (ticket / thread /
attachment) when their input is unstructured prose past the chunk-size
budget.

Strategy: descend the natural-boundary hierarchy (paragraph break →
single newline → sentence terminator → hard split) until each piece
is below the target size. Each emitted chunk overlaps the previous by
``CHUNK_OVERLAP_CHARS`` so a sentence that crosses a boundary remains
recoverable on either side.

Design notes:

- Character-based, not token-based. We don't run the tokenizer at
  chunk time because (a) we'd be guessing the embedding model's
  tokenizer (Vertex / Anthropic / OpenAI all differ), and (b) we
  defer all token cost to the embed batch task. ~1500 chars maps to
  ~300–400 tokens for English prose, which is the sweet spot for the
  3072-dim embedding family — small enough that a single specific
  fact stays focused, large enough that surrounding context comes
  along.
- No NLP dependency. ``re`` is the only import. Sentence splitting
  is heuristic ("``.``/``!``/``?`` followed by whitespace then a
  capital"); good enough for the long tail and explicitly resilient
  to log noise / config files / pasted code where real sentence
  boundaries don't apply.
- Empty / whitespace input returns ``[]`` — the caller handles that.

See ``codewiki/CHUNKING_DESIGN.md`` for the per-source strategy table.
"""

from __future__ import annotations

import re

from contextedge.services.chunkers.base import ChunkSpec, Chunker


CHUNK_TARGET_CHARS = 1500
"""Soft target. Chunks may be slightly larger if no clean boundary exists."""

CHUNK_OVERLAP_CHARS = 150
"""Overlap between adjacent chunks so a fact at a boundary is recoverable."""

# Sentence terminator + whitespace + capital letter / digit / quote.
# Heuristic — does not use NLP. Catches "End of sentence. Next one."
# but avoids splitting "v1.2.3" or "10:42 a.m. UTC" mid-token.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\(\[])")


class FallbackChunker:
    """Recursive prose splitter.

    Pure function. No I/O, no DB, no LLM.
    """

    name = "fallback"
    version = 1

    def chunk(
        self,
        *,
        title: str | None,
        body: str | None,
        payload: dict,
    ) -> list[ChunkSpec]:
        """Split ``body`` into chunks; ``title`` is folded into chunk 0.

        ``payload`` is unused here but kept on the signature so the
        Chunker Protocol is uniform across implementations.
        """
        text = _compose(title, body)
        if not text.strip():
            return []

        # Descend the boundary hierarchy. Each level returns spans
        # (offset_start, offset_end, content) so we keep char offsets
        # accurate against the original body for citation back into
        # the parent EvidenceItem.
        spans = _split_recursive(text)

        chunks: list[ChunkSpec] = []
        for offset_start, offset_end, piece in spans:
            chunks.append(
                ChunkSpec(
                    text=piece,
                    chunk_kind="body",
                    char_offset_start=offset_start,
                    char_offset_end=offset_end,
                )
            )
        return chunks


def _compose(title: str | None, body: str | None) -> str:
    """Fold title into the body text so chunk 0 carries the title."""
    parts: list[str] = []
    if title and title.strip():
        parts.append(title.strip())
    if body and body.strip():
        parts.append(body)
    return "\n\n".join(parts)


def split_for_overlap(text: str) -> list[tuple[int, int, str]]:
    """Public entry-point used by other chunkers.

    Returns (offset_start, offset_end, content) for each chunk.
    """
    return _split_recursive(text)


def _split_recursive(text: str) -> list[tuple[int, int, str]]:
    """Recursive splitter on paragraph → line → sentence → hard.

    Returns a list of (offset_start, offset_end, content). Offsets are
    against the *input* text, not the original raw payload; callers
    that need raw-payload offsets must compose.
    """
    if len(text) <= CHUNK_TARGET_CHARS:
        return [(0, len(text), text)]

    # Try paragraph boundaries first. Anything still oversize after
    # paragraph split recurses to single-newline split, then sentence
    # split, then hard split as the floor.
    spans = _split_by_separator(text, sep="\n\n")
    spans = _expand_oversize(spans, sep="\n")
    spans = _expand_oversize(spans, sep=None, sentence_split=True)
    spans = _expand_oversize(spans, sep=None, hard_split=True)

    return _add_overlap(spans)


def _split_by_separator(text: str, *, sep: str) -> list[tuple[int, int, str]]:
    """Split ``text`` on ``sep`` into spans with original offsets.

    Empty pieces are dropped. Separator characters are kept on the
    *left* of the boundary so trailing whitespace doesn't show up at
    the start of a chunk.
    """
    if not text:
        return []
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(text):
        idx = text.find(sep, cursor)
        if idx < 0:
            piece = text[cursor:]
            if piece.strip():
                spans.append((cursor, len(text), piece))
            break
        end = idx + len(sep)
        piece = text[cursor:end]
        if piece.strip():
            spans.append((cursor, end, piece))
        cursor = end
    return spans


def _split_by_sentence(text: str, base_offset: int) -> list[tuple[int, int, str]]:
    """Heuristic sentence split.

    Splits at sentence-terminator + whitespace + capital pattern.
    Single-sentence inputs return a single span unchanged.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for match in _SENTENCE_BREAK.finditer(text):
        end = match.end()
        piece = text[cursor:end]
        if piece.strip():
            spans.append((base_offset + cursor, base_offset + end, piece))
        cursor = end
    tail = text[cursor:]
    if tail.strip():
        spans.append((base_offset + cursor, base_offset + len(text), tail))
    return spans


def _expand_oversize(
    spans: list[tuple[int, int, str]],
    *,
    sep: str | None,
    sentence_split: bool = False,
    hard_split: bool = False,
) -> list[tuple[int, int, str]]:
    """Expand any span over the target size using the next finer split."""
    out: list[tuple[int, int, str]] = []
    for start, end, piece in spans:
        if len(piece) <= CHUNK_TARGET_CHARS:
            out.append((start, end, piece))
            continue
        if sentence_split:
            sub = _split_by_sentence(piece, base_offset=start)
            # If sentence split couldn't help (no sentence boundaries),
            # fall through to caller's next pass.
            if len(sub) > 1:
                out.extend(sub)
                continue
            out.append((start, end, piece))
            continue
        if hard_split:
            out.extend(_hard_split(piece, base_offset=start))
            continue
        if sep is None:
            out.append((start, end, piece))
            continue
        sub = _split_by_separator(piece, sep=sep)
        # Re-base offsets to the original text.
        out.extend([(start + a, start + b, p) for (a, b, p) in sub])
    return out


def _hard_split(text: str, base_offset: int) -> list[tuple[int, int, str]]:
    """Fixed-size split as the last-resort floor.

    Reached when paragraph / line / sentence boundaries all failed to
    bring the piece under the target — typically a single very long
    line in a config dump or minified file.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    step = CHUNK_TARGET_CHARS
    while cursor < len(text):
        end = min(cursor + step, len(text))
        spans.append((base_offset + cursor, base_offset + end, text[cursor:end]))
        cursor = end
    return spans


def _add_overlap(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Add ``CHUNK_OVERLAP_CHARS`` of leading context to each chunk past 0.

    Overlap is a *prefix* taken from the prior chunk's tail, not
    a re-slice of the input — keeps boundary phrases recoverable from
    either side without doubling storage. Offsets reflect the
    overlapped span so citation back to the parent body still works.
    """
    if len(spans) <= 1:
        return spans
    out: list[tuple[int, int, str]] = [spans[0]]
    for i in range(1, len(spans)):
        start, end, piece = spans[i]
        prev_start, prev_end, prev_piece = spans[i - 1]
        overlap_start = max(prev_start, prev_end - CHUNK_OVERLAP_CHARS)
        prefix = prev_piece[-(prev_end - overlap_start):]
        if prefix:
            piece = prefix + piece
            start = overlap_start
        out.append((start, end, piece))
    return out
