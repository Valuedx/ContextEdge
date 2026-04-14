"""Pattern clustering service."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.graph.builder import persist_pattern_enrichment_edges
from contextedge.services.memory_service import promote_pattern_memory


async def create_pattern_from_episodes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    title: str,
    episode_ids: list[uuid.UUID],
    confidence: float = 0.5,
    description: str | None = None,
    trigger_conditions: list[str] | None = None,
    core_entities: list[str] | None = None,
    observed_errors: list[str] | None = None,
    root_causes: list[str] | None = None,
    resolution_steps: list[str] | None = None,
    evidence_summary: dict | None = None,
) -> Pattern:
    """Create a pattern from a cluster of episodes."""
    pattern = Pattern(
        tenant_id=tenant_id,
        domain_id=domain_id,
        title=title,
        description=description,
        pattern_type="recurring_issue",
        confidence=confidence,
        episode_count=len(episode_ids),
        trigger_conditions=trigger_conditions,
        core_entities=core_entities,
        observed_errors=observed_errors,
        root_causes=root_causes,
        resolution_steps=resolution_steps,
        evidence_summary=evidence_summary,
    )
    db.add(pattern)
    await db.flush()

    for ep_id in episode_ids:
        link = PatternEvidenceLink(
            pattern_id=pattern.id,
            episode_id=ep_id,
            link_type="member",
        )
        db.add(link)

    await db.flush()

    await persist_pattern_enrichment_edges(
        db,
        tenant_id,
        pattern.id,
        domain_id,
        trigger_conditions=trigger_conditions,
        core_entities=core_entities,
        observed_errors=observed_errors,
        root_causes=root_causes,
    )

    await promote_pattern_memory(
        db,
        tenant_id=tenant_id,
        pattern=pattern,
        episode_ids=episode_ids,
    )
    await db.refresh(pattern)
    return pattern
