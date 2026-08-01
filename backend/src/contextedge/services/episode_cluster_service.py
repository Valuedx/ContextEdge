"""Resolve the full evidence cluster an episode should be built from.

P0 of the correlation/episode review: correlation produced edges and
case links, but episode reconstruction received only the single newly
processed evidence id ("comma-separated list ... for MVP wiring") — so
a ServiceNow ticket correlated with a Teams thread still produced a
single-source episode. This module materializes the connected component
before reconstruction:

- **Deterministic case membership**: evidence sharing a canonical case
  (CaseLink) with anything already in the cluster.
- **Accepted correlation edges**: CorrelationEdge in either direction.
- **Visibility**: tenant-scoped; legal-hold and pending-redaction
  evidence never enters a cluster (it must never reach the LLM).
- **Temporal boundary**: a candidate joins only if its timestamp
  (record-source time, ingestion fallback) lies within
  ``CLUSTER_TIME_WINDOW`` of its NEAREST seed — correlation chains must
  not drag in last quarter's ticket through a shared key. Undated
  evidence fails open for membership; the size cap backstops it.
- **Bounds**: ``MAX_HOPS`` expansion rounds, ``MAX_CLUSTER_SIZE``
  members; truncation is recorded, never silent.

The cluster carries per-evidence ``reasons`` (why each item is here —
the review surface renders these) and a stable ``fingerprint`` (hash of
the sorted member set) powering draft idempotency and supersede-on-
growth in the reconstruction path.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.episode import CorrelationEdge
from contextedge.models.evidence import EvidenceItem
from contextedge.models.session import CaseLink

logger = structlog.get_logger()

MAX_CLUSTER_SIZE = 50
MAX_HOPS = 3
CLUSTER_TIME_WINDOW = timedelta(days=30)


@dataclass(slots=True)
class EpisodeCluster:
    fingerprint: str
    evidence_ids: list[uuid.UUID]
    reasons: dict[str, list[str]]
    truncated: bool = False
    canonical_case_ids: list[uuid.UUID] = field(default_factory=list)


def cluster_fingerprint(evidence_ids: list[uuid.UUID]) -> str:
    joined = ",".join(sorted(str(eid) for eid in evidence_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def _visible_times(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime | None]:
    """Visibility gate + timestamps in one query. Missing rows, foreign
    tenants, legal holds, and pending redactions are simply absent from
    the result — they never enter a cluster."""
    if not evidence_ids:
        return {}
    rows = (
        await db.execute(
            select(
                EvidenceItem.id,
                func.coalesce(EvidenceItem.created_at_source, EvidenceItem.ingested_at),
            ).where(
                EvidenceItem.id.in_(tuple(evidence_ids)),
                EvidenceItem.tenant_id == tenant_id,
                or_(
                    EvidenceItem.sensitivity_label.is_(None),
                    EvidenceItem.sensitivity_label != "legal_hold",
                ),
                or_(
                    EvidenceItem.redaction_status.is_(None),
                    EvidenceItem.redaction_status.notin_(("pending", "pending_redaction")),
                ),
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _within_window(
    candidate_time: datetime | None, seed_times: list[datetime]
) -> bool:
    """Temporal membership rule: within CLUSTER_TIME_WINDOW of the
    NEAREST seed. Undated candidates fail open (membership allowed);
    dated candidates with no dated seed also fail open."""
    if candidate_time is None or not seed_times:
        return True
    nearest = min(abs(candidate_time - seed) for seed in seed_times)
    return nearest <= CLUSTER_TIME_WINDOW


async def resolve_episode_cluster(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    seed_evidence_ids: list[uuid.UUID],
) -> EpisodeCluster:
    """Connected component over case links + correlation edges, seeded
    at the given evidence, bounded and time-fenced."""
    seed_set = set(seed_evidence_ids)
    times = await _visible_times(db, tenant_id, seed_set)
    members: dict[uuid.UUID, list[str]] = {
        eid: ["seed"] for eid in seed_set if eid in times
    }
    seed_times = [t for t in times.values() if t is not None]
    canonical_case_ids: set[uuid.UUID] = set()
    truncated = False

    frontier = set(members)
    for _hop in range(MAX_HOPS):
        if not frontier or len(members) >= MAX_CLUSTER_SIZE:
            break
        discovered: dict[uuid.UUID, list[str]] = {}

        # Deterministic case membership: frontier's case-link rows →
        # canonical cases → all evidence in those cases.
        case_rows = (
            await db.execute(
                select(CaseLink.canonical_case_id).where(
                    CaseLink.tenant_id == tenant_id,
                    CaseLink.evidence_id.in_(tuple(frontier)),
                )
            )
        ).scalars().all()
        new_cases = set(case_rows) - canonical_case_ids
        canonical_case_ids.update(new_cases)
        if new_cases:
            member_rows = (
                await db.execute(
                    select(CaseLink.evidence_id, CaseLink.canonical_case_id).where(
                        CaseLink.tenant_id == tenant_id,
                        CaseLink.canonical_case_id.in_(tuple(new_cases)),
                        CaseLink.evidence_id.is_not(None),
                    )
                )
            ).all()
            for evidence_id, case_id in member_rows:
                if evidence_id not in members:
                    discovered.setdefault(evidence_id, []).append(
                        f"case:{str(case_id)[:8]}"
                    )

        # Ticket-number memberships (P1): evidence attached to the same
        # case via quoted ticket numbers. mentioned_only NEVER expands —
        # that's the multi-ticket digest guard holding at cluster time.
        membership_case_rows = (
            await db.execute(
                select(EvidenceCaseMembership.canonical_case_id).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id.in_(tuple(frontier)),
                    EvidenceCaseMembership.status == "active",
                    # mentioned_only = digest guard; recurrence = similar
                    # problem, never the same occurrence (C2).
                    EvidenceCaseMembership.relationship_type.notin_(
                        ("mentioned_only", "recurrence")
                    ),
                )
            )
        ).scalars().all()
        membership_cases = set(membership_case_rows)
        if membership_cases:
            member_evidence_rows = (
                await db.execute(
                    select(
                        EvidenceCaseMembership.evidence_id,
                        EvidenceCaseMembership.relationship_type,
                    ).where(
                        EvidenceCaseMembership.tenant_id == tenant_id,
                        EvidenceCaseMembership.canonical_case_id.in_(
                            tuple(membership_cases)
                        ),
                        EvidenceCaseMembership.status == "active",
                        EvidenceCaseMembership.relationship_type.notin_(
                            ("mentioned_only", "recurrence")
                        ),
                    )
                )
            ).all()
            for evidence_id, relationship in member_evidence_rows:
                if evidence_id not in members:
                    discovered.setdefault(evidence_id, []).append(
                        f"ticket_ref:{relationship}"
                    )

        # Accepted correlation edges, both directions.
        edge_rows = (
            await db.execute(
                select(
                    CorrelationEdge.source_evidence_id,
                    CorrelationEdge.target_evidence_id,
                    CorrelationEdge.correlation_type,
                ).where(
                    CorrelationEdge.tenant_id == tenant_id,
                    or_(
                        CorrelationEdge.source_evidence_id.in_(tuple(frontier)),
                        CorrelationEdge.target_evidence_id.in_(tuple(frontier)),
                    ),
                )
            )
        ).all()
        for source_id, target_id, correlation_type in edge_rows:
            other = target_id if source_id in frontier else source_id
            if other not in members:
                discovered.setdefault(other, []).append(
                    f"correlation:{correlation_type}"
                )

        if not discovered:
            break

        # Negative-evidence fence (A7): evidence explicitly dissociated
        # from any case this cluster is anchored to must not be pulled
        # back in through a different signal (thread edge, case link) —
        # a severed link stays severed until a reviewer re-adds it.
        anchor_cases = canonical_case_ids | membership_cases
        negated_ids: set[uuid.UUID] = set()
        if anchor_cases:
            negated_ids = set(
                (
                    await db.execute(
                        select(EvidenceCaseMembership.evidence_id).where(
                            EvidenceCaseMembership.tenant_id == tenant_id,
                            EvidenceCaseMembership.evidence_id.in_(
                                tuple(discovered)
                            ),
                            EvidenceCaseMembership.status == "negative",
                            EvidenceCaseMembership.canonical_case_id.in_(
                                tuple(anchor_cases)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )

        candidate_times = await _visible_times(db, tenant_id, set(discovered))
        next_frontier: set[uuid.UUID] = set()
        for evidence_id, why in discovered.items():
            if evidence_id in negated_ids:
                continue  # negative evidence: explicitly not this case
            if evidence_id not in candidate_times:
                continue  # invisible: foreign tenant / legal hold / redaction
            if not _within_window(candidate_times[evidence_id], seed_times):
                continue  # temporal fence: not part of THIS incident's window
            if len(members) >= MAX_CLUSTER_SIZE:
                truncated = True
                break
            members[evidence_id] = why
            next_frontier.add(evidence_id)
        frontier = next_frontier

    ordered = sorted(members)
    cluster = EpisodeCluster(
        fingerprint=cluster_fingerprint(ordered),
        evidence_ids=ordered,
        reasons={str(eid): reasons for eid, reasons in members.items()},
        truncated=truncated,
        canonical_case_ids=sorted(canonical_case_ids),
    )
    if truncated:
        logger.warning(
            "episode_cluster.truncated",
            tenant_id=str(tenant_id),
            size=len(ordered),
            max_size=MAX_CLUSTER_SIZE,
        )
    return cluster
