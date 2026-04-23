"""Episode reconstruction service."""

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.models.episode import Episode, EpisodeStep
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()


def _merge_identity_refs(values: list[dict | None]) -> dict | None:
    merged: list[dict] = []
    seen: set[str] = set()
    for refs in values:
        identities = (refs or {}).get("identities")
        if not isinstance(identities, list):
            continue
        for item in identities:
            if not isinstance(item, dict):
                continue
            canonical_id = item.get("canonical_id")
            if not canonical_id or canonical_id in seen:
                continue
            seen.add(str(canonical_id))
            merged.append(item)
    if not merged:
        return None
    return {"identities": merged}


async def create_episodes_from_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    evidence_items: list[dict],
    evidence_ids: list[uuid.UUID],
) -> list[Episode]:
    """Run LLM extraction and create one or more episodes with steps."""
    try:
        extracted_episodes = await reconstruct_episode(
            evidence_items, tenant_id=tenant_id, db=db,
        )
    except Exception as exc:
        logger.warning(
            "episode_reconstruction_llm_failed",
            tenant_id=str(tenant_id),
            evidence_count=len(evidence_items),
            error=str(exc),
        )
        return []

    evidence_result = await db.execute(
        select(EvidenceItem.canonical_entity_refs).where(EvidenceItem.id.in_(evidence_ids))
    )
    entity_refs = _merge_identity_refs([row[0] for row in evidence_result.all()])
    
    created_episodes = []
    for ep_data in extracted_episodes:
        # Generate semantic embedding for the episode
        title = ep_data.get("title", "Untitled Episode")
        root_cause = ep_data.get("root_cause_summary", "")
        emb_text = f"{title}\n\n{root_cause}"
        try:
            # Review F-03: thread tenant_id + db so the embedding call lands
            # in /admin/cost and respects the per-tenant budget gate.
            embedding = await generate_embedding(
                emb_text, tenant_id=tenant_id, db=db,
            )
        except Exception:
            logger.warning("episode_embedding_failed", ep_title=title)
            embedding = None

        episode = Episode(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=title,
            status="draft",
            extraction_confidence=float(ep_data.get("overall_confidence", 0.5)),
            root_cause_summary=root_cause,
            final_outcome=ep_data.get("final_outcome"),
            reviewer_state="pending_review",
            evidence_ids=[str(eid) for eid in evidence_ids],
            entity_refs=entity_refs,
            embedding=embedding,
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
