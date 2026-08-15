"""Pattern clustering service."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import build_episode_graph, persist_pattern_enrichment_edges
from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.services.identity_service import identity_ids_from_refs
from contextedge.services.memory_service import promote_pattern_memory


class DomainMismatchError(ValueError):
    """Raised when a pattern would mix episode content across domain
    boundaries — a pattern is domain-visible knowledge, so its member
    episodes must all be readable under the pattern's domain."""


async def _assert_domain_safe_membership(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    episode_ids: list[uuid.UUID],
) -> None:
    """Defense in depth at the pattern-creation choke point.

    Rules (pattern domain D, episode domain E):
    - E == D is always fine.
    - E is NULL into a domain-D pattern is fine — tenant-global episodes
      are already visible to every domain; tagging narrows, never leaks.
    - E == some domain into a NULL-domain pattern is NEVER fine — a NULL
      pattern is visible to ALL domains, so domain-scoped content would
      leak everywhere.
    - Episodes from another tenant, or ids that don't exist, fail loud.
    """
    rows = (
        await db.execute(
            select(Episode.id, Episode.tenant_id, Episode.domain_id).where(
                Episode.id.in_(episode_ids)
            )
        )
    ).all()
    found = {row[0]: (row[1], row[2]) for row in rows}
    for episode_id in episode_ids:
        if episode_id not in found:
            raise DomainMismatchError(f"Episode {episode_id} does not exist.")
        episode_tenant, episode_domain = found[episode_id]
        if episode_tenant != tenant_id:
            # Deliberately the same error/shape as "missing" — do not
            # confirm the existence of another tenant's episode.
            raise DomainMismatchError(f"Episode {episode_id} does not exist.")
        if episode_domain is not None and episode_domain != domain_id:
            raise DomainMismatchError(
                f"Episode {episode_id} belongs to domain {episode_domain}; "
                f"a pattern in domain {domain_id or 'GLOBAL'} may only "
                "contain episodes from that domain or tenant-global ones."
            )


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
    await _assert_domain_safe_membership(db, tenant_id, domain_id, episode_ids)

    # Preventive Deduplication: merge into an existing pattern when the
    # title matches — scoped to the SAME domain (the domain-safety
    # assertion above covers the incoming episodes; merging into a
    # pattern of another domain would leak across that boundary) and to
    # active patterns only. Fail-soft: a dedup pre-check must never
    # break pattern creation.
    from sqlalchemy import func
    clean_title = title.strip()
    existing_pattern = None
    try:
        existing_pattern_res = await db.execute(
            select(Pattern).where(
                Pattern.tenant_id == tenant_id,
                Pattern.domain_id == domain_id,
                Pattern.active_flag.is_(True),
                func.lower(Pattern.title) == clean_title.lower(),
            ).limit(1)
        )
        existing_pattern = existing_pattern_res.scalar_one_or_none()
    except Exception:  # noqa: BLE001
        existing_pattern = None

    if existing_pattern:
        for ep_id in episode_ids:
            await add_episode_to_pattern(db, tenant_id, existing_pattern.id, ep_id)
        return existing_pattern

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


async def add_episode_to_pattern(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    episode_id: uuid.UUID,
) -> Pattern:
    """Add a new episode to an existing pattern, update count/graph/memory, and
    auto-enqueue playbook regeneration."""
    pattern = await db.get(Pattern, pattern_id)
    if not pattern or pattern.tenant_id != tenant_id:
        raise ValueError(f"Pattern {pattern_id} not found")

    await _assert_domain_safe_membership(db, tenant_id, pattern.domain_id, [episode_id])

    existing_link = (
        await db.execute(
            select(PatternEvidenceLink).where(
                PatternEvidenceLink.pattern_id == pattern_id,
                PatternEvidenceLink.episode_id == episode_id,
            )
        )
    ).scalar_one_or_none()

    if not existing_link:
        link = PatternEvidenceLink(
            pattern_id=pattern.id,
            episode_id=episode_id,
            link_type="member",
        )
        db.add(link)
        pattern.episode_count += 1
        await db.flush()

        ep = await db.get(Episode, episode_id)
        if ep:
            await build_episode_graph(
                db,
                tenant_id,
                ep.id,
                pattern.id,
                identity_ids_from_refs(ep.entity_refs),
                domain_id=pattern.domain_id,
            )

        try:
            from contextedge.workers.pattern_tasks import generate_playbook_candidate

            generate_playbook_candidate.delay(str(pattern.id), str(tenant_id))
        except Exception:
            pass

    return pattern


async def deduplicate_evidence_items(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Merge duplicate evidence items sharing identical title and evidence_type."""
    from sqlalchemy import delete, func, text

    from contextedge.models.evidence import EvidenceItem, RawEvidenceObject

    group_stmt = text("""
        SELECT title, evidence_type, COUNT(*)
        FROM evidence_items
        WHERE tenant_id = :tenant_id AND title IS NOT NULL AND evidence_type != 'thread_message'
        GROUP BY title, evidence_type
        HAVING COUNT(*) > 1
    """)
    groups = (await db.execute(group_stmt, {"tenant_id": tenant_id})).all()

    merged_evidence_count = 0

    for title, ev_type, _cnt in groups:
        ev_stmt = (
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.title == title,
                EvidenceItem.evidence_type == ev_type,
            )
            .order_by(EvidenceItem.ingested_at.asc())
        )
        items = (await db.execute(ev_stmt)).scalars().all()
        if len(items) <= 1:
            continue

        canonical = items[0]
        duplicates = items[1:]

        for dup in duplicates:
            for query_str in (
                "UPDATE case_links SET evidence_id = :can_id WHERE evidence_id = :dup_id",
                "DELETE FROM evidence_identity_links WHERE evidence_id = :dup_id",
                "UPDATE episode_evidence_links SET evidence_id = :can_id"
                " WHERE evidence_id = :dup_id AND episode_id NOT IN"
                " (SELECT episode_id FROM episode_evidence_links"
                " WHERE evidence_id = :can_id)",
                "DELETE FROM episode_evidence_links WHERE evidence_id = :dup_id",
                "UPDATE pattern_evidence_links SET evidence_id = :can_id"
                " WHERE evidence_id = :dup_id AND pattern_id NOT IN"
                " (SELECT pattern_id FROM pattern_evidence_links"
                " WHERE evidence_id = :can_id)",
                "DELETE FROM pattern_evidence_links WHERE evidence_id = :dup_id",
                "UPDATE playbook_evidence_links SET evidence_id = :can_id"
                " WHERE evidence_id = :dup_id AND playbook_version_id NOT IN"
                " (SELECT playbook_version_id FROM playbook_evidence_links"
                " WHERE evidence_id = :can_id)",
                "DELETE FROM playbook_evidence_links WHERE evidence_id = :dup_id",
            ):
                try:
                    await db.execute(text(query_str), {"can_id": canonical.id, "dup_id": dup.id})
                except Exception:
                    pass

            await db.execute(delete(EvidenceItem).where(EvidenceItem.id == dup.id))

            if dup.raw_object_ref:
                ref_cnt = (
                    await db.execute(
                        select(func.count())
                        .select_from(EvidenceItem)
                        .where(EvidenceItem.raw_object_ref == dup.raw_object_ref)
                    )
                ).scalar_one()
                if ref_cnt == 0:
                    await db.execute(
                        delete(RawEvidenceObject).where(
                            RawEvidenceObject.id == dup.raw_object_ref
                        )
                    )

            merged_evidence_count += 1

    await db.flush()
    return merged_evidence_count


async def deduplicate_patterns_and_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict[str, int]:
    """Scan and merge duplicate Evidence Items, Episodes, Patterns, and Playbooks for a tenant."""
    from sqlalchemy import func

    from contextedge.models.pattern import GraphEdge
    from contextedge.models.playbook import Playbook, PlaybookVersion
    from contextedge.services.episode_service import deduplicate_episodes

    # 0. Deduplicate Evidence Items & Episodes first
    merged_evidence_count = await deduplicate_evidence_items(db, tenant_id)
    merged_episodes_count = await deduplicate_episodes(db, tenant_id)

    # 1. Group patterns by normalized title
    pats = (
        await db.execute(
            select(Pattern).where(Pattern.tenant_id == tenant_id)
        )
    ).scalars().all()

    grouped_patterns: dict[str, list[Pattern]] = {}
    for p in pats:
        key = p.title.strip().lower()
        grouped_patterns.setdefault(key, []).append(p)

    merged_patterns_count = 0
    for key, group in grouped_patterns.items():
        if len(group) <= 1:
            continue

        # Sort by episode count desc, then earliest created
        group.sort(key=lambda x: (x.episode_count, x.created_at or 0), reverse=True)
        canonical = group[0]
        duplicates = group[1:]

        for dup in duplicates:
            # Re-link member episodes
            dup_links = (
                await db.execute(
                    select(PatternEvidenceLink).where(
                        PatternEvidenceLink.pattern_id == dup.id
                    )
                )
            ).scalars().all()

            for link in dup_links:
                existing_link = (
                    await db.execute(
                        select(PatternEvidenceLink).where(
                            PatternEvidenceLink.pattern_id == canonical.id,
                            PatternEvidenceLink.episode_id == link.episode_id,
                        )
                    )
                ).scalar_one_or_none()

                if not existing_link:
                    link.pattern_id = canonical.id
                else:
                    await db.delete(link)

            # Re-link graph edges safely
            edges = (
                await db.execute(
                    select(GraphEdge).where(
                        (GraphEdge.source_node_id == dup.id)
                        | (GraphEdge.target_node_id == dup.id)
                    )
                )
            ).scalars().all()

            for edge in edges:
                new_src = (
                    canonical.id
                    if edge.source_node_id == dup.id
                    else edge.source_node_id
                )
                new_tgt = (
                    canonical.id
                    if edge.target_node_id == dup.id
                    else edge.target_node_id
                )

                existing_edge = (
                    await db.execute(
                        select(GraphEdge).where(
                            GraphEdge.tenant_id == edge.tenant_id,
                            GraphEdge.source_node_type == edge.source_node_type,
                            GraphEdge.source_node_id == new_src,
                            GraphEdge.target_node_type == edge.target_node_type,
                            GraphEdge.target_node_id == new_tgt,
                            GraphEdge.edge_type == edge.edge_type,
                        )
                    )
                ).scalar_one_or_none()

                if existing_edge and existing_edge.id != edge.id:
                    await db.delete(edge)
                else:
                    edge.source_node_id = new_src
                    edge.target_node_id = new_tgt

            # Re-link playbooks
            pbs = (
                await db.execute(
                    select(Playbook).where(Playbook.pattern_id == dup.id)
                )
            ).scalars().all()
            for pb in pbs:
                pb.pattern_id = canonical.id

            await db.delete(dup)
            merged_patterns_count += 1

        count_res = await db.execute(
            select(func.count(PatternEvidenceLink.id)).where(
                PatternEvidenceLink.pattern_id == canonical.id
            )
        )
        canonical.episode_count = count_res.scalar() or 0

    await db.flush()

    # 2. Deduplicate Playbooks per pattern or normalized title
    pbs = (
        await db.execute(
            select(Playbook).where(Playbook.tenant_id == tenant_id)
        )
    ).scalars().all()

    grouped_playbooks: dict[tuple, list[Playbook]] = {}
    for pb in pbs:
        pb_key = (pb.tenant_id, pb.pattern_id or pb.title.strip().lower())
        grouped_playbooks.setdefault(pb_key, []).append(pb)

    merged_playbooks_count = 0
    for pb_key, group in grouped_playbooks.items():
        if len(group) <= 1:
            continue

        group.sort(key=lambda x: x.updated_at or x.created_at or 0, reverse=True)
        canonical_pb = group[0]
        duplicates_pb = group[1:]

        for dup_pb in duplicates_pb:
            from sqlalchemy import delete, update

            from contextedge.models.playbook import PlaybookEvidenceLink

            dup_versions = (
                await db.execute(
                    select(PlaybookVersion).where(
                        PlaybookVersion.playbook_id == dup_pb.id
                    )
                )
            ).scalars().all()

            for v in dup_versions:
                existing_ver = (
                    await db.execute(
                        select(PlaybookVersion).where(
                            PlaybookVersion.playbook_id == canonical_pb.id,
                            PlaybookVersion.semantic_version == v.semantic_version,
                        )
                    )
                ).scalar_one_or_none()

                if not existing_ver:
                    await db.execute(
                        update(PlaybookVersion)
                        .where(PlaybookVersion.id == v.id)
                        .values(playbook_id=canonical_pb.id)
                    )
                else:
                    await db.execute(
                        delete(PlaybookEvidenceLink).where(
                            PlaybookEvidenceLink.playbook_version_id == v.id
                        )
                    )
                    await db.execute(
                        delete(PlaybookVersion).where(PlaybookVersion.id == v.id)
                    )

            await db.execute(
                delete(Playbook).where(Playbook.id == dup_pb.id)
            )
            merged_playbooks_count += 1

    await db.flush()
    return {
        "merged_evidence": merged_evidence_count,
        "merged_episodes": merged_episodes_count,
        "merged_patterns": merged_patterns_count,
        "merged_playbooks": merged_playbooks_count,
    }

