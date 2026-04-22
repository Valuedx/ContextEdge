"""Shared helpers for converting raw payloads into normalized evidence fields."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, Thread


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    # Remove common prefixes: Re:, Fw:, URGENT:, [EXTERNAL], etc.
    t = re.sub(r"(?i)^(re|fw|fwd|urgent|\[[^\]]+\]|important|alert):\s*", "", title.strip())
    # Collapse whitespace
    return " ".join(t.lower().split())


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
    p = payload or {}
    return (
        p.get("body") or p.get("body_text") or p.get("description")
        or p.get("text") or p.get("snippet") or str(p)[:8000]
    )


def evidence_content_hash_from_payload(payload: dict | None) -> str:
    body = evidence_body_from_payload(payload)
    # Normalize for hashing: collapse all whitespace, lower case, strip basic reply markers
    norm_body = " ".join(body.split()).lower()
    norm_body = re.sub(r"^[> \t]+", "", norm_body, flags=re.MULTILINE)
    return hashlib.sha256(norm_body.encode("utf-8", errors="replace")).hexdigest()


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
    p = payload or {}
    external_thread_id = p.get("_thread_id")
    source_id = evidence.source_id
    
    thread = None

    # 1. Try explicit external thread ID if available
    if external_thread_id:
        thread = (
            await db.execute(
                select(Thread).where(
                    Thread.tenant_id == tenant_id,
                    Thread.source_id == source_id,
                    Thread.external_thread_id == str(external_thread_id),
                )
            )
        ).scalar_one_or_none()

    # 2. Fallback: Title-based matching for fragmented trails
    if thread is None:
        raw_title = evidence_title_from_payload(p)
        norm_title = _normalize_title(raw_title)
        
        # Check for existing threads within the last 30 days with the same normalized title
        since = datetime.now(timezone.utc) - timedelta(days=30) if "timezone" in globals() else datetime.utcnow() - timedelta(days=30)
        
        # We need to fetch threads and compare normalized titles (or we could index norm_title)
        # For now, we'll fetch recent threads from this source
        recent_threads = await db.execute(
            select(Thread).where(
                Thread.tenant_id == tenant_id,
                Thread.source_id == source_id,
                Thread.last_message_at >= since
            ).order_by(Thread.last_message_at.desc()).limit(20)
        )
        
        for t in recent_threads.scalars().all():
            if _normalize_title(t.title) == norm_title:
                thread = t
                break

    if thread is None:
        raw_title = evidence_title_from_payload(p)
        thread = Thread(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=evidence.source_object_id,
            external_thread_id=str(external_thread_id) if external_thread_id else f"fuzzy:{uuid.uuid4()}",
            title=raw_title[:500] if raw_title else None,
            hydration_status="pending",
            first_message_at=evidence.created_at_source or evidence.ingested_at,
            last_message_at=evidence.created_at_source or evidence.ingested_at,
        )
        db.add(thread)
        await db.flush()
    else:
        # Update thread timestamps
        msg_ts = evidence.created_at_source or evidence.ingested_at
        if not thread.first_message_at or msg_ts < thread.first_message_at:
            thread.first_message_at = msg_ts
        if not thread.last_message_at or msg_ts > thread.last_message_at:
            thread.last_message_at = msg_ts

    evidence.thread_id = thread.id
    await db.flush()
    return thread.id
