"""Document-parser registry — mirrors ``connectors/registry.py`` shape.

Lazy registration so a missing optional parsing dependency degrades to
"this format is unsupported" instead of taking down the evidence
pipeline at import time. That failure mode is not hypothetical: the PDF
and DOCX libraries are optional extras, and a deployment that installs
the base package must still ingest logs and transcripts.
"""

from __future__ import annotations

import structlog

from contextedge.services.documents.base import DocumentParser

parsers: dict[str, DocumentParser] = {}
_registered = False


def _register_parsers() -> None:
    global _registered
    _registered = True
    log = structlog.get_logger()

    loaders: list[tuple[str, callable]] = [
        ("pdf_native", _load_pdf),
    ]
    for key, loader in loaders:
        try:
            parsers[key] = loader()
        except Exception as exc:  # noqa: BLE001 - optional dependency
            log.warning(
                "document_parser.register_failed",
                parser=key,
                error_type=type(exc).__name__,
                hint="install the document extras to enable this format",
            )


def _load_pdf() -> DocumentParser:
    import pdfplumber  # noqa: F401 - fail here if the dep is absent

    from contextedge.services.documents.pdf import PdfDocumentParser

    return PdfDocumentParser()


def get_parser(
    *, filename: str | None, mime_type: str | None
) -> DocumentParser | None:
    """The parser for this file, or ``None`` when no adapter claims it.

    ``None`` is a normal answer, not an error — it is how a ``.log`` or a
    ``.json`` falls through to the existing text extractors.
    """
    if not _registered:
        _register_parsers()
    for parser in parsers.values():
        if parser.supports(filename=filename, mime_type=mime_type):
            return parser
    return None


def supported_document_parsers() -> list[str]:
    if not _registered:
        _register_parsers()
    return list(parsers.keys())
