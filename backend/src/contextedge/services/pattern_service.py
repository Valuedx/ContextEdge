"""Pattern clustering service."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.graph.builder import persist_pattern_enrichment_edges, build_episode_graph
from contextedge.services.identity_service import identity_ids_from_refs
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

    # 1. Relational Links (Membership)
    for ep_id in episode_ids:
        link = PatternEvidenceLink(
            pattern_id=pattern.id,
            episode_id=ep_id,
            link_type="member",
        )
        db.add(link)

    await db.flush()

    # 2. Graph Enrichment Edges (Virtual Concepts -> Pattern)
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

    # 3. Graph Membership & Impact Edges (Episode -> Pattern, Episode -> Identity)
    # This powers the "Episode" and "Identity" nodes in the graph view.
    # Fetch full episode data to get entity_refs
    ep_result = await db.execute(
        select(Episode).where(Episode.id.in_(episode_ids))
    )
    episodes = ep_result.scalars().all()
    for ep in episodes:
        await build_episode_graph(
            db,
            tenant_id,
            ep.id,
            pattern.id,
            identity_ids_from_refs(ep.entity_refs),
            domain_id=domain_id,
        )

    # 4. Long-term memory promotion
    await promote_pattern_memory(
        db,
        tenant_id=tenant_id,
        pattern=pattern,
        episode_ids=episode_ids,
    )
    await db.refresh(pattern)

    # 5. Review F-08: auto-enqueue candidate playbook generation. The
    # only prior entry points were a manual API call (POST
    # /playbooks/generate) and the pattern-tasks Celery beat; patterns
    # would accrue without candidates unless someone clicked. Local
    # import avoids a circular dependency (pattern_tasks imports this
    # module). Wrapped in try/except so a broken pattern-tasks import
    # doesn't break pattern creation.
    try:
        from contextedge.workers.pattern_tasks import generate_playbook_candidate

        generate_playbook_candidate.delay(str(pattern.id), str(tenant_id))
    except Exception as exc:  # pragma: no cover — belt-and-braces
        import structlog

        structlog.get_logger().warning(
            "pattern_service.playbook_enqueue_failed",
            tenant_id=str(tenant_id), pattern_id=str(pattern.id), error=str(exc),
        )

    return pattern
