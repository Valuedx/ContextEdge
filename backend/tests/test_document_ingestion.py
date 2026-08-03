"""Phase 4a: structured document ingestion.

The PDF tests build real PDFs with reportlab rather than checking in
fixtures, so the assertions run against actual pdfplumber output. They
skip cleanly when the optional document extras are absent — a base
install must still pass.
"""

from __future__ import annotations

import io

import pytest

from contextedge.services.artifact_extraction_service import (
    DOCUMENT_PARSER_TYPES,
    MAX_ATTACHMENT_TEXT_CHARS,
    MAX_DOCUMENT_TEXT_CHARS,
    build_combined_evidence_body,
    extract_artifact_text,
)
from contextedge.services.documents import (
    DocumentElement,
    render_elements_to_text,
)
from contextedge.services.documents.base import (
    ELEMENT_FIGURE,
    ELEMENT_HEADING,
    ELEMENT_LIST_ITEM,
    ELEMENT_PARAGRAPH,
    ELEMENT_TABLE,
    EXTRACTION_NATIVE,
    EXTRACTION_VISION,
    ParsedDocument,
)
from contextedge.services.documents.registry import get_parser

pdfplumber = pytest.importorskip("pdfplumber", reason="document extras not installed")
reportlab = pytest.importorskip("reportlab", reason="reportlab needed to build fixtures")

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

W, H = A4


def _sop_pdf(*, with_blank_image_page: bool = True) -> bytes:
    """A realistic SOP: numbered sections, nested sub-sections, a list,
    a table, and (optionally) an image-only page with no text layer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    y = H - 30 * mm
    c.setFont("Helvetica-Bold", 22)
    c.drawString(25 * mm, y, "Acme VPN Certificate Renewal SOP")
    y -= 15 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(25 * mm, y, "1. Purpose")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    c.drawString(25 * mm, y, "Renew the VPN gateway certificate without downtime.")
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(25 * mm, y, "2. Prerequisites")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    for item in ("- Change ticket approved", "- Certificate backed up"):
        c.drawString(25 * mm, y, item)
        y -= 6 * mm
    c.showPage()

    y = H - 30 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(25 * mm, y, "3. Procedure")
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(25 * mm, y, "3.1 Back up the certificate")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawString(25 * mm, y, "Export the current certificate first.")
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(25 * mm, y, "3.2 Renew")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawString(25 * mm, y, "Request the replacement from the internal CA.")
    y -= 12 * mm

    rows = [
        ["Error", "Cause", "Action"],
        ["401", "Expired credential", "Rotate credential"],
        ["503", "Agent offline", "Restart agent"],
    ]
    x0, col_w, row_h = 25 * mm, 45 * mm, 8 * mm
    for row in rows:
        for col, cell in enumerate(row):
            c.rect(x0 + col * col_w, y - row_h, col_w, row_h)
            c.setFont("Helvetica", 8)
            c.drawString(x0 + col * col_w + 2 * mm, y - row_h + 3 * mm, cell)
        y -= row_h
    c.showPage()

    if with_blank_image_page:
        c.setFillColorRGB(0.7, 0.8, 0.9)
        c.rect(30 * mm, 80 * mm, 140 * mm, 120 * mm, fill=1)
        c.showPage()

    c.setFont("Helvetica-Bold", 16)
    c.drawString(25 * mm, H - 30 * mm, "4. Rollback")
    c.setFont("Helvetica", 10)
    c.drawString(25 * mm, H - 40 * mm, "Restore the backup and reload the gateway.")
    c.showPage()

    c.save()
    return buf.getvalue()


# --- parser selection --------------------------------------------------------


def test_pdf_is_claimed_by_the_document_parser():
    assert get_parser(filename="x.pdf", mime_type=None) is not None
    assert get_parser(filename=None, mime_type="application/pdf") is not None
    assert get_parser(filename="X.PDF", mime_type=None) is not None


def test_non_document_formats_fall_through():
    """Returning None is the normal path for logs and JSON — they must
    keep reaching the existing text extractors."""
    assert get_parser(filename="app.log", mime_type="text/plain") is None
    assert get_parser(filename="events.json", mime_type="application/json") is None


# --- PDF structure -----------------------------------------------------------


def test_pdf_yields_structured_elements_with_page_and_section():
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    assert parsed.page_count == 4
    assert parsed.elements

    kinds = {e.element_type for e in parsed.elements}
    assert ELEMENT_HEADING in kinds
    assert ELEMENT_PARAGRAPH in kinds
    assert ELEMENT_TABLE in kinds

    # Every element is citable: page number and a bounding box.
    for element in parsed.elements:
        assert element.page_number is not None
        assert element.bounding_box is not None
        assert element.extraction_method == EXTRACTION_NATIVE


def test_heading_hierarchy_nests_by_numbered_depth():
    """Section path is what a citation points at. "3.2" must nest under
    "3", not replace it — font size alone cannot tell those apart."""
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    renew = next(e for e in parsed.elements if e.text.startswith("3.2 Renew"))
    assert renew.section_path[0].startswith("3. Procedure")
    assert renew.section_path[-1].startswith("3.2 Renew")
    assert len(renew.section_path) == 2


def test_table_is_attached_to_the_section_it_sits_under():
    """Reading-order regression. Emitting tables as they are *found*
    rather than where they *sit* put the table at the top of its page,
    detaching it from the procedure step it documents — and that step is
    exactly what a playbook would cite."""
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    table = next(e for e in parsed.elements if e.element_type == ELEMENT_TABLE)
    assert table.section_path[-1].startswith("3.2 Renew")

    # And it comes after that heading in reading order.
    heading = next(e for e in parsed.elements if e.text.startswith("3.2 Renew"))
    assert table.sequence > heading.sequence


def test_table_cells_survive_structurally():
    """A table flattened into prose is worse than no table."""
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    table = next(e for e in parsed.elements if e.element_type == ELEMENT_TABLE)
    rows = table.structured_content["rows"]
    assert rows[0][0] == "Error"
    assert any("401" in (cell or "") for row in rows for cell in row)


def test_table_text_is_not_duplicated_as_paragraphs():
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    paragraphs = [
        e.text for e in parsed.elements if e.element_type == ELEMENT_PARAGRAPH
    ]
    assert not any("Expired credential" in p for p in paragraphs)


def test_pages_without_a_text_layer_are_recorded_not_guessed():
    """No OCR engine is used anywhere. A page with no text layer is
    reported as a selective-vision candidate — never silently dropped,
    and never invented."""
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(with_blank_image_page=True), filename="sop.pdf")

    assert parsed.text_pages_missing() == [3]

    without = parser.parse(_sop_pdf(with_blank_image_page=False), filename="sop.pdf")
    assert without.text_pages_missing() == []


def test_list_bullets_are_not_doubled():
    parser = get_parser(filename="sop.pdf", mime_type="application/pdf")
    parsed = parser.parse(_sop_pdf(), filename="sop.pdf")

    items = [e for e in parsed.elements if e.element_type == ELEMENT_LIST_ITEM]
    assert items
    assert all(not e.text.startswith("-") for e in items)
    assert "- - " not in render_elements_to_text(parsed.elements)


def test_corrupt_pdf_reports_a_parse_failure_not_unsupported():
    """A PDF pdfplumber cannot open is still a PDF. Reporting
    "unsupported_artifact_type" would send an operator looking for a
    missing feature instead of a corrupt file."""
    result = extract_artifact_text(
        filename="broken.pdf", mime_type="application/pdf", data=b"%PDF-1.4 garbage"
    )
    assert result.status == "failed"
    assert result.parser_type == "pdf_native"
    assert result.error.startswith("document_parse_failed")


# --- pipeline integration ----------------------------------------------------


def test_pdf_reaches_the_artifact_pipeline_with_coverage_and_provenance():
    """Before this, PDFs returned unsupported_artifact_type."""
    result = extract_artifact_text(
        filename="sop.pdf", mime_type="application/pdf", data=_sop_pdf()
    )

    assert result.status == "completed"
    assert result.parser_type == "pdf_native"
    # Coverage, not parser quality: 3 of 4 pages carry text.
    assert result.parser_confidence == 0.75
    meta = result.parser_metadata
    assert meta["page_count"] == 4
    assert meta["pages_without_text"] == [3]
    assert meta["element_count"] > 0
    assert meta["elements"][0]["page"] == 1

    # Headings survive into the text so the document chunker splits on
    # the author's own sections.
    assert "# 1. Purpose" in result.text
    assert "## 3.1" in result.text or "## 3.2" in result.text


def test_documents_get_a_larger_body_budget_than_logs():
    """The 4 KB cap is right for a log and truncates a 60-page SOP to
    about its title page — past which every citable section lives."""
    from types import SimpleNamespace

    long_text = "x" * 50_000
    doc = SimpleNamespace(
        extraction_status="completed",
        extracted_text=long_text,
        parser_type="pdf_native",
        filename="sop.pdf",
    )
    log = SimpleNamespace(
        extraction_status="completed",
        extracted_text=long_text,
        parser_type="log_text",
        filename="app.log",
    )

    doc_body = build_combined_evidence_body(None, [doc])
    log_body = build_combined_evidence_body(None, [log])

    assert len(doc_body) > MAX_ATTACHMENT_TEXT_CHARS * 5
    assert len(log_body) < MAX_ATTACHMENT_TEXT_CHARS + 200
    assert MAX_DOCUMENT_TEXT_CHARS > MAX_ATTACHMENT_TEXT_CHARS
    assert "pdf_native" in DOCUMENT_PARSER_TYPES


def test_mixed_batch_keeps_each_attachment_on_its_own_budget():
    from types import SimpleNamespace

    doc = SimpleNamespace(
        extraction_status="completed",
        extracted_text="D" * 50_000,
        parser_type="pdf_native",
        filename="sop.pdf",
    )
    log = SimpleNamespace(
        extraction_status="completed",
        extracted_text="L" * 50_000,
        parser_type="log_text",
        filename="app.log",
    )
    body = build_combined_evidence_body(None, [doc, log])
    assert body.count("D") > MAX_ATTACHMENT_TEXT_CHARS
    assert body.count("L") <= MAX_ATTACHMENT_TEXT_CHARS


# --- text rendering ----------------------------------------------------------


def test_render_marks_figures_so_a_procedure_does_not_read_as_truncated():
    """"Configure as shown below" followed by nothing reads as a
    truncated document. The placeholder keeps the figure's place until
    the multimodal pass interprets it."""
    elements = [
        DocumentElement(
            element_type=ELEMENT_PARAGRAPH, text="Configure as shown below.", sequence=0
        ),
        DocumentElement(
            element_type=ELEMENT_FIGURE,
            text="[figure: not yet interpreted]",
            sequence=1,
        ),
    ]
    rendered = render_elements_to_text(elements)
    assert "Configure as shown below." in rendered
    assert "figure" in rendered


def test_render_truncates_on_a_line_boundary():
    elements = [
        DocumentElement(element_type=ELEMENT_PARAGRAPH, text="line " * 200, sequence=i)
        for i in range(20)
    ]
    out = render_elements_to_text(elements, max_chars=500)
    assert len(out) <= 500


def test_element_section_ref_is_a_citable_target():
    element = DocumentElement(
        element_type=ELEMENT_PARAGRAPH,
        text="Restore the backup.",
        sequence=9,
        page_number=14,
        section_path=["5. Certificate Renewal", "5.3 Rollback"],
    )
    assert element.section_ref() == "p14 § 5. Certificate Renewal > 5.3 Rollback"


def test_parsed_document_reports_incompleteness():
    """A partially-parsed document is useful, but a caller assessing
    completeness must be able to tell pages were missed rather than that
    the document was short."""
    assert ParsedDocument(elements=[]).is_complete is True
    assert ParsedDocument(elements=[], warnings=["page 3 failed"]).is_complete is False


def test_vision_extraction_method_is_distinguishable():
    """A model-transcribed element must never look like a parsed one:
    one is exact, the other is a paraphrase."""
    native = DocumentElement(
        element_type=ELEMENT_PARAGRAPH, text="a", sequence=0
    )
    vision = DocumentElement(
        element_type=ELEMENT_PARAGRAPH,
        text="a",
        sequence=1,
        extraction_method=EXTRACTION_VISION,
        confidence=0.8,
    )
    assert native.extraction_method == EXTRACTION_NATIVE
    assert native.confidence == 1.0
    assert vision.extraction_method != native.extraction_method
