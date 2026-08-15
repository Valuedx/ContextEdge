"""Proposing and applying knowledge supersession (F4b).

F4 made retrieval see whether a procedure has ever *worked*. It could still not
see whether an article had been *replaced* — the versioning module knows that
"VPN SOP v2.docx" supersedes "VPN SOP.docx", and its own docstring names the
gap: retrieval "returns superseded guidance and nothing marks it as
superseded".

The heuristic proposes; a human decides; acceptance writes a ``superseded_by``
edge, and retrieval reads the edge. Three properties make that safe:

- **A filename is not grounds for retiring an SOP.** "Final" and "v2" are
  written by people in a hurry and folders get reorganised, so the finding is
  stored as a proposal — the ``IdentityMergeProposal`` pattern, for the same
  reason it was chosen there.
- **Rejection is durable.** Without persisting it, a scheduled pass re-raises
  every declined pair forever, and a review queue that repeats itself is a
  queue nobody reads.
- **The signals travel with the proposal.** A reviewer who cannot see WHY the
  heuristic paired two documents will either rubber-stamp it or ignore it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.evidence import EvidenceItem
from contextedge.models.knowledge_supersession import KnowledgeSupersessionProposal
from contextedge.services.documents.versioning import (
    document_family,
    parse_version,
    qualifier_rank,
)
from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES

logger = structlog.get_logger()

# Below this, the pair is not worth a reviewer's attention: a shared family
# with no version and no qualifier difference is two documents with similar
# names, which is not evidence of anything.
MIN_PROPOSAL_CONFIDENCE = 0.5
# Cap per run, so a corpus with a hundred near-identical filenames cannot bury
# the queue in one pass. Truncation is logged, never silent.
MAX_PROPOSALS_PER_RUN = 50
# The comparison is O(n²) within a family, so the corpus a single scan loads is
# bounded too. Newest first: a document that replaced something is more likely
# to be recent than the one it replaced.
MAX_SCANNED_DOCUMENTS = 2000


def compare_candidates(
    predecessor_name: str, successor_name: str
) -> tuple[float, dict] | None:
    """``(confidence, signals)`` that *successor* replaces *predecessor*.

    Returns None when the evidence does not point that way at all — including
    when it points the other way, because proposing a reversed pair is worse
    than proposing nothing.
    """
    old_version = parse_version(predecessor_name)
    new_version = parse_version(successor_name)
    old_rank = qualifier_rank(predecessor_name)
    new_rank = qualifier_rank(successor_name)

    signals = {
        "predecessor": {"filename": predecessor_name, "version": old_version,
                        "qualifier_rank": old_rank},
        "successor": {"filename": successor_name, "version": new_version,
                      "qualifier_rank": new_rank},
    }

    # An explicit version on both sides is the strongest signal there is.
    if old_version is not None and new_version is not None:
        if new_version <= old_version:
            return None
        signals["basis"] = "explicit_version"
        return 0.9, signals

    # A version on one side only: "v2" replacing an unversioned original is
    # the common real case, but it is weaker — the unversioned file might be
    # the newer rewrite.
    if new_version is not None and old_version is None:
        signals["basis"] = "version_added"
        return 0.7, signals
    if old_version is not None and new_version is None:
        return None

    # No versions anywhere: fall back to revision words, which are the weakest
    # signal and only count when they actually disagree.
    if new_rank > old_rank:
        signals["basis"] = "qualifier_words"
        return 0.55, signals
    return None


async def propose_supersessions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    knowledge_evidence: list[EvidenceItem],
    proposed_by: str = "document_versioning_heuristic",
) -> list[KnowledgeSupersessionProposal]:
    """Group knowledge by filename family and propose the replacements.

    Existing proposals for a pair are never re-created — that is what makes a
    rejection durable, and it is checked per pair rather than per run so a
    partially-decided family still yields its undecided pairs.
    """
    families: dict[str, list[EvidenceItem]] = {}
    for item in knowledge_evidence:
        name = (item.title or "").strip()
        if not name:
            continue
        family = document_family(name)
        if family:
            families.setdefault(family, []).append(item)

    proposals: list[KnowledgeSupersessionProposal] = []
    truncated = 0
    for family, items in families.items():
        if len(items) < 2:
            continue
        for predecessor in items:
            for successor in items:
                if predecessor.id == successor.id:
                    continue
                scored = compare_candidates(
                    predecessor.title or "", successor.title or ""
                )
                if scored is None:
                    continue
                confidence, signals = scored
                if confidence < MIN_PROPOSAL_CONFIDENCE:
                    continue
                if len(proposals) >= MAX_PROPOSALS_PER_RUN:
                    truncated += 1
                    continue
                existing = (
                    await db.execute(
                        select(KnowledgeSupersessionProposal).where(
                            KnowledgeSupersessionProposal.tenant_id == tenant_id,
                            KnowledgeSupersessionProposal.predecessor_evidence_id
                            == predecessor.id,
                            KnowledgeSupersessionProposal.successor_evidence_id
                            == successor.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue
                proposal = KnowledgeSupersessionProposal(
                    tenant_id=tenant_id,
                    predecessor_evidence_id=predecessor.id,
                    successor_evidence_id=successor.id,
                    document_family=family[:300],
                    confidence=confidence,
                    signals=signals,
                    reason=(
                        f"{successor.title!r} appears to replace {predecessor.title!r} "
                        f"({signals.get('basis')})"
                    )[:1000],
                    proposed_by=proposed_by,
                )
                db.add(proposal)
                proposals.append(proposal)

    if truncated:
        logger.warning(
            "knowledge_supersession.truncated",
            tenant_id=str(tenant_id),
            dropped=truncated,
            cap=MAX_PROPOSALS_PER_RUN,
        )
    await db.flush()
    return proposals


async def scan_tenant_knowledge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    domain_id: uuid.UUID | None = None,
    proposed_by: str = "document_versioning_heuristic",
) -> list[KnowledgeSupersessionProposal]:
    """Propose over a tenant's knowledge corpus.

    Bounded by ``MAX_SCANNED_DOCUMENTS`` rather than paged: the comparison is
    within-family, so a partial corpus can only miss pairs, never invent them —
    and the newest documents are the ones most likely to have replaced
    something, so that is the end to keep.
    """
    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.evidence_type.in_(tuple(KNOWLEDGE_EVIDENCE_TYPES)),
        )
        .order_by(
            # Source date when the connector knew one, ingest date otherwise —
            # a corpus loaded in a single backfill shares one `ingested_at`,
            # which would make that ordering arbitrary.
            func.coalesce(
                EvidenceItem.created_at_source, EvidenceItem.ingested_at
            ).desc()
        )
        .limit(MAX_SCANNED_DOCUMENTS)
    )
    if domain_id is not None:
        stmt = stmt.where(EvidenceItem.domain_id == domain_id)
    evidence = list((await db.execute(stmt)).scalars().all())
    return await propose_supersessions(
        db, tenant_id, knowledge_evidence=evidence, proposed_by=proposed_by
    )


async def decide_proposal(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    proposal_id: uuid.UUID,
    accept: bool,
    decided_by: uuid.UUID,
    now: datetime | None = None,
) -> KnowledgeSupersessionProposal | None:
    """Accept or reject. Acceptance writes the ``superseded_by`` edge.

    A decided proposal is never re-decided: flipping an accepted supersession
    would leave the edge behind, and silently keeping a stale edge is worse
    than refusing the second decision.
    """
    proposal = await db.get(KnowledgeSupersessionProposal, proposal_id)
    if proposal is None or proposal.tenant_id != tenant_id:
        return None
    if proposal.status != "pending":
        return proposal

    proposal.status = "accepted" if accept else "rejected"
    proposal.decided_by = decided_by
    proposal.decided_at = now or datetime.now(UTC)

    if accept:
        predecessor = await db.get(EvidenceItem, proposal.predecessor_evidence_id)
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            proposal.predecessor_evidence_id,
            "evidence",
            proposal.successor_evidence_id,
            "superseded_by",
            confidence=proposal.confidence,
            metadata={"origin": "knowledge_supersession", "family": proposal.document_family},
            domain_id=getattr(predecessor, "domain_id", None),
        )

    await db.flush()
    logger.info(
        "knowledge_supersession.decided",
        tenant_id=str(tenant_id),
        proposal_id=str(proposal.id),
        status=proposal.status,
    )
    return proposal


async def superseded_evidence_ids(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these have an ACTIVE ``superseded_by`` edge.

    Active only: the edge is temporal, so a supersession that was later closed
    (the successor withdrawn, say) stops demoting its predecessor without
    anyone having to remember to undo a flag.
    """
    if not evidence_ids:
        return set()
    from contextedge.models.pattern import GraphEdge

    rows = (
        await db.execute(
            select(GraphEdge.source_node_id).where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "superseded_by",
                GraphEdge.source_node_type == "evidence",
                GraphEdge.source_node_id.in_(tuple(evidence_ids)),
                GraphEdge.valid_to.is_(None),
            )
        )
    ).scalars().all()
    return set(rows)
