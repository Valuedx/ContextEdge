"""Deterministic attachment artifact extraction and evidence merge helpers."""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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
        filename = str(attachment.get("filename") or attachment.get("name") or f"attachment-{index + 1}")
        mime_type = (
            str(attachment.get("mime_type") or attachment.get("content_type") or "application/octet-stream")
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
    for artifact in attachments:
        if artifact.extraction_status != "completed" or not artifact.extracted_text:
            continue
        snippet = artifact.extracted_text.strip()[:MAX_ATTACHMENT_TEXT_CHARS]
        if not snippet:
            continue
        parser_label = artifact.parser_type or "deterministic"
        sections.append(f"[Attachment: {artifact.filename} | parser={parser_label}]\n{snippet}")
    if not sections:
        return None
    return "\n\n".join(sections)[:MAX_COMBINED_BODY_CHARS]


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
        1 for artifact in attachments if artifact.extraction_status == "completed" and artifact.extracted_text
    )

    evidence.title = evidence_title_from_payload(payload)[:500]
    combined_body = build_combined_evidence_body(evidence_body_from_payload(payload), attachments)
    evidence.body_text = combined_body
    evidence.body_summary = combined_body[:500] if combined_body else None
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)

    identity_content = "\n".join(part for part in [evidence.title or "", evidence.body_text or ""] if part)
    if identity_content.strip():
        await link_evidence_identities(
            db,
            tenant_id=tenant_id,
            evidence=evidence,
            content=identity_content,
            source_id=source_id,
            source_metadata={"raw_object_id": str(evidence.raw_object_ref)} if evidence.raw_object_ref else None,
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
            parser_metadata={"filename": artifact.filename, "mime_type": _clean_mime_type(artifact.mime_type)},
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
    artifact.extracted_at = datetime.now(timezone.utc)
    await db.flush()

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
