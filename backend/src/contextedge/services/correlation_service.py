"""Case correlation service for linking evidence across sources.

Two correlation tiers:

- **Case links** — deterministic external case / thread identifiers,
  confidence 1.0.
- **Identity co-occurrence** — gated, scored, and time-windowed. A shared
  person alone must never correlate two incidents ("John Smith worked on
  Incident A in January and commented on Incident B in July"); shared
  non-person entities (devices, services) within the window carry the
  signal, and provisional / needs-review identities carry none.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import (
    CanonicalIdentity,
    CorrelationEdge,
    EvidenceIdentityLink,
)
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject, Thread
from contextedge.models.session import CaseLink
from contextedge.models.source import Source
from contextedge.services.artifact_extraction_service import load_raw_payload
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.identity_service import get_identity_ids_for_evidence

logger = structlog.get_logger()

# Identity co-occurrence only counts within this window; outside it two
# mentions of the same entity are unrelated operational history.
IDENTITY_CORRELATION_WINDOW = timedelta(days=7)


def _identity_correlation_signal(
    shared_identity_ids: set[uuid.UUID],
    identity_types: dict[uuid.UUID, str],
) -> tuple[float, str] | None:
    """Score a shared-identity correlation, or None when the signal is too
    weak to record (person-only single identity)."""
    non_person = [
        identity_id
        for identity_id in shared_identity_ids
        if identity_types.get(identity_id) not in (None, "person")
    ]
    if non_person:
        confidence = 0.65 + (0.1 if len(shared_identity_ids) >= 2 else 0.0)
        return min(confidence, 0.75), "Shared non-person entity within time window"
    if len(shared_identity_ids) >= 2:
        return 0.5, "Multiple shared identities within time window"
    return None


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


def extract_case_link_candidates(
    *,
    source_type: str,
    raw_object: RawEvidenceObject | None,
    raw_payload: dict | None = None,
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
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        add(f"{source_type}:thread", payload.get("_thread_id"))
        if source_type == "servicenow":
            # Task-to-task reference sys_ids (problem_id / rfc / caused_by /
            # parent_incident) join the same key namespace as the referenced
            # record's own external_id — so incident↔problem↔change
            # correlate at 1.0 regardless of ingestion order. cmdb_ci /
            # assignment_group are deliberately NOT case-link keys (shared
            # infrastructure would mass-merge unrelated cases); they go
            # through the entity path in servicenow_reference_service.
            from contextedge.services.servicenow_reference_service import (
                extract_task_references,
            )

            for _edge_type, ref_sys_id in extract_task_references(payload):
                add(source_type, ref_sys_id)

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
    raw_payload: dict | None = None
    if evidence.raw_object_ref is not None:
        raw_object = await db.get(RawEvidenceObject, evidence.raw_object_ref)
        if raw_object is not None:
            try:
                raw_payload = await load_raw_payload(raw_object)
            except (ValueError, Exception):
                raw_payload = (
                    raw_object.raw_payload if isinstance(raw_object.raw_payload, dict) else {}
                )

    thread_external_id = None
    if evidence.thread_id is not None:
        thread = await db.get(Thread, evidence.thread_id)
        if thread is not None:
            thread_external_id = thread.external_thread_id

    candidates = extract_case_link_candidates(
        source_type=source.source_type or "unknown",
        raw_object=raw_object,
        raw_payload=raw_payload,
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
        existing_links[0].canonical_case_id
        if existing_links
        else (uuid.uuid4() if candidates else None)
    )
    related_evidence_ids = {
        link.evidence_id
        for link in existing_links
        if link.evidence_id is not None and link.evidence_id != evidence.id
    }
    # Remember which relations are backed by a deterministic case link —
    # when both signals exist, the 1.0 case-link tier must win over the
    # fuzzy identity tier (edges are created once and never upgraded).
    case_link_related_ids = set(related_evidence_ids)

    # Identity tier: only resolved/verified identities carry correlation
    # signal — a provisional identity is an unreviewed guess.
    identity_ids = await get_identity_ids_for_evidence(db, tenant_id, evidence.id)
    identity_types: dict[uuid.UUID, str] = {}
    if identity_ids:
        type_rows = await db.execute(
            select(CanonicalIdentity.id, CanonicalIdentity.entity_type).where(
                CanonicalIdentity.id.in_(tuple(identity_ids)),
                CanonicalIdentity.tenant_id == tenant_id,
                CanonicalIdentity.is_active.is_(True),
                CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
            )
        )
        identity_types = {row[0]: row[1] for row in type_rows.all()}

    shared_by_evidence: dict[uuid.UUID, set[uuid.UUID]] = {}
    if identity_types:
        link_rows = await db.execute(
            select(
                EvidenceIdentityLink.evidence_id,
                EvidenceIdentityLink.identity_id,
            ).where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(identity_types)),
                EvidenceIdentityLink.evidence_id != evidence.id,
            )
        )
        for related_id, identity_id in link_rows.all():
            shared_by_evidence.setdefault(related_id, set()).add(identity_id)

    # Time-window gate for the identity tier.
    related_times: dict[uuid.UUID, datetime | None] = {}
    if shared_by_evidence:
        time_rows = await db.execute(
            select(EvidenceItem.id, EvidenceItem.ingested_at).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id.in_(tuple(shared_by_evidence)),
            )
        )
        related_times = {row[0]: row[1] for row in time_rows.all()}

    evidence_time = evidence.ingested_at
    identity_correlations: dict[uuid.UUID, tuple[float, str]] = {}
    for related_id, shared in shared_by_evidence.items():
        related_time = related_times.get(related_id)
        # Fail closed on missing timestamps: the identity tier is gated on
        # time proximity, and an unknown time cannot prove proximity.
        if evidence_time is None or related_time is None:
            continue
        if abs(evidence_time - related_time) > IDENTITY_CORRELATION_WINDOW:
            continue
        signal = _identity_correlation_signal(shared, identity_types)
        if signal is None:
            continue
        identity_correlations[related_id] = signal

    identity_related_evidence_ids = set(identity_correlations)
    related_evidence_ids.update(identity_related_evidence_ids)
    if not candidates and not identity_related_evidence_ids:
        return {"status": "skipped", "reason": "no_candidates"}

    now = datetime.now(UTC)
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

        # Keep the link's original evidence anchor: overwriting evidence_id
        # made the row a pointer to whatever evidence arrived last instead
        # of a stable case-membership record.
        link.canonical_case_id = canonical_case_id
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
        if related_evidence_id in case_link_related_ids:
            # Deterministic tier wins even when identities are also shared.
            confidence = 1.0
            explanation = f"Matched canonical case {canonical_case_id}"
            correlation_type = "case_link_match"
        else:
            confidence, explanation = identity_correlations[related_evidence_id]
            correlation_type = "identity_match"
        await create_correlation(
            db,
            tenant_id,
            evidence.id,
            related_evidence_id,
            correlation_type,
            confidence,
            explanation=explanation,
            created_by="correlation_worker",
        )
        correlations_created += 1

    # ServiceNow reference enrichment (Phase 1): typed evidence→evidence
    # edges + CI / assignment-group entities from the reference fields the
    # connector now ingests. Session autoflush makes the CaseLink rows
    # added above visible to reverse healing's SELECT. Fail-soft: a
    # failure here loses enrichment, never the correlation itself.
    snow_references: dict | None = None
    if (source.source_type or "") == "servicenow" and isinstance(raw_payload, dict):
        try:
            from contextedge.services.servicenow_reference_service import (
                process_servicenow_references,
            )

            # SAVEPOINT so a database error inside enrichment rolls back
            # its partial edges and the session stays usable for the
            # flush + operational event below.
            async with db.begin_nested():
                snow_references = await process_servicenow_references(
                    db,
                    tenant_id,
                    evidence,
                    raw_payload,
                    own_sys_id=raw_object.external_id if raw_object is not None else None,
                )
        except Exception as exc:
            logger.warning(
                "servicenow_reference.enrichment_failed",
                tenant_id=str(tenant_id),
                evidence_id=str(evidence.id),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="evidence_item",
        entity_id=evidence.id,
        event_type="correlation.case_linked",
        payload={
            "canonical_case_id": str(canonical_case_id) if canonical_case_id else None,
            "candidate_count": len(candidates),
            "case_links_created": created_links,
            "case_links_updated": updated_links,
            "correlations_created": correlations_created,
            "identity_match_candidates": len(identity_related_evidence_ids),
            "servicenow_references": snow_references,
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
        "servicenow_references": snow_references,
    }
