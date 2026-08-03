"""Native-PDF adapter, on pdfplumber.

**Library choice.** pdfplumber (MIT, over pdfminer.six) rather than
PyMuPDF. PyMuPDF is faster and has better layout analysis, and it is
licensed *"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial
License"* — AGPL obligations or a paid licence, neither of which a
commercial product should acquire by way of a connector adapter.
pdfplumber gives text with bounding boxes, table extraction, and image
regions, which is everything this adapter needs.

**This adapter reads the text layer only. It performs no OCR** — no OCR
engine is used anywhere in this pipeline by design. Pages with no text
layer are recorded in ``metadata["empty_pages"]`` and left for the
selective multimodal pass; they are never silently dropped, and they are
never guessed at here.

Heading detection is font-size-based and deliberately conservative: a
line is a heading when its dominant character size is meaningfully larger
than the document's body size, or when it matches a numbered-section
shape (``5.3 Rollback``). False *negatives* cost section granularity;
false positives fragment a procedure into nonsense sections and corrupt
the section path that citations depend on, so the thresholds favour
missing a heading over inventing one.
"""

from __future__ import annotations

import io
import re
import statistics
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

PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
PDF_EXTENSIONS = {".pdf"}

# A line qualifies as a heading when its dominant character height is at
# least this multiple of the document's median body height.
HEADING_SIZE_RATIO = 1.15
# Headings are short. A long line at heading size is a pull-quote or a
# mis-detected body run, not a section title.
MAX_HEADING_CHARS = 160
# Bounded so one pathological upload cannot exhaust a worker.
MAX_PAGES = 500

_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+\S")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-•*•●]|\(?[a-z0-9]{1,3}[.)])\s+\S")


class PdfDocumentParser:
    """Parses a native PDF into structured elements."""

    name = "pdf_native"

    def supports(self, *, filename: str | None, mime_type: str | None) -> bool:
        clean = (mime_type or "").split(";", 1)[0].strip().lower()
        if clean in PDF_MIME_TYPES:
            return True
        return any((filename or "").lower().endswith(ext) for ext in PDF_EXTENSIONS)

    def parse(self, data: bytes, *, filename: str | None = None) -> ParsedDocument:
        import pdfplumber

        elements: list[DocumentElement] = []
        warnings: list[str] = []
        empty_pages: list[int] = []
        sequence = 0
        section_path: list[str] = []
        page_count = 0

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            if page_count > MAX_PAGES:
                warnings.append(
                    f"document has {page_count} pages; parsed the first {MAX_PAGES}"
                )

            body_size = _median_char_size(pdf.pages[:20])

            for page_index, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
                try:
                    page_elements, sequence = self._parse_page(
                        page,
                        page_number=page_index,
                        body_size=body_size,
                        sequence=sequence,
                        section_path=section_path,
                    )
                except Exception as exc:  # noqa: BLE001
                    # One malformed page must not cost the whole document.
                    logger.warning(
                        "document.pdf_page_failed",
                        page=page_index,
                        error_type=type(exc).__name__,
                    )
                    warnings.append(f"page {page_index} could not be parsed")
                    continue

                if not any(
                    e.element_type != ELEMENT_FIGURE and e.text.strip()
                    for e in page_elements
                ):
                    # No text layer. Candidate for the multimodal pass —
                    # recorded, never guessed at, never silently dropped.
                    empty_pages.append(page_index)

                elements.extend(page_elements)

        return ParsedDocument(
            elements=elements,
            page_count=page_count,
            warnings=warnings,
            metadata={
                "parser": self.name,
                "filename": filename,
                "empty_pages": empty_pages,
                "body_font_size": body_size,
            },
        )

    def _parse_page(
        self,
        page: Any,
        *,
        page_number: int,
        body_size: float,
        sequence: int,
        section_path: list[str],
    ) -> tuple[list[DocumentElement], int]:
        """Parse one page into elements, in reading order.

        Everything is collected with its vertical position and sorted
        before sequence numbers are assigned. Emitting tables and figures
        as they are *found* rather than where they *sit* puts a table at
        the top of its page regardless of the section it belongs to —
        which silently detaches it from the procedure step it documents,
        and that step is exactly what a playbook would cite.

        Section path is therefore also resolved in the sorted pass, so a
        table appearing under "3.2 Renew" inherits that path rather than
        whatever heading happened to precede the previous page.
        """
        # (top, x0, kind, payload) — sorted before sequencing.
        staged: list[tuple[float, float, str, Any]] = []

        table_boxes: list[tuple[float, float, float, float]] = []
        for table in page.find_tables():
            rows = table.extract()
            if not rows:
                continue
            bbox = tuple(table.bbox)
            table_boxes.append(bbox)
            # A one-cell box holding a short label is a section header
            # drawn as a table, not tabular data. Measured on a 318-doc
            # KB corpus: 199 of ~213 detected "tables" were exactly this
            # — "Issue:", "Error:", "Solution:", "Steps To Reproduce:".
            # Left as tables, the document's real section structure is
            # invisible: everything collapses under the title and no
            # procedure step is ever attributable to a section.
            label = _single_cell_label(rows)
            if label is not None:
                staged.append((bbox[1], bbox[0], "label_heading", label))
            else:
                staged.append((bbox[1], bbox[0], "table", (bbox, rows)))

        for line in _lines(page):
            # A table's cell text is excluded from the prose pass — a
            # table flattened into paragraphs is worse than no table.
            if _inside_any(line["bbox"], table_boxes):
                continue
            if not line["text"].strip():
                continue
            staged.append((line["bbox"][1], line["bbox"][0], "line", line))

        # Figures are placeholders with coordinates. Interpretation is the
        # later multimodal pass; what matters now is that a procedure
        # saying "configure as shown below" does not read as though
        # nothing followed it.
        for image in getattr(page, "images", [])[:20]:
            bbox = (
                float(image.get("x0", 0)),
                float(image.get("top", 0)),
                float(image.get("x1", 0)),
                float(image.get("bottom", 0)),
            )
            if _area(bbox) < 5_000:
                continue  # icons, bullets, logos
            staged.append((bbox[1], bbox[0], "figure", bbox))

        staged.sort(key=lambda item: (item[0], item[1]))

        elements: list[DocumentElement] = []
        for _top, _x0, kind, payload in staged:
            if kind == "label_heading":
                _push_section(section_path, payload)
                elements.append(
                    DocumentElement(
                        element_type=ELEMENT_HEADING,
                        text=payload,
                        sequence=sequence,
                        page_number=page_number,
                        section_path=list(section_path),
                        extraction_method=EXTRACTION_NATIVE,
                        structured_content={"from_table_cell": True},
                    )
                )
                sequence += 1
            elif kind == "table":
                bbox, rows = payload
                elements.append(
                    DocumentElement(
                        element_type=ELEMENT_TABLE,
                        text=_render_table(rows),
                        sequence=sequence,
                        page_number=page_number,
                        section_path=list(section_path),
                        bounding_box=bbox,
                        extraction_method=EXTRACTION_NATIVE,
                        structured_content={"rows": rows, "row_count": len(rows)},
                    )
                )
            elif kind == "figure":
                elements.append(
                    DocumentElement(
                        element_type=ELEMENT_FIGURE,
                        text="[figure: not yet interpreted]",
                        sequence=sequence,
                        page_number=page_number,
                        section_path=list(section_path),
                        bounding_box=payload,
                        extraction_method=EXTRACTION_NATIVE,
                        confidence=0.0,
                        structured_content={"needs_vision": True},
                    )
                )
            else:
                text = payload["text"].strip()
                if _is_heading(text, payload["size"], body_size):
                    _push_section(section_path, text)
                    element_type = ELEMENT_HEADING
                elif _LIST_ITEM_RE.match(text):
                    element_type = ELEMENT_LIST_ITEM
                    text = _strip_bullet(text)
                else:
                    element_type = ELEMENT_PARAGRAPH
                elements.append(
                    DocumentElement(
                        element_type=element_type,
                        text=text,
                        sequence=sequence,
                        page_number=page_number,
                        section_path=list(section_path),
                        bounding_box=payload["bbox"],
                        extraction_method=EXTRACTION_NATIVE,
                    )
                )
            sequence += 1

        return elements, sequence


# --- helpers ----------------------------------------------------------------


def _median_char_size(pages: list) -> float:
    """Body text size, sampled from the first pages.

    Median rather than mean: headings and footers are outliers, and a
    mean drags the baseline up enough to stop detecting headings at all.
    """
    sizes: list[float] = []
    for page in pages:
        for char in getattr(page, "chars", [])[:2000]:
            size = char.get("size")
            if size:
                sizes.append(float(size))
    return statistics.median(sizes) if sizes else 10.0


def _lines(page: Any) -> list[dict]:
    """Group characters into lines with their dominant font size."""
    chars = getattr(page, "chars", [])
    if not chars:
        return []

    rows: dict[int, list[dict]] = {}
    for char in chars:
        # Round the baseline so characters on the same visual line group
        # together despite sub-point differences.
        key = int(round(float(char.get("top", 0))))
        rows.setdefault(key, []).append(char)

    lines: list[dict] = []
    for key in sorted(rows):
        row = sorted(rows[key], key=lambda c: float(c.get("x0", 0)))
        text = "".join(c.get("text", "") for c in row)
        sizes = [float(c["size"]) for c in row if c.get("size")]
        lines.append(
            {
                "text": text,
                "size": statistics.median(sizes) if sizes else 0.0,
                "bbox": (
                    min(float(c.get("x0", 0)) for c in row),
                    min(float(c.get("top", 0)) for c in row),
                    max(float(c.get("x1", 0)) for c in row),
                    max(float(c.get("bottom", 0)) for c in row),
                ),
            }
        )
    return lines


def _is_heading(text: str, size: float, body_size: float) -> bool:
    if len(text) > MAX_HEADING_CHARS:
        return False
    # A sentence is prose, whatever size it is set in. KB authors
    # routinely emphasise an instruction ("Make username of more than 15
    # character.") at heading size; treating it as a section invents a
    # section named after one instruction, and every following chunk then
    # cites that as its section path.
    if text.rstrip().endswith((".", "!", "?")) and not _NUMBERED_HEADING_RE.match(text):
        return False
    if size >= body_size * HEADING_SIZE_RATIO:
        return True
    # A numbered section is a heading even at body size — plenty of SOPs
    # are written that way, and missing them collapses the whole document
    # into one section.
    return bool(_NUMBERED_HEADING_RE.match(text)) and len(text) <= 80


def _push_section(section_path: list[str], heading: str) -> None:
    """Maintain the heading breadcrumb.

    Depth comes from the numbered prefix when there is one (``5.3`` is
    depth 2), because font size alone cannot distinguish a sub-section
    from a sibling. Unnumbered headings replace the deepest entry rather
    than nesting indefinitely.
    """
    match = _NUMBERED_HEADING_RE.match(heading)
    if match:
        depth = len(match.group(1).split("."))
    else:
        depth = len(section_path) or 1
    del section_path[depth - 1 :]
    section_path.append(heading.strip())


# A label box is short. Anything longer is a one-row data table, or a
# paragraph that happens to sit inside a bordered box.
MAX_LABEL_HEADING_CHARS = 80


def _single_cell_label(rows: list[list]) -> str | None:
    """The heading text of a one-cell label box, or ``None``.

    Requires a single row with exactly one populated cell — a genuine
    one-row table (``["401", "Expired credential", "Rotate"]``) has
    several, and must stay a table.
    """
    if len(rows) != 1:
        return None
    populated = [(cell or "").strip() for cell in rows[0] if (cell or "").strip()]
    if len(populated) != 1:
        return None
    text = " ".join(populated[0].split())
    if not text or len(text) > MAX_LABEL_HEADING_CHARS:
        return None
    # A sentence in a bordered box is content, not a section label.
    # Observed in the corpus: "Make username of more than 15 character."
    # sits in the same box style as "Resolution:", and promoting it to a
    # heading invents a section named after one instruction — which then
    # becomes the section path every following chunk cites.
    if text.endswith((".", "!", "?")) and not text.endswith(".."):
        return None
    return text


_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-•*●]|\(?[a-z0-9]{1,3}[.)])\s+")


def _strip_bullet(text: str) -> str:
    """Drop the source bullet glyph.

    The text renderer re-adds a canonical ``- ``; keeping the original
    produces ``- - item``. The list *item* is what matters downstream,
    not which glyph the author's template used.
    """
    return _BULLET_PREFIX_RE.sub("", text, count=1).strip() or text.strip()


def _render_table(rows: list[list]) -> str:
    """Pipe-rendered table for the text view. The cells survive
    structurally in ``structured_content``; this is for retrieval."""
    out: list[str] = []
    for row in rows[:200]:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _inside_any(
    bbox: tuple[float, float, float, float],
    boxes: list[tuple[float, float, float, float]],
) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)
