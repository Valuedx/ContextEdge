"""Fixes found by driving real KB PDFs through the whole pipeline.

Every defect here passed its unit tests and failed on first contact with
a real document. They are regression-tested at the level that would have
caught them: the persisted contract, not the in-memory object.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from contextedge.services.artifact_extraction_service import (
    MAX_PERSISTED_ELEMENTS,
    extract_artifact_text,
    redact_artifact_content,
)
from contextedge.services.redaction_service import redact

pytest.importorskip("pdfplumber", reason="document extras not installed")
pytest.importorskip("reportlab", reason="reportlab needed to build fixtures")


def _pdf_with_figure() -> bytes:
    """A page with text and an EMBEDDED RASTER IMAGE.

    The image has to be a real raster XObject: pdfplumber's ``page.images``
    reports embedded images, not vector shapes, so a rectangle drawn with
    ``canvas.rect()`` produces no figure element. Real KB articles embed
    screenshots, so the fixture must too — an earlier version used a
    vector rectangle and silently exercised the no-figure path.
    """
    import io

    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    # Noisy enough not to compress to nothing, which the render-size floor
    # would (correctly) reject as having no content worth reading.
    noise = Image.new("RGB", (240, 180))
    noise.putdata(
        [((x * 7) % 256, (y * 11) % 256, ((x + y) * 13) % 256)
         for y in range(180) for x in range(240)]
    )

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 760, "Resolution:")
    c.setFont("Helvetica", 10)
    c.drawString(60, 740, "Configure as shown below.")
    c.drawImage(ImageReader(noise), 60, 400, width=360, height=270)
    c.showPage()
    c.save()
    return buf.getvalue()


# --- the persisted element contract ------------------------------------------


def test_persisted_elements_carry_structured_content():
    """The figure pass selects on structured["needs_vision"] and the
    chunker reads structured for has_figure / needs_vision.

    This key was omitted from the persisted dict, so every figure looked
    like it needed no interpretation and the vision pass was unreachable
    — it ran on real documents and interpreted nothing. Unit tests passed
    throughout, because they built elements in memory rather than reading
    back what was stored.
    """
    result = extract_artifact_text(
        filename="sop.pdf", mime_type="application/pdf", data=_pdf_with_figure()
    )
    assert result.status == "completed"
    elements = result.parser_metadata["elements"]
    assert elements

    for element in elements:
        assert "structured" in element, "consumers read structured; it must persist"

    figures = [e for e in elements if e["type"] == "figure"]
    assert figures, "fixture should produce a figure"
    assert figures[0]["structured"].get("needs_vision") is True


def test_persisted_elements_satisfy_every_consumer_key():
    """Guards the string-literal contract between the persister, the
    chunker, and the vision rebuild — three places that must agree."""
    result = extract_artifact_text(
        filename="sop.pdf", mime_type="application/pdf", data=_pdf_with_figure()
    )
    element = result.parser_metadata["elements"][0]
    for key in ("type", "page", "section", "bbox", "method", "text", "structured"):
        assert key in element, f"consumers read {key!r}"


def test_element_truncation_is_reported_not_silent():
    """A truncation nobody logs reads as "the document was short"."""
    result = extract_artifact_text(
        filename="sop.pdf", mime_type="application/pdf", data=_pdf_with_figure()
    )
    assert "elements_dropped" in result.parser_metadata
    assert result.parser_metadata["elements_dropped"] == 0
    assert MAX_PERSISTED_ELEMENTS > 0


# --- artifact redaction ------------------------------------------------------


def test_redaction_covers_element_text_not_only_the_body():
    """The document chunker builds chunks from parser_metadata elements,
    NOT from body_text. Redacting only the body would leave every chunk
    unredacted — and chunks are the surface search returns."""
    artifact = SimpleNamespace(
        extracted_text="Mail dana@acme.example",
        parser_metadata={
            "elements": [
                {"type": "figure", "text": "User: admin@acme.example", "method": "vision"},
                {"type": "paragraph", "text": "nothing secret"},
            ]
        },
    )
    counts = redact_artifact_content(artifact)

    assert counts.get("EMAIL") == 2
    assert "dana@acme.example" not in artifact.extracted_text
    elements = artifact.parser_metadata["elements"]
    assert "admin@acme.example" not in elements[0]["text"]
    assert elements[1]["text"] == "nothing secret"


def test_redaction_is_a_noop_when_disabled():
    from contextedge.config import settings

    artifact = SimpleNamespace(
        extracted_text="dana@acme.example", parser_metadata={"elements": []}
    )
    original = settings.redaction_enabled
    try:
        settings.redaction_enabled = False
        assert redact_artifact_content(artifact) == {}
        assert artifact.extracted_text == "dana@acme.example"
    finally:
        settings.redaction_enabled = original


def test_redaction_tolerates_missing_metadata():
    artifact = SimpleNamespace(extracted_text=None, parser_metadata=None)
    assert redact_artifact_content(artifact) == {}


# --- the ruleset the vision path needs ---------------------------------------


# Token-shaped fixtures are ASSEMBLED AT RUNTIME, never written as
# literals. A literal here is indistinguishable from a real credential to
# a secret scanner — GitHub push protection blocked this file's first
# version over a fake Slack token — and a test suite that trains people
# to click "allow this secret" is a liability. Splitting the prefix from
# the body keeps the scanner quiet without weakening what is tested.
def _fake(prefix: str, body: str) -> str:
    return prefix + body


@pytest.mark.parametrize(
    "text,rule",
    [
        (f"Token: {_fake('ghp' + '_', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')}", "API_TOKEN"),
        (_fake("xox" + "b-", "1234567890-ABCDEFGHIJKLMNOP"), "API_TOKEN"),
        (_fake("sk" + "-", "abcdefghijklmnopqrstuvwx"), "API_TOKEN"),
        (_fake("eyJ", "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dQw4w9WgXcQabcdef"), "JWT"),
        ("Authorization: Bearer abcdef1234567890XYZ", "BEARER_TOKEN"),
        ("spring.datasource.password=Sup3rS3cret!", "SECRET_ASSIGNMENT"),
        ("api_key: AKfycbx9912LongEnoughValue", "SECRET_ASSIGNMENT"),
        ("db?user=admin&password=hunter2xyz", "SECRET_ASSIGNMENT"),
    ],
)
def test_secrets_in_screenshots_and_logs_are_redacted(text, rule):
    """Screenshots of config files and terminals are exactly where these
    live, and nothing matched them before the figure pass existed."""
    out, counts = redact(text)
    assert rule in counts
    assert "[REDACTED:" in out


def test_a_token_is_not_partially_eaten_by_a_numeric_rule():
    """Rule order is load-bearing. PHONE previously fired inside a
    GitHub token, leaving the alphabetic body in place followed by
    "[REDACTED:PHONE]" — which reads as redacted and is not. A
    partly-redacted secret passes review."""
    token = _fake("ghp" + "_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    out, counts = redact(token)
    assert counts == {"API_TOKEN": 1}
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in out
    assert "PHONE" not in counts


@pytest.mark.parametrize(
    "text",
    [
        "The password policy requires rotation every 90 days.",
        "AutomationEdge 7.4.1 build 20230321",
        "admin readwrite\nmonitorRole readonly",
    ],
)
def test_operational_content_is_not_over_redacted(text):
    """The ActiveMQ role config is the answer a KB article exists to
    give. Redacting it would destroy the content the figure pass was
    built to recover."""
    out, counts = redact(text)
    assert counts == {}
    assert out == text


def test_private_keys_still_redact_as_a_whole_block():
    """The 40-char AWS_SECRET_KEY rule runs first; its lookarounds must
    not fragment a key body and break the BEGIN/END span."""
    key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAy8Dbv8prpJ/0kKhlGeJYozo2t60EG8L0561g13R29LvMR5hy\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out, counts = redact(key)
    assert counts == {"PRIVATE_KEY": 1}
    assert out.strip() == "[REDACTED:PRIVATE_KEY]"
