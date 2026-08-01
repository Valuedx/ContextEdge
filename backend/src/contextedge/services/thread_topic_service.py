"""Thread-topic state machine (backlog A3).

The thread is the unit of conversation; the topic is what it is
currently about. Rules (abstention over guessing throughout):

- **Set on explicit anchors only**: a resolved, non-digest ticket
  mention, or a correction's propagated case. A digest naming three
  tickets sets nothing; a plain mention of a second ticket does not
  steal the topic (first anchor wins until a correction).
- **Provisional topics** mark pre-ticket threads (set from the
  thread's own canonical case at correlate time). No memberships are
  ever written under a provisional topic — the thread's case links
  already group its messages; the row exists so the arriving anchor
  can unify retroactively.
- **Inheritance**: un-anchored, non-dissociative messages in an
  anchored thread gain a ``thread_topic`` membership. The A7 negation
  fence applies — a thread-negated case never propagates.
- **Unification**: when a provisional thread anchors, existing thread
  evidence lacking memberships is linked in one bounded sweep, so the
  40-message thread whose third message said "tracking under
  INC0010427" ends up fully attached, not just messages 4+.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.evidence import EvidenceItem
from contextedge.models.thread_topic import ThreadTopic
from contextedge.services.ticket_bridge_service import (
    _add_membership,
    _thread_negated_case_ids,
    states_dissociation,
)

logger = structlog.get_logger()

THREAD_TOPIC_CONFIDENCE = 0.75
# Bounded retroactive sweep on unification. A thread longer than this
# converges through the per-message path as messages recorrelate.
UNIFY_SWEEP_LIMIT = 200


async def get_thread_topic(
    db: AsyncSession, tenant_id: uuid.UUID, thread_id: uuid.UUID
) -> ThreadTopic | None:
    return (
        await db.execute(
            select(ThreadTopic).where(
                ThreadTopic.tenant_id == tenant_id,
                ThreadTopic.thread_id == thread_id,
            )
        )
    ).scalar_one_or_none()


async def set_thread_topic(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    thread_id: uuid.UUID,
    canonical_case_id: uuid.UUID,
    *,
    provisional: bool,
    set_by: str,
    confidence: float = THREAD_TOPIC_CONFIDENCE,
) -> dict:
    """Upsert the thread's topic. An anchored topic is never demoted to
    provisional; a provisional one anchoring triggers the retroactive
    unification sweep. Topic *changes* between anchored cases happen
    only via corrections (set_by='correction')."""
    counts = {"set": False, "changed": False, "unified": 0}
    topic = await get_thread_topic(db, tenant_id, thread_id)

    if topic is None:
        try:
            async with db.begin_nested():
                db.add(
                    ThreadTopic(
                        tenant_id=tenant_id,
                        thread_id=thread_id,
                        canonical_case_id=canonical_case_id,
                        is_provisional=provisional,
                        set_by=set_by,
                        confidence=confidence,
                    )
                )
                await db.flush()
            counts["set"] = True
        except IntegrityError:
            return counts  # concurrent writer won; next call sees theirs
        if not provisional:
            counts["unified"] = await _unify_thread(
                db, tenant_id, thread_id, canonical_case_id
            )
        return counts

    if provisional:
        return counts  # never demote or reseat with a provisional signal

    if topic.is_provisional:
        # Anchor arrives for a pre-ticket thread: promote + unify.
        topic.is_provisional = False
        topic.canonical_case_id = canonical_case_id
        topic.set_by = set_by
        topic.confidence = confidence
        counts["set"] = True
        counts["unified"] = await _unify_thread(
            db, tenant_id, thread_id, canonical_case_id
        )
        return counts

    if topic.canonical_case_id != canonical_case_id:
        if set_by != "correction":
            # First anchor wins; a competing plain anchor is recorded as
            # a signal, never a silent topic steal (abstention).
            logger.info(
                "thread_topic.competing_anchor",
                tenant_id=str(tenant_id),
                thread_id=str(thread_id),
                current_case=str(topic.canonical_case_id),
                competing_case=str(canonical_case_id),
            )
            return counts
        from contextedge.services.event_log_service import append_operational_event

        old_case = topic.canonical_case_id
        topic.canonical_case_id = canonical_case_id
        topic.set_by = set_by
        topic.confidence = confidence
        counts["set"] = True
        counts["changed"] = True
        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="thread",
            entity_id=thread_id,
            event_type="thread.topic_changed",
            payload={
                "old_case_id": str(old_case),
                "new_case_id": str(canonical_case_id),
                "set_by": set_by,
            },
        )
    return counts


async def _unify_thread(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    thread_id: uuid.UUID,
    canonical_case_id: uuid.UUID,
) -> int:
    """Retroactive attach: thread evidence with no active non-mentioned
    membership gains the topic case. Dissociative messages and
    thread-negated cases are skipped — the A1/A7 guards hold here."""
    if canonical_case_id in await _thread_negated_case_ids(
        db, tenant_id, thread_id
    ):
        return 0
    evidence_rows = (
        (
            await db.execute(
                select(EvidenceItem)
                .where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.thread_id == thread_id,
                )
                .order_by(EvidenceItem.ingested_at.asc())
                .limit(UNIFY_SWEEP_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not evidence_rows:
        return 0
    anchored = set(
        (
            await db.execute(
                select(EvidenceCaseMembership.evidence_id).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id.in_(
                        tuple(ev.id for ev in evidence_rows)
                    ),
                    EvidenceCaseMembership.status == "active",
                    EvidenceCaseMembership.relationship_type != "mentioned_only",
                )
            )
        )
        .scalars()
        .all()
    )
    unified = 0
    for ev in evidence_rows:
        if ev.id in anchored or states_dissociation(ev):
            continue
        if await _add_membership(
            db,
            tenant_id,
            ev.id,
            canonical_case_id,
            "thread_topic",
            THREAD_TOPIC_CONFIDENCE,
            "thread_topic",
        ):
            unified += 1
    if unified:
        logger.info(
            "thread_topic.unified",
            tenant_id=str(tenant_id),
            thread_id=str(thread_id),
            unified=unified,
        )
    return unified


async def apply_thread_topic(
    db: AsyncSession, tenant_id: uuid.UUID, evidence: EvidenceItem
) -> dict:
    """Per-message inheritance: an un-anchored, non-dissociative message
    in an anchored thread joins the topic case."""
    counts = {"applied": 0, "skipped": None}
    thread_id = getattr(evidence, "thread_id", None)
    if thread_id is None:
        return counts
    topic = await get_thread_topic(db, tenant_id, thread_id)
    if topic is None or topic.is_provisional:
        counts["skipped"] = "no_anchored_topic"
        return counts
    if states_dissociation(evidence):
        counts["skipped"] = "dissociative"
        return counts
    existing = (
        await db.execute(
            select(EvidenceCaseMembership.id).where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.evidence_id == evidence.id,
                EvidenceCaseMembership.status == "active",
                EvidenceCaseMembership.relationship_type != "mentioned_only",
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        counts["skipped"] = "already_anchored"
        return counts
    if topic.canonical_case_id in await _thread_negated_case_ids(
        db, tenant_id, thread_id
    ):
        counts["skipped"] = "negated"
        return counts
    if await _add_membership(
        db,
        tenant_id,
        evidence.id,
        topic.canonical_case_id,
        "thread_topic",
        topic.confidence,
        "thread_topic",
    ):
        counts["applied"] = 1
    return counts
