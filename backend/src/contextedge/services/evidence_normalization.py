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

# What a message contributes when everything in it was already said
# earlier in the same thread.
QUOTED_ONLY_MARKER = "[quoted history only - no new text]"


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
    from contextedge.services.thread_text_service import (
        is_delivery_failure,
        strip_quoted,
        strip_trailing_boilerplate,
    )

    p = payload or {}
    sender = p.get("from") or p.get("sender") or p.get("author") or p.get("email")

    # Hydration made this call with the whole thread in hand and emptied
    # the body accordingly; re-deriving it from what is left would ask
    # the question of a body that no longer contains the answer.
    if p.get("is_delivery_failure") is True:
        return DELIVERY_FAILURE_MARKER

    # A message whose body cleaned down to nothing is empty. It is not an
    # invitation to describe the payload instead.
    #
    # raw_body_from_payload ends its `or` chain at ``str(payload)``,
    # which is right for records that have no body concept at all — a CI
    # record or a config object is better searchable as its own fields
    # than as nothing. Here it is exactly wrong: an emptied body falls
    # through to a repr of the payload that still holds ``body_original``
    # — the quoted history this stripping exists to remove, and a
    # bounce's recipient addresses — and puts it back into the text that
    # gets embedded, chunked and read. One live message was carrying
    # 2,408 characters of dict repr as its evidence body.
    if "body" in p and not str(p.get("body") or "").strip():
        return QUOTED_ONLY_MARKER

    raw = raw_body_from_payload(p)
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

    # Signatures and legal disclaimers go here too, for the same reason
    # quote stripping does: most conversational evidence never passes
    # through hydration, and a signature carries the sender's name, title,
    # phone and employer into identity extraction on every single message.
    stripped = strip_trailing_boilerplate(strip_quoted(raw))
    # A message that was entirely a quote contributed no new text, and
    # returning the quote instead re-embeds the whole conversation for a
    # message that added nothing to it. Marked rather than emptied: the
    # title falls back to a body snippet, so nothing here may return "".
    return stripped or QUOTED_ONLY_MARKER


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
