"""Episode reconstruction service."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.models.episode import Episode, EpisodeStep


async def create_episodes_from_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    evidence_items: list[dict],
    evidence_ids: list[uuid.UUID],
) -> list[Episode]:
    """Run LLM extraction and create one or more episodes with steps."""
    extracted_episodes = await reconstruct_episode(evidence_items)
    
    created_episodes = []

    for ep_data in extracted_episodes:
        episode = Episode(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=ep_data.get("title", "Untitled Episode"),
            status="draft",
            extraction_confidence=float(ep_data.get("overall_confidence", 0.5)),
            root_cause_summary=ep_data.get("root_cause_summary"),
            final_outcome=ep_data.get("final_outcome"),
            reviewer_state="pending_review",
            evidence_ids=[str(eid) for eid in evidence_ids],
        )
        db.add(episode)
        await db.flush()

        for step_data in ep_data.get("steps", []):
            step = EpisodeStep(
                episode_id=episode.id,
                step_order=step_data.get("step_order", 0),
                step_type=step_data.get("step_type", "unknown"),
                text=step_data.get("text", ""),
                observation=step_data.get("observation"),
                result_state=step_data.get("result_state", "unknown"),
                failed_flag=step_data.get("failed_flag", False),
                successful_flag=step_data.get("successful_flag", False),
                extraction_confidence=float(step_data.get("confidence", 0.5)),
                evidence_refs=step_data.get("evidence_refs"),
            )
            db.add(step)
        
        await db.flush()
        await db.refresh(episode)
        created_episodes.append(episode)

    return created_episodes
