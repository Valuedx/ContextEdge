"""Overview statistics API providing exact database counts for dashboard metrics."""
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.episode import Episode
from contextedge.models.evidence import EvidenceItem
from contextedge.models.playbook import Playbook
from contextedge.models.source import Source

router = APIRouter()


class OverviewStatsResponse(BaseModel):
    active_sources: int
    connected_sources: int
    total_evidence: int
    total_episodes: int
    pending_episodes: int
    approved_playbooks: int
    candidate_playbooks: int


@router.get("/stats", response_model=OverviewStatsResponse)
async def get_overview_stats(
    db: DbSession,
    user: AuthUser,
) -> OverviewStatsResponse:
    """Return exact counts for the dashboard overview metrics."""
    tenant_id = user.tenant_id

    # Active sources & connected sources
    active_sources_q = select(func.count()).select_from(Source).where(
        Source.tenant_id == tenant_id,
        Source.is_active.is_(True),
    )
    connected_sources_q = select(func.count()).select_from(Source).where(
        Source.tenant_id == tenant_id,
        Source.is_active.is_(True),
        Source.auth_status == "connected",
    )

    # Primary evidence items (excludes individual thread replies/messages, matching GET /evidence contract)
    evidence_q = select(func.count()).select_from(EvidenceItem).where(
        EvidenceItem.tenant_id == tenant_id,
        EvidenceItem.evidence_type != "thread_message",
    )

    # Episodes (excluding superseded drafts)
    episodes_q = select(func.count()).select_from(Episode).where(
        Episode.tenant_id == tenant_id,
        Episode.reviewer_state != "superseded",
    )
    pending_episodes_q = select(func.count()).select_from(Episode).where(
        Episode.tenant_id == tenant_id,
        Episode.reviewer_state == "pending_review",
    )

    # Playbooks
    approved_playbooks_q = select(func.count()).select_from(Playbook).where(
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state == "approved",
    )
    candidate_playbooks_q = select(func.count()).select_from(Playbook).where(
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state.in_(["candidate", "under_review"]),
    )

    # Execute count queries
    active_sources = (await db.execute(active_sources_q)).scalar() or 0
    connected_sources = (await db.execute(connected_sources_q)).scalar() or 0
    total_evidence = (await db.execute(evidence_q)).scalar() or 0
    total_episodes = (await db.execute(episodes_q)).scalar() or 0
    pending_episodes = (await db.execute(pending_episodes_q)).scalar() or 0
    approved_playbooks = (await db.execute(approved_playbooks_q)).scalar() or 0
    candidate_playbooks = (await db.execute(candidate_playbooks_q)).scalar() or 0

    return OverviewStatsResponse(
        active_sources=active_sources,
        connected_sources=connected_sources,
        total_evidence=total_evidence,
        total_episodes=total_episodes,
        pending_episodes=pending_episodes,
        approved_playbooks=approved_playbooks,
        candidate_playbooks=candidate_playbooks,
    )
