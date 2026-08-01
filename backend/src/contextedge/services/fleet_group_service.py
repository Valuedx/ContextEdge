"""Fleet / major-incident grouping (backlog B6).

Doc-3's occurrence tier: LPT001, LPT121 and DTP055 all boot-looping
after the same Windows patch is ONE fleet incident. Detection is
deterministic and degraded-mode by design — same blamed change +
enough distinct incidents inside a tight window (issue-signature
sharpening arrives when B3 signatures accumulate); grouping itself is
**reviewer-gated**: the detector only ever writes a suggestion, accept
mints the parent case and attaches ``fleet_member`` memberships (which
the cluster resolver expands — fleet members ARE one occurrence), and
rejection is permanent per change reference.

Two Wi-Fi failures on the same laptop model three months apart share
no change and never suggest anything.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.fleet_group import FleetGroupSuggestion
from contextedge.models.pattern import GraphEdge
from contextedge.services.ticket_bridge_service import _add_membership

logger = structlog.get_logger()

FLEET_MIN_MEMBERS = 3
FLEET_WINDOW_DAYS = 7
FLEET_MEMBER_CONFIDENCE = 0.9
MAX_MEMBERS_PER_GROUP = 200


async def detect_fleet_groups(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    window_days: int = FLEET_WINDOW_DAYS,
    min_members: int = FLEET_MIN_MEMBERS,
) -> dict:
    """Changes blamed by >= min_members distinct incidents within the
    window become pending suggestions. Idempotent per change reference:
    an existing suggestion (any status) is left alone — rejection is
    permanent, acceptance already grouped, pending gets member updates
    only while still pending."""
    counts = {"groups_suggested": 0, "groups_updated": 0}
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    # The grouping key is the change's EXTERNAL id, not its evidence id:
    # re-ingesting a change mints a new content-hashed evidence row, and
    # a rejected group must stay rejected across re-ingestion.
    change_evidence = aliased(EvidenceItem)
    rows = (
        await db.execute(
            select(
                GraphEdge.target_node_id,
                RawEvidenceObject.external_id,
                GraphEdge.source_node_id,
            )
            .join(EvidenceItem, EvidenceItem.id == GraphEdge.source_node_id)
            .outerjoin(
                change_evidence, change_evidence.id == GraphEdge.target_node_id
            )
            .outerjoin(
                RawEvidenceObject,
                RawEvidenceObject.id == change_evidence.raw_object_ref,
            )
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "caused_by_change",
                GraphEdge.source_node_type == "evidence",
                GraphEdge.target_node_type == "evidence",
                GraphEdge.valid_to.is_(None),
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.ingested_at >= cutoff,
            )
            .limit(2000)
        )
    ).all()

    members_by_change: dict[str, set[uuid.UUID]] = {}
    evidence_by_change: dict[str, uuid.UUID] = {}
    for change_id, external_id, incident_id in rows:
        key = external_id or str(change_id)
        members_by_change.setdefault(key, set()).add(incident_id)
        evidence_by_change[key] = change_id

    for change_key, members in members_by_change.items():
        if len(members) < min_members:
            continue
        change_ref = change_key[:120]
        change_id = evidence_by_change[change_key]
        existing = (
            await db.execute(
                select(FleetGroupSuggestion).where(
                    FleetGroupSuggestion.tenant_id == tenant_id,
                    FleetGroupSuggestion.change_ref == change_ref,
                )
            )
        ).scalar_one_or_none()
        member_list = sorted(str(m) for m in members)[:MAX_MEMBERS_PER_GROUP]
        if existing is not None:
            if existing.status == "pending" and set(member_list) != set(
                existing.member_evidence_ids or []
            ):
                existing.member_evidence_ids = member_list
                existing.member_count = len(member_list)
                counts["groups_updated"] += 1
            continue  # accepted stays grouped; rejected stays rejected
        try:
            async with db.begin_nested():
                db.add(
                    FleetGroupSuggestion(
                        tenant_id=tenant_id,
                        change_ref=change_ref,
                        change_evidence_id=change_id,
                        member_evidence_ids=member_list,
                        member_count=len(member_list),
                        status="pending",
                    )
                )
                await db.flush()
            counts["groups_suggested"] += 1
            logger.info(
                "fleet_group.suggested",
                tenant_id=str(tenant_id),
                change_ref=change_ref,
                members=len(member_list),
            )
        except IntegrityError:
            continue
    return counts


async def accept_fleet_group(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    suggestion: FleetGroupSuggestion,
    reviewed_by: str,
) -> dict:
    """Reviewer accept: mint the parent case and attach every member as
    ``fleet_member`` — only from this point do the members cluster
    together under the fleet incident."""
    parent_case_id = uuid.uuid4()
    attached = 0
    for evidence_id in suggestion.member_evidence_ids or []:
        if await _add_membership(
            db,
            tenant_id,
            uuid.UUID(evidence_id),
            parent_case_id,
            "fleet_member",
            FLEET_MEMBER_CONFIDENCE,
            "fleet_group",
        ):
            attached += 1
    suggestion.status = "accepted"
    suggestion.parent_case_id = parent_case_id
    suggestion.reviewed_by = reviewed_by
    suggestion.reviewed_at = datetime.now(UTC)
    await db.flush()

    from contextedge.services.event_log_service import append_operational_event

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="fleet_group",
        entity_id=suggestion.id,
        event_type="correlation.fleet_group_accepted",
        payload={
            "parent_case_id": str(parent_case_id),
            "change_ref": suggestion.change_ref,
            "members": attached,
            "reviewed_by": reviewed_by,
        },
    )
    return {"parent_case_id": str(parent_case_id), "members_attached": attached}


async def reject_fleet_group(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    suggestion: FleetGroupSuggestion,
    reviewed_by: str,
) -> None:
    """Permanent: the unique change_ref row stays, so the detector can
    never re-suggest the group."""
    suggestion.status = "rejected"
    suggestion.reviewed_by = reviewed_by
    suggestion.reviewed_at = datetime.now(UTC)
    await db.flush()
