"""State-transition events as evidence (diagnosis roadmap B2).

Most incident-causing changes never get a change record (measured:
``increased_user_load``, ``log_accumulation``, ``wan_interruption``, a
browser auto-upgrade — none would appear in change management). The
graph therefore holds observed EVENTS: discrete state transitions
("version 118 -> 119", "config key changed"), never metric samples —
metrics stay in the monitoring stack.

Events bypass the LLM pipeline entirely. They arrive structured, so
they are born classified (``operational``), born summarized (the title
IS the summary), and never touch classification, extraction, or the
budget gate. That is what makes "just keep them" affordable.

Retention: events are diagnostic within days, not months. They carry
``evidence_type="event"`` so the retention machinery can TTL them
independently of tickets.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import select

from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()

EVENT_EVIDENCE_TYPE = "event"
EVENTS_SOURCE_TYPE = "internal_events"


async def _events_source_id(db, tenant_id: uuid.UUID) -> uuid.UUID:
    """Find-or-create the tenant's synthetic events source.

    evidence_items.source_id is NOT NULL — every row belongs to a
    Source — and observed events have no connector. One push-mode
    source per tenant owns them all; owner is the tenant's first user
    (a Source requires an owner, and a tenant with no users has nobody
    to diagnose for anyway).
    """
    from contextedge.models.source import Source
    from contextedge.models.tenant import User

    source_id = (
        await db.execute(
            select(Source.id)
            .where(
                Source.tenant_id == tenant_id,
                Source.source_type == EVENTS_SOURCE_TYPE,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if source_id is not None:
        return source_id
    owner_id = (
        await db.execute(
            select(User.id).where(User.tenant_id == tenant_id).limit(1)
        )
    ).scalar_one_or_none()
    if owner_id is None:
        raise ValueError("tenant has no users to own the events source")
    source = Source(
        tenant_id=tenant_id,
        source_type=EVENTS_SOURCE_TYPE,
        display_name="Internal Observations (events)",
        owner_user_id=owner_id,
        auth_type="none",
        auth_status="connected",
        discovery_status="complete",
        sync_mode="push",
    )
    db.add(source)
    await db.flush()
    return source.id


async def record_state_event(
    db,
    tenant_id: uuid.UUID,
    *,
    ci_name: str,
    event_kind: str,
    from_value: str | None,
    to_value: str | None,
    occurred_at: datetime,
    source_label: str = "inventory_diff",
    domain_id: uuid.UUID | None = None,
    detail: str | None = None,
) -> EvidenceItem | None:
    """Record one observed state transition, linked to its CI.

    Idempotent on (tenant, title, occurred_at): the inventory differ
    re-observing the same transition must not mint a second event.
    Returns the evidence row, or None when it already existed.
    """
    transition = " -> ".join(v for v in (from_value, to_value) if v)
    title = f"{ci_name}: {event_kind}" + (f" {transition}" if transition else "")
    title = title[:500]

    existing = (
        await db.execute(
            select(EvidenceItem.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.evidence_type == EVENT_EVIDENCE_TYPE,
                EvidenceItem.title == title,
                EvidenceItem.evidence_time == occurred_at,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    body_lines = [
        f"Observed state transition on {ci_name}.",
        f"Kind: {event_kind}",
    ]
    if from_value:
        body_lines.append(f"From: {from_value}")
    if to_value:
        body_lines.append(f"To: {to_value}")
    if detail:
        body_lines.append(detail[:1000])
    body_lines.append(f"Source: {source_label}")

    ev = EvidenceItem(
        tenant_id=tenant_id,
        domain_id=domain_id,
        source_id=await _events_source_id(db, tenant_id),
        evidence_type=EVENT_EVIDENCE_TYPE,
        source_type=source_label,
        title=title,
        body_text="\n".join(body_lines),
        # Born classified and born summarized — no LLM ever runs on an
        # event, which is the entire cost model of this layer.
        body_summary=title,
        relevance_state="operational",
        relevance_score=1.0,
        evidence_time=occurred_at,
    )
    db.add(ev)
    await db.flush()

    # affects_ci to the CI entity, find-or-create — the same join key
    # incidents and change records use, which is what makes the
    # diagnosis-time window query (B4) a pure graph join.
    try:
        entity = (
            await db.execute(
                select(Entity)
                .where(
                    Entity.tenant_id == tenant_id,
                    Entity.name == ci_name[:255],
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if entity is None:
            entity = Entity(
                tenant_id=tenant_id,
                name=ci_name[:255],
                entity_type="configuration_item",
            )
            db.add(entity)
            await db.flush()

        from contextedge.graph.builder import ensure_edge

        await ensure_edge(
            db,
            tenant_id,
            source_type="evidence",
            source_id=ev.id,
            target_type="entity",
            target_id=entity.id,
            edge_type="affects_ci",
            weight=1.0,
            confidence=1.0,
            domain_id=domain_id,
        )
    except Exception as exc:
        # The event row is the record; the edge is reach. Degrade, never
        # drop the observation over graph bookkeeping.
        logger.warning(
            "event_evidence.ci_link_failed",
            evidence_id=str(ev.id),
            ci=ci_name[:80],
            error=str(exc),
        )

    logger.info(
        "event_evidence.recorded",
        evidence_id=str(ev.id),
        ci=ci_name[:80],
        kind=event_kind,
    )
    return ev
