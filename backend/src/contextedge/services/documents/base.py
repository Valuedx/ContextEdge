"""Structured document elements — the shape every parser produces.

This abstraction exists because the alternative was explicitly rejected:
bolting a PDF-to-text call into the attachment merger. That path flattens
a 60-page SOP into one evidence body under a 16 KB cap, which loses page
provenance, table structure, and every screenshot — and leaves nothing to
cite when a playbook step needs to point at "page 14, section 5.3".

A parser's job is to produce ``DocumentElement`` rows. It is NOT to
produce text. Text is one rendering of the elements, generated for the
existing ``extracted_text`` field so the current pipeline keeps working;
the elements are the durable artifact that structure-aware chunking,
step-level citations, and figure interpretation all build on.

Design constraints that shaped this:

- **Ordering must survive.** ``sequence`` is a document-wide running
  index, not a per-page one, so a caller can reconstruct reading order
  without sorting on ``(page, y, x)`` and guessing at multi-column
  layouts.
- **Provenance must be precise enough to open.** Page plus bounding box
  means a reviewer can be taken to the exact region a claim came from.
  A citation to "the SOP" is not reviewable.
- **Extraction method must be recorded per element**, not per document.
  A hybrid PDF has native text on one page and a scanned appendix on the
  next; a model-transcribed element must never be indistinguishable from
  a parsed one, because one is exact and the other is a paraphrase.
- **Confidence is per element** for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# What produced an element's content. Kept explicit so a downstream
# consumer can weight, or refuse, model-derived content.
#
# ``native`` — parsed from the document's own text/layout layer. Exact.
# ``vision`` — read by a multimodal model (figures, or pages with no text
#              layer). A paraphrase, not a transcript. No OCR engine is
#              used anywhere: where a scan must be read, the multimodal
#              model reads it and the result is marked ``vision``.
# ``derived`` — computed by us (e.g. a table rendered to text).
EXTRACTION_NATIVE = "native"
EXTRACTION_VISION = "vision"
EXTRACTION_DERIVED = "derived"

# Element kinds. Deliberately close to the ``chunk_kind`` vocabulary the
# evidence model already anticipates (``heading_section``, ``code_block``,
# ``ocr_text``) so structure-driven chunking is a mapping rather than a
# translation.
ELEMENT_HEADING = "heading"
ELEMENT_PARAGRAPH = "paragraph"
ELEMENT_LIST_ITEM = "list_item"
ELEMENT_TABLE = "table"
ELEMENT_FIGURE = "figure"
ELEMENT_CAPTION = "caption"
ELEMENT_CODE = "code_block"
ELEMENT_PAGE_BREAK = "page_break"


@dataclass(slots=True)
class DocumentElement:
    """One structural unit of a parsed document."""

    element_type: str
    text: str
    sequence: int
    page_number: int | None = None
    # Heading path at this point in the document, outermost first:
    # ``["5. Certificate Renewal", "5.3 Rollback"]``. This is what makes
    # a chunk citable as a section rather than as a character offset.
    section_path: list[str] = field(default_factory=list)
    # (x0, top, x1, bottom) in PDF points, origin top-left.
    bounding_box: tuple[float, float, float, float] | None = None
    extraction_method: str = EXTRACTION_NATIVE
    confidence: float = 1.0
    # Table cells, figure metadata, or anything a parser knows that the
    # flat text loses.
    structured_content: dict[str, Any] = field(default_factory=dict)

    def section_ref(self) -> str:
        """Human-readable citation target, e.g. ``"p14 § 5 > 5.3"``."""
        parts: list[str] = []
        if self.page_number is not None:
            parts.append(f"p{self.page_number}")
        if self.section_path:
            parts.append("§ " + " > ".join(self.section_path))
        return " ".join(parts)


@dataclass(slots=True)
class ParsedDocument:
    """A parser's full output for one file."""

    elements: list[DocumentElement]
    page_count: int = 0
    # Populated when the parser could not fully read the document. A
    # partially-parsed document is still useful, but a caller assessing
    # completeness must be able to tell that pages were missed rather
    # than that the document was short.
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return not self.warnings

    def text_pages_missing(self) -> list[int]:
        """Pages that yielded no native text.

        These are the candidates for multimodal reading — a page with no
        text layer is either blank or an image, and only a model can tell
        which. Surfaced here so the vision pass can be *selective*:
        sending every page of every upload to a vision model is what
        makes this pipeline unaffordable at the corpus size where it
        starts being useful.
        """
        return sorted(self.metadata.get("empty_pages", []))


@runtime_checkable
class DocumentParser(Protocol):
    """Adapter contract. One implementation per document family."""

    name: str

    def supports(self, *, filename: str | None, mime_type: str | None) -> bool:
        ...

    def parse(self, data: bytes, *, filename: str | None = None) -> ParsedDocument:
        ...


def render_elements_to_text(
    elements: list[DocumentElement], *, max_chars: int | None = None
) -> str:
    """Flatten elements into heading-preserving text.

    Produces the ``extracted_text`` the existing attachment pipeline
    stores, so documents become searchable the moment a parser lands —
    without waiting for structure-aware chunking. Headings render as
    markdown ``#`` so the document chunker splits on the author's own
    sections, the same contract the Zoho KB body conversion follows.
    """
    out: list[str] = []
    for element in elements:
        text = (element.text or "").strip()
        if not text:
            continue
        if element.element_type == ELEMENT_HEADING:
            depth = max(1, min(len(element.section_path) or 1, 6))
            out.append(f"\n{'#' * depth} {text}\n")
        elif element.element_type == ELEMENT_LIST_ITEM:
            out.append(f"- {text}")
        elif element.element_type == ELEMENT_TABLE:
            out.append(f"\n{text}\n")
        elif element.element_type == ELEMENT_FIGURE:
            # A figure with no interpretation yet still marks its place,
            # so a procedure that says "configure as shown below" does
            # not read as if nothing followed it.
            label = text or "[figure]"
            out.append(f"\n{label}\n")
        else:
            out.append(text)

    rendered = "\n".join(out)
    # Collapse the blank-line runs the block spacing above introduces.
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    rendered = rendered.strip()

    if max_chars is not None and len(rendered) > max_chars:
        cut = rendered[:max_chars]
        boundary = cut.rfind("\n")
        rendered = (cut[:boundary] if boundary > max_chars // 2 else cut).rstrip()
    return rendered
