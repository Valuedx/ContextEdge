"""Episode reconstruction service."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.models.episode import Episode, EpisodeStep


async def create_episode_from_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    evidence_items: list[dict],
    evidence_ids: list[uuid.UUID],
) -> Episode:
    """Run LLM extraction and create an episode with steps."""
    extracted = await reconstruct_episode(evidence_items)

    episode = Episode(
        tenant_id=tenant_id,
        domain_id=domain_id,
        title=extracted.get("title", "Untitled Episode"),
        status="draft",
        extraction_confidence=float(extracted.get("overall_confidence", 0.5)),
        root_cause_summary=extracted.get("root_cause_summary"),
        final_outcome=extracted.get("final_outcome"),
        reviewer_state="pending_review",
        evidence_ids=[str(eid) for eid in evidence_ids],
    )
    db.add(episode)
    await db.flush()

    for step_data in extracted.get("steps", []):
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
    return episode
