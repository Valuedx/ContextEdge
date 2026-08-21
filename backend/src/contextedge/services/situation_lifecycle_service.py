"""Situation lifecycle: recovery is evidenced, never inferred from silence.

Roadmap H8. A situation currently only ever starts. This gives it the rest of
its life — `emerging → active → stabilizing → resolved`, plus reopen,
recurrence and merge.

## The rule everything else follows from

**Absence of signal is never recovery.** A situation going quiet means the
tickets stopped arriving, which happens when it is fixed, when everyone gave
up, when the reporters went home, and when a connector broke. Only one of those
is recovery, and nothing in the silence distinguishes them.

So every transition toward `resolved` requires positive evidence: member
incidents carrying a resolution in the source system. A situation with no new
signals for a week and no resolved members stays `active`, which looks wrong on
a dashboard and is the only honest reading.

## Reopen is not recurrence

The distinction the fixtures were built around, one level up from S1 vs S5:

**Reopen** — a situation that had recovered gains a new unresolved signal. Same
occurrence, resumed. The situation returns to `reopened` and keeps its identity,
its onset and its history.

**Recurrence** — the same failure happens again later, as a NEW situation
linked to the earlier one by `recurred_from`. Different occurrence, same shape.

Collapsing them loses whichever number you were about to quote. Treating a
recurrence as a reopen produces one situation with an onset weeks in the past
and an MTTR that spans the gap between two unrelated outages. Treating a reopen
as a recurrence doubles the incident count and hides that the first fix did not
hold — which is exactly the signal the efficacy ledger wants.

## Merge preserves lineage

Two situations that turn out to be one occurrence merge, and the loser keeps
pointing at its survivor. The database enforces it: a row in state `merged`
must name `merged_into_situation_id`. Memberships move rather than duplicate.

## Split is deliberately not implemented

One situation that turns out to be two is a real case and an unsafe automation.
A split proposal is safe; an automatic split silently rewrites history that
somebody may already have acted on, and there is no way to tell afterwards
which half the reader saw. v1 records the case and leaves the decision to a
human, per the roadmap.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.evidence import EvidenceItem
from contextedge.models.situation import (
    OperationalSituation,
    SituationEvidenceMembership,
)

logger = structlog.get_logger()

LIFECYCLE_VERSION = "h8.v1"

EMERGING = "emerging"
ACTIVE = "active"
STABILIZING = "stabilizing"
RESOLVED = "resolved"
REOPENED = "reopened"
MERGED = "merged"
INVALIDATED = "invalidated"

# Source-system case states that count as a member having recovered. Anything
# else — including NULL — is not recovery. `cancelled` is deliberately absent:
# a cancelled ticket is a withdrawn report, not a fixed problem.
RESOLVED_CASE_STATES = ("resolved", "closed")

# States a situation may not be moved out of by automatic evaluation. `merged`
# and `invalidated` are decisions somebody made; recomputing over them is how a
# system teaches people that deciding is pointless.
TERMINAL_STATES = (MERGED, INVALIDATED)


@dataclass(frozen=True)
class LifecycleAssessment:
    situation_id: uuid.UUID
    current_state: str
    proposed_state: str
    members: int
    resolved_members: int
    reason: str

    @property
    def changed(self) -> bool:
        return self.proposed_state != self.current_state

    def as_dict(self) -> dict[str, Any]:
        return {
            "situation_id": str(self.situation_id),
            "current_state": self.current_state,
            "proposed_state": self.proposed_state,
            "members": self.members,
            "resolved_members": self.resolved_members,
            "changed": self.changed,
            "reason": self.reason,
        }


async def _member_states(
    db: AsyncSession, tenant_id: uuid.UUID, situation_id: uuid.UUID
) -> list[str | None]:
    rows = await db.execute(
        select(EvidenceItem.case_state)
        .join(
            SituationEvidenceMembership,
            SituationEvidenceMembership.evidence_id == EvidenceItem.id,
        )
        .where(
            SituationEvidenceMembership.tenant_id == tenant_id,
            SituationEvidenceMembership.situation_id == situation_id,
            SituationEvidenceMembership.membership_status.not_in(
                ("rejected", "retired")
            ),
        )
    )
    return [r[0] for r in rows.all()]


def assess_lifecycle(
    situation: OperationalSituation, member_case_states: Sequence[str | None]
) -> LifecycleAssessment:
    """What state the evidence supports. Pure, so it can be argued with.

    Never proposes `resolved` from quiet. The only path to resolved is every
    member carrying a resolution in the source system.
    """
    total = len(member_case_states)
    resolved = sum(
        1 for s in member_case_states if (s or "").lower() in RESOLVED_CASE_STATES
    )
    current = situation.state

    if current in TERMINAL_STATES:
        return LifecycleAssessment(
            situation.id, current, current, total, resolved,
            f"{current} is a decision somebody made; automatic evaluation does "
            f"not move it",
        )

    if total == 0:
        return LifecycleAssessment(
            situation.id, current, current, total, resolved,
            "no live members to assess",
        )

    if resolved == total:
        return LifecycleAssessment(
            situation.id, current, RESOLVED, total, resolved,
            f"all {total} member(s) carry a resolution in the source system",
        )

    # Something un-resolved is present. If the situation had already recovered,
    # this is the same occurrence resuming — not a new one.
    if current in (RESOLVED, STABILIZING) and resolved < total:
        return LifecycleAssessment(
            situation.id, current, REOPENED, total, resolved,
            f"{total - resolved} member(s) no longer resolved after the "
            f"situation had recovered",
        )

    if resolved > 0:
        return LifecycleAssessment(
            situation.id, current, STABILIZING, total, resolved,
            f"{resolved} of {total} member(s) resolved — recovery evidenced, "
            f"not merely quieter",
        )

    return LifecycleAssessment(
        situation.id, current, current, total, resolved,
        "no member carries a resolution; silence is not recovery",
    )


async def evaluate_situation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    situation: OperationalSituation,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> LifecycleAssessment:
    """Assess one situation, optionally writing the transition."""
    now = now or datetime.now(UTC)
    states = await _member_states(db, tenant_id, situation.id)
    assessment = assess_lifecycle(situation, states)

    if apply and assessment.changed:
        situation.state = assessment.proposed_state
        if assessment.proposed_state == STABILIZING and situation.stabilizing_at is None:
            situation.stabilizing_at = now
        elif assessment.proposed_state == RESOLVED:
            situation.resolved_at = now
        elif assessment.proposed_state == REOPENED:
            # Clear the recovery stamps: they described a recovery that did not
            # hold, and leaving them makes the next MTTR read from a moment the
            # situation was not actually over.
            situation.resolved_at = None
            situation.stabilizing_at = None
        logger.info(
            "situation.lifecycle_transition",
            tenant_id=str(tenant_id),
            lifecycle_version=LIFECYCLE_VERSION,
            **assessment.as_dict(),
        )
    return assessment


async def evaluate_all_situations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    apply: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    situations = (
        (
            await db.execute(
                select(OperationalSituation)
                .where(
                    OperationalSituation.tenant_id == tenant_id,
                    OperationalSituation.state.not_in(TERMINAL_STATES),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    assessments = [
        await evaluate_situation(db, tenant_id, s, apply=apply) for s in situations
    ]
    changed = [a for a in assessments if a.changed]
    by_state: dict[str, int] = {}
    for a in assessments:
        key = a.proposed_state if a.changed else a.current_state
        by_state[key] = by_state.get(key, 0) + 1
    return {
        "assessed": len(assessments),
        "changed": len(changed),
        "applied": apply,
        "states": by_state,
        "transitions": [a.as_dict() for a in changed],
    }


async def merge_situations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    loser_id: uuid.UUID,
    survivor_id: uuid.UUID,
    *,
    reason: str,
    reviewed_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Fold one situation into another without losing where it came from.

    Memberships move rather than duplicate — the membership uniqueness
    constraint means a signal already in the survivor is dropped rather than
    inserted twice, which is the correct outcome and would otherwise be an
    IntegrityError at the least convenient moment.

    The loser keeps pointing at its survivor. The database requires it: a row
    in state `merged` must name `merged_into_situation_id`.
    """
    if loser_id == survivor_id:
        return {"error": "a situation cannot merge into itself"}

    loser = await db.get(OperationalSituation, loser_id)
    survivor = await db.get(OperationalSituation, survivor_id)
    if loser is None or survivor is None:
        return {"error": "situation not found"}
    if loser.tenant_id != tenant_id or survivor.tenant_id != tenant_id:
        return {"error": "situation not found"}
    if survivor.state in TERMINAL_STATES:
        return {
            "error": f"survivor is {survivor.state}; merging into it would hide "
            f"the result"
        }

    survivor_members = {
        r[0]
        for r in (
            await db.execute(
                select(SituationEvidenceMembership.evidence_id).where(
                    SituationEvidenceMembership.situation_id == survivor_id
                )
            )
        ).all()
    }
    moving = (
        (
            await db.execute(
                select(SituationEvidenceMembership).where(
                    SituationEvidenceMembership.situation_id == loser_id
                )
            )
        )
        .scalars()
        .all()
    )
    moved = dropped = 0
    for membership in moving:
        if membership.evidence_id in survivor_members:
            # Already present in the survivor. Retire rather than delete: the
            # record that this signal was once filed under the other situation
            # is the lineage the merge is supposed to preserve.
            membership.membership_status = "retired"
            membership.review_reason = f"merged into {survivor_id}: {reason}"
            dropped += 1
            continue
        membership.situation_id = survivor_id
        moved += 1

    loser.state = MERGED
    loser.merged_into_situation_id = survivor_id
    if reviewed_by:
        loser.reviewed_by = reviewed_by
    survivor.incident_count = len(survivor_members) + moved
    if loser.onset_at and (
        survivor.onset_at is None or loser.onset_at < survivor.onset_at
    ):
        survivor.onset_at = loser.onset_at
    if loser.last_signal_at and (
        survivor.last_signal_at is None
        or loser.last_signal_at > survivor.last_signal_at
    ):
        survivor.last_signal_at = loser.last_signal_at

    await ensure_edge(
        db,
        tenant_id,
        "situation",
        loser_id,
        "situation",
        survivor_id,
        "merged_into",
        metadata={"reason": reason, "lifecycle_version": LIFECYCLE_VERSION},
    )
    result = {
        "loser": str(loser_id),
        "survivor": str(survivor_id),
        "members_moved": moved,
        "members_already_present": dropped,
    }
    logger.info("situation.merged", tenant_id=str(tenant_id), **result)
    return result


async def link_recurrence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    situation_id: uuid.UUID,
    earlier_situation_id: uuid.UUID,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """Record that this situation is the same failure happening again.

    NOT a merge and NOT a reopen. The two situations stay separate because
    they are separate occurrences; the edge says they share a failure mode.
    Merging them would produce a single outage spanning the quiet weeks
    between, and every duration computed from it would be wrong.
    """
    if situation_id == earlier_situation_id:
        return {"error": "a situation cannot recur from itself"}
    await ensure_edge(
        db,
        tenant_id,
        "situation",
        situation_id,
        "situation",
        earlier_situation_id,
        "recurred_from",
        metadata={"reason": reason, "lifecycle_version": LIFECYCLE_VERSION},
    )
    logger.info(
        "situation.recurrence_linked",
        tenant_id=str(tenant_id),
        situation_id=str(situation_id),
        earlier=str(earlier_situation_id),
    )
    return {"situation": str(situation_id), "recurred_from": str(earlier_situation_id)}
