"""Episode reconstruction service."""

import uuid

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY
from contextedge.ai.provider import generate_embedding
from contextedge.models.episode import Episode, EpisodeEvidenceLink, EpisodeStep
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()


async def _resolve_primary_case_ref(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> str | None:
    """The ticket number this episode should be cited by.

    An episode without one can be named but never opened: an agent cites the
    title and the reader has nothing to look up. That is the difference
    between a claim someone can verify in a click and one they must take on
    trust.

    Which identifier wins, when the cited evidence spans several cases:

    1. Follow the episode's OWN evidence to its canonical cases
       (``case_links``), never the whole cluster — the extractor may split a
       mixed cluster, and each episode should carry its own ticket.
    2. Prefer the case that the most of that evidence points at. An episode
       reconstructed from six messages about INC0001 and one stray reference
       to INC0002 is about INC0001.
    3. Within the case, take the identifier the correlation layer already
       marked ``is_authoritative`` — that flag exists precisely to answer
       this, so this code defers to it rather than inventing a second rule.
    4. Ties break on the lowest identifier value, so the same inputs always
       produce the same answer.

    Returns None when the evidence has no linked case yet, which is normal:
    correlation may not have run. The field stays empty rather than being
    filled with a guess.
    """
    if not evidence_ids:
        return None

    from contextedge.models.case_bridge import CaseIdentifier
    from contextedge.models.session import CaseLink

    rows = (
        await db.execute(
            select(CaseLink.canonical_case_id)
            .where(
                CaseLink.tenant_id == tenant_id,
                CaseLink.evidence_id.in_(evidence_ids),
            )
        )
    ).scalars().all()
    if not rows:
        return None

    counts: dict[uuid.UUID, int] = {}
    for case_id in rows:
        counts[case_id] = counts.get(case_id, 0) + 1
    # Most-referenced case first; ties resolve on the id so the choice is
    # reproducible rather than dependent on row order.
    best_case = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]

    identifiers = (
        await db.execute(
            select(CaseIdentifier.display_value, CaseIdentifier.is_authoritative)
            .where(
                CaseIdentifier.tenant_id == tenant_id,
                CaseIdentifier.canonical_case_id == best_case,
            )
        )
    ).all()
    if not identifiers:
        return None

    authoritative = sorted(
        (value for value, is_auth in identifiers if is_auth and value)
    )
    if authoritative:
        return authoritative[0]
    fallback = sorted(value for value, _ in identifiers if value)
    return fallback[0] if fallback else None


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
    cluster_fingerprint: str | None = None,
    cluster_reasons: dict[str, list[str]] | None = None,
) -> list[Episode]:
    """Run LLM extraction and create one or more episodes with steps.

    Evidence membership is PER EPISODE: when the extractor splits a
    mixed cluster, each episode gets only the evidence it cited
    (validated against the input — the model can never mint evidence).
    An episode whose citations are missing/invalid falls back to the
    full cluster with a logged flag, never silently."""
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

    # Review F-06: an empty extractor response is valid JSON but
    # distinct from "LLM failed" — log it so ops can tell drift
    # ("extractor stopped finding episodes") from outages.
    if not extracted_episodes:
        logger.info(
            "episode_reconstruction_zero_result",
            tenant_id=str(tenant_id),
            evidence_count=len(evidence_items),
        )
        return []

    all_entity_rows = (
        await db.execute(
            select(EvidenceItem.id, EvidenceItem.canonical_entity_refs).where(
                EvidenceItem.id.in_(evidence_ids)
            )
        )
    ).all()
    entity_refs_by_evidence = {row[0]: row[1] for row in all_entity_rows}
    valid_id_strings = {str(eid) for eid in evidence_ids}

    created_episodes = []
    for ep_data in extracted_episodes:
        # Per-episode membership from the model's citations, validated
        # against the input cluster.
        cited = [
            ref for ref in (ep_data.get("evidence_refs") or []) if ref in valid_id_strings
        ]
        if cited:
            episode_evidence_ids = [uuid.UUID(ref) for ref in dict.fromkeys(cited)]
            membership_source = "model_attribution"
        else:
            episode_evidence_ids = list(evidence_ids)
            membership_source = "full_cluster_fallback"
            logger.info(
                "episode_membership_fallback",
                tenant_id=str(tenant_id),
                episode_title=ep_data.get("title"),
                cluster_size=len(evidence_ids),
            )
        entity_refs = _merge_identity_refs(
            [entity_refs_by_evidence.get(eid) for eid in episode_evidence_ids]
        )
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

        # Preventive Episode Deduplication — constrained to the SAME
        # OCCURRENCE. A bare title match merges tenant-wide: two
        # different incidents that both title "Password reset issue"
        # would fuse and cross-link unrelated evidence — exactly the
        # cross-case contamination the recurrence system exists to
        # avoid ("similar problem, NEVER the same occurrence"). Merge
        # only when the existing episode is active AND shares evidence
        # with the incoming one; otherwise create normally and let the
        # fingerprint supersession handle cluster-identity dedup.
        from sqlalchemy import func
        clean_title = title.strip().lower()
        existing_ep = None
        try:
            existing_ep_res = await db.execute(
                select(Episode).where(
                    Episode.tenant_id == tenant_id,
                    func.lower(Episode.title) == clean_title,
                    Episode.reviewer_state.notin_(("superseded", "rejected")),
                ).limit(5)
            )
            incoming_ids = {str(e) for e in episode_evidence_ids}
            existing_ep = next(
                (
                    ep
                    for ep in existing_ep_res.scalars()
                    if incoming_ids & {str(e) for e in (ep.evidence_ids or [])}
                ),
                None,
            )
        except Exception:  # noqa: BLE001 - a dedup PRE-CHECK must never
            existing_ep = None  # break episode creation itself.
        if existing_ep:
            for evidence_id in episode_evidence_ids:
                link_exists = (
                    await db.execute(
                        select(EpisodeEvidenceLink).where(
                            EpisodeEvidenceLink.episode_id == existing_ep.id,
                            EpisodeEvidenceLink.evidence_id == evidence_id,
                        )
                    )
                ).scalar_one_or_none()
                if not link_exists:
                    why = (cluster_reasons or {}).get(str(evidence_id))
                    # Reasons arrive as a LIST; join like the new-episode
                    # branch below does. Binding the list raw into this
                    # String column raised asyncpg DataError on every
                    # merge once the draft pool grew dense enough for
                    # same-title merges to become the common path.
                    if isinstance(why, (list, tuple)):
                        why = ",".join(str(reason) for reason in why)
                    db.add(
                        EpisodeEvidenceLink(
                            tenant_id=tenant_id,
                            episode_id=existing_ep.id,
                            evidence_id=evidence_id,
                            link_reason=(why or membership_source or "evidence_merged")[:120],
                        )
                    )
            created_episodes.append(existing_ep)
            continue

        episode = Episode(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=title,
            primary_case_ref=await _resolve_primary_case_ref(
                db, tenant_id, episode_evidence_ids
            ),
            status="draft",
            extraction_confidence=float(ep_data.get("overall_confidence", 0.5)),
            root_cause_summary=root_cause,
            final_outcome=ep_data.get("final_outcome"),
            reviewer_state="pending_review",
            evidence_ids=[str(eid) for eid in episode_evidence_ids],
            cluster_fingerprint=cluster_fingerprint,
            entity_refs=entity_refs,
            contradictions=ep_data.get("contradictions") or None,
            embedding=embedding,
            generation_provenance=ep_data.get(GENERATION_PROVENANCE_KEY),
        )
        db.add(episode)
        await db.flush()

        # Normalized provenance (0037): one row per grounding evidence,
        # carrying WHY it is in the cluster (review renders this).
        reasons = cluster_reasons or {}
        for evidence_id in episode_evidence_ids:
            why = reasons.get(str(evidence_id))
            db.add(
                EpisodeEvidenceLink(
                    tenant_id=tenant_id,
                    episode_id=episode.id,
                    evidence_id=evidence_id,
                    link_reason=(
                        ",".join(why)[:120] if why else membership_source
                    ),
                )
            )

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

        # Synthesis-quality signal (P4 item 6): the fraction of steps the
        # model could not ground in evidence is the day-1 measurable
        # proxy for unsupported claims. Logged per episode so drift in
        # grounding quality shows up in operational events, not anecdotes.
        steps = ep_data.get("steps", [])
        ungrounded = sum(1 for s in steps if not s.get("evidence_refs"))
        logger.info(
            "episode.synthesis_quality",
            tenant_id=str(tenant_id),
            episode_id=str(episode.id),
            steps_total=len(steps),
            steps_ungrounded=ungrounded,
            ungrounded_ratio=(ungrounded / len(steps)) if steps else 0.0,
        )

    return created_episodes


async def deduplicate_episodes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> int:
    """Merge duplicate episodes with matching normalized titles for a tenant."""

    eps = (
        await db.execute(
            select(Episode).where(
                Episode.tenant_id == tenant_id,
                # Already-superseded/rejected episodes are settled
                # history — re-merging them would churn links forever.
                Episode.reviewer_state.notin_(("superseded", "rejected")),
            )
        )
    ).scalars().all()

    # Group by (title, shared evidence): title alone merges different
    # incidents that happen to share a label. Within a title group,
    # only episodes overlapping in evidence merge together.
    grouped_episodes: dict[str, list[Episode]] = {}
    for ep in eps:
        key = ep.title.strip().lower()
        grouped_episodes.setdefault(key, []).append(ep)

    def _overlap_components(group: list[Episode]) -> list[list[Episode]]:
        """Split a same-title group into evidence-overlap connected
        components — only episodes sharing evidence are the same
        occurrence and may merge."""
        sets = [{str(e) for e in (ep.evidence_ids or [])} for ep in group]
        parent = list(range(len(group)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if sets[i] & sets[j]:
                    parent[find(i)] = find(j)
        components: dict[int, list[Episode]] = {}
        for i, ep in enumerate(group):
            components.setdefault(find(i), []).append(ep)
        return list(components.values())

    merged_count = 0
    for key, title_group in grouped_episodes.items():
      for group in _overlap_components(title_group):
        if len(group) <= 1:
            continue

        # Keep approved / highest confidence / earliest created
        group.sort(
            key=lambda x: (
                1 if x.reviewer_state == "approved" else 0,
                x.extraction_confidence or 0,
                -(x.created_at.timestamp() if x.created_at else 0),
            ),
            reverse=True,
        )
        canonical = group[0]
        duplicates = group[1:]

        for dup in duplicates:
            await _merge_episode_into(db, dup, canonical)
            merged_count += 1

    await db.flush()
    return merged_count


async def _merge_episode_into(db: AsyncSession, dup: "Episode", canonical: "Episode") -> None:
    """Fold ``dup`` into ``canonical``: re-link everything, then supersede.

    Extracted so the title-based sweep and the containment sweep share one
    implementation — two copies of this re-linking would drift, and a missed
    link type leaves orphaned rows pointing at a superseded episode.
    """
    from contextedge.models.episode import EpisodeStep
    from contextedge.models.pattern import GraphEdge, PatternEvidenceLink

    # 1. Re-link EpisodeEvidenceLink
    dup_ev_links = (
        await db.execute(
            select(EpisodeEvidenceLink).where(
                EpisodeEvidenceLink.episode_id == dup.id
            )
        )
    ).scalars().all()

    for link in dup_ev_links:
        existing_link = (
            await db.execute(
                select(EpisodeEvidenceLink).where(
                    EpisodeEvidenceLink.episode_id == canonical.id,
                    EpisodeEvidenceLink.evidence_id == link.evidence_id,
                )
            )
        ).scalar_one_or_none()

        if not existing_link:
            link.episode_id = canonical.id
        else:
            await db.delete(link)

    # 2. Re-link PatternEvidenceLink
    dup_pat_links = (
        await db.execute(
            select(PatternEvidenceLink).where(
                PatternEvidenceLink.episode_id == dup.id
            )
        )
    ).scalars().all()

    for plink in dup_pat_links:
        existing_plink = (
            await db.execute(
                select(PatternEvidenceLink).where(
                    PatternEvidenceLink.pattern_id == plink.pattern_id,
                    PatternEvidenceLink.episode_id == canonical.id,
                )
            )
        ).scalar_one_or_none()

        if not existing_plink:
            plink.episode_id = canonical.id
        else:
            await db.delete(plink)

    # 3. Re-link EpisodeStep
    steps = (
        await db.execute(
            select(EpisodeStep).where(EpisodeStep.episode_id == dup.id)
        )
    ).scalars().all()
    for step in steps:
        step.episode_id = canonical.id

    # 4. Re-link Graph edges
    edges = (
        await db.execute(
            select(GraphEdge).where(
                (GraphEdge.source_node_id == dup.id)
                | (GraphEdge.target_node_id == dup.id)
            )
        )
    ).scalars().all()

    for edge in edges:
        new_src = canonical.id if edge.source_node_id == dup.id else edge.source_node_id
        new_tgt = canonical.id if edge.target_node_id == dup.id else edge.target_node_id

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

    # Supersede, never hard-delete: the governed lifecycle keeps
    # the audit trail and stays reversible (same rule as the
    # reconstruction-race cleanup).
    dup.reviewer_state = "superseded"


async def supersede_contained_episodes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    dry_run: bool = False,
) -> dict:
    """Retire episodes whose evidence is wholly contained in a bigger one.

    The gap this closes. Title-based dedup requires the two episodes to carry
    the SAME normalized title, and a regrown cluster never does: each time a
    thread message lands, the extractor writes a fresh, differently-worded
    account of the same incident. Measured on the live corpus, one ticket
    accumulated **44 live episodes** of a single "Agent Unknown State"
    incident with **zero** exact-title matches between any pair — so dedup
    could not fire even once — while **97 pairs** had one evidence set fully
    containing another. 190 of 434 covered tickets carried 4+ episodes.

    Containment is the right test precisely because it is strict. If every
    piece of evidence behind B is also behind A, then A is an account of the
    same material and a more complete one; keeping B adds no fact and costs
    the reader a choice between near-identical write-ups. That is the whole
    observed failure: a cluster grows, gets re-narrated, and the earlier
    telling is never retired.

    What it deliberately does NOT do is merge on partial overlap. On the same
    ticket, 148 pairs overlapped without containment at Jaccard 0.04-0.33 —
    "BOT Failures After OS Upgrade" against "Agent VSM01 Unknown State".
    Those share a ticket, not an incident, and a threshold low enough to
    catch them would fuse genuinely different problems. Strict containment
    needs no threshold to tune and cannot make that mistake.

    Largest-first, so a chain (A ⊇ B ⊇ C) collapses into A in one pass rather
    than leaving C pointing at a superseded B.
    """
    episodes = (
        await db.execute(
            select(Episode).where(
                Episode.tenant_id == tenant_id,
                Episode.reviewer_state.notin_(("superseded", "rejected")),
            )
        )
    ).scalars().all()

    sets: dict[uuid.UUID, set[str]] = {}
    for ep in episodes:
        ids = {str(e) for e in (ep.evidence_ids or [])}
        if ids:
            sets[ep.id] = ids

    # Biggest first: a container must be processed before what it contains,
    # or the chain collapses one link per sweep instead of all at once.
    ordered = sorted(
        (ep for ep in episodes if ep.id in sets),
        key=lambda ep: (
            len(sets[ep.id]),
            1 if ep.reviewer_state == "approved" else 0,
            ep.extraction_confidence or 0.0,
        ),
        reverse=True,
    )

    retired: set[uuid.UUID] = set()
    planned: list[tuple[str, str]] = []
    for container in ordered:
        if container.id in retired:
            continue
        big = sets[container.id]
        for other in ordered:
            if other.id == container.id or other.id in retired:
                continue
            small = sets[other.id]
            # Strict containment. Equal sets are containment both ways; the
            # ordering above already put the better-kept one first, so the
            # later one is the duplicate.
            if small <= big and len(small) < len(big) or (
                small == big and other.id != container.id
            ):
                retired.add(other.id)
                planned.append((str(other.id), str(container.id)))
                if not dry_run:
                    await _merge_episode_into(db, other, container)

    if not dry_run:
        await db.flush()
        logger.info(
            "episode.contained_superseded",
            tenant_id=str(tenant_id),
            retired=len(retired),
            live_before=len(episodes),
        )

    return {
        "live_before": len(episodes),
        "retired": len(retired),
        "live_after": len(episodes) - len(retired),
        "pairs": planned[:20],
    }


# Semantic sibling threshold. Chosen from the live corpus, not by feel:
# pairs describing clearly DIFFERENT incidents on the same ticket topped out
# at cosine 0.578 ("BOT Failures After OS Upgrade" vs "Agent Unknown State
# Investigation" = 0.526; "VAPT Vulnerabilities" vs "Agent VSM01 Unknown
# State" = 0.571), while re-narrations of one incident ran 0.81-0.98
# ("Process Studio SSO Login 'Address Already In Use: Bind' Error" vs
# 'Process Studio "Address already in use: bind" error with SSO' = 0.950).
#
# 0.85 sits well clear of the highest observed false pair. Note the corpus-
# wide distribution is CONTINUOUS — there is no natural gap to snap to — so
# this threshold is a judgement backed by sampling, and it is deliberately
# on the strict side. It is also never used alone; see below.
SIMILAR_EPISODE_MIN_COSINE = 0.85


async def supersede_similar_episodes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    min_cosine: float = SIMILAR_EPISODE_MIN_COSINE,
    dry_run: bool = False,
) -> dict:
    """Retire episodes that re-tell the same incident from a different slice.

    Containment dedup handles a cluster that GREW: the later account contains
    the earlier one's evidence outright. It cannot touch the other half of
    the problem — repeated reconstructions of the SAME cluster, where the
    extractor splits it differently each time and produces equal-sized,
    overlapping-but-not-nested accounts. On the live corpus the worst ticket
    still held 20 episodes after containment, roughly half of them one
    "Agent Unknown State" incident told from different subsets.

    **Shared evidence is required, and that is not a tuning knob.** At
    cosine >= 0.85 the corpus holds 319 pairs that share evidence and 29 that
    share none. The disjoint ones — "SSO 403 Forbidden Error" against "SSO
    Configuration and Login Failure (HTTP 403 Forbidden)" — are exactly the
    case the correlation layer already rules on: *similar problem, never the
    same occurrence*. An embedding cannot distinguish one incident from its
    recurrence next month; only shared evidence can, so merging without it
    would fuse two real occurrences into one and silently destroy the
    recurrence signal. This pass therefore refuses them, even though several
    are visibly duplicates, because being right about the ones we can prove
    matters more than catching every one.

    Highest-evidence account wins, so the survivor is the fullest telling.
    """
    episodes = (
        await db.execute(
            select(Episode).where(
                Episode.tenant_id == tenant_id,
                Episode.reviewer_state.notin_(("superseded", "rejected")),
                Episode.embedding.isnot(None),
            )
        )
    ).scalars().all()
    by_id = {ep.id: ep for ep in episodes}
    sets = {
        ep.id: {str(e) for e in (ep.evidence_ids or [])}
        for ep in episodes
    }

    # pgvector does the distance work; the evidence-overlap test is cheap in
    # Python and keeps the SQL readable.
    pairs = (
        await db.execute(
            text("""
                select a.id as a_id, b.id as b_id,
                       1 - (a.embedding <=> b.embedding) as cosine
                from episodes a
                join episodes b
                  on a.id < b.id
                 and a.tenant_id = b.tenant_id
                where a.tenant_id = :t
                  and a.reviewer_state not in ('superseded','rejected')
                  and b.reviewer_state not in ('superseded','rejected')
                  and a.embedding is not null and b.embedding is not null
                  and 1 - (a.embedding <=> b.embedding) >= :c
                order by 1 - (a.embedding <=> b.embedding) desc
            """),
            {"t": str(tenant_id), "c": min_cosine},
        )
    ).mappings().all()

    retired: set[uuid.UUID] = set()
    planned: list[dict] = []
    skipped_no_shared_evidence = 0

    for row in pairs:
        a, b = by_id.get(row["a_id"]), by_id.get(row["b_id"])
        if a is None or b is None or a.id in retired or b.id in retired:
            continue
        if not (sets.get(a.id) and sets.get(b.id)):
            continue
        if not (sets[a.id] & sets[b.id]):
            skipped_no_shared_evidence += 1
            continue

        # Keep the fullest account: most evidence, then approved, then
        # confidence. The survivor should be the one a reader learns most from.
        keep, drop = sorted(
            (a, b),
            key=lambda ep: (
                len(sets[ep.id]),
                1 if ep.reviewer_state == "approved" else 0,
                ep.extraction_confidence or 0.0,
            ),
            reverse=True,
        )
        retired.add(drop.id)
        planned.append({
            "kept": keep.title, "retired": drop.title, "cosine": round(float(row["cosine"]), 3)
        })
        if not dry_run:
            await _merge_episode_into(db, drop, keep)

    if not dry_run:
        await db.flush()
        logger.info(
            "episode.similar_superseded",
            tenant_id=str(tenant_id),
            retired=len(retired),
            refused_no_shared_evidence=skipped_no_shared_evidence,
        )

    return {
        "live_before": len(episodes),
        "retired": len(retired),
        "live_after": len(episodes) - len(retired),
        "refused_no_shared_evidence": skipped_no_shared_evidence,
        "examples": planned[:12],
    }
