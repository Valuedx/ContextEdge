"""Case correlation service for linking evidence across sources."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import CorrelationEdge
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject, Thread
from contextedge.models.session import CaseLink
from contextedge.models.source import Source
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.identity_service import (
    find_related_evidence_ids_by_identity_ids,
    get_identity_ids_for_evidence,
)


async def create_correlation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_evidence_id: uuid.UUID,
    target_evidence_id: uuid.UUID,
    correlation_type: str,
    confidence: float,
    explanation: str | None = None,
    created_by: str = "system",
) -> CorrelationEdge:
    """Create a correlation edge between two evidence items."""
    edge = CorrelationEdge(
        tenant_id=tenant_id,
        source_evidence_id=source_evidence_id,
        target_evidence_id=target_evidence_id,
        correlation_type=correlation_type,
        confidence=confidence,
        explanation=explanation,
        created_by=created_by,
    )
    db.add(edge)
    await db.flush()
    return edge


async def get_correlated_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> list[CorrelationEdge]:
    """Get all evidence correlated to a given evidence item."""
    result = await db.execute(
        select(CorrelationEdge).where(
            CorrelationEdge.tenant_id == tenant_id,
            (CorrelationEdge.source_evidence_id == evidence_id)
            | (CorrelationEdge.target_evidence_id == evidence_id),
        )
    )
    return list(result.scalars().all())


def extract_case_link_candidates(
    *,
    source_type: str,
    raw_object: RawEvidenceObject | None,
    thread_external_id: str | None = None,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(system: str, external_id: str | None) -> None:
        if not external_id:
            return
        key = (system, str(external_id))
        if key in seen:
            return
        seen.add(key)
        candidates.append(key)

    if raw_object is not None:
        add(source_type, raw_object.external_id)
        payload = raw_object.raw_payload if isinstance(raw_object.raw_payload, dict) else {}
        add(f"{source_type}:thread", payload.get("_thread_id"))

    add(f"{source_type}:thread", thread_external_id)
    return candidates


async def correlate_evidence_item(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> dict:
    evidence = await db.get(EvidenceItem, evidence_id)
    if evidence is None or evidence.tenant_id != tenant_id:
        return {"status": "skipped", "reason": "evidence_not_found"}

    source = await db.get(Source, evidence.source_id)
    if source is None or source.tenant_id != tenant_id:
        return {"status": "skipped", "reason": "source_not_found"}

    raw_object = None
    if evidence.raw_object_ref is not None:
        raw_object = await db.get(RawEvidenceObject, evidence.raw_object_ref)

    thread_external_id = None
    if evidence.thread_id is not None:
        thread = await db.get(Thread, evidence.thread_id)
        if thread is not None:
            thread_external_id = thread.external_thread_id

    candidates = extract_case_link_candidates(
        source_type=source.source_type or "unknown",
        raw_object=raw_object,
        thread_external_id=thread_external_id,
    )
    existing_links: list[CaseLink] = []
    for system, external_id in candidates:
        result = await db.execute(
            select(CaseLink).where(
                CaseLink.tenant_id == tenant_id,
                CaseLink.system == system,
                CaseLink.external_id == external_id,
            )
        )
        existing_links.extend(result.scalars().all())

    canonical_case_id = (
        existing_links[0].canonical_case_id if existing_links else (uuid.uuid4() if candidates else None)
    )
    related_evidence_ids = {
        link.evidence_id
        for link in existing_links
        if link.evidence_id is not None and link.evidence_id != evidence.id
    }
    identity_ids = await get_identity_ids_for_evidence(db, tenant_id, evidence.id)
    identity_related_evidence_ids = await find_related_evidence_ids_by_identity_ids(
        db,
        tenant_id,
        identity_ids,
        exclude_evidence_id=evidence.id,
    )
    related_evidence_ids.update(identity_related_evidence_ids)
    if not candidates and not identity_related_evidence_ids:
        return {"status": "skipped", "reason": "no_candidates"}

    now = datetime.now(timezone.utc)
    created_links = 0
    updated_links = 0

    for system, external_id in candidates:
        link = next(
            (
                row
                for row in existing_links
                if row.system == system and row.external_id == external_id
            ),
            None,
        )
        if link is None:
            db.add(
                CaseLink(
                    tenant_id=tenant_id,
                    canonical_case_id=canonical_case_id,
                    system=system,
                    external_id=external_id,
                    evidence_id=evidence.id,
                    confidence=1.0,
                    first_seen=now,
                    last_seen=now,
                )
            )
            created_links += 1
            continue

        link.canonical_case_id = canonical_case_id
        link.evidence_id = evidence.id
        link.last_seen = now
        link.confidence = max(float(link.confidence or 0.0), 1.0)
        updated_links += 1

    correlations_created = 0
    for related_evidence_id in related_evidence_ids:
        edge = (
            await db.execute(
                select(CorrelationEdge).where(
                    CorrelationEdge.tenant_id == tenant_id,
                    or_(
                        and_(
                            CorrelationEdge.source_evidence_id == evidence.id,
                            CorrelationEdge.target_evidence_id == related_evidence_id,
                        ),
                        and_(
                            CorrelationEdge.source_evidence_id == related_evidence_id,
                            CorrelationEdge.target_evidence_id == evidence.id,
                        ),
                    ),
                )
            )
        ).scalar_one_or_none()
        if edge is not None:
            continue
        await create_correlation(
            db,
            tenant_id,
            evidence.id,
            related_evidence_id,
            "case_link_match" if related_evidence_id not in identity_related_evidence_ids else "identity_match",
            1.0 if related_evidence_id not in identity_related_evidence_ids else 0.65,
            explanation=(
                f"Matched canonical case {canonical_case_id}"
                if related_evidence_id not in identity_related_evidence_ids
                else "Matched one or more canonical identities"
            ),
            created_by="correlation_worker",
        )
        correlations_created += 1

    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="evidence_item",
        entity_id=evidence.id,
        event_type="correlation.case_linked",
        payload={
            "canonical_case_id": str(canonical_case_id),
            "candidate_count": len(candidates),
            "case_links_created": created_links,
            "case_links_updated": updated_links,
            "correlations_created": correlations_created,
            "identity_match_candidates": len(identity_related_evidence_ids),
        },
    )
    return {
        "status": "ok",
        "canonical_case_id": str(canonical_case_id) if canonical_case_id else None,
        "candidate_count": len(candidates),
        "case_links_created": created_links,
        "case_links_updated": updated_links,
        "correlations_created": correlations_created,
        "identity_match_candidates": len(identity_related_evidence_ids),
    }
