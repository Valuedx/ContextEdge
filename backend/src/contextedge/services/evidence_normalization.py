"""Shared helpers for converting raw payloads into normalized evidence fields."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, Thread


def evidence_title_from_payload(payload: dict | None) -> str:
    body = payload or {}
    return body.get("title") or body.get("subject") or "Untitled"


def evidence_body_from_payload(payload: dict | None) -> str:
    body = payload or {}
    return body.get("body") or body.get("body_text") or str(body)[:8000]


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
