"""Empirical validation of knowledge against what actually happened.

Phase 3's first slice, and deliberately only that. The full knowledge-
health model in the review — per-dimension freshness, applicability rule
extraction, an eleven-value status enum, version-diff drift — is a
quarter of work whose shape should be decided by what reviewers actually
use, not up front.

This piece was chosen because it answers the question a reviewer asks
first ("has this procedure ever worked?") from data ContextEdge already
has, with **no new LLM extraction**: playbooks record which knowledge
they were built on (``based_on_kb`` links, phase 2), executions record
whether they succeeded and whether that success was independently
verified (migration ``0036``), and episodes record outcomes.

The judgement it does NOT make is whether an article is *correct*.
Empirical support is one dimension. An article can be perfectly correct
and never used, or used constantly for a task that would succeed anyway.
So this reports counts and a support level, and refuses to collapse them
into a single trust score — a single number invites automation against
evidence that does not support automation.

Two properties worth keeping if this is extended:

- **Silence is not failure.** A knowledge item with no executions is
  ``unproven``, never ``failing``. Most knowledge is simply not
  exercised often, and treating absence as a negative signal would
  demote the whole corpus on day one.
- **Verified success outranks reported success.** ``execution_runs``
  distinguishes a run that claimed success from one whose effect was
  re-checked against telemetry afterwards. Only the latter is real
  evidence, and conflating them would let a playbook that always reports
  success look proven.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem
from contextedge.models.execution import ExecutionRun
from contextedge.models.playbook import PlaybookEvidenceLink
from contextedge.services.playbook_service import KNOWLEDGE_LINK_TYPE

logger = structlog.get_logger()

# Support levels. Four, because each maps to a distinct decision a
# reviewer or the runtime makes — not a scale to be averaged.
SUPPORT_UNPROVEN = "unproven"        # never exercised; say so, do not judge
SUPPORT_EMERGING = "emerging"        # some success, not yet a pattern
SUPPORT_PROVEN = "proven"            # repeated, independently verified
SUPPORT_CONTESTED = "contested"      # meaningful failures alongside successes

# A single verified success is an anecdote. Repetition is what
# distinguishes "worked once" from "works".
PROVEN_MIN_VERIFIED = 3
# Failure share above which successes no longer settle the question.
CONTESTED_FAILURE_RATIO = 0.34


@dataclass(slots=True)
class KnowledgeValidation:
    evidence_id: uuid.UUID
    title: str
    support: str = SUPPORT_UNPROVEN
    playbook_versions: int = 0
    executions: int = 0
    verified_successes: int = 0
    reported_successes: int = 0
    failures: int = 0
    # Playbook versions citing this knowledge, for the reviewer to open.
    version_ids: list[str] = field(default_factory=list)

    @property
    def failure_ratio(self) -> float:
        decided = self.verified_successes + self.reported_successes + self.failures
        return (self.failures / decided) if decided else 0.0

    def as_dict(self) -> dict:
        return {
            "evidence_id": str(self.evidence_id),
            "title": self.title,
            "support": self.support,
            "playbook_versions": self.playbook_versions,
            "executions": self.executions,
            "verified_successes": self.verified_successes,
            "reported_successes": self.reported_successes,
            "failures": self.failures,
            "failure_ratio": round(self.failure_ratio, 3),
            "version_ids": self.version_ids,
        }


def classify_support(
    *,
    executions: int,
    verified_successes: int,
    reported_successes: int,
    failures: int,
) -> str:
    """Support level from outcome counts.

    Ordering matters: contested is checked before proven, because a
    procedure with many verified successes AND many failures is not
    proven — it is inconsistent, and that is the more actionable fact.
    """
    if executions == 0:
        return SUPPORT_UNPROVEN

    decided = verified_successes + reported_successes + failures
    if decided and (failures / decided) >= CONTESTED_FAILURE_RATIO:
        return SUPPORT_CONTESTED
    if verified_successes >= PROVEN_MIN_VERIFIED:
        return SUPPORT_PROVEN
    if verified_successes or reported_successes:
        return SUPPORT_EMERGING
    return SUPPORT_UNPROVEN


async def validate_knowledge_item(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_id: uuid.UUID
) -> KnowledgeValidation:
    """Operational support for one knowledge item."""
    evidence = await db.get(EvidenceItem, evidence_id)
    title = (getattr(evidence, "title", None) or "Untitled")[:300]
    result = KnowledgeValidation(evidence_id=evidence_id, title=title)

    version_ids = (
        (
            await db.execute(
                select(PlaybookEvidenceLink.playbook_version_id).where(
                    PlaybookEvidenceLink.evidence_id == evidence_id,
                    PlaybookEvidenceLink.link_type == KNOWLEDGE_LINK_TYPE,
                )
            )
        )
        .scalars()
        .all()
    )
    version_ids = [v for v in version_ids if v is not None]
    result.playbook_versions = len(version_ids)
    result.version_ids = [str(v) for v in version_ids]
    if not version_ids:
        return result

    # Matched on playbook VERSION, not playbook. A knowledge item cited
    # by v1 must not collect credit for v3's executions: v3 may have
    # dropped the very step the article supported, and attributing those
    # runs would report an article as proven on evidence produced by a
    # procedure that no longer follows it.
    runs = (
        (
            await db.execute(
                select(ExecutionRun).where(
                    ExecutionRun.tenant_id == tenant_id,
                    ExecutionRun.playbook_version_id.in_(version_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    for run in runs:
        outcome = (getattr(run, "outcome", None) or "").lower()
        verification = (getattr(run, "verification_status", None) or "").lower()
        result.executions += 1
        if outcome in ("failed", "failure", "error"):
            result.failures += 1
        elif outcome in ("success", "partial", "completed"):
            # A run that claimed success is weaker evidence than one
            # whose effect was re-checked against telemetry afterwards.
            # Conflating them lets a playbook that always reports
            # success look proven.
            if verification == "verified":
                result.verified_successes += 1
            elif verification == "failed":
                result.failures += 1
            else:
                result.reported_successes += 1

    result.support = classify_support(
        executions=result.executions,
        verified_successes=result.verified_successes,
        reported_successes=result.reported_successes,
        failures=result.failures,
    )
    return result


async def persist_knowledge_support(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_id: uuid.UUID
) -> KnowledgeValidation | None:
    """Recompute one item's support and store it on the row (F4, 0057).

    Retrieval cannot afford to recompute this — it is several queries per
    candidate article per playbook generation, the same cost argument that
    kept applicability lexical until 0051. So it is stored, and refreshed by
    the event that changes the answer: a verification verdict.

    Returns None when the evidence does not belong to the tenant. Deliberately
    does NOT commit: the caller owns the transaction, and this runs inside the
    verification sweep's.
    """
    evidence = await db.get(EvidenceItem, evidence_id)
    if evidence is None or evidence.tenant_id != tenant_id:
        return None
    validation = await validate_knowledge_item(db, tenant_id, evidence_id)
    payload = validation.as_dict()
    # The reviewer-facing fields are already in as_dict(); the ranker needs
    # only ``support``, and storing the counts alongside it means a reviewer
    # asking "why is this contested?" does not have to re-run the query.
    payload.pop("title", None)
    evidence.knowledge_support = payload
    await db.flush()
    return validation


async def refresh_support_for_playbook_version(
    db: AsyncSession, tenant_id: uuid.UUID, playbook_version_id: uuid.UUID
) -> int:
    """Refresh every knowledge item cited by one playbook version.

    Called after a verification verdict lands, because that verdict is exactly
    what changes these numbers. Bounded by the citations of a single version —
    a handful of articles, not the corpus.
    """
    evidence_ids = (
        (
            await db.execute(
                select(PlaybookEvidenceLink.evidence_id).where(
                    PlaybookEvidenceLink.playbook_version_id == playbook_version_id,
                    PlaybookEvidenceLink.link_type == KNOWLEDGE_LINK_TYPE,
                    PlaybookEvidenceLink.evidence_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    refreshed = 0
    for evidence_id in {eid for eid in evidence_ids if eid}:
        if await persist_knowledge_support(db, tenant_id, evidence_id) is not None:
            refreshed += 1
    return refreshed


async def validate_tenant_knowledge(
    db: AsyncSession, tenant_id: uuid.UUID, limit: int = 200
) -> list[KnowledgeValidation]:
    """Support levels for every knowledge item a playbook was built on.

    Scoped to cited knowledge rather than the whole corpus: an article
    no playbook uses has no operational record to assess, and reporting
    it as ``unproven`` alongside genuinely exercised items would bury
    the signal in the corpus size.
    """
    from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES

    cited = (
        (
            await db.execute(
                select(PlaybookEvidenceLink.evidence_id)
                .where(PlaybookEvidenceLink.link_type == KNOWLEDGE_LINK_TYPE)
                .distinct()
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    evidence_ids = [e for e in cited if e is not None]
    if not evidence_ids:
        return []

    # Tenant fence: the link table has no tenant column, so scope through
    # the evidence rows themselves.
    owned = (
        (
            await db.execute(
                select(EvidenceItem.id).where(
                    EvidenceItem.id.in_(evidence_ids),
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.evidence_type.in_(sorted(KNOWLEDGE_EVIDENCE_TYPES)),
                )
            )
        )
        .scalars()
        .all()
    )

    out = [
        await validate_knowledge_item(db, tenant_id, evidence_id)
        for evidence_id in owned
    ]
    logger.info(
        "knowledge.validation_summary",
        tenant_id=str(tenant_id),
        assessed=len(out),
        proven=sum(1 for v in out if v.support == SUPPORT_PROVEN),
        contested=sum(1 for v in out if v.support == SUPPORT_CONTESTED),
    )
    return out
