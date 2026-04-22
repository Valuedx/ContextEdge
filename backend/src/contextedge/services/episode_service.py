"""Episode reconstruction service."""

import re
import uuid

import structlog
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding
from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.models.episode import Episode, EpisodeStep
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()


def _normalize_title(title: str) -> str:
    """Normalize title by removing common prefixes and cleaning whitespace."""
    if not title:
        return ""
    # Remove common prefixes: Re:, Fw:, URGENT:, [EXTERNAL], etc.
    # We use a loop to handle multiple nested prefixes like "Re: Fwd: Re: ..."
    t = title.strip()
    while True:
        # Match re:, fw:, etc (require colon) OR bracketed [INFO] (optional colon)
        new_t = re.sub(r"(?i)^((re|fw|fwd|urgent|important|alert|ext):\s*|\[[^\]]+\]:?\s*)", "", t)
        if new_t == t:
            break
        t = new_t
    
    # Remove common suffixes
    t = re.sub(r"(?i)\s+-\s+(urgent|important|re|fw)$", "", t)
    # Collapse whitespace
    return " ".join(t.lower().split())


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
    target_episode_id: uuid.UUID | None = None,
) -> list[Episode]:
    """Run LLM extraction and create one or more episodes with steps."""
    try:
        extracted_episodes = await reconstruct_episode(evidence_items)
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
    
    # 1. Normalize and Group extracted episodes from the LLM response by title
    grouped_extracted = {}  # norm_title -> merged_ep_data
    for ep in extracted_episodes:
        raw_title = ep.get("title", "Untitled Episode").strip()
        norm_title = _normalize_title(raw_title)
        
        if norm_title in grouped_extracted:
            logger.info("episode_batch.merging_duplicate_title", title=raw_title, norm=norm_title)
            # Merge steps from this finding into the existing one for this title
            existing = grouped_extracted[norm_title]
            existing_steps = existing.get("steps", [])
            new_steps = ep.get("steps", [])
            # Simple merge: append steps (deduplication by text could be added later)
            existing["steps"] = existing_steps + new_steps
        else:
            grouped_extracted[norm_title] = ep

    deduped_extracted = list(grouped_extracted.values())

    created_episodes = []
    for ep_data in deduped_extracted:
        # Final clean title for storage
        raw_title = ep_data.get("title", "Untitled Episode")
        norm_title = _normalize_title(raw_title)
        title = raw_title # Fallback to LLM title but we check against norm
        
    # 2. Check for target episode if provided
    target_ep = None
    if target_episode_id:
        target_ep = await db.get(Episode, target_episode_id)
        if target_ep and target_ep.tenant_id != tenant_id:
            target_ep = None

    created_episodes = []
    target_assigned = False

    for ep_data in deduped_extracted:
        # Final clean title for storage
        raw_title = ep_data.get("title", "Untitled Episode")
        norm_title = _normalize_title(raw_title)
        title = raw_title # Fallback to LLM title but we check against norm
        
        existing_ep = None
        
        # A. If target_ep is provided and matches this title, use it
        if target_ep and not target_assigned:
            if _normalize_title(target_ep.title) == norm_title or len(deduped_extracted) == 1:
                existing_ep = target_ep
                target_assigned = True
                logger.info("episode_merge.using_target", title=raw_title, target_id=str(target_ep.id))
        
        # B. If no target yet, look for matching draft in DB
        if existing_ep is None:
            drafts_res = await db.execute(
                select(Episode).where(
                    Episode.tenant_id == tenant_id,
                    Episode.status == "draft"
                )
            )
            for d_ep in drafts_res.scalars().all():
                if _normalize_title(d_ep.title) == norm_title:
                    # Double check it hasn't already been picked in this batch
                    if not any(e.id == d_ep.id for e in created_episodes):
                        existing_ep = d_ep
                        break

        if existing_ep:
            logger.info("episode_deduplication.merging_with_existing", title=raw_title, match=existing_ep.title, ep_id=str(existing_ep.id))
            
            # Sync evidence IDs: ensure Episode knows about all 8+ items in the trail now
            current_ids = set(existing_ep.evidence_ids or [])
            for eid in evidence_ids:
                current_ids.add(str(eid))
            existing_ep.evidence_ids = list(current_ids)

            # Merge steps: replace all existing steps with the freshly-extracted ones
            # so that any new emails from the trail are reflected.
            new_steps = ep_data.get("steps", [])
            if new_steps:
                # Delete old steps for this episode
                await db.execute(
                    delete(EpisodeStep).where(EpisodeStep.episode_id == existing_ep.id)
                )
                await db.flush()
                # Insert fresh steps from the current LLM run
                for step_data in new_steps:
                    step_type = str(step_data.get("step_type", "unknown"))[:30]
                    result_state = str(step_data.get("result_state", "unknown"))[:30]
                    step = EpisodeStep(
                        episode_id=existing_ep.id,
                        step_order=step_data.get("step_order", 0),
                        step_type=step_type,
                        text=step_data.get("text", ""),
                        observation=step_data.get("observation"),
                        result_state=result_state,
                        failed_flag=step_data.get("failed_flag", False),
                        successful_flag=step_data.get("successful_flag", False),
                        extraction_confidence=float(step_data.get("confidence", 0.5)),
                        evidence_refs=step_data.get("evidence_refs"),
                    )
                    db.add(step)
                await db.flush()
                logger.info(
                    "episode_deduplication.steps_refreshed",
                    episode_id=str(existing_ep.id),
                    step_count=len(new_steps),
                )

            # Also refresh root_cause and final_outcome if the new run has them
            if ep_data.get("root_cause_summary"):
                existing_ep.root_cause_summary = ep_data["root_cause_summary"]
            if ep_data.get("final_outcome"):
                existing_ep.final_outcome = ep_data["final_outcome"]
            await db.flush()
            created_episodes.append(existing_ep)
            continue

        # Generate semantic embedding for the episode
        root_cause = ep_data.get("root_cause_summary", "")
        emb_text = f"{title}\n\n{root_cause}"
        try:
            embedding = await generate_embedding(emb_text)
        except Exception:
            logger.warning("episode_embedding_failed", ep_title=title)
            embedding = None

        episode = Episode(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=title[:500],
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
            step_type = str(step_data.get("step_type", "unknown"))[:30]
            result_state = str(step_data.get("result_state", "unknown"))[:30]
            
            step = EpisodeStep(
                episode_id=episode.id,
                step_order=step_data.get("step_order", 0),
                step_type=step_type,
                text=step_data.get("text", ""),
                observation=step_data.get("observation"),
                result_state=result_state,
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
