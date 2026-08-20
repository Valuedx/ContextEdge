"""Which change caused this? Ranked candidates, never a verdict.

Roadmap H6. The question people actually ask when an incident lands, and the
one the system could not answer until ServiceNow supplied change records with
real execution times.

## A ranking, not a probability

`correlation_score` is a rank under an explainable additive model. 0.85 means
"strong on the factors below", never "85% likely to be the cause". Everything
rendering it must use candidate language. The day a calibrated probabilistic
model exists it gets its own column rather than quietly redefining this one.

## Confirmation comes from governance, never from the score

A change is `confirmed` only when something governed says so — a ServiceNow
`caused_by` relation a human wrote, an approved RCA, a reviewed decision. No
score, however high, promotes a candidate to confirmed. Allowing that would let
inference launder itself into fact, and the next model reading this table could
not tell what somebody asserted from what something computed.

## A change after onset cannot be the cause

Enforced in the database, not just here: the schema refuses
`temporal_relation='after_onset'` together with a causal status. Post-onset
changes are still recorded — as `remediation`, because a change on the affected
CI *after* things broke is usually somebody fixing it, and knowing what was
tried matters even when it is not the cause.

## Why exclusion is not the mechanism

Unlike applicability (E2), this does not suppress. Every change in the window
that touches the blast radius becomes a row, with a score and a reason. An
operator scanning candidates is served by a ranked list they can argue with;
they are not served by a filter that silently dropped the one they needed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.pattern import GraphEdge
from contextedge.models.situation import (
    OperationalSituation,
    SituationChangeCandidate,
    SituationEvidenceMembership,
)

logger = structlog.get_logger()

CORRELATION_VERSION = "h6.v1"

# How far before onset a change is still worth considering. Seven days matches
# the roadmap's B4 default and the topology TTL — one idea, one number.
DEFAULT_LOOKBACK = timedelta(days=7)

# How far after onset a change is recorded as remediation rather than ignored.
DEFAULT_FORWARD = timedelta(days=2)

# Dependency edges a blast radius may traverse. `contains` is composition, not
# dependency: a rack containing a switch says nothing about what fails when the
# switch does.
TOPOLOGY_EDGE_TYPES = ("depends_on", "runs_on", "hosted_on", "uses", "connected_to")

# Additive, explainable, and capped at 1.0. Each factor is recorded in
# score_breakdown so a candidate can be argued with rather than merely ranked.
SCORE_SAME_CI = 0.5
SCORE_ONE_HOP = 0.25
SCORE_WITHIN_2H = 0.3
SCORE_WITHIN_24H = 0.15
SCORE_WITHIN_WINDOW = 0.05
SCORE_OUT_OF_WINDOW_EXECUTION = 0.1

# Score at or above which a before-onset candidate is `suspected` rather than
# merely a candidate. Untuned: there is no labelled cause set to tune against,
# and the number is chosen so that same-CI plus close-in-time clears it while
# either alone does not.
SUSPECTED_SCORE = 0.7
CANDIDATE_SCORE = 0.4

BEFORE_ONSET = "before_onset"
OVERLAPS_ONSET = "overlaps_onset"
AFTER_ONSET = "after_onset"


@dataclass
class ChangeCandidate:
    change_evidence_id: uuid.UUID
    title: str
    occurred_at: datetime | None
    temporal_relation: str
    minutes_from_onset: int | None
    topology_distance: int | None
    score: float
    status: str
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    confirmation_basis: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_evidence_id": str(self.change_evidence_id),
            "title": self.title,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "temporal_relation": self.temporal_relation,
            "minutes_from_onset": self.minutes_from_onset,
            "topology_distance": self.topology_distance,
            "correlation_score": round(self.score, 3),
            "status": self.status,
            "score_breakdown": self.breakdown,
            "reason_summary": "; ".join(self.reasons),
            "confirmation_basis": self.confirmation_basis,
        }


async def _situation_entities(
    db: AsyncSession, tenant_id: uuid.UUID, situation_id: uuid.UUID
) -> set[uuid.UUID]:
    """CIs the situation's member evidence points at.

    Derived from membership rather than read from `situation_entity_impacts`,
    which H4 will populate and which is empty today. Deriving keeps H6 working
    before H4 lands instead of waiting on it.
    """
    member_ids = [
        r[0]
        for r in (
            await db.execute(
                select(SituationEvidenceMembership.evidence_id).where(
                    SituationEvidenceMembership.tenant_id == tenant_id,
                    SituationEvidenceMembership.situation_id == situation_id,
                    SituationEvidenceMembership.membership_status.not_in(
                        ("rejected", "retired")
                    ),
                )
            )
        ).all()
    ]
    if not member_ids:
        return set()
    rows = await db.execute(
        select(GraphEdge.target_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "affects_ci",
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id.in_(member_ids),
        )
    )
    return {r[0] for r in rows.all()}


async def _one_hop(
    db: AsyncSession, tenant_id: uuid.UUID, entity_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Entities one dependency edge away, in either direction.

    Both directions on purpose: a change to what this CI depends on can break
    it, and a change to something that depends on it can reveal the break.
    """
    if not entity_ids:
        return set()
    rows = await db.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type.in_(list(TOPOLOGY_EDGE_TYPES)),
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id.in_(list(entity_ids))
            | GraphEdge.target_node_id.in_(list(entity_ids)),
        )
    )
    seen = set(entity_ids)
    out: set[uuid.UUID] = set()
    for source_id, target_id in rows.all():
        for node in (source_id, target_id):
            if node not in seen:
                out.add(node)
    return out


def _temporal(
    change_at: datetime | None, onset: datetime | None
) -> tuple[str, int | None]:
    if change_at is None or onset is None:
        return "unknown", None
    delta = change_at - onset
    minutes = int(delta.total_seconds() // 60)
    if abs(minutes) <= 5:
        return OVERLAPS_ONSET, minutes
    return (AFTER_ONSET if minutes > 0 else BEFORE_ONSET), minutes


def _score_candidate(
    *,
    distance: int | None,
    temporal_relation: str,
    minutes_from_onset: int | None,
    out_of_window: bool,
) -> tuple[float, dict[str, float], list[str]]:
    breakdown: dict[str, float] = {}
    reasons: list[str] = []

    if distance == 0:
        breakdown["same_ci"] = SCORE_SAME_CI
        reasons.append("touches the same CI")
    elif distance == 1:
        breakdown["one_hop"] = SCORE_ONE_HOP
        reasons.append("touches a CI one dependency hop away")

    if temporal_relation in (BEFORE_ONSET, OVERLAPS_ONSET) and minutes_from_onset is not None:
        gap = abs(minutes_from_onset)
        if gap <= 120:
            breakdown["within_2h"] = SCORE_WITHIN_2H
            reasons.append(f"executed {gap} minutes before onset")
        elif gap <= 24 * 60:
            breakdown["within_24h"] = SCORE_WITHIN_24H
            reasons.append(f"executed {gap // 60} hours before onset")
        else:
            breakdown["within_window"] = SCORE_WITHIN_WINDOW
            reasons.append(f"executed {gap // (60 * 24)} days before onset")

    if out_of_window:
        breakdown["out_of_window_execution"] = SCORE_OUT_OF_WINDOW_EXECUTION
        reasons.append("executed outside its approved window")

    return min(sum(breakdown.values()), 1.0), breakdown, reasons


def _status_for(
    *, score: float, temporal_relation: str, distance: int | None, confirmed: bool
) -> str:
    """The ladder. Confirmation is governance-only; everything else is a rank.

    A post-onset change on the blast radius is recorded as `remediation` rather
    than dropped: it is usually somebody fixing the thing, and what was tried
    matters even when it is not the cause. The schema's CHECK constraint
    refuses a causal status here, so this is belt and braces on purpose.
    """
    if confirmed:
        return "confirmed"
    if temporal_relation == AFTER_ONSET:
        return "remediation" if distance == 0 else "weak_candidate"
    if score >= SUSPECTED_SCORE:
        return "suspected"
    if score >= CANDIDATE_SCORE:
        return "candidate"
    return "weak_candidate"


async def correlate_changes_for_situation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    situation: OperationalSituation,
    *,
    lookback: timedelta = DEFAULT_LOOKBACK,
    forward: timedelta = DEFAULT_FORWARD,
    include_one_hop: bool = True,
) -> list[ChangeCandidate]:
    """Rank the changes that could explain this situation."""
    onset = situation.onset_at
    if onset is None:
        return []

    direct = await _situation_entities(db, tenant_id, situation.id)
    if not direct:
        return []
    neighbours = await _one_hop(db, tenant_id, sorted(direct)) if include_one_hop else set()
    distance_by_entity = {e: 0 for e in direct}
    for e in neighbours:
        distance_by_entity.setdefault(e, 1)

    window_start = onset - lookback
    window_end = onset + forward

    rows = await db.execute(
        select(
            EvidenceItem.id,
            EvidenceItem.title,
            EvidenceItem.created_at_source,
            EvidenceItem.source_facets,
            GraphEdge.target_node_id,
        )
        .join(
            GraphEdge,
            (GraphEdge.source_node_id == EvidenceItem.id)
            & (GraphEdge.edge_type == "affects_ci")
            & (GraphEdge.valid_to.is_(None)),
        )
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.evidence_type == "change",
            EvidenceItem.created_at_source.is_not(None),
            EvidenceItem.created_at_source >= window_start,
            EvidenceItem.created_at_source <= window_end,
            GraphEdge.target_node_id.in_(list(distance_by_entity)),
        )
    )

    # A change touching several affected CIs scores on its closest one.
    best: dict[uuid.UUID, tuple[int, str, datetime | None, dict]] = {}
    for eid, title, occurred, facets, entity_id in rows.all():
        distance = distance_by_entity.get(entity_id)
        if distance is None:
            continue
        prior = best.get(eid)
        if prior is None or distance < prior[0]:
            best[eid] = (distance, title or "", occurred, facets or {})

    confirmed_ids = await _governed_confirmations(db, tenant_id, list(best))
    out_of_window_ids = await _out_of_window_changes(db, tenant_id, list(best))

    candidates: list[ChangeCandidate] = []
    for eid, (distance, title, occurred, _facets) in best.items():
        temporal_relation, minutes = _temporal(occurred, onset)
        out_of_window = eid in out_of_window_ids
        score, breakdown, reasons = _score_candidate(
            distance=distance,
            temporal_relation=temporal_relation,
            minutes_from_onset=minutes,
            out_of_window=out_of_window,
        )
        # Deliberately not named after the SituationEntityImpact column this
        # would otherwise shadow. The governance column register detects
        # writers by scanning source text for an assignment to the column
        # name -- comments included -- so both a local variable and a comment
        # quoting one can impersonate a writer and quietly retire a gap H4
        # still owes. This comment therefore avoids spelling it.
        confirmation = confirmed_ids.get(eid)
        if confirmation:
            reasons.append("the source system records this change as the cause")
        candidates.append(
            ChangeCandidate(
                change_evidence_id=eid,
                title=title,
                occurred_at=occurred,
                temporal_relation=temporal_relation,
                minutes_from_onset=minutes,
                topology_distance=distance,
                score=1.0 if confirmation else score,
                status=_status_for(
                    score=score,
                    temporal_relation=temporal_relation,
                    distance=distance,
                    confirmed=bool(confirmation),
                ),
                breakdown=breakdown,
                reasons=reasons,
                confirmation_basis=confirmation,
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.minutes_from_onset or 0))
    return candidates


async def _out_of_window_changes(
    db: AsyncSession, tenant_id: uuid.UUID, change_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Changes whose ACTUAL execution fell outside their APPROVED window.

    A governance signal, and a sharper one than proximity: plenty of changes
    happen near an incident, far fewer happened at a time nobody approved.

    Computed here from the raw payload rather than read from `source_facets`,
    which is empty on every row in this corpus — `derive_facets` only populates
    when a source declares `facet_fields` and the ServiceNow source declares
    none. Scoring on a facet nothing writes would be a factor that can never
    fire, which reads exactly like a factor that never matters.

    ServiceNow semantics: `start_date`/`end_date` are the approved window,
    `work_start`/`work_end` the actual execution. Absence on either side is not
    a violation -- a change with no recorded actual is unproven, not guilty.
    """
    if not change_ids:
        return set()
    rows = await db.execute(
        select(EvidenceItem.id, RawEvidenceObject.raw_payload)
        .join(RawEvidenceObject, RawEvidenceObject.id == EvidenceItem.raw_object_ref)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.id.in_(list(change_ids)),
        )
    )
    out: set[uuid.UUID] = set()
    for eid, payload in rows.all():
        if not isinstance(payload, dict):
            continue
        planned_start = _parse_dt(payload.get("start_date"))
        planned_end = _parse_dt(payload.get("end_date"))
        actual_start = _parse_dt(payload.get("work_start"))
        if not (planned_start and planned_end and actual_start):
            continue
        if actual_start < planned_start or actual_start > planned_end:
            out.add(eid)
    return out


def _parse_dt(value: Any) -> datetime | None:
    """ServiceNow datetimes arrive as 'YYYY-MM-DD HH:MM:SS' in UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


async def _governed_confirmations(
    db: AsyncSession, tenant_id: uuid.UUID, change_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Changes the SOURCE SYSTEM says caused an incident.

    `caused_by_change` edges come from a ServiceNow reference field a human
    filled in. That is the only thing here allowed to produce `confirmed`.
    """
    if not change_ids:
        return {}
    rows = await db.execute(
        select(GraphEdge.target_node_id, GraphEdge.source_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "caused_by_change",
            GraphEdge.valid_to.is_(None),
            GraphEdge.target_node_id.in_(list(change_ids)),
        )
    )
    return {
        change_id: {
            "kind": "itsm_caused_by",
            "asserted_by": "source_system",
            "incident_evidence_id": str(incident_id),
        }
        for change_id, incident_id in rows.all()
    }


async def persist_candidates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    situation: OperationalSituation,
    candidates: Sequence[ChangeCandidate],
) -> dict[str, int]:
    """Write candidates, updating rather than duplicating on re-run.

    Idempotent for the same reason situation correlation is: a scheduled run
    that mints a new candidate row per tick turns one suspect change into a
    growing pile of identical ones.

    A human decision is never overwritten. A row someone reviewed or rejected
    keeps its status — recomputing over a reviewer is how a system teaches
    people that reviewing is pointless.
    """
    existing_rows = (
        await db.execute(
            select(SituationChangeCandidate).where(
                SituationChangeCandidate.tenant_id == tenant_id,
                SituationChangeCandidate.situation_id == situation.id,
            )
        )
    ).scalars().all()
    by_change = {row.change_evidence_id: row for row in existing_rows}

    created = updated = preserved = 0
    for candidate in candidates:
        row = by_change.get(candidate.change_evidence_id)
        if row is not None and (row.reviewed_by or row.status == "rejected"):
            preserved += 1
            continue
        if row is None:
            row = SituationChangeCandidate(
                tenant_id=tenant_id,
                situation_id=situation.id,
                change_evidence_id=candidate.change_evidence_id,
            )
            db.add(row)
            created += 1
        else:
            updated += 1
        row.status = candidate.status
        row.correlation_score = candidate.score
        row.temporal_relation = candidate.temporal_relation
        row.minutes_from_onset = candidate.minutes_from_onset
        row.topology_distance = candidate.topology_distance
        row.score_breakdown = candidate.breakdown
        row.reason_summary = "; ".join(candidate.reasons)
        row.confirmation_basis = candidate.confirmation_basis

    situation.change_candidate_count = len(by_change) + created
    result = {"created": created, "updated": updated, "preserved": preserved}
    logger.info(
        "situation.changes_correlated",
        tenant_id=str(tenant_id),
        situation_id=str(situation.id),
        correlation_version=CORRELATION_VERSION,
        **result,
    )
    return result


async def correlate_all_situations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Rank change candidates for every live situation."""
    situations = (
        (
            await db.execute(
                select(OperationalSituation)
                .where(
                    OperationalSituation.tenant_id == tenant_id,
                    OperationalSituation.state.not_in(("merged", "invalidated")),
                    OperationalSituation.onset_at.is_not(None),
                )
                .order_by(OperationalSituation.onset_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    totals = {"situations": 0, "candidates": 0, "confirmed": 0, "created": 0}
    for situation in situations:
        candidates = await correlate_changes_for_situation(db, tenant_id, situation)
        totals["situations"] += 1
        totals["candidates"] += len(candidates)
        totals["confirmed"] += sum(1 for c in candidates if c.status == "confirmed")
        if not dry_run and candidates:
            written = await persist_candidates(db, tenant_id, situation, candidates)
            totals["created"] += written["created"]
    totals["dry_run"] = dry_run
    return totals


def _now() -> datetime:
    return datetime.now(UTC)
