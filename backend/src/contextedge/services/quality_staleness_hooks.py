"""Event-driven quality staleness — link evidence and policy changes to playbooks."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookEvidenceLink, PlaybookVersion
from contextedge.services.playbook_quality_service import (
    STALE_ONTOLOGY_CHANGED,
    STALE_POLICY_CHANGED,
    STALE_SOURCE_CHANGED,
    signal_quality_stale,
)

logger = structlog.get_logger()


async def playbook_ids_linked_to_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Playbooks citing any of these evidence items on a published version."""
    if not evidence_ids:
        return []
    rows = (
        await db.execute(
            select(Playbook.id)
            .join(PlaybookVersion, PlaybookVersion.playbook_id == Playbook.id)
            .join(
                PlaybookEvidenceLink,
                PlaybookEvidenceLink.playbook_version_id == PlaybookVersion.id,
            )
            .where(
                Playbook.tenant_id == tenant_id,
                PlaybookEvidenceLink.evidence_id.in_(tuple(evidence_ids)),
            )
            .distinct()
        )
    ).scalars().all()
    return list(rows)


async def signal_stale_for_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
    *,
    origin: str = "evidence_changed",
) -> int:
    """Mark assessments stale for every playbook linked to the evidence."""
    if not evidence_ids:
        return 0
    playbook_ids = await playbook_ids_linked_to_evidence(db, tenant_id, evidence_ids)
    total = 0
    for playbook_id in playbook_ids:
        total += await signal_quality_stale(
            db,
            tenant_id,
            playbook_id,
            reason=STALE_SOURCE_CHANGED,
            origin=origin,
        )
    if total:
        logger.info(
            "quality_staleness.evidence",
            tenant_id=str(tenant_id),
            evidence_count=len(evidence_ids),
            playbooks=len(playbook_ids),
            assessments=total,
            origin=origin,
        )
    return total


async def signal_stale_for_tenant_policy_change(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    origin: str = "policy_pack_changed",
) -> int:
    """Mark assessments stale when the active policy pack changes."""
    return await _signal_stale_all_tenant_playbooks(
        db, tenant_id, reason=STALE_POLICY_CHANGED, origin=origin
    )


async def signal_stale_for_tenant_ontology_change(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    origin: str = "ontology_changed",
) -> int:
    """Mark assessments stale when the active product ontology changes."""
    return await _signal_stale_all_tenant_playbooks(
        db, tenant_id, reason=STALE_ONTOLOGY_CHANGED, origin=origin
    )


async def _signal_stale_all_tenant_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    reason: str,
    origin: str,
) -> int:
    rows = (
        await db.execute(
            select(Playbook.id).where(Playbook.tenant_id == tenant_id)
        )
    ).scalars().all()
    total = 0
    for playbook_id in rows:
        total += await signal_quality_stale(
            db,
            tenant_id,
            playbook_id,
            reason=reason,
            origin=origin,
        )
    if total:
        logger.info(
            "quality_staleness.tenant",
            tenant_id=str(tenant_id),
            playbooks=len(rows),
            assessments=total,
            reason=reason,
            origin=origin,
        )
    return total
