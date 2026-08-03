"""Phase 4e: multimodal figure interpretation.

No live model calls — ``llm_complete`` is mocked. The one thing verified
against a real provider during development is recorded in the PR: a KB
figure containing ``admin readwrite`` / ``monitorRole readonly`` /
``controlRole readwrite`` was read out of pixels into chunk text, and
none of those values appear anywhere in that article's prose.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.observability import build_messages
from contextedge.services.documents.base import (
    ELEMENT_FIGURE,
    ELEMENT_PARAGRAPH,
    EXTRACTION_NATIVE,
    EXTRACTION_VISION,
    DocumentElement,
)
from contextedge.services.documents.vision import (
    MAX_FIGURES_PER_DOCUMENT,
    FigureInterpretation,
    _parse_interpretation,
    interpret_document_figures,
    interpret_figure,
    render_figure,
)

VISION_JSON = """{
  "figure_type": "config_file",
  "summary": "ActiveMQ role permissions",
  "visible_text": "admin readwrite\\nmonitorRole readonly",
  "application": "ActiveMQ",
  "actions": ["Open jmx.access"],
  "warnings": [],
  "contains_sensitive": false,
  "confidence": 0.9
}"""


def _figure(page=1, bbox=(10.0, 10.0, 300.0, 200.0), sequence=0):
    return DocumentElement(
        element_type=ELEMENT_FIGURE,
        text="[figure: not yet interpreted]",
        sequence=sequence,
        page_number=page,
        section_path=["Resolution"],
        bounding_box=bbox,
        confidence=0.0,
        structured_content={"needs_vision": True},
    )


# --- multimodal message construction ----------------------------------------


def test_images_become_data_uri_blocks_not_links():
    """The source document is tenant data. Sending a URL the provider
    resolves would put it on a network path we do not control."""
    messages = build_messages("sys", "describe this", images=[b"\x89PNG-bytes"])
    user = messages[-1]
    assert user["role"] == "user"
    assert isinstance(user["content"], list)
    assert user["content"][0] == {"type": "text", "text": "describe this"}
    url = user["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert "http" not in url


def test_text_precedes_images_so_the_instruction_is_read_first():
    messages = build_messages(None, "prompt", images=[b"a", b"b"])
    kinds = [block["type"] for block in messages[-1]["content"]]
    assert kinds == ["text", "image_url", "image_url"]


def test_message_shape_is_unchanged_without_images():
    messages = build_messages("sys", "prompt")
    assert messages[-1] == {"role": "user", "content": "prompt"}


@pytest.mark.asyncio
async def test_vision_goes_through_the_metered_provider_path():
    """Vision is the most expensive call type. A parallel client would
    be the one kind of request that escaped budget enforcement, usage
    recording, the circuit breaker, and the timeout."""
    with patch(
        "contextedge.ai.provider.llm_complete", AsyncMock(return_value=VISION_JSON)
    ) as mock:
        result = await interpret_figure(
            b"png", context="Resolution", tenant_id="t-1", db=object()
        )

    assert result is not None
    kwargs = mock.await_args.kwargs
    assert kwargs["tenant_id"] == "t-1"
    assert kwargs["db"] is not None
    assert kwargs["images"] == [b"png"]
    assert kwargs["prompt_name"] == "document_figure_vision"


# --- response parsing --------------------------------------------------------


def test_interpretation_parses_and_clamps():
    parsed = _parse_interpretation(VISION_JSON)
    assert parsed.figure_type == "config_file"
    assert parsed.application == "ActiveMQ"
    assert "admin readwrite" in parsed.visible_text
    assert parsed.actions == ["Open jmx.access"]
    assert parsed.confidence == 0.9


def test_interpretation_tolerates_fenced_json():
    parsed = _parse_interpretation(f"```json\n{VISION_JSON}\n```")
    assert parsed is not None
    assert parsed.summary == "ActiveMQ role permissions"


def test_interpretation_rejects_unusable_responses():
    """A figure with no interpretation keeps its placeholder — better
    than a fabricated description presented as fact."""
    assert _parse_interpretation("") is None
    assert _parse_interpretation("I cannot see the image.") is None
    assert _parse_interpretation("{not json") is None
    assert _parse_interpretation('{"summary": ""}') is None
    assert _parse_interpretation("[1,2,3]") is None


def test_confidence_is_clamped_to_a_valid_range():
    assert _parse_interpretation('{"summary":"x","confidence":5}').confidence == 1.0
    assert _parse_interpretation('{"summary":"x","confidence":-2}').confidence == 0.0
    assert _parse_interpretation('{"summary":"x","confidence":"junk"}').confidence == 0.0


def test_rendered_text_leads_with_the_summary_then_actions_then_content():
    text = FigureInterpretation(
        summary="Credential Vault screen",
        application="AutomationEdge",
        actions=["Select credential", "Choose Rotate"],
        visible_text="admin readwrite",
        warnings=["Active executions may fail"],
    ).to_text()
    assert text.startswith("[figure: Credential Vault screen]")
    assert "Screen: AutomationEdge" in text
    assert "- Select credential" in text
    assert "admin readwrite" in text
    assert "Warning: Active executions may fail" in text


# --- the document pass -------------------------------------------------------


@pytest.mark.asyncio
async def test_interpreted_figures_are_marked_vision_not_native():
    """A model-read element must never be indistinguishable from a
    parsed one: one is exact, the other is a paraphrase."""
    elements = [
        DocumentElement(
            element_type=ELEMENT_PARAGRAPH, text="See below.", sequence=0,
            page_number=1,
        ),
        _figure(sequence=1),
    ]
    with (
        patch(
            "contextedge.services.documents.vision.render_figure",
            return_value=b"png",
        ),
        patch(
            "contextedge.services.documents.vision.interpret_figure",
            AsyncMock(return_value=_parse_interpretation(VISION_JSON)),
        ),
    ):
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )

    assert counts["interpreted"] == 1
    figure = elements[1]
    assert figure.extraction_method == EXTRACTION_VISION
    assert figure.confidence == 0.9
    assert "admin readwrite" in figure.text
    assert figure.structured_content["needs_vision"] is False
    assert figure.structured_content["figure_type"] == "config_file"
    # The parsed paragraph is untouched.
    assert elements[0].extraction_method == EXTRACTION_NATIVE


@pytest.mark.asyncio
async def test_a_failed_interpretation_keeps_the_placeholder():
    """Losing an interpretation degrades a document. Raising would lose
    the document."""
    elements = [_figure()]
    with (
        patch(
            "contextedge.services.documents.vision.render_figure",
            return_value=b"png",
        ),
        patch(
            "contextedge.services.documents.vision.interpret_figure",
            AsyncMock(return_value=None),
        ),
    ):
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )

    assert counts["failed"] == 1
    assert counts["interpreted"] == 0
    assert elements[0].extraction_method == EXTRACTION_NATIVE
    assert elements[0].structured_content["needs_vision"] is True


@pytest.mark.asyncio
async def test_per_document_figure_budget_is_bounded_and_reported():
    """A folder of SOPs at one call per figure is how a tenant's daily
    budget disappears in a single upload. A bound that is announced is a
    known limitation; a silent one reads as "there were only N"."""
    elements = [_figure(sequence=i) for i in range(MAX_FIGURES_PER_DOCUMENT + 5)]
    calls = []

    async def fake(image, *, context, tenant_id, db):
        calls.append(image)
        return _parse_interpretation(VISION_JSON)

    with (
        patch(
            "contextedge.services.documents.vision.render_figure",
            return_value=b"png",
        ),
        patch(
            "contextedge.services.documents.vision.interpret_figure",
            side_effect=fake,
        ),
    ):
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )

    assert len(calls) == MAX_FIGURES_PER_DOCUMENT
    assert counts["over_limit"] == 5
    assert counts["interpreted"] == MAX_FIGURES_PER_DOCUMENT


@pytest.mark.asyncio
async def test_a_document_with_no_figures_costs_nothing():
    elements = [
        DocumentElement(element_type=ELEMENT_PARAGRAPH, text="prose", sequence=0)
    ]
    with patch(
        "contextedge.services.documents.vision.interpret_figure", AsyncMock()
    ) as mock:
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )
    assert counts["figures"] == 0
    assert mock.await_count == 0


@pytest.mark.asyncio
async def test_unrenderable_figures_are_skipped_without_a_model_call():
    elements = [_figure()]
    with (
        patch(
            "contextedge.services.documents.vision.render_figure", return_value=None
        ),
        patch(
            "contextedge.services.documents.vision.interpret_figure", AsyncMock()
        ) as mock,
    ):
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )
    assert counts["skipped_render"] == 1
    assert mock.await_count == 0


@pytest.mark.asyncio
async def test_sensitive_figures_are_counted_for_operator_attention():
    """Screenshots routinely show usernames, hostnames, and tokens.
    Flagged so an operator can find what was pulled out of an image."""
    elements = [_figure()]
    sensitive = _parse_interpretation(
        '{"summary":"login screen","contains_sensitive":true,"confidence":0.8}'
    )
    with (
        patch(
            "contextedge.services.documents.vision.render_figure", return_value=b"p"
        ),
        patch(
            "contextedge.services.documents.vision.interpret_figure",
            AsyncMock(return_value=sensitive),
        ),
    ):
        counts = await interpret_document_figures(
            elements, b"pdf", tenant_id="t", db=object()
        )
    assert counts["sensitive"] == 1
    assert elements[0].structured_content["contains_sensitive"] is True


# --- rendering ---------------------------------------------------------------

pytest.importorskip("pdfplumber", reason="document extras not installed")
pytest.importorskip("reportlab", reason="reportlab needed to build fixtures")


def _one_page_pdf() -> bytes:
    """A page with dense content, not a flat fill.

    The size gate uses rendered PNG bytes as a proxy for information
    content, and a solid rectangle compresses below it — correctly, since
    there is nothing in it to read. Real figures are screenshots of text
    and UI, so the fixture has to be too.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillColorRGB(0.85, 0.9, 0.95)
    c.rect(40, 180, 340, 240, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Courier", 8)
    for i in range(26):
        c.drawString(48, 400 - i * 8, f"{i:02d}  monitorRole readonly  admin readwrite")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_render_crops_a_figure_region_to_png():
    # pdfplumber boxes are top-left origin; reportlab draws bottom-left,
    # so the fixture's content at reportlab y=180..420 on an A4 page is
    # at pdfplumber top=422..662. Production never does this conversion —
    # bounding boxes come from pdfplumber itself.
    element = _figure(page=1, bbox=(40.0, 422.0, 380.0, 662.0))
    data = render_figure(_one_page_pdf(), element)
    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_skips_a_region_with_nothing_in_it():
    """Rendered size is the proxy for information content: a blank or
    flat region compresses below the floor and is not worth a call."""
    blank = _figure(page=1, bbox=(40.0, 40.0, 380.0, 120.0))
    assert render_figure(_one_page_pdf(), blank) is None


def test_render_returns_none_for_unusable_geometry():
    pdf = _one_page_pdf()
    assert render_figure(pdf, _figure(bbox=None)) is None
    assert render_figure(pdf, _figure(page=None)) is None
    # Page beyond the document.
    assert render_figure(pdf, _figure(page=99)) is None
    # Inverted box.
    assert render_figure(pdf, _figure(bbox=(300.0, 300.0, 100.0, 100.0))) is None


def test_render_survives_a_box_overflowing_the_page():
    """Some generators emit boxes a fraction of a point outside the media
    box; cropping to that raises rather than rendering."""
    element = _figure(page=1, bbox=(-5.0, -5.0, 99_999.0, 99_999.0))
    assert render_figure(_one_page_pdf(), element) is not None
