"""DOCX adapter, on python-docx (MIT).

Produces the same ``DocumentElement`` rows as the PDF adapter, so
structure-aware chunking, citations, and the figure pass all work
unchanged for Word documents.

**Tracked changes are the reason this is not a five-line wrapper.** A
naive extractor walks the XML and emits every run it finds, which means
a document mid-revision yields BOTH the deleted instruction and the one
that replaced it:

    <w:del>  Restart the database service.
    <w:ins>  Reload the certificate without restarting.

Concatenated, that reads as "restart the database, and also reload
without restarting" — a contradiction that exists nowhere in the
document as any reviewer would see it in Word. Worse, it is a
*procedural* contradiction in a system whose whole job is surfacing
those: it would reach a reviewer as a genuine conflict between the SOP
and practice.

So deletions are dropped and insertions are kept, which is what "accept
all changes" shows and what the document's current normative content
actually is. Deleted text is counted, not silently discarded, because a
document under heavy revision is a signal about its trustworthiness.

Word has no page concept before rendering, so ``page_number`` is None on
every element. Section path still works — it comes from heading styles —
so citations degrade to "§ 5.3 Rollback" rather than "p14 § 5.3", which
is the honest representation.
"""

from __future__ import annotations

import io
import re
from typing import Any

import structlog

from contextedge.services.documents.base import (
    ELEMENT_FIGURE,
    ELEMENT_HEADING,
    ELEMENT_LIST_ITEM,
    ELEMENT_PARAGRAPH,
    ELEMENT_TABLE,
    EXTRACTION_NATIVE,
    DocumentElement,
    ParsedDocument,
)

logger = structlog.get_logger()

DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
DOCX_EXTENSIONS = {".docx"}

MAX_PARAGRAPHS = 20_000
MAX_TABLE_ROWS = 300

_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_LIST_STYLE_RE = re.compile(r"list|bullet", re.IGNORECASE)


class DocxDocumentParser:
    """Parses a .docx into structured elements."""

    name = "docx_native"

    def supports(self, *, filename: str | None, mime_type: str | None) -> bool:
        clean = (mime_type or "").split(";", 1)[0].strip().lower()
        if clean in DOCX_MIME_TYPES:
            return True
        return any((filename or "").lower().endswith(ext) for ext in DOCX_EXTENSIONS)

    def parse(self, data: bytes, *, filename: str | None = None) -> ParsedDocument:
        import docx

        document = docx.Document(io.BytesIO(data))

        elements: list[DocumentElement] = []
        warnings: list[str] = []
        section_path: list[str] = []
        sequence = 0
        deleted_runs = 0
        inserted_runs = 0

        for block in _iter_blocks(document):
            if sequence >= MAX_PARAGRAPHS:
                warnings.append(f"document exceeded {MAX_PARAGRAPHS} blocks; truncated")
                break

            if block["kind"] == "table":
                rows = block["rows"]
                if not rows:
                    continue
                elements.append(
                    DocumentElement(
                        element_type=ELEMENT_TABLE,
                        text=_render_table(rows),
                        sequence=sequence,
                        section_path=list(section_path),
                        extraction_method=EXTRACTION_NATIVE,
                        structured_content={"rows": rows, "row_count": len(rows)},
                    )
                )
                sequence += 1
                continue

            paragraph = block["paragraph"]
            text, stats = _accepted_text(paragraph)
            deleted_runs += stats["deleted"]
            inserted_runs += stats["inserted"]

            if _has_image(paragraph):
                elements.append(
                    DocumentElement(
                        element_type=ELEMENT_FIGURE,
                        text="[figure: not yet interpreted]",
                        sequence=sequence,
                        section_path=list(section_path),
                        extraction_method=EXTRACTION_NATIVE,
                        confidence=0.0,
                        # No bounding box: python-docx does not expose
                        # geometry, so the figure pass cannot crop a
                        # region out of a .docx. Recorded as present so
                        # a procedure saying "as shown below" does not
                        # read as truncated.
                        structured_content={"needs_vision": False, "source": "docx"},
                    )
                )
                sequence += 1

            if not text.strip():
                continue

            style = (getattr(paragraph.style, "name", "") or "").strip()
            heading_level = _heading_level(style)
            if heading_level is not None:
                _push_section(section_path, text, heading_level)
                element_type = ELEMENT_HEADING
            elif _LIST_STYLE_RE.search(style):
                element_type = ELEMENT_LIST_ITEM
            else:
                element_type = ELEMENT_PARAGRAPH

            elements.append(
                DocumentElement(
                    element_type=element_type,
                    text=text,
                    sequence=sequence,
                    section_path=list(section_path),
                    extraction_method=EXTRACTION_NATIVE,
                )
            )
            sequence += 1

        if deleted_runs:
            # Not a warning: dropping deletions is correct behaviour, not
            # a degradation. But a document under heavy revision is worth
            # knowing about when judging whether it is current.
            logger.info(
                "document.docx_tracked_changes",
                filename=filename,
                deleted_runs=deleted_runs,
                inserted_runs=inserted_runs,
            )

        return ParsedDocument(
            elements=elements,
            page_count=0,  # Word has no pages before rendering.
            warnings=warnings,
            metadata={
                "parser": self.name,
                "filename": filename,
                "empty_pages": [],
                "tracked_changes": {
                    "deleted_runs": deleted_runs,
                    "inserted_runs": inserted_runs,
                },
            },
        )


# --- helpers ----------------------------------------------------------------


def _iter_blocks(document: Any):
    """Paragraphs and tables in document order.

    python-docx exposes ``paragraphs`` and ``tables`` as separate lists,
    which loses their interleaving — a table would otherwise land after
    every paragraph regardless of where it sits. Walking the body XML
    keeps reading order, the same property the PDF adapter sorts for.
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield {"kind": "paragraph", "paragraph": Paragraph(child, document)}
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            rows = []
            for row in table.rows[:MAX_TABLE_ROWS]:
                rows.append([cell.text.strip() for cell in row.cells])
            yield {"kind": "table", "rows": rows}


def _accepted_text(paragraph: Any) -> tuple[str, dict[str, int]]:
    """Text as "accept all changes" would render it.

    Runs inside ``<w:del>`` are dropped and runs inside ``<w:ins>`` are
    kept. Emitting both is what turns a revision into a fabricated
    procedural contradiction.
    """
    from docx.oxml.ns import qn

    stats = {"deleted": 0, "inserted": 0}
    parts: list[str] = []

    for node in paragraph._p.iter():
        if node.tag == qn("w:delText"):
            stats["deleted"] += 1
            continue
        if node.tag != qn("w:t"):
            continue
        # Is this run inside a tracked insertion or deletion?
        parent = node.getparent()
        inside_del = False
        while parent is not None:
            if parent.tag == qn("w:del"):
                inside_del = True
                break
            if parent.tag == qn("w:ins"):
                stats["inserted"] += 1
                break
            parent = parent.getparent()
        if inside_del:
            stats["deleted"] += 1
            continue
        parts.append(node.text or "")

    return "".join(parts).strip(), stats


def _has_image(paragraph: Any) -> bool:
    from docx.oxml.ns import qn

    return any(node.tag == qn("a:blip") for node in paragraph._p.iter())


def _heading_level(style_name: str) -> int | None:
    match = _HEADING_STYLE_RE.match(style_name)
    if match:
        return int(match.group(1))
    if style_name.strip().lower() == "title":
        return 1
    return None


def _push_section(section_path: list[str], heading: str, level: int) -> None:
    """Maintain the breadcrumb using Word's own outline level, which is
    more reliable than the PDF adapter's font-size inference."""
    depth = max(1, min(level, 6))
    del section_path[depth - 1 :]
    section_path.append(heading.strip())


def _render_table(rows: list[list[str]]) -> str:
    out: list[str] = []
    for row in rows:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)
