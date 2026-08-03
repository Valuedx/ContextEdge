"""Deterministic attachment artifact extraction and evidence merge helpers."""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.embeddings import embed_evidence
from contextedge.models.evidence import AttachmentArtifact, EvidenceItem, RawEvidenceObject
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.evidence_normalization import (
    evidence_body_from_payload,
    evidence_title_from_payload,
)
from contextedge.services.identity_service import link_evidence_identities
from contextedge.services.object_store import download_artifact, download_raw, upload_artifact

MAX_ATTACHMENT_TEXT_CHARS = 4_000
MAX_COMBINED_BODY_CHARS = 16_000

# Documents get their own, much larger budgets. The 4 KB / 16 KB caps
# above are correct for a log attachment — nobody needs the whole log in
# the evidence body, and the artifact keeps the full text — but applying
# them to a 60-page SOP truncates it to roughly its title page. The
# procedure, rollback, and verification sections a playbook needs to cite
# are all past the cut.
#
# These bound the *evidence body*, which is what the classifier and
# embedder read. Structure-aware chunking is what will make long
# documents properly retrievable; until then a generous cap is the
# difference between a searchable SOP and a searchable cover sheet.
MAX_DOCUMENT_TEXT_CHARS = 200_000
MAX_DOCUMENT_BODY_CHARS = 400_000

# Element metadata persisted per artifact. Bounded because it lands in a
# JSONB column: a 500-page document can produce tens of thousands of
# elements, and the row should stay readable.
MAX_PERSISTED_ELEMENTS = 2_000

# Parser types that produce documents rather than log-ish text, and so
# earn the larger budgets above.
DOCUMENT_PARSER_TYPES = frozenset({"pdf_native", "docx_native"})
TEXT_EXTENSIONS = {".txt", ".text", ".md", ".csv"}
LOG_EXTENSIONS = {".log", ".out", ".err"}
JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}
TRANSCRIPT_EXTENSIONS = {".srt", ".vtt", ".transcript"}
JSON_MIME_TYPES = {"application/json", "application/x-ndjson"}
TRANSCRIPT_MIME_TYPES = {"text/vtt", "application/x-subrip"}


@dataclass(slots=True)
class ArtifactExtractionResult:
    status: str
    parser_type: str | None
    parser_confidence: float | None
    text: str | None
    parser_metadata: dict | None
    error: str | None = None


def _clean_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    return mime_type.split(";", 1)[0].strip().lower()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _flatten_json(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(_flatten_json(item, child_prefix))
        return lines
    if isinstance(value, list):
        lines: list[str] = []
        for index, item in enumerate(value):
            child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            lines.extend(_flatten_json(item, child_prefix))
        return lines
    if value is None:
        rendered = "null"
    else:
        rendered = str(value)
    return [f"{prefix}={rendered}" if prefix else rendered]


def _normalize_transcript_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT":
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if cleaned_lines and cleaned_lines[-1] == line:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _detect_parser(filename: str | None, mime_type: str | None) -> str | None:
    suffix = Path(filename or "").suffix.lower()
    clean_mime = _clean_mime_type(mime_type)

    if clean_mime in JSON_MIME_TYPES or suffix in JSON_EXTENSIONS:
        return "json_log"
    if clean_mime in TRANSCRIPT_MIME_TYPES or suffix in TRANSCRIPT_EXTENSIONS:
        return "transcript_text"
    if suffix in LOG_EXTENSIONS:
        return "log_text"
    if clean_mime.startswith("text/") or suffix in TEXT_EXTENSIONS:
        return "plain_text"
    return None


def extract_artifact_text(
    *,
    filename: str | None,
    mime_type: str | None,
    data: bytes,
) -> ArtifactExtractionResult:
    # Structured document parsers are tried first: a PDF or DOCX is not a
    # text blob, and the elements they produce (page, section path,
    # bounding box, tables, figure placeholders) are what step-level
    # citations and structure-aware chunking need. The text rendering
    # returned here keeps the existing pipeline working; the elements are
    # carried in parser_metadata for consumers that want them.
    document_result = _extract_structured_document(
        filename=filename, mime_type=mime_type, data=data
    )
    if document_result is not None:
        return document_result

    parser_type = _detect_parser(filename, mime_type)
    if parser_type is None:
        return ArtifactExtractionResult(
            status="unsupported",
            parser_type=None,
            parser_confidence=0.0,
            text=None,
            parser_metadata={"mime_type": _clean_mime_type(mime_type), "filename": filename},
            error="unsupported_artifact_type",
        )

    if parser_type == "json_log":
        try:
            parsed = json.loads(_decode_text(data))
        except json.JSONDecodeError as exc:
            return ArtifactExtractionResult(
                status="failed",
                parser_type=parser_type,
                parser_confidence=0.0,
                text=None,
                parser_metadata={"mime_type": _clean_mime_type(mime_type), "filename": filename},
                error=f"invalid_json: {exc.msg}",
            )
        lines = _flatten_json(parsed)
        text = "\n".join(line for line in lines if line.strip())
        confidence = 0.99
    else:
        text = _decode_text(data)
        confidence = 1.0 if parser_type != "transcript_text" else 0.97
        if parser_type == "transcript_text":
            text = _normalize_transcript_text(text)

    normalized = text.strip()
    line_count = 0 if not normalized else normalized.count("\n") + 1
    return ArtifactExtractionResult(
        status="completed",
        parser_type=parser_type,
        parser_confidence=confidence,
        text=normalized,
        parser_metadata={
            "mime_type": _clean_mime_type(mime_type),
            "filename": filename,
            "line_count": line_count,
            "byte_count": len(data),
        },
    )


def _extract_structured_document(
    *, filename: str | None, mime_type: str | None, data: bytes
) -> ArtifactExtractionResult | None:
    """Parse via a document adapter, or ``None`` if none claims the file.

    Returning ``None`` is the normal path for logs, JSON, and
    transcripts — they fall through to the text extractors below.

    A parse failure returns a *failed* result rather than raising or
    falling through: a PDF that pdfplumber cannot open is a PDF, and
    reporting "unsupported_artifact_type" for it would send an operator
    looking for a missing feature instead of a corrupt file.
    """
    from contextedge.services.documents import get_parser, render_elements_to_text

    parser = get_parser(filename=filename, mime_type=mime_type)
    if parser is None:
        return None

    try:
        parsed = parser.parse(data, filename=filename)
    except Exception as exc:  # noqa: BLE001
        return ArtifactExtractionResult(
            status="failed",
            parser_type=parser.name,
            parser_confidence=0.0,
            text=None,
            parser_metadata={
                "mime_type": _clean_mime_type(mime_type),
                "filename": filename,
            },
            error=f"document_parse_failed: {type(exc).__name__}",
        )

    text = render_elements_to_text(parsed.elements, max_chars=MAX_DOCUMENT_TEXT_CHARS)
    empty_pages = parsed.metadata.get("empty_pages") or []

    return ArtifactExtractionResult(
        status="completed",
        parser_type=parser.name,
        # Confidence reflects coverage, not parser quality: a document
        # whose pages are largely image-only has been read correctly and
        # still yields little. Downstream completeness assessment needs
        # to see that difference.
        parser_confidence=_document_coverage(parsed.page_count, len(empty_pages)),
        text=text,
        parser_metadata={
            "mime_type": _clean_mime_type(mime_type),
            "filename": filename,
            "byte_count": len(data),
            "page_count": parsed.page_count,
            "element_count": len(parsed.elements),
            # Pages with no text layer. These are the selective targets
            # for the later multimodal pass — recorded, never guessed at.
            "pages_without_text": empty_pages[:100],
            "warnings": parsed.warnings[:20],
            "elements": [
                {
                    "type": e.element_type,
                    "page": e.page_number,
                    "section": e.section_path,
                    "bbox": list(e.bounding_box) if e.bounding_box else None,
                    "method": e.extraction_method,
                    "text": e.text[:2000],
                }
                for e in parsed.elements[:MAX_PERSISTED_ELEMENTS]
            ],
        },
    )


def _document_coverage(page_count: int, empty_page_count: int) -> float:
    if page_count <= 0:
        return 0.0
    return round(max(0.0, (page_count - empty_page_count) / page_count), 3)


def _attachment_payload_entries(payload: dict | None) -> list[dict]:
    attachments = (payload or {}).get("attachments")
    if not isinstance(attachments, list):
        return []
    return [item for item in attachments if isinstance(item, dict)]


def _attachment_content_bytes(attachment: dict) -> bytes | None:
    if attachment.get("content_base64"):
        return base64.b64decode(str(attachment["content_base64"]))

    content = attachment.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, (dict, list)):
        return json.dumps(content, default=str).encode("utf-8")
    if isinstance(content, (int, float, bool)):
        return str(content).encode("utf-8")
    return None


async def load_raw_payload(raw: RawEvidenceObject) -> dict:
    if raw.raw_payload and raw.raw_payload.get("_offloaded"):
        if not raw.object_storage_key:
            raise ValueError("raw_payload_offloaded_without_storage_key")
        return json.loads(download_raw(raw.object_storage_key).decode("utf-8"))
    return raw.raw_payload or {}


async def register_attachment_artifacts(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict | None,
) -> list[AttachmentArtifact]:
    entries = _attachment_payload_entries(payload)
    if not entries:
        return []

    existing_rows = await db.execute(
        select(AttachmentArtifact).where(AttachmentArtifact.evidence_id == evidence.id)
    )
    existing_pairs = {
        (artifact.filename, artifact.object_storage_key)
        for artifact in existing_rows.scalars().all()
    }

    created: list[AttachmentArtifact] = []
    for index, attachment in enumerate(entries):
        filename = str(
            attachment.get("filename") or attachment.get("name") or f"attachment-{index + 1}"
        )
        mime_type = (
            str(
                attachment.get("mime_type")
                or attachment.get("content_type")
                or "application/octet-stream"
            )
            .strip()
            or "application/octet-stream"
        )
        object_key = attachment.get("object_storage_key")
        data = None
        artifact_id = uuid.uuid4()
        if not object_key:
            data = _attachment_content_bytes(attachment)
            if data is None:
                continue
            object_key = upload_artifact(
                str(tenant_id),
                str(evidence.id),
                str(artifact_id),
                filename,
                data,
                content_type=mime_type,
            )
        key = (filename[:500], str(object_key))
        if key in existing_pairs:
            continue

        parser_metadata = {
            "source": "raw_payload_attachment",
            "attachment_index": index,
        }
        attachment_metadata = attachment.get("metadata")
        if isinstance(attachment_metadata, dict) and attachment_metadata:
            parser_metadata["attachment_metadata"] = attachment_metadata
        provenance = attachment.get("provenance")
        if provenance is not None:
            parser_metadata["provenance"] = provenance

        artifact = AttachmentArtifact(
            id=artifact_id,
            evidence_id=evidence.id,
            filename=filename[:500],
            mime_type=mime_type[:100],
            size_bytes=int(attachment.get("size_bytes") or len(data or b"")),
            object_storage_key=str(object_key),
            extraction_status="pending",
            parser_metadata=parser_metadata,
        )
        db.add(artifact)
        await db.flush()
        created.append(artifact)
        existing_pairs.add(key)

    return created


def build_combined_evidence_body(
    base_body: str | None,
    attachments: list[AttachmentArtifact],
) -> str | None:
    sections = [base_body.strip()] if base_body and base_body.strip() else []
    has_document = False
    for artifact in attachments:
        if artifact.extraction_status != "completed" or not artifact.extracted_text:
            continue
        # Per-artifact budget by kind: a log is sampled, a document is
        # kept. Applying the log cap to an SOP truncates it to about its
        # title page, past which every section a playbook would cite
        # lives.
        is_document = (artifact.parser_type or "") in DOCUMENT_PARSER_TYPES
        has_document = has_document or is_document
        budget = MAX_DOCUMENT_TEXT_CHARS if is_document else MAX_ATTACHMENT_TEXT_CHARS
        snippet = artifact.extracted_text.strip()[:budget]
        if not snippet:
            continue
        parser_label = artifact.parser_type or "deterministic"
        sections.append(f"[Attachment: {artifact.filename} | parser={parser_label}]\n{snippet}")
    if not sections:
        return None
    combined_budget = MAX_DOCUMENT_BODY_CHARS if has_document else MAX_COMBINED_BODY_CHARS
    return "\n\n".join(sections)[:combined_budget]


async def synchronize_evidence_artifacts(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
    source_id: uuid.UUID | None,
) -> dict:
    attachments_result = await db.execute(
        select(AttachmentArtifact)
        .where(AttachmentArtifact.evidence_id == evidence.id)
        .order_by(AttachmentArtifact.created_at.asc(), AttachmentArtifact.filename.asc())
    )
    attachments = attachments_result.scalars().all()
    completed_count = sum(
        1
        for artifact in attachments
        if artifact.extraction_status == "completed" and artifact.extracted_text
    )

    evidence.title = evidence_title_from_payload(payload)[:500]
    combined_body = build_combined_evidence_body(evidence_body_from_payload(payload), attachments)
    evidence.body_text = combined_body
    evidence.body_summary = combined_body[:500] if combined_body else None
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)

    # Re-chunk against the merged body.
    #
    # Attachment extraction runs AFTER normalize, so the chunks written
    # during normalize were built from the body *before* any attachment
    # text existed. Nothing re-chunked afterwards, which meant an
    # uploaded document's content reached the parent embedding and the
    # body column but never reached evidence_chunks at all — invisible to
    # every chunk-level retrieval path, which for a long document is the
    # only one that works.
    #
    # Structured elements are passed through on a payload COPY so the
    # chunker stays a pure function of its arguments instead of reaching
    # into the database for them.
    await _rechunk_with_documents(
        db, tenant_id=tenant_id, evidence=evidence, payload=payload,
        attachments=attachments,
    )

    identity_content = "\n".join(
        part for part in [evidence.title or "", evidence.body_text or ""] if part
    )
    if identity_content.strip():
        await link_evidence_identities(
            db,
            tenant_id=tenant_id,
            evidence=evidence,
            content=identity_content,
            source_id=source_id,
            source_metadata=(
                {"raw_object_id": str(evidence.raw_object_ref)}
                if evidence.raw_object_ref
                else None
            ),
        )

    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="evidence_item",
        entity_id=evidence.id,
        event_type="evidence.artifacts_merged",
        payload={
            "attachment_count": len(attachments),
            "completed_attachment_count": completed_count,
        },
    )
    return {
        "attachment_count": len(attachments),
        "completed_attachment_count": completed_count,
    }


async def _interpret_artifact_figures(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    artifact: AttachmentArtifact,
    data: bytes,
) -> dict | None:
    """Run the multimodal figure pass and fold results back into the text.

    Only touches documents that produced figure elements needing vision,
    so a log or a figure-free PDF costs nothing. Fail-soft: a document
    keeps its parsed text and its figure placeholders when the pass
    fails, which is strictly better than losing the extraction.
    """
    from contextedge.config import settings as _settings

    if not getattr(_settings, "document_vision_enabled", True):
        return None
    if (artifact.parser_type or "") not in DOCUMENT_PARSER_TYPES:
        return None

    meta = artifact.parser_metadata or {}
    raw_elements = meta.get("elements") or []
    if not any(
        isinstance(e, dict) and (e.get("structured") or {}).get("needs_vision")
        for e in raw_elements
    ):
        return None

    from contextedge.services.documents.base import DocumentElement
    from contextedge.services.documents.vision import interpret_document_figures

    # Rebuild elements as dataclasses for the vision pass, then write the
    # interpreted text back onto the persisted dicts by position.
    rebuilt: list[DocumentElement] = []
    for index, raw in enumerate(raw_elements):
        if not isinstance(raw, dict):
            continue
        bbox = raw.get("bbox")
        rebuilt.append(
            DocumentElement(
                element_type=raw.get("type") or "paragraph",
                text=raw.get("text") or "",
                sequence=index,
                page_number=raw.get("page"),
                section_path=list(raw.get("section") or []),
                bounding_box=tuple(bbox) if bbox and len(bbox) == 4 else None,
                extraction_method=raw.get("method") or "native",
                structured_content=raw.get("structured") or {},
            )
        )

    try:
        counts = await interpret_document_figures(
            rebuilt, data, tenant_id=tenant_id, db=db
        )
    except Exception as exc:  # noqa: BLE001
        import structlog

        structlog.get_logger().warning(
            "document.figure_pass_failed",
            artifact_id=str(artifact.id),
            error_type=type(exc).__name__,
        )
        return None

    if not counts.get("interpreted"):
        return counts

    for element in rebuilt:
        raw = raw_elements[element.sequence]
        if isinstance(raw, dict):
            raw["text"] = element.text[:2000]
            raw["method"] = element.extraction_method
            raw["structured"] = element.structured_content

    from contextedge.services.documents import render_elements_to_text

    artifact.parser_metadata = {
        **meta,
        "elements": raw_elements,
        "vision": counts,
    }
    artifact.extracted_text = render_elements_to_text(
        rebuilt, max_chars=MAX_DOCUMENT_TEXT_CHARS
    )
    await db.flush()

    import structlog

    structlog.get_logger().info(
        "document.figures_interpreted",
        artifact_id=str(artifact.id),
        **counts,
    )
    return counts


async def _rechunk_with_documents(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
    attachments: list[AttachmentArtifact],
) -> int:
    """Rebuild chunks after attachment text has been merged in.

    Fail-soft: chunking is an optimisation over the parent embedding,
    which has already been refreshed by the caller. Losing a re-chunk
    degrades retrieval granularity; raising here would roll back the
    body and embedding updates too, which is strictly worse.
    """
    elements: list[dict] = []
    for artifact in attachments:
        if artifact.extraction_status != "completed":
            continue
        meta = artifact.parser_metadata or {}
        for element in meta.get("elements") or []:
            if isinstance(element, dict):
                elements.append({**element, "artifact": artifact.filename})

    chunk_payload = dict(payload)
    if elements:
        chunk_payload["_document_elements"] = elements

    try:
        from contextedge.services.evidence_chunk_service import write_chunks

        chunks = await write_chunks(
            db,
            tenant_id=tenant_id,
            evidence=evidence,
            payload=chunk_payload,
            source_type=evidence.source_type,
        )
        return len(chunks)
    except Exception as exc:  # noqa: BLE001
        import structlog

        structlog.get_logger().warning(
            "artifact.rechunk_failed",
            tenant_id=str(tenant_id),
            evidence_id=str(evidence.id),
            error_type=type(exc).__name__,
        )
        return 0


async def process_attachment_artifact(
    db: AsyncSession,
    *,
    artifact_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    artifact = await db.get(AttachmentArtifact, artifact_id)
    if artifact is None:
        return {"error": "artifact_not_found"}

    evidence = await db.get(EvidenceItem, artifact.evidence_id)
    if evidence is None or evidence.tenant_id != tenant_id:
        return {"error": "evidence_not_found"}

    artifact.extraction_status = "processing"
    artifact.extraction_error = None
    await db.flush()

    try:
        data = download_artifact(artifact.object_storage_key)
        extraction = extract_artifact_text(
            filename=artifact.filename,
            mime_type=artifact.mime_type,
            data=data,
        )
    except Exception as exc:
        extraction = ArtifactExtractionResult(
            status="failed",
            parser_type=None,
            parser_confidence=0.0,
            text=None,
            parser_metadata={
                "filename": artifact.filename,
                "mime_type": _clean_mime_type(artifact.mime_type),
            },
            error=str(exc),
        )

    artifact.extraction_status = extraction.status
    artifact.parser_type = extraction.parser_type
    artifact.parser_confidence = extraction.parser_confidence
    artifact.extracted_text = extraction.text
    artifact.extraction_error = extraction.error
    artifact.parser_metadata = {
        **(artifact.parser_metadata or {}),
        **(extraction.parser_metadata or {}),
    }
    artifact.extracted_at = datetime.now(UTC)
    await db.flush()

    # Interpret figures before the body is merged downstream, so the
    # rendered text carries what the screenshots say. Verified on the KB
    # corpus: an article whose resolution reads "output similar to the
    # image below" has its actual config values only in the image.
    if extraction.status == "completed":
        await _interpret_artifact_figures(
            db, tenant_id=tenant_id, artifact=artifact, data=data
        )

    merged_summary = None
    if extraction.status == "completed" and evidence.raw_object_ref:
        raw = await db.get(RawEvidenceObject, evidence.raw_object_ref)
        if raw is not None and raw.tenant_id == tenant_id:
            payload = await load_raw_payload(raw)
            merged_summary = await synchronize_evidence_artifacts(
                db,
                tenant_id=tenant_id,
                evidence=evidence,
                payload=payload,
                source_id=raw.source_id,
            )

    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(AttachmentArtifact)
            .where(
                AttachmentArtifact.evidence_id == evidence.id,
                AttachmentArtifact.extraction_status.in_(("pending", "processing")),
            )
        )
    ).scalar_one()
    follow_up_ready = int(pending_count or 0) == 0

    event_type = "artifact.extracted"
    if extraction.status == "unsupported":
        event_type = "artifact.unsupported"
    elif extraction.status == "failed":
        event_type = "artifact.extraction_failed"

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="attachment_artifact",
        entity_id=artifact.id,
        event_type=event_type,
        payload={
            "evidence_id": str(evidence.id),
            "filename": artifact.filename,
            "status": extraction.status,
            "parser_type": extraction.parser_type,
            "follow_up_ready": follow_up_ready,
            "error": extraction.error,
        },
    )

    return {
        "artifact_id": str(artifact.id),
        "evidence_id": str(evidence.id),
        "status": extraction.status,
        "parser_type": extraction.parser_type,
        "follow_up_ready": follow_up_ready,
        "merged_summary": merged_summary,
    }
