"""One incident identifier in, the operational context around it out.

Roadmap H7, and the acceptance criterion the whole roadmap was written for: an
agent handed a single incident should obtain what was going on around it rather
than reasoning from the description alone.

This composes rather than computes. Situations come from H3, change candidates
from H6, criticality and owner from C2, efficacy and negative knowledge from
E1-E3, and the honesty about what is missing from H2. The value here is not any
new inference — it is that the answer arrives as one bounded, provenanced
object instead of nine queries somebody has to know to run.

## Facets, not a blob

Each facet is answered independently and carries its own status and
provenance. That matters more than it sounds: a bundle that merges everything
into one payload cannot say *which part* it is missing, and an agent reading it
cannot tell a quiet estate from an unconfigured one. Every facet can say "no
data, and here is why", reusing the vocabulary from coverage reporting.

`blind_spots` lists the facets whose emptiness must NOT be read as a zero. That
list is the most important field in the response and the easiest to skip past.

## Bounded, because the reader has a budget

Every facet is capped. The agent projection has a node budget for the same
reason: an unbounded context bundle spends its budget on whatever happens to be
numerous, which on a busy CI is never the interesting part. Caps are per facet
so one noisy dimension cannot crowd out the rest, and a truncated facet says it
was truncated rather than quietly returning a prefix.

## Security-filtered

Record-bearing facets honour ``allowed_domain_ids``. This differs deliberately
from coverage reporting, which is tenant-wide: coverage answers "what can this
deployment see at all", a property of instrumentation, while this returns
actual records a particular reader may or may not be entitled to.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import GraphEdge
from contextedge.models.situation import (
    OperationalSituation,
    SituationEvidenceMembership,
)

logger = structlog.get_logger()

# Per-facet caps. Deliberately small: this is read by something with a token
# budget, and the fifteenth duplicate ticket informs nobody.
MAX_DUPLICATES = 10
MAX_CHANGES = 8
MAX_TOPOLOGY = 15
MAX_RECURRENCE = 10
MAX_REMEDIATIONS = 5

AVAILABLE = "available"
EMPTY = "empty"
UNSUPPORTED = "unsupported"


@dataclass
class Facet:
    """One dimension of context, answered on its own terms."""

    name: str
    status: str
    provenance: str
    items: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    truncated: bool = False

    @property
    def answerable(self) -> bool:
        return self.status in (AVAILABLE, EMPTY)

    def as_dict(self) -> dict[str, Any]:
        return {
            "facet": self.name,
            "status": self.status,
            "provenance": self.provenance,
            "count": len(self.items),
            "truncated": self.truncated,
            "items": self.items,
            "note": self.note,
        }


@dataclass
class DiagnosticContext:
    incident: dict[str, Any]
    facets: list[Facet] = field(default_factory=list)
    generated_at: datetime | None = None

    # Dimensions the DEPLOYMENT cannot answer, as opposed to facets that
    # merely failed here. Populated from the coverage facet.
    deployment_blind_spots: list[str] = field(default_factory=list)

    @property
    def blind_spots(self) -> list[str]:
        """Everything whose emptiness must not be read as a zero.

        Two different absences, deliberately merged into one list. A facet that
        could not answer *for this incident* and a dimension this deployment
        cannot answer *at all* are different causes with the same consequence:
        a reader who treats either as "none found" concludes something the data
        does not support.

        Keeping them apart produced exactly that: the bundle reported "blind
        spots: none" on a deployment with no monitoring connector, because the
        coverage facet had answered successfully -- about not being able to
        answer. One list, so the field cannot be read as reassurance.
        """
        facet_gaps = [f.name for f in self.facets if not f.answerable]
        return facet_gaps + [
            d for d in self.deployment_blind_spots if d not in facet_gaps
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident": self.incident,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "facets": [f.as_dict() for f in self.facets],
            "blind_spots": self.blind_spots,
        }


def _domain_filter(allowed_domain_ids: Sequence[uuid.UUID] | None) -> list:
    """Domain scoping for the record-bearing facets.

    ``None`` means tenant-wide authority. A restricted reader sees only their
    own domains, and deliberately NOT domain-NULL evidence: NULL is the
    encoding for reviewed tenant-global knowledge, and unassigned ingest rides
    the same convention, so including it would leak un-scoped records into a
    scoped view.

    Only the incident lookup was filtered before this. Every other facet
    returned titles, timestamps and change candidates regardless of domain --
    a bundle described as security-filtered that filtered its front door and
    left the windows open.
    """
    if allowed_domain_ids is None:
        return []
    return [EvidenceItem.domain_id.in_(list(allowed_domain_ids))]


def _cap(items: list, limit: int) -> tuple[list, bool]:
    return items[:limit], len(items) > limit


async def build_diagnostic_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    incident_evidence_id: uuid.UUID,
    *,
    allowed_domain_ids: Sequence[uuid.UUID] | None = None,
    now: datetime | None = None,
) -> DiagnosticContext | None:
    """The bundle. Returns None when the incident is not readable here."""
    now = now or datetime.now(UTC)

    conditions = [
        EvidenceItem.id == incident_evidence_id,
        EvidenceItem.tenant_id == tenant_id,
    ]
    if allowed_domain_ids is not None:
        conditions.append(EvidenceItem.domain_id.in_(list(allowed_domain_ids)))
    incident = (
        await db.execute(select(EvidenceItem).where(*conditions))
    ).scalar_one_or_none()
    if incident is None:
        return None

    context = DiagnosticContext(
        incident={
            "evidence_id": str(incident.id),
            "title": incident.title,
            "evidence_type": incident.evidence_type,
            "source_type": incident.source_type,
            "occurred_at": (
                incident.created_at_source.isoformat()
                if incident.created_at_source
                else None
            ),
            "case_state": incident.case_state,
        },
        generated_at=now,
    )

    context.facets.append(await _situation_facet(db, tenant_id, incident.id))
    context.facets.append(await _impact_facet(db, tenant_id, incident.id))
    context.facets.append(
        await _duplicates_facet(db, tenant_id, incident.id, allowed_domain_ids)
    )
    context.facets.append(
        await _changes_facet(db, tenant_id, incident.id, allowed_domain_ids)
    )
    context.facets.append(
        await _recurrence_facet(db, tenant_id, incident.id, allowed_domain_ids)
    )
    context.facets.append(await _remediation_facet(db, tenant_id, incident))
    coverage = await _coverage_facet(db, tenant_id)
    context.facets.append(coverage)
    context.deployment_blind_spots = [
        item["facet"] for item in coverage.items if item.get("facet")
    ]

    logger.info(
        "diagnostic_context.built",
        tenant_id=str(tenant_id),
        incident_id=str(incident.id),
        facets=len(context.facets),
        blind_spots=context.blind_spots,
    )
    return context


async def _situation_facet(
    db: AsyncSession, tenant_id: uuid.UUID, incident_id: uuid.UUID
) -> Facet:
    """Is this one occurrence among many, or on its own?"""
    row = (
        await db.execute(
            select(OperationalSituation)
            .join(
                SituationEvidenceMembership,
                SituationEvidenceMembership.situation_id == OperationalSituation.id,
            )
            .where(
                OperationalSituation.tenant_id == tenant_id,
                SituationEvidenceMembership.evidence_id == incident_id,
                SituationEvidenceMembership.membership_status.not_in(
                    ("rejected", "retired")
                ),
            )
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return Facet(
            name="situation",
            status=EMPTY,
            provenance="situation correlation (H3)",
            note=(
                "This incident is not grouped with any other signal. It may be "
                "isolated, or the signals that would group it may not be here."
            ),
        )
    return Facet(
        name="situation",
        status=AVAILABLE,
        provenance="situation correlation (H3)",
        items=[
            {
                "situation_id": str(row.id),
                "title": row.title,
                "state": row.state,
                "situation_type": row.situation_type,
                "onset_at": row.onset_at.isoformat() if row.onset_at else None,
                "last_signal_at": (
                    row.last_signal_at.isoformat() if row.last_signal_at else None
                ),
                "incident_count": row.incident_count,
                "confidence": row.situation_confidence,
            }
        ],
        note=(
            f"One of {row.incident_count} signals describing a single occurrence."
        ),
    )


async def _impact_facet(
    db: AsyncSession, tenant_id: uuid.UUID, incident_id: uuid.UUID
) -> Facet:
    """What this touches, and how much it matters."""
    rows = await db.execute(
        select(Entity)
        .join(GraphEdge, GraphEdge.target_node_id == Entity.id)
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "affects_ci",
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id == incident_id,
        )
    )
    entities = list(rows.scalars().unique().all())
    if not entities:
        return Facet(
            name="impact",
            status=EMPTY,
            provenance="affects_ci edges + CMDB attributes (C1/C2)",
            note="No configuration item is linked to this incident.",
        )

    direct_ids = [e.id for e in entities]
    neighbours = await db.execute(
        select(Entity, GraphEdge.edge_type)
        .join(
            GraphEdge,
            (GraphEdge.target_node_id == Entity.id)
            | (GraphEdge.source_node_id == Entity.id),
        )
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type.in_(
                ("depends_on", "runs_on", "hosted_on", "uses", "connected_to")
            ),
            GraphEdge.valid_to.is_(None),
            (GraphEdge.source_node_id.in_(direct_ids))
            | (GraphEdge.target_node_id.in_(direct_ids)),
        )
    )
    items: list[dict[str, Any]] = []
    for e in entities:
        attrs = e.attributes or {}
        items.append(
            {
                "entity_id": str(e.id),
                "name": e.name,
                "entity_type": e.entity_type,
                "relation": "affected",
                "criticality": attrs.get("criticality"),
                "support_group": attrs.get("support_group"),
                "owner": attrs.get("owner"),
            }
        )
    seen = set(direct_ids)
    for entity, edge_type in neighbours.all():
        if entity.id in seen:
            continue
        seen.add(entity.id)
        attrs = entity.attributes or {}
        items.append(
            {
                "entity_id": str(entity.id),
                "name": entity.name,
                "entity_type": entity.entity_type,
                "relation": f"neighbour ({edge_type})",
                "criticality": attrs.get("criticality"),
                "support_group": attrs.get("support_group"),
                "owner": attrs.get("owner"),
            }
        )

    capped, truncated = _cap(items, MAX_TOPOLOGY)
    critical = [i for i in capped if i.get("criticality")]
    note = f"{len(entities)} directly affected, {len(items) - len(entities)} one hop away."
    if critical:
        note += (
            f" Highest stated criticality: {critical[0]['criticality']}"
            f" ({critical[0]['name']})."
        )
    else:
        note += " No CI here states a criticality — blast radius cannot be prioritised."
    return Facet(
        name="impact",
        status=AVAILABLE,
        provenance="affects_ci edges + CMDB attributes (C1/C2)",
        items=capped,
        note=note,
        truncated=truncated,
    )


async def _duplicates_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    allowed_domain_ids: Sequence[uuid.UUID] | None = None,
) -> Facet:
    """Other tickets reporting this same occurrence."""
    rows = await db.execute(
        select(EvidenceItem)
        .join(GraphEdge, GraphEdge.source_node_id == EvidenceItem.id)
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type.in_(("child_of_incident", "duplicate_of")),
            GraphEdge.valid_to.is_(None),
            GraphEdge.target_node_id == incident_id,
            *_domain_filter(allowed_domain_ids),
        )
    )
    children = list(rows.scalars().unique().all())
    if not children:
        return Facet(
            name="duplicates",
            status=EMPTY,
            provenance="child_of_incident / duplicate_of edges",
            note="No other ticket is recorded as reporting this same occurrence.",
        )
    items = [
        {
            "evidence_id": str(c.id),
            "title": c.title,
            "occurred_at": (
                c.created_at_source.isoformat() if c.created_at_source else None
            ),
        }
        for c in children
    ]
    capped, truncated = _cap(items, MAX_DUPLICATES)
    return Facet(
        name="duplicates",
        status=AVAILABLE,
        provenance="child_of_incident / duplicate_of edges",
        items=capped,
        note=(
            f"{len(children)} further ticket(s) report this occurrence. Impact "
            f"scale, not {len(children)} separate problems."
        ),
        truncated=truncated,
    )


async def _changes_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    allowed_domain_ids: Sequence[uuid.UUID] | None = None,
) -> Facet:
    """What changed near this, ranked. Candidates, never a verdict."""
    from contextedge.services.change_correlation_service import (
        correlate_changes_for_situation,
    )

    situation = (
        await db.execute(
            select(OperationalSituation)
            .join(
                SituationEvidenceMembership,
                SituationEvidenceMembership.situation_id == OperationalSituation.id,
            )
            .where(
                OperationalSituation.tenant_id == tenant_id,
                SituationEvidenceMembership.evidence_id == incident_id,
            )
            .limit(1)
        )
    ).scalars().first()

    if situation is None:
        return Facet(
            name="changes",
            status=EMPTY,
            provenance="situation-aware change correlation (H6)",
            note=(
                "Change correlation is anchored on a situation, and this incident "
                "belongs to none. Not evidence that no change occurred."
            ),
        )

    candidates = await correlate_changes_for_situation(db, tenant_id, situation)
    if allowed_domain_ids is not None and candidates:
        # H6 ranks tenant-wide; the bundle must not hand a restricted reader a
        # change they cannot otherwise see. Filtered after ranking so the
        # ordering stays the one H6 computed.
        visible = {
            r[0]
            for r in (
                await db.execute(
                    select(EvidenceItem.id).where(
                        EvidenceItem.tenant_id == tenant_id,
                        EvidenceItem.id.in_([c.change_evidence_id for c in candidates]),
                        *_domain_filter(allowed_domain_ids),
                    )
                )
            ).all()
        }
        candidates = [c for c in candidates if c.change_evidence_id in visible]
    if not candidates:
        return Facet(
            name="changes",
            status=EMPTY,
            provenance="situation-aware change correlation (H6)",
            note="No change touched the blast radius inside the window.",
        )
    items = [c.as_dict() for c in candidates]
    capped, truncated = _cap(items, MAX_CHANGES)
    confirmed = [c for c in candidates if c.status == "confirmed"]
    note = f"{len(candidates)} candidate change(s), ranked. "
    note += (
        f"{len(confirmed)} confirmed by the source system."
        if confirmed
        else "None confirmed — these are suspects, not causes."
    )
    return Facet(
        name="changes",
        status=AVAILABLE,
        provenance="situation-aware change correlation (H6)",
        items=capped,
        note=note,
        truncated=truncated,
    )


async def _recurrence_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    allowed_domain_ids: Sequence[uuid.UUID] | None = None,
) -> Facet:
    """Has this happened before? A shared problem is recurrence, not one event."""
    problem_rows = await db.execute(
        select(GraphEdge.target_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "related_problem",
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id == incident_id,
        )
    )
    problem_ids = [r[0] for r in problem_rows.all()]
    if not problem_ids:
        return Facet(
            name="recurrence",
            status=EMPTY,
            provenance="related_problem edges",
            note="No problem record links this incident to earlier occurrences.",
        )

    sibling_rows = await db.execute(
        select(EvidenceItem)
        .join(GraphEdge, GraphEdge.source_node_id == EvidenceItem.id)
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "related_problem",
            GraphEdge.valid_to.is_(None),
            GraphEdge.target_node_id.in_(problem_ids),
            EvidenceItem.id != incident_id,
            *_domain_filter(allowed_domain_ids),
        )
    )
    siblings = list(sibling_rows.scalars().unique().all())
    if not siblings:
        return Facet(
            name="recurrence",
            status=EMPTY,
            provenance="related_problem edges",
            note=(
                "A problem record is linked, but no other incident shares it. "
                "First recorded occurrence of this root cause."
            ),
        )
    items = [
        {
            "evidence_id": str(s.id),
            "title": s.title,
            "occurred_at": (
                s.created_at_source.isoformat() if s.created_at_source else None
            ),
        }
        for s in sorted(
            siblings,
            key=lambda x: x.created_at_source or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
    ]
    capped, truncated = _cap(items, MAX_RECURRENCE)
    return Facet(
        name="recurrence",
        status=AVAILABLE,
        provenance="related_problem edges",
        items=capped,
        note=(
            f"Shares a problem record with {len(siblings)} other incident(s). "
            f"Separate occurrences of one root cause, not one long event."
        ),
        truncated=truncated,
    )


async def _remediation_facet(
    db: AsyncSession, tenant_id: uuid.UUID, incident: EvidenceItem
) -> Facet:
    """What to do, how well it has worked, and what is known to fail."""
    from contextedge.services.remediation_advisory_service import advise_remediations

    advice = await advise_remediations(
        db,
        tenant_id,
        context=None,
        limit=MAX_REMEDIATIONS,
        classify_live=True,
    )
    if not advice:
        return Facet(
            name="remediation",
            status=EMPTY,
            provenance="efficacy + applicability + negative knowledge (E1-E3)",
            note="No pattern carries a remediation for this tenant yet.",
        )
    items = [a.as_dict() for a in advice]
    with_failures = sum(1 for a in advice if a.known_failures)
    return Facet(
        name="remediation",
        status=AVAILABLE,
        provenance="efficacy + applicability + negative knowledge (E1-E3)",
        items=items,
        note=(
            f"{len(advice)} remediation(s), ranked by whether they are "
            f"defensible rather than merely present. {with_failures} carry "
            f"known failures."
        ),
    )


async def _coverage_facet(db: AsyncSession, tenant_id: uuid.UUID) -> Facet:
    """What this deployment cannot see at all.

    Last on purpose. A reader who has scanned the facts above is exactly the
    reader about to draw a conclusion, and this is the paragraph that says
    which conclusions are not available here.
    """
    from contextedge.services.coverage_service import build_coverage_report

    report = await build_coverage_report(db, tenant_id)
    unanswerable = [f for f in report.facets if not f.is_answerable]
    return Facet(
        name="coverage",
        status=AVAILABLE,
        provenance="capability declaration + sync state (H2)",
        items=[f.as_dict() for f in unanswerable],
        note=(
            "Everything above is answerable."
            if not unanswerable
            else (
                f"{len(unanswerable)} dimension(s) cannot be answered here: "
                + ", ".join(f.facet for f in unanswerable)
                + ". An empty result in those is not a zero."
            )
        ),
    )
