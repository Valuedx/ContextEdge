"""Pattern clustering service."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidenceLink


async def create_pattern_from_episodes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    title: str,
    episode_ids: list[uuid.UUID],
    confidence: float = 0.5,
) -> Pattern:
    """Create a pattern from a cluster of episodes."""
    pattern = Pattern(
        tenant_id=tenant_id,
        domain_id=domain_id,
        title=title,
        pattern_type="recurring_issue",
        confidence=confidence,
        episode_count=len(episode_ids),
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
    await db.refresh(pattern)
    return pattern
