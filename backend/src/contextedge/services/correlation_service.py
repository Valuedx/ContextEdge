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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import EvidenceCaseMembership
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


# Rarity weighting for the identity tier. An entity linked to a handful
# of evidence items is a strong correlation signal (vpn-gw-emea-03 in
# four items is one story); an entity linked to hundreds is operational
# wallpaper ("corporate network" would correlate everything — the
# mass-merge lesson applied to identities). Degree = count of evidence
# links for the identity across the tenant.
RARE_DEGREE_MAX = 5
HUB_DEGREE_MIN = 200
RARE_ENTITY_CONFIDENCE = 0.75
COMMON_ENTITY_CONFIDENCE = 0.65


def _identity_correlation_signal(
    shared_identity_ids: set[uuid.UUID],
    identity_types: dict[uuid.UUID, str],
    identity_degrees: dict[uuid.UUID, int] | None = None,
) -> tuple[float, str] | None:
    """Score a shared-identity correlation, or None when the signal is too
    weak to record (person-only single identity, or hub-only overlap).

    Hub identities (degree >= HUB_DEGREE_MIN) carry no correlation
    signal at all — not even toward the multi-identity person tier.
    Unknown degrees fail open as common (missing stats must not silence
    the tier).
    """
    degrees = identity_degrees or {}
    non_hub = {
        identity_id
        for identity_id in shared_identity_ids
        if degrees.get(identity_id, 0) < HUB_DEGREE_MIN
    }
    non_person = [
        identity_id
        for identity_id in non_hub
        if identity_types.get(identity_id) not in (None, "person")
    ]
    if non_person:
        rare = any(
            0 < degrees.get(identity_id, 0) <= RARE_DEGREE_MAX
            for identity_id in non_person
        )
        base = RARE_ENTITY_CONFIDENCE if rare else COMMON_ENTITY_CONFIDENCE
        confidence = base + (0.1 if len(non_hub) >= 2 else 0.0)
        label = "rare operational entity" if rare else "shared non-person entity"
        return min(confidence, 0.85), f"Shared {label} within time window"
    if len(non_hub) >= 2:
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
    source_config: dict | None = None,
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
        if source_type == "jira_sm":
            # Linked issue keys join the same namespace as each issue's
            # own key — symmetric, ordering-independent correlation,
            # mirroring the ServiceNow sys_id contract. Components /
            # services are deliberately NOT keys (mass-merge guard).
            from contextedge.services.jira_reference_service import (
                extract_issue_references,
                resolves_link_types,
            )

            resolves = resolves_link_types(source_config)
            for _edge_type, issue_key in extract_issue_references(payload, resolves):
                add(source_type, issue_key)
        if source_type == "sapphireims":
            # Related-ticket ids join the namespace symmetrically — same
            # contract, third system. CI / service names are NOT keys
            # (mass-merge guard).
            from contextedge.services.sapphireims_reference_service import (
                extract_ticket_references,
            )

            for ticket_id in extract_ticket_references(payload):
                add(source_type, ticket_id)

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
        source_config=source.config if isinstance(source.config, dict) else None,
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

    # Degree stats for rarity weighting — one grouped count for every
    # trusted identity, computed BEFORE the link fetch so hub identities
    # never fan out: an identity linked to hundreds of evidence items
    # must not correlate them all, and must not drag hundreds of link
    # rows into every correlate call either.
    identity_degrees: dict[uuid.UUID, int] = {}
    if identity_types:
        degree_rows = await db.execute(
            select(
                EvidenceIdentityLink.identity_id,
                # distinct: the same (evidence, identity) pair can be
                # linked more than once (alias + strong-id matches).
                func.count(func.distinct(EvidenceIdentityLink.evidence_id)),
            )
            .where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(identity_types)),
            )
            .group_by(EvidenceIdentityLink.identity_id)
        )
        identity_degrees = {row[0]: int(row[1]) for row in degree_rows.all()}
    non_hub_identity_ids = {
        identity_id
        for identity_id in identity_types
        if identity_degrees.get(identity_id, 0) < HUB_DEGREE_MIN
    }

    shared_by_evidence: dict[uuid.UUID, set[uuid.UUID]] = {}
    if non_hub_identity_ids:
        link_rows = await db.execute(
            select(
                EvidenceIdentityLink.evidence_id,
                EvidenceIdentityLink.identity_id,
            ).where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(non_hub_identity_ids)),
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
        signal = _identity_correlation_signal(shared, identity_types, identity_degrees)
        if signal is None:
            continue
        identity_correlations[related_id] = signal

    # Negative signal (C3): conflicting ticket anchors are a HARD veto.
    # Two evidence items firmly attached to DIFFERENT cases sharing a
    # rare device within the window is the "same infrastructure,
    # different incidents" trap — the identity tier must not glue them.
    # Recurrence memberships are excluded from both sides: recurrence
    # explicitly means a different occurrence and must neither create
    # nor suppress a conflict.
    conflict_vetoes = 0
    if identity_correlations:
        anchor_relationships = (
            "primary_case",
            "explicit_reference",
            "reply_inheritance",
            "thread_topic",
        )
        seed_case_rows = (
            await db.execute(
                select(EvidenceCaseMembership.canonical_case_id).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id == evidence.id,
                    EvidenceCaseMembership.status == "active",
                    EvidenceCaseMembership.relationship_type.in_(
                        anchor_relationships
                    ),
                )
            )
        ).scalars().all()
        seed_case_set = set(seed_case_rows)
        if seed_case_set:
            related_case_rows = (
                await db.execute(
                    select(
                        EvidenceCaseMembership.evidence_id,
                        EvidenceCaseMembership.canonical_case_id,
                    ).where(
                        EvidenceCaseMembership.tenant_id == tenant_id,
                        EvidenceCaseMembership.evidence_id.in_(
                            tuple(identity_correlations)
                        ),
                        EvidenceCaseMembership.status == "active",
                        EvidenceCaseMembership.relationship_type.in_(
                            anchor_relationships
                        ),
                    )
                )
            ).all()
            cases_by_evidence: dict[uuid.UUID, set[uuid.UUID]] = {}
            for related_id, case_id in related_case_rows:
                cases_by_evidence.setdefault(related_id, set()).add(case_id)
            for related_id in list(identity_correlations):
                other_cases = cases_by_evidence.get(related_id, set())
                if other_cases and not (other_cases & seed_case_set):
                    del identity_correlations[related_id]
                    conflict_vetoes += 1
            if conflict_vetoes:
                logger.info(
                    "correlation.conflicting_ticket_veto",
                    tenant_id=str(tenant_id),
                    evidence_id=str(evidence.id),
                    vetoed=conflict_vetoes,
                )

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

    # Ticket-number bridging (P1): ticket sources register their number
    # as an authoritative identifier + primary membership (reconciling
    # pending mentions); conversational sources resolve quoted numbers
    # into memberships. Same SAVEPOINT containment as the branches below.
    ticket_bridge: dict | None = None
    try:
        from contextedge.services.ticket_bridge_service import (
            CONVERSATIONAL_SOURCE_TYPES,
            TICKET_SOURCE_TYPES,
            bridge_conversational_mentions,
            register_ticket_identifier,
        )

        bridge_source = source.source_type or ""
        if bridge_source in TICKET_SOURCE_TYPES:
            async with db.begin_nested():
                ticket_bridge = await register_ticket_identifier(
                    db,
                    tenant_id,
                    evidence=evidence,
                    source_type=bridge_source,
                    payload=raw_payload if isinstance(raw_payload, dict) else None,
                    canonical_case_id=canonical_case_id,
                )
        elif bridge_source in CONVERSATIONAL_SOURCE_TYPES:
            # A8: lifecycle first — an edit retires the prior version's
            # rows before this version's bridging writes fresh ones; a
            # delete retracts everything the message established.
            if bridge_source == "teams" and isinstance(raw_payload, dict) and (
                raw_payload.get("is_deleted") or raw_payload.get("last_edited_at")
            ):
                from contextedge.services.ticket_bridge_service import (
                    reconcile_message_lifecycle,
                )

                async with db.begin_nested():
                    lifecycle_result = await reconcile_message_lifecycle(
                        db, tenant_id, evidence, raw_payload
                    )
            else:
                lifecycle_result = None
            async with db.begin_nested():
                ticket_bridge = await bridge_conversational_mentions(
                    db,
                    tenant_id,
                    evidence,
                    payload=raw_payload if isinstance(raw_payload, dict) else None,
                )
            if ticket_bridge is not None and lifecycle_result is not None:
                ticket_bridge["lifecycle"] = lifecycle_result
            if bridge_source == "teams" and isinstance(raw_payload, dict):
                from contextedge.services.ticket_bridge_service import (
                    inherit_reply_membership,
                )

                async with db.begin_nested():
                    reply_result = await inherit_reply_membership(
                        db, tenant_id, evidence, raw_payload
                    )
                if ticket_bridge is not None:
                    ticket_bridge["reply_inheritance"] = reply_result
            # A2: a confident correction retires what its target message
            # established. Runs AFTER the bridge so the correction's own
            # tokens have become memberships it can propagate from.
            if getattr(evidence, "message_function", None) == "correction":
                from contextedge.services.ticket_bridge_service import (
                    apply_correction,
                )

                async with db.begin_nested():
                    correction_result = await apply_correction(
                        db,
                        tenant_id,
                        evidence,
                        raw_payload if isinstance(raw_payload, dict) else None,
                    )
                if ticket_bridge is not None:
                    ticket_bridge["correction"] = correction_result
            # A3 thread topics: anchors set the topic, corrections
            # re-seat it, anchorless threads get a provisional seed, and
            # un-anchored messages inherit the anchored topic.
            if getattr(evidence, "thread_id", None) is not None:
                from contextedge.services.thread_topic_service import (
                    apply_thread_topic,
                    get_thread_topic,
                    set_thread_topic,
                )

                topic_result: dict = {}
                async with db.begin_nested():
                    anchor_case = (ticket_bridge or {}).get("anchor_case_id")
                    corrected_case = (
                        (ticket_bridge or {}).get("correction") or {}
                    ).get("corrected_case_id")
                    if corrected_case is not None:
                        topic_result["set"] = await set_thread_topic(
                            db,
                            tenant_id,
                            evidence.thread_id,
                            uuid.UUID(corrected_case),
                            provisional=False,
                            set_by="correction",
                        )
                    elif anchor_case is not None:
                        topic_result["set"] = await set_thread_topic(
                            db,
                            tenant_id,
                            evidence.thread_id,
                            uuid.UUID(anchor_case),
                            provisional=False,
                            set_by="anchor",
                        )
                    elif (
                        canonical_case_id is not None
                        and await get_thread_topic(
                            db, tenant_id, evidence.thread_id
                        )
                        is None
                    ):
                        # Pre-ticket thread: remember it is about an
                        # incident, under its own (provisional) case.
                        topic_result["set"] = await set_thread_topic(
                            db,
                            tenant_id,
                            evidence.thread_id,
                            canonical_case_id,
                            provisional=True,
                            set_by="thread_seed",
                        )
                    topic_result["applied"] = await apply_thread_topic(
                        db, tenant_id, evidence
                    )
                if ticket_bridge is not None:
                    ticket_bridge["thread_topic"] = topic_result
            # A4: indirect references ("John's ticket") — last resort,
            # only when every earlier tier left the message un-anchored
            # in THIS pass (a pre-existing membership from an earlier
            # pass is harmless: first-writer-wins blocks a duplicate).
            tb = ticket_bridge or {}
            unanchored = (
                not tb.get("memberships")
                and not (tb.get("reply_inheritance") or {}).get("inherited")
                and not (
                    (tb.get("thread_topic") or {}).get("applied") or {}
                ).get("applied")
            )
            if unanchored:
                from contextedge.services.conversational_reference_service import (
                    resolve_conversational_references,
                )

                async with db.begin_nested():
                    reference_result = await resolve_conversational_references(
                        db, tenant_id, evidence
                    )
                if ticket_bridge is not None:
                    ticket_bridge["conversational_reference"] = reference_result
    except Exception as exc:
        logger.warning(
            "ticket_bridge.failed",
            tenant_id=str(tenant_id),
            evidence_id=str(evidence.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )

    # SapphireIMS reference enrichment — same SAVEPOINT containment and
    # fail-soft contract as the branches above.
    sapphire_references: dict | None = None
    if (source.source_type or "") == "sapphireims" and isinstance(raw_payload, dict):
        try:
            from contextedge.services.sapphireims_reference_service import (
                process_sapphireims_references,
            )

            async with db.begin_nested():
                sapphire_references = await process_sapphireims_references(
                    db, tenant_id, evidence, raw_payload
                )
        except Exception as exc:
            logger.warning(
                "sapphireims_reference.enrichment_failed",
                tenant_id=str(tenant_id),
                evidence_id=str(evidence.id),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # Jira SM reference enrichment — same SAVEPOINT containment and
    # fail-soft contract as the ServiceNow branch above.
    jira_references: dict | None = None
    if (source.source_type or "") == "jira_sm" and isinstance(raw_payload, dict):
        try:
            from contextedge.services.jira_reference_service import (
                process_jira_references,
            )

            async with db.begin_nested():
                jira_references = await process_jira_references(
                    db,
                    tenant_id,
                    evidence,
                    raw_payload,
                    own_key=raw_object.external_id if raw_object is not None else None,
                    source_config=source.config if isinstance(source.config, dict) else None,
                )
        except Exception as exc:
            logger.warning(
                "jira_reference.enrichment_failed",
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
            "jira_references": jira_references,
            "sapphireims_references": sapphire_references,
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
        "jira_references": jira_references,
        "sapphireims_references": sapphire_references,
        "ticket_bridge": ticket_bridge,
    }
