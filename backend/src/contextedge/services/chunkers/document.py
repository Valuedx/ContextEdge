"""Structure-driven chunker for parsed documents (SOPs, runbooks, KB).

Consumes the ``DocumentElement`` rows the document parsers produce
(``services/documents/``) rather than a flat string, so chunk boundaries
follow the author's own sections instead of a character count.

Why that matters concretely: a fixed-size split through a procedure puts
"3. Restart the service" in one chunk and the warning that says not to
restart during an active execution in the next. Retrieval returns one
without the other, and the half it returns is the dangerous half.

The rules, in priority order:

1. **A heading starts a new chunk.** Sections are the unit the author
   chose; splitting across one merges two topics.
2. **A procedure step keeps its figure and its warning.** A screenshot
   or a caution immediately following a step belongs to that step —
   "configure as shown below" is unusable without the image, and a step
   without its warning is unsafe. These attach even when the step is
   already at the size budget.
3. **A large table is its own chunk.** Splitting a table mid-row
   destroys the row-to-header alignment that makes it readable.
4. **Everything else accumulates** to ``CHUNK_TARGET_CHARS`` and flushes.

Falls back to the markdown-heading behaviour of ``AttachmentChunker``
when no structured elements are present — which is the case for evidence
whose body came from somewhere other than a document parser.
"""

from __future__ import annotations

import re
from dataclasses import replace

from contextedge.services.chunkers.base import ChunkSpec
from contextedge.services.chunkers.fallback import CHUNK_TARGET_CHARS, FallbackChunker

# Chunk kinds. These are the vocabulary ``models/evidence.py`` already
# anticipated for structured documents; before this chunker nothing
# produced them.
KIND_HEADING_SECTION = "heading_section"
KIND_PROCEDURE_STEP = "procedure_step"
KIND_WARNING = "warning"
KIND_TABLE = "table"
KIND_FIGURE = "figure"
KIND_CODE_BLOCK = "code_block"

# A table longer than this earns its own chunk rather than riding along
# with surrounding prose.
STANDALONE_TABLE_CHARS = 400

# Deterministic cues only — no model call at chunk time. A false positive
# here mislabels a chunk kind, which biases retrieval; the patterns are
# therefore anchored to the start of the line, where these markers
# actually appear in procedural writing.
_WARNING_RE = re.compile(
    r"^\s*(warning|caution|danger|important|note|attention|do not|never)\b[:!.\s]",
    re.IGNORECASE,
)
# Accepts "1. ", "1) ", "Step 2:" and "Step 2." — the colon form is
# common in real KB articles and was missed by an earlier version that
# required a dot or paren after the digit.
_STEP_RE = re.compile(r"^\s*(?:step\s+)?\d+[.):]\s*\S", re.IGNORECASE)

# Markup, config, and command content. Deterministic and conservative:
# a chunk is code only when most of its lines look like code, so a
# paragraph that mentions "<broker>" in passing stays prose.
_CODE_LINE_RE = re.compile(
    r"""^\s*(
        </?[A-Za-z][\w:.-]*[\s/>]      # XML/HTML tag
        | [A-Za-z_.]+\s*=\s*\S         # key=value config
        | (?:sudo|cd|ls|cp|mv|rm|chmod|chown|systemctl|service|docker|
             kubectl|curl|wget|java|python|pip|npm|git|psql|mysql|export|
             set|net|sc|tasklist|netstat)\s
        | [$#>]\s*\S                   # shell prompt
        | at\s+[\w$.]+\([^)]*\)        # stack frame
        | [{}\[\]();]\s*$              # block punctuation
    )""",
    re.IGNORECASE | re.VERBOSE,
)
CODE_LINE_MAJORITY = 0.6
MIN_CODE_LINES = 2

# Section names that mark a procedure body. A numbered line inside one of
# these is a step; the same shape under "References" is a citation.
# Includes the vocabulary real KB articles actually use — measured on a
# 318-document corpus, where the dominant section labels were "Solution",
# "Resolution", "Issue", "Error", and "Steps To Reproduce" rather than
# the "Procedure" the term implies.
_PROCEDURE_SECTION_RE = re.compile(
    r"\b(procedure|steps?|instructions?|remediation|resolution|solution|"
    r"reproduce|workaround|fix|rollback|verification|configuration)\b",
    re.IGNORECASE,
)


class DocumentChunker:
    """Splits parsed documents on their own structure."""

    name = "document"
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
        elements = _elements_from_payload(payload)
        if not elements:
            # No structured parse available — the body is still prose,
            # and the heading-aware attachment chunker handles it better
            # than a flat recursive split.
            from contextedge.services.chunkers.attachment import AttachmentChunker

            return AttachmentChunker().chunk(title=title, body=body, payload=payload)

        specs: list[ChunkSpec] = []
        buffer: list[dict] = []

        def flush() -> None:
            if not buffer:
                return
            spec = _build_spec(buffer)
            if spec is not None:
                specs.append(spec)
            buffer.clear()

        for element in elements:
            kind = element.get("type") or "paragraph"
            text = (element.get("text") or "").strip()

            if kind == "heading":
                flush()
                buffer.append(element)
                continue

            if kind == "table":
                # Rule 3: a big table stands alone; a small one rides
                # with its surrounding prose, where it usually reads as
                # part of the explanation.
                if len(text) >= STANDALONE_TABLE_CHARS:
                    flush()
                    buffer.append(element)
                    flush()
                else:
                    buffer.append(element)
                continue

            if kind == "figure":
                # Rule 2: never separate a figure from the step above it.
                # An empty buffer means the figure opens a section, which
                # is fine — it still belongs with what follows.
                buffer.append(element)
                continue

            is_step = _is_step(text, element.get("section") or [])
            is_warning = bool(_WARNING_RE.match(text))

            # Rule 2 again: a warning stays with the step it qualifies.
            # A step, however, starts a new chunk once one is already
            # buffered — otherwise a whole procedure collapses into one
            # chunk and retrieval can no longer point at a single step.
            if is_step and _buffer_has_step(buffer):
                flush()

            buffer.append(element)

            if not is_warning and _buffer_chars(buffer) >= CHUNK_TARGET_CHARS:
                flush()

        flush()
        specs = _merge_heading_only(specs)

        if title and specs:
            # Fold the document title into chunk 0 so a title-similarity
            # hit still surfaces the right card, matching the ticket
            # chunker's behaviour.
            first = specs[0]
            specs[0] = replace(first, text=f"{title}\n\n{first.text}".strip())

        return [s for s in specs if s.text.strip()]


# --- helpers ----------------------------------------------------------------


def _elements_from_payload(payload: dict) -> list[dict]:
    """Structured elements stashed on the payload by the re-chunk path.

    The parser writes elements to the artifact's ``parser_metadata``;
    ``synchronize_evidence_artifacts`` copies them onto a payload copy
    under this private key so the chunker stays a pure function of its
    arguments rather than reaching into the database.
    """
    elements = (payload or {}).get("_document_elements")
    if not isinstance(elements, list):
        return []
    return [e for e in elements if isinstance(e, dict)]


def _buffer_chars(buffer: list[dict]) -> int:
    return sum(len(e.get("text") or "") for e in buffer)


def _buffer_has_step(buffer: list[dict]) -> bool:
    return any(
        _is_step((e.get("text") or "").strip(), e.get("section") or [])
        for e in buffer
        if (e.get("type") or "") not in {"heading", "figure", "table"}
    )


def _is_step(text: str, section_path: list) -> bool:
    """A numbered line inside a procedural section.

    The section check matters: "1. RFC 4271" under "References" is a
    citation, not an instruction, and labelling it ``procedure_step``
    would put it in front of an engineer looking for what to do.
    """
    if not _STEP_RE.match(text):
        return False
    if text.lower().startswith("step "):
        return True
    joined = " ".join(str(s) for s in section_path)
    return bool(_PROCEDURE_SECTION_RE.search(joined))


def _classify(buffer: list[dict]) -> str:
    """Chunk kind from the elements it contains, most specific first."""
    types = {e.get("type") for e in buffer}
    texts = [(e.get("text") or "").strip() for e in buffer]
    sections = buffer[0].get("section") or []

    if any(_is_step(t, sections) for t in texts):
        return KIND_PROCEDURE_STEP
    if any(_WARNING_RE.match(t) for t in texts):
        return KIND_WARNING
    if types == {"table"}:
        return KIND_TABLE
    if types and types <= {"figure"}:
        return KIND_FIGURE
    if _is_code(texts):
        return KIND_CODE_BLOCK
    return KIND_HEADING_SECTION


def _is_code(texts: list[str]) -> bool:
    """Most lines look like markup, config, or commands.

    Worth its own kind: a config snippet is a distinct thing to retrieve
    from the prose explaining it, and the two answer different questions
    ("what do I set" vs "why"). Measured on a real KB corpus, config and
    XML blocks were a large share of solution sections.
    """
    lines = [line for t in texts for line in t.splitlines() if line.strip()]
    if len(lines) < MIN_CODE_LINES:
        return False
    hits = sum(1 for line in lines if _CODE_LINE_RE.match(line))
    return hits / len(lines) >= CODE_LINE_MAJORITY


def _merge_heading_only(specs: list[ChunkSpec]) -> list[ChunkSpec]:
    """Fold a heading-only chunk into the one that follows it.

    A section whose heading is immediately followed by another heading —
    common when a document uses a label box as a divider — otherwise
    produces a chunk whose entire content is "How To Reproduce:". That
    has nothing to retrieve on, costs an embedding, and dilutes the
    index. Merging forward keeps the heading as the lead-in of the
    section it introduces, which is where a reader expects it.

    A trailing heading-only chunk has nothing to merge into and is
    dropped.
    """
    out: list[ChunkSpec] = []
    pending: list[str] = []

    for spec in specs:
        if _is_heading_only(spec):
            pending.append(spec.text.strip())
            continue
        if pending:
            spec = replace(spec, text="\n".join([*pending, spec.text]).strip())
            pending.clear()
        out.append(spec)

    return out


def _is_heading_only(spec: ChunkSpec) -> bool:
    """A chunk whose whole text is its own section heading."""
    text = spec.text.strip()
    if not text or "\n" in text:
        return False
    return bool(spec.parent_section) and spec.parent_section.endswith(text)


def _build_spec(buffer: list[dict]) -> ChunkSpec | None:
    texts = [(e.get("text") or "").strip() for e in buffer]
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        return None

    pages = sorted({e["page"] for e in buffer if e.get("page") is not None})
    section_path = buffer[0].get("section") or []
    methods = sorted({e.get("method") for e in buffer if e.get("method")})

    metadata: dict = {}
    if pages:
        metadata["page"] = pages[0]
        if len(pages) > 1:
            metadata["page_range"] = [pages[0], pages[-1]]
    if section_path:
        metadata["section_path"] = list(section_path)
    if methods:
        # A chunk containing model-transcribed content must be
        # distinguishable from a wholly parsed one: one is exact, the
        # other is a paraphrase, and a reviewer weighing a citation
        # needs to know which they are reading.
        metadata["extraction_methods"] = methods
    if any((e.get("structured") or {}).get("needs_vision") for e in buffer):
        metadata["needs_vision"] = True
    if any(e.get("type") == "figure" for e in buffer):
        metadata["has_figure"] = True

    return ChunkSpec(
        text=text,
        chunk_kind=_classify(buffer),
        parent_section=" > ".join(str(s) for s in section_path) or None,
        metadata=metadata,
    )
