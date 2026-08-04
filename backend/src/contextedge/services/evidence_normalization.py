"""Shared helpers for converting raw payloads into normalized evidence fields."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, Thread


def evidence_title_from_payload(payload: dict | None) -> str:
    p = payload or {}
    # 1. Try common title/subject fields
    title = (
        p.get("title") or p.get("subject") or p.get("summary")
        or p.get("short_description") or p.get("subject_line")
    )
    if title and isinstance(title, str) and title.strip():
        return title.strip()

    # 2. Try common name/filename fields
    name = (
        p.get("filename") or p.get("file_name") or p.get("name")
        or p.get("display_name") or p.get("key")
    )
    if name and isinstance(name, str) and name.strip():
        return name.strip()

    # 3. Fallback to a snippet of the body
    body = (p.get("body") or p.get("body_text") or p.get("description")
            or p.get("text") or p.get("snippet"))

    if body and isinstance(body, str) and body.strip():
        # Take first 60 chars, clean up newlines
        snippet = " ".join(body.split())[:60].strip()
        if snippet:
            return f"{snippet}..." if len(body) > 60 else snippet

    # 4. Global generic fallback
    return "Untitled Evidence"


def evidence_body_from_payload(payload: dict | None) -> str:
    """The text this evidence contributes.

    Quoted history is stripped here rather than only during thread
    hydration, because most conversational evidence never passes through
    hydration at all: chat messages, emails and work notes arrive from
    their connectors as individual items and go straight to
    normalization. Stripping in one place only would leave every one of
    those carrying the whole prior conversation.

    Structural stripping only — cutting at a quote marker needs nothing
    but the message itself. Removing text that merely appeared in an
    EARLIER message requires the thread in arrival order, which exists
    during hydration and not here.

    Measured on 304 real Zoho messages: 3,051,681 characters of raw body
    reduced to 230,811 — 92% of what would otherwise be embedded,
    chunked and read by every extractor was the same conversation
    repeated.
    """
    p = payload or {}
    raw = (
        p.get("body") or p.get("body_text") or p.get("description")
        or p.get("text") or p.get("snippet") or str(p)[:8000]
    )

    from contextedge.services.thread_text_service import strip_quoted

    stripped = strip_quoted(raw)
    # Never return nothing where there was something. A message that is
    # entirely quoted still needs a body for the title fallback and the
    # content hash; suppressing it here would make distinct replies
    # collide on an empty hash and dedupe each other away.
    return stripped or raw


def evidence_content_hash_from_payload(payload: dict | None) -> str:
    body = evidence_body_from_payload(payload)
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


async def ensure_thread_for_evidence(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
) -> uuid.UUID | None:
    """Create or find the Thread for this evidence based on ``_thread_id`` in the payload.

    Links the evidence to the thread and returns the thread's UUID, or ``None``
    if the payload carries no thread reference.
    """
    external_thread_id = (payload or {}).get("_thread_id")
    if not external_thread_id:
        return None

    source_id = evidence.source_id
    thread = (
        await db.execute(
            select(Thread).where(
                Thread.tenant_id == tenant_id,
                Thread.source_id == source_id,
                Thread.external_thread_id == str(external_thread_id),
            )
        )
    ).scalar_one_or_none()

    if thread is None:
        title = evidence_title_from_payload(payload)
        thread = Thread(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=evidence.source_object_id,
            external_thread_id=str(external_thread_id),
            title=title[:500] if title else None,
            hydration_status="pending",
        )
        db.add(thread)
        await db.flush()

    evidence.thread_id = thread.id
    await db.flush()
    return thread.id
