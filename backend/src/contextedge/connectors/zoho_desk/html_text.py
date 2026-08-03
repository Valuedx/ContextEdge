"""HTML → heading-preserving plain text, on the standard library only.

Zoho Desk returns rich text as HTML: KB article bodies (``answer``) are
full WYSIWYG documents with heading hierarchies, tables, and inline
images, and ticket thread bodies are HTML email. Everything downstream
of ingest — the classifier, the embedder, the chunkers — reads
``body_text``, so the HTML has to become text exactly once, here.

Two properties matter more than fidelity:

1. **Headings survive as ``#`` markers.** ``chunkers/attachment.py``
   splits markdown on heading boundaries and records the heading path as
   ``parent_section``. A KB article flattened to undifferentiated prose
   loses that structure and chunks on character count instead of on the
   author's own section boundaries — which is the difference between
   retrieving "Resolution" and retrieving the back half of "Symptoms"
   glued to the front half of "Resolution". Verified against the live
   instance: article bodies are ``<h3 class="toc_anchors">``-structured.
2. **No new dependency.** ``bs4``/``lxml`` are not in
   ``pyproject.toml`` and a connector is a bad reason to add a parser to
   every deployment. ``html.parser`` is in the standard library and is
   lenient about the malformed markup that pasted email produces.

Deliberately lossy: styling, classes, scripts, and ``<img>`` binaries
are dropped. Images become ``[image: alt]`` placeholders so a body that
is mostly screenshots does not silently normalize to an empty string —
an empty body is indistinguishable from a fetch failure downstream.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags whose *content* is machinery, not prose.
_DROP_CONTENT = frozenset({"script", "style", "head", "noscript", "template"})

_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

# Tags that end the current line without starting a titled block.
_BREAK_TAGS = frozenset(
    {"p", "div", "br", "tr", "section", "article", "blockquote", "pre", "hr"}
)

MAX_OUTPUT_CHARS = 200_000


class _TextExtractor(HTMLParser):
    """Streaming HTML → markdown-ish text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress_depth = 0
        self._list_stack: list[str] = []
        # Heading text is buffered so the "### " prefix and the text land
        # on one line even when the heading contains nested inline spans.
        self._heading: str | None = None

    # --- emit helpers ---------------------------------------------------

    def _emit(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def _newline(self, count: int = 1) -> None:
        self._parts.append("\n" * count)

    # --- HTMLParser hooks -----------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_CONTENT:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return

        if tag in _HEADINGS:
            self._newline(2)
            self._emit(_HEADINGS[tag] + " ")
            self._heading = ""
            return

        if tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._newline()
            return

        if tag == "li":
            self._newline()
            depth = max(len(self._list_stack) - 1, 0)
            self._emit("  " * depth + "- ")
            return

        if tag in ("td", "th"):
            # Cell separator; the row break comes from <tr>.
            if self._parts and not self._parts[-1].endswith(("\n", "| ")):
                self._emit(" | ")
            return

        if tag == "img":
            alt = ""
            for key, value in attrs:
                if key == "alt" and value:
                    alt = value.strip()
            self._emit(f"[image: {alt}]" if alt else "[image]")
            return

        if tag in _BREAK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth:
            return

        if tag in _HEADINGS:
            self._heading = None
            self._newline()
            return

        if tag in ("ul", "ol") and self._list_stack:
            self._list_stack.pop()
            self._newline()
            return

        if tag in _BREAK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._suppress_depth or not data:
            return
        if self._heading is not None:
            # Collapse newlines inside a heading so the "#" prefix keeps
            # its line — a heading broken across lines stops being one.
            self._emit(" ".join(data.split()))
            return
        self._emit(data)

    def text(self) -> str:
        return "".join(self._parts)


# Runs of blank lines collapse to exactly one blank line; trailing
# whitespace on a line is noise the embedder pays for.
_TRAILING_WS_RE = re.compile(r"[ \t]+(\n|$)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")

# A heading marker with no actual heading text after it. Zoho's editor
# produces these routinely — the live KB opens articles with an <h3>
# wrapping only a decorative banner image, which yields either a bare
# "###" or "### [image]" depending on whether the editor also nested a
# block element inside. Both are dropped: left in, they read as section
# boundaries to the chunker and as noise to the embedder.
_EMPTY_HEADING_RE = re.compile(
    r"^#{1,6}[ \t]*(?:\[image[^\]]*\][ \t]*)*$\n?", re.MULTILINE
)


def html_to_text(value: object, *, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Convert an HTML fragment to heading-preserving plain text.

    Non-string input returns ``""``. Input with no tags is returned
    whitespace-normalized rather than round-tripped through the parser,
    so a body that was already plain text is not reformatted.

    Truncates at ``max_chars`` on a line boundary. A KB article an order
    of magnitude past the chunker's budget is a document dump; the
    chunker would cap the useful part anyway, and carrying the rest
    costs storage and redaction time for no retrieval gain.
    """
    if not isinstance(value, str) or not value.strip():
        return ""

    if "<" not in value:
        return _tidy(value, max_chars)

    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must not fail ingest
        # Whatever was parsed before the failure is still better than
        # dropping the body; fall through to tidying what we have.
        pass
    return _tidy(parser.text(), max_chars)


def _tidy(text: str, max_chars: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = _SPACE_RUN_RE.sub(" ", text)
    text = _TRAILING_WS_RE.sub(r"\1", text)
    text = _EMPTY_HEADING_RE.sub("", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = cut.rfind("\n")
    return (cut[:boundary] if boundary > max_chars // 2 else cut).rstrip()
