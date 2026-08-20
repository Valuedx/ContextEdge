"""Deterministic situation correlation: which signals describe ONE occurrence.

Roadmap H3. An `OperationalSituation` says many signals describe one bounded
real-world occurrence. Getting that wrong is not a degraded answer, it is a
fabricated one: merge too eagerly and the system reports a three-week outage
that never happened; merge too timidly and six tickets get six parallel
diagnoses.

No LLM. Every decision here is a join or a comparison, because a merge is a
factual claim about the world and a model's opinion is not evidence for it.

## What merges, and what deliberately does not

**Authoritative links merge.** `child_of_incident` and `duplicate_of` are
written by a human in the source system who looked at both records and said
they are the same thing. That is better evidence than anything inferable, and
it produces a `confirmed` membership.

**A shared problem does NOT merge.** `related_problem` is authoritative too,
and it asserts something different: same *root cause*, which spans occurrences
by definition. A known error that recurs every Monday is one problem and many
situations. This is the single most tempting wrong join available -- the edge
is right there, it is human-authored, and using it produces a confident,
plausible, false answer.

**A shared CI does NOT merge.** A domain controller touches password resets,
DNS complaints, GPO failures and disk alerts in the same afternoon. They share
infrastructure, not an occurrence.

**Same CI + time window + symptom agreement merges, weakly** (`inferred`).
All three are required. Any two of them are satisfied constantly by unrelated
work on shared infrastructure.

## Vetoes

- **Time window.** A situation is bounded. Signals outside its window start a
  new one rather than extending it forever.
- **Hub CIs.** A CI above the traffic threshold cannot anchor an inferred
  merge at all: on shared infrastructure, "same CI" carries almost no
  information, and the false-merge rate rises with the CI's popularity.
- **Environment mismatch.** Prod and staging failing alike is two occurrences.
  Implemented, and currently inert -- `source_facets` is empty on every row in
  this corpus, so no evidence states an environment to disagree about. Recorded
  rather than removed: the rule is right, the data is missing.

## Measured coverage on the current corpus

Symptom agreement is thin. `issue_signatures` is empty (nothing has
reconstructed episodes yet) and the six `error_signatures` present all come
from randomly generated demo records, none of which match the authored
scenarios. So in practice the authoritative tier does the merging here and the
inferred tier rarely fires. That is a coverage statement, not a design flaw --
but a reader who assumes the inferred tier is carrying weight would be wrong,
so it is written down.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import GraphEdge
from contextedge.models.situation import (
    OperationalSituation,
    SituationEvidenceMembership,
)

logger = structlog.get_logger()

# Bumped when the rules change, so a situation carries the logic that made it
# and a re-run can tell its own output from an older generation's.
CORRELATION_VERSION = "h3.v1"

# Signals that assert "these records are the same occurrence".
AUTHORITATIVE_EDGE_TYPES = ("child_of_incident", "duplicate_of")

# Authoritative, and deliberately excluded. See the module docstring: a shared
# root cause is not a shared occurrence. Named here rather than merely omitted
# so the exclusion is visible to anyone extending the list.
NON_MERGING_EDGE_TYPES = ("related_problem", "affects_ci", "assigned_to_group")

# How far apart two signals may be and still describe one occurrence. A
# situation extends as signals arrive; this bounds the gap, not the total span.
DEFAULT_WINDOW = timedelta(hours=24)

# How far back to look for candidate evidence.
DEFAULT_LOOKBACK = timedelta(days=30)

# A CI with more than this many distinct incidents over the lookback is
# infrastructure everything touches. Same-CI stops being evidence of a shared
# occurrence and becomes evidence that the CI is popular.
HUB_CI_INCIDENT_THRESHOLD = 8

# One incident is an incident. A situation is a claim that several signals
# describe one thing, and a claim about one signal is just the signal.
MIN_SITUATION_MEMBERS = 2

INCIDENT_EVIDENCE_TYPES = ("incident", "ticket")


@dataclass
class _Candidate:
    evidence_id: uuid.UUID
    title: str
    occurred_at: datetime | None
    ci_ids: frozenset[uuid.UUID] = frozenset()
    signature_ids: frozenset[uuid.UUID] = frozenset()
    environment: str | None = None


@dataclass
class SituationCorrelationResult:
    situations_created: int = 0
    situations_extended: int = 0
    memberships_created: int = 0
    groups_considered: int = 0
    singletons_skipped: int = 0
    hub_cis_suppressed: tuple[str, ...] = ()
    merges_by_method: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "situations_created": self.situations_created,
            "situations_extended": self.situations_extended,
            "memberships_created": self.memberships_created,
            "groups_considered": self.groups_considered,
            "singletons_skipped": self.singletons_skipped,
            "hub_cis_suppressed": list(self.hub_cis_suppressed),
            "merges_by_method": dict(self.merges_by_method),
            "correlation_version": CORRELATION_VERSION,
        }


class _Union:
    """Union-find over evidence ids, recording why each union happened.

    The reason is kept because a membership has to be able to say what put it
    there. A situation whose members cannot explain themselves cannot be
    reviewed, and an unreviewable merge is a permanent one.
    """

    def __init__(self) -> None:
        self._parent: dict[uuid.UUID, uuid.UUID] = {}
        self.reasons: dict[uuid.UUID, str] = {}

    def add(self, item: uuid.UUID) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: uuid.UUID) -> uuid.UUID:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: uuid.UUID, b: uuid.UUID, method: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self._parent[rb] = ra
        # An authoritative reason outranks an inferred one for the same pair.
        for node in (a, b):
            if method == "authoritative" or node not in self.reasons:
                self.reasons[node] = method
        return True

    def groups(self) -> dict[uuid.UUID, list[uuid.UUID]]:
        out: dict[uuid.UUID, list[uuid.UUID]] = {}
        for item in self._parent:
            out.setdefault(self.find(item), []).append(item)
        return out


async def _load_candidates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    since: datetime,
) -> dict[uuid.UUID, _Candidate]:
    rows = await db.execute(
        select(
            EvidenceItem.id,
            EvidenceItem.title,
            EvidenceItem.created_at_source,
            EvidenceItem.created_at,
            EvidenceItem.source_facets,
        ).where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.evidence_type.in_(list(INCIDENT_EVIDENCE_TYPES)),
            func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at)
            >= since,
        )
    )
    candidates: dict[uuid.UUID, _Candidate] = {}
    for eid, title, at_source, created, facets in rows.all():
        candidates[eid] = _Candidate(
            evidence_id=eid,
            title=title or "",
            occurred_at=at_source or created,
            environment=(facets or {}).get("environment") if facets else None,
        )
    return candidates


async def _attach_edges(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    candidates: dict[uuid.UUID, _Candidate],
    edge_type: str,
    target_type: str,
    attribute: str,
) -> None:
    """Populate a candidate's CI or signature set from active graph edges."""
    if not candidates:
        return
    rows = await db.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == edge_type,
            GraphEdge.target_node_type == target_type,
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id.in_(list(candidates)),
        )
    )
    collected: dict[uuid.UUID, set[uuid.UUID]] = {}
    for source_id, target_id in rows.all():
        collected.setdefault(source_id, set()).add(target_id)
    for eid, targets in collected.items():
        setattr(candidates[eid], attribute, frozenset(targets))


async def _authoritative_pairs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    candidate_ids: Sequence[uuid.UUID],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    if not candidate_ids:
        return []
    rows = await db.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type.in_(list(AUTHORITATIVE_EDGE_TYPES)),
            GraphEdge.valid_to.is_(None),
            GraphEdge.source_node_id.in_(list(candidate_ids)),
            GraphEdge.target_node_id.in_(list(candidate_ids)),
        )
    )
    return [(a, b) for a, b in rows.all()]


def _hub_cis(
    candidates: Iterable[_Candidate], threshold: int = HUB_CI_INCIDENT_THRESHOLD
) -> set[uuid.UUID]:
    counts: dict[uuid.UUID, int] = {}
    for candidate in candidates:
        for ci in candidate.ci_ids:
            counts[ci] = counts.get(ci, 0) + 1
    return {ci for ci, n in counts.items() if n > threshold}


def _may_merge_inferred(
    a: _Candidate,
    b: _Candidate,
    window: timedelta,
    hubs: set[uuid.UUID],
) -> bool:
    """Same CI AND inside the window AND symptom agreement -- all three.

    Any two of these are satisfied constantly by unrelated work on shared
    infrastructure, which is why none of them is sufficient alone.
    """
    shared_ci = (a.ci_ids & b.ci_ids) - hubs
    if not shared_ci:
        return False
    if a.occurred_at is None or b.occurred_at is None:
        return False
    if abs(a.occurred_at - b.occurred_at) > window:
        return False
    # Environment veto: stated disagreement blocks the merge. Absent on both
    # sides is not disagreement -- treating unknown as a mismatch would veto
    # every merge on a corpus that states no environment, which is this one.
    if a.environment and b.environment and a.environment != b.environment:
        return False
    return bool(a.signature_ids & b.signature_ids)


def _situation_type(member_count: int, has_authoritative: bool) -> str:
    if member_count >= 5:
        return "incident_storm"
    return "degradation" if has_authoritative else "unknown"


def _group_fingerprint(evidence_ids: Iterable[uuid.UUID]) -> str:
    """Stable identity for a set of members.

    Recorded rather than used as the lookup key, because a situation ACCUMULATES
    signals: the seventh ticket changes the set, so a set-hash would call the
    grown situation a different one and mint a second row for the same outage.
    Overlap is the identity test; the fingerprint is how a run recognises that
    nothing changed.
    """
    joined = ",".join(sorted(str(e) for e in evidence_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def _existing_situation_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: Sequence[uuid.UUID],
) -> OperationalSituation | None:
    """The live situation these signals already belong to, if any.

    Identity is overlap, not set equality: one member already placed means this
    is that occurrence seen again, with more signals. Merged and invalidated
    situations are excluded -- a merged one has a survivor that should collect
    the new signal, and re-attaching to the corpse would resurrect it.

    Ties (members spread across two situations) resolve to the earliest onset
    and are left alone otherwise. Collapsing two situations into one is a merge,
    merge needs lineage, and lineage is H8's job -- doing it here would lose the
    record of which occurrence was which.
    """
    if not evidence_ids:
        return None
    rows = await db.execute(
        select(OperationalSituation)
        .join(
            SituationEvidenceMembership,
            SituationEvidenceMembership.situation_id == OperationalSituation.id,
        )
        .where(
            OperationalSituation.tenant_id == tenant_id,
            OperationalSituation.state.not_in(("merged", "invalidated")),
            SituationEvidenceMembership.evidence_id.in_(list(evidence_ids)),
            SituationEvidenceMembership.membership_status.not_in(
                ("rejected", "retired")
            ),
        )
        .order_by(OperationalSituation.onset_at.asc().nulls_last())
        .limit(1)
    )
    return rows.scalars().first()


async def _existing_member_ids(
    db: AsyncSession, situation_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await db.execute(
        select(SituationEvidenceMembership.evidence_id).where(
            SituationEvidenceMembership.situation_id == situation_id
        )
    )
    return {r[0] for r in rows.all()}


async def correlate_situations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    window: timedelta = DEFAULT_WINDOW,
    lookback: timedelta = DEFAULT_LOOKBACK,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SituationCorrelationResult:
    """Assemble incident evidence into situations. Returns what it did.

    ``dry_run`` computes the grouping and writes nothing, which is how a rule
    change gets measured against a real corpus before it is allowed to create
    rows.

    Idempotent. A group whose members already belong to a live situation
    extends that situation instead of creating another one, and a group that
    has not changed at all writes nothing. Without this a scheduled run mints a
    fresh outage on every tick -- measured before the fix: two runs over one
    unchanged corpus produced two situations and twelve memberships for one
    six-ticket occurrence.
    """
    now = now or datetime.now(UTC)
    since = now - lookback
    result = SituationCorrelationResult()

    candidates = await _load_candidates(db, tenant_id, since)
    if not candidates:
        return result

    await _attach_edges(
        db, tenant_id, candidates, "affects_ci", "entity", "ci_ids"
    )
    await _attach_edges(
        db, tenant_id, candidates, "exhibits", "error_signature", "signature_ids"
    )

    hubs = _hub_cis(candidates.values())
    union = _Union()
    for eid in candidates:
        union.add(eid)

    # Tier 1: authoritative. A human in the source system said these are one.
    for a, b in await _authoritative_pairs(db, tenant_id, list(candidates)):
        if union.union(a, b, "authoritative"):
            result.merges_by_method["authoritative"] = (
                result.merges_by_method.get("authoritative", 0) + 1
            )

    # Tier 2: inferred. Same CI, inside the window, agreeing on symptom.
    ordered = sorted(
        candidates.values(),
        key=lambda c: (c.occurred_at or datetime.max.replace(tzinfo=UTC)),
    )
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if a.occurred_at and b.occurred_at and (b.occurred_at - a.occurred_at) > window:
                break  # ordered by time: nothing later can be closer
            if _may_merge_inferred(a, b, window, hubs):
                if union.union(a.evidence_id, b.evidence_id, "inferred"):
                    result.merges_by_method["inferred"] = (
                        result.merges_by_method.get("inferred", 0) + 1
                    )

    hub_names = tuple(sorted(str(ci) for ci in hubs))
    result.hub_cis_suppressed = hub_names

    for root, members in union.groups().items():
        result.groups_considered += 1
        if len(members) < MIN_SITUATION_MEMBERS:
            result.singletons_skipped += 1
            continue
        member_candidates = [candidates[m] for m in members]
        has_authoritative = any(
            union.reasons.get(m) == "authoritative" for m in members
        )
        times = [c.occurred_at for c in member_candidates if c.occurred_at]
        onset = min(times) if times else None
        last_signal = max(times) if times else None
        anchor = min(
            member_candidates,
            key=lambda c: (c.occurred_at or datetime.max.replace(tzinfo=UTC)),
        )

        if dry_run:
            result.situations_created += 1
            result.memberships_created += len(members)
            continue

        fingerprint = _group_fingerprint(members)
        situation = await _existing_situation_for(db, tenant_id, members)
        if situation is not None:
            already = await _existing_member_ids(db, situation.id)
            new_members = [
                c for c in member_candidates if c.evidence_id not in already
            ]
            if not new_members and situation.fingerprint == fingerprint:
                continue  # nothing changed; re-running must not churn rows
            result.situations_extended += 1
            for member in new_members:
                method = union.reasons.get(member.evidence_id, "inferred")
                db.add(
                    SituationEvidenceMembership(
                        tenant_id=tenant_id,
                        situation_id=situation.id,
                        evidence_id=member.evidence_id,
                        evidence_role="related_incident",
                        membership_status=(
                            "confirmed" if method == "authoritative" else "inferred"
                        ),
                        membership_confidence=(
                            0.95 if method == "authoritative" else 0.5
                        ),
                        correlation_method=method,
                        first_seen_at=member.occurred_at,
                        last_seen_at=member.occurred_at,
                        machine_decision_version=CORRELATION_VERSION,
                    )
                )
                result.memberships_created += 1
            situation.incident_count = len(already) + len(new_members)
            situation.fingerprint = fingerprint
            if onset and (situation.onset_at is None or onset < situation.onset_at):
                situation.onset_at = onset
            if last_signal and (
                situation.last_signal_at is None
                or last_signal > situation.last_signal_at
            ):
                situation.last_signal_at = last_signal
            continue

        situation = OperationalSituation(
            tenant_id=tenant_id,
            situation_type=_situation_type(len(members), has_authoritative),
            # `active` needs authoritative linkage or strong multi-signal
            # evidence (see SITUATION_STATES). An inferred-only group has
            # neither, so it stays `emerging` and says so.
            state="active" if has_authoritative else "emerging",
            title=anchor.title[:500] or "Untitled situation",
            situation_confidence=0.9 if has_authoritative else 0.5,
            onset_at=onset,
            detected_at=now,
            last_signal_at=last_signal,
            incident_count=len(members),
            fingerprint=fingerprint,
            correlation_version=CORRELATION_VERSION,
        )
        db.add(situation)
        await db.flush()
        result.situations_created += 1

        for member in member_candidates:
            method = union.reasons.get(member.evidence_id, "inferred")
            db.add(
                SituationEvidenceMembership(
                    tenant_id=tenant_id,
                    situation_id=situation.id,
                    evidence_id=member.evidence_id,
                    evidence_role=(
                        "primary_incident"
                        if member.evidence_id == anchor.evidence_id
                        else "related_incident"
                    ),
                    membership_status=(
                        "confirmed" if method == "authoritative" else "inferred"
                    ),
                    membership_confidence=0.95 if method == "authoritative" else 0.5,
                    correlation_method=method,
                    first_seen_at=member.occurred_at,
                    last_seen_at=member.occurred_at,
                    machine_decision_version=CORRELATION_VERSION,
                )
            )
            result.memberships_created += 1

    logger.info(
        "situation.correlated",
        tenant_id=str(tenant_id),
        **result.as_dict(),
    )
    return result
