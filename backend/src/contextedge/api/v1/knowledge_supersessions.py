"""Reviewer surface for knowledge supersession proposals (F4b).

The heuristic proposes and a human decides — which means the human needs
somewhere to decide. A proposal table with no review surface is the same gap
F4b exists to close, wearing different clothes: findings accumulate, nobody
sees them, and retrieval keeps serving the article that was replaced.

`knowledge_manager` throughout: retiring an SOP is a knowledge decision, not an
administrative one, and it is the role that already gates contradictions and
correlation review.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.knowledge_supersession import (
    SUPERSESSION_STATUSES,
    KnowledgeSupersessionProposal,
)
from contextedge.services.knowledge_supersession_service import (
    decide_proposal,
    scan_tenant_knowledge,
)

router = APIRouter()


class SupersessionProposalResponse(BaseModel):
    id: UUID
    predecessor_evidence_id: UUID
    successor_evidence_id: UUID
    document_family: str
    confidence: float
    # The parsed versions and qualifier ranks behind the pairing. A reviewer
    # who cannot see WHY two documents were paired will either rubber-stamp
    # the queue or ignore it.
    signals: dict
    reason: str | None
    status: str
    proposed_by: str | None
    decided_by: UUID | None
    decided_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SupersessionDecision(BaseModel):
    accept: bool


@router.get("", response_model=list[SupersessionProposalResponse])
async def list_supersession_proposals(
    db: DbSession,
    user: AuthUser,
    status_filter: str = Query("pending", alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("knowledge_manager")
    if status_filter not in SUPERSESSION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of {list(SUPERSESSION_STATUSES)}",
        )
    rows = (
        await db.execute(
            select(KnowledgeSupersessionProposal)
            .where(
                KnowledgeSupersessionProposal.tenant_id == user.tenant_id,
                KnowledgeSupersessionProposal.status == status_filter,
            )
            # Strongest signal first: a reviewer working down the queue should
            # meet the explicit version bumps before the "final"/"draft" pairs.
            .order_by(
                KnowledgeSupersessionProposal.confidence.desc(),
                KnowledgeSupersessionProposal.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)


@router.post("/scan", response_model=list[SupersessionProposalResponse])
async def scan_for_supersessions(
    db: DbSession,
    user: AuthUser,
    domain_id: UUID | None = None,
):
    """Run the filename heuristic over the tenant's knowledge corpus.

    Deliberately on demand rather than on a schedule: nothing has reviewed a
    proposal yet, and a queue filling itself before anyone reads it is how a
    review surface becomes noise. Re-running is safe — an already-decided pair
    is never re-proposed, which is what makes a rejection durable.
    """
    user.require_role("knowledge_manager")
    return await scan_tenant_knowledge(
        db,
        user.tenant_id,
        domain_id=domain_id,
        # The heuristic is the author; the human is who asked it to run. Both
        # matter when a reviewer asks where a proposal came from.
        proposed_by=f"document_versioning_heuristic (scan by {user.email})"[:120],
    )


@router.post("/{proposal_id}/decide", response_model=SupersessionProposalResponse)
async def decide_supersession_proposal(
    proposal_id: UUID, body: SupersessionDecision, db: DbSession, user: AuthUser
):
    """Accept (writes the ``superseded_by`` edge) or reject (durably)."""
    user.require_role("knowledge_manager")
    proposal = await decide_proposal(
        db,
        user.tenant_id,
        proposal_id=proposal_id,
        accept=body.accept,
        decided_by=user.user_id,
    )
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supersession proposal not found",
        )
    # The service refuses to re-decide (flipping an accepted supersession would
    # leave the edge behind). Repeating the same decision is idempotent, but a
    # reviewer reversing someone else's must be told, not handed a 200 whose
    # body quietly disagrees with what they clicked.
    requested = "accepted" if body.accept else "rejected"
    if proposal.status != requested:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Proposal already {proposal.status}",
        )
    return proposal
