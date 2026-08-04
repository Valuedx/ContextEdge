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


# What a non-delivery report contributes instead of its boilerplate.
# Short, stable, and carries no addresses.
DELIVERY_FAILURE_MARKER = "[automated delivery failure notification]"


def raw_body_from_payload(payload: dict | None) -> str:
    """The body exactly as the connector supplied it, uncleaned."""
    p = payload or {}
    return (
        p.get("body") or p.get("body_text") or p.get("description")
        or p.get("text") or p.get("snippet") or str(p)[:8000]
    )


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
    raw = raw_body_from_payload(payload)

    from contextedge.services.thread_text_service import (
        is_delivery_failure,
        strip_quoted,
    )

    p = payload or {}
    sender = p.get("from") or p.get("sender") or p.get("author") or p.get("email")
    if is_delivery_failure(raw, sender if isinstance(sender, str) else None):
        # A bounce is the mail system talking about a message, not a
        # message. Its body is remediation boilerplate plus the failed
        # recipients' addresses — which identity extraction would
        # otherwise mine into person entities.
        #
        # Reduced to a marker rather than to nothing: the title falls
        # back to a body snippet, and an empty body would leave the
        # evidence unnameable.
        return DELIVERY_FAILURE_MARKER

    stripped = strip_quoted(raw)
    # Never return nothing where there was something. A message that is
    # entirely quoted still needs a body for the title fallback;
    # suppressing it here would leave the evidence unnameable.
    return stripped or raw


def evidence_content_hash_from_payload(payload: dict | None) -> str:
    """Dedup identity for an upstream row.

    Hashes the RAW body, not the cleaned one, for the same reason the
    caller hashes pre-redaction: dedup must not move when the cleaning
    rules are tuned. If this hashed the stripped text, adding one quote
    marker to the pattern list would change the hash of every message
    that contains it, and the next sync would re-ingest the lot as new.

    It also keeps distinct delivery failures distinct — cleaning reduces
    them all to the same marker, and hashing that would collapse every
    bounce in a source into one row.
    """
    body = raw_body_from_payload(payload)
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
