import uuid

import structlog
from sqlalchemy import select

from sqlalchemy.orm import selectinload
from contextedge.ai.generators import playbook_generator
from contextedge.ai.extractors.pattern_extractor import synthesize_pattern
from contextedge.ai.provider import generate_embedding
from contextedge.models.episode import Episode
from contextedge.models.pattern import NegativeKnowledgeItem, Pattern, PatternEvidenceLink
from contextedge.models.playbook import Playbook
from contextedge.models.tenant import User
from contextedge.graph.builder import ensure_edge, link_node_to_identities
from contextedge.services.identity_service import identity_ids_from_refs
from contextedge.services.pattern_service import create_pattern_from_episodes
from contextedge.services.playbook_service import create_playbook_version
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


async def _linked_episode_ids(db, tenant_id: uuid.UUID) -> set[uuid.UUID]:
    r = await db.execute(
        select(PatternEvidenceLink.episode_id)
        .join(Pattern, Pattern.id == PatternEvidenceLink.pattern_id)
        .where(
            Pattern.tenant_id == tenant_id,
            PatternEvidenceLink.episode_id.is_not(None),
        )
    )
    return {row[0] for row in r.all() if row[0]}


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="pattern.cluster_episodes",
)
def cluster_episodes(self, domain_id: str, tenant_id: str):
    """Group approved episodes in a domain into semantic patterns using vector similarity."""

    async def work(db):
        tid = uuid.UUID(tenant_id)
        did = uuid.UUID(domain_id)

        logger.info(
            "cluster_episodes_started",
            tenant_id=str(tid),
            domain_id=str(did),
        )

        # 0. Repair: backfill embeddings for ALL approved episodes in the tenant
        # (no domain filter — episodes may have any domain_id or None)
        repair_r = await db.execute(
            select(Episode)
            .where(
                Episode.tenant_id == tid,
                Episode.reviewer_state == "approved",
                Episode.embedding.is_(None),
            )
        )
        episodes_needing_embedding = list(repair_r.scalars().all())
        repaired = 0
        for ep in episodes_needing_embedding:
            emb_text = f"{ep.title}\n\n{ep.root_cause_summary or ''}".strip()
            if not emb_text:
                continue
            try:
                ep.embedding = await generate_embedding(emb_text)
                await db.flush()
                repaired += 1
            except Exception as emb_exc:
                logger.warning(
                    "episode_embedding_repair_failed",
                    episode_id=str(ep.id),
                    error=str(emb_exc),
                )
        if repaired:
            logger.info("episode_embeddings_repaired", count=repaired)

        # 1. Identify which episodes are already linked to a pattern
        linked = await _linked_episode_ids(db, tid)
        logger.info("cluster_episodes_linked", linked_count=len(linked))

        # 2. Fetch ALL unlinked approved episodes with embeddings across the tenant.
        # Domain filter is intentionally removed here — we cluster at tenant level
        # and tag the resulting pattern with the requested domain_id.
        r = await db.execute(
            select(Episode)
            .where(
                Episode.tenant_id == tid,
                Episode.reviewer_state == "approved",
                Episode.embedding.is_not(None),
                Episode.id.not_in(tuple(linked)) if linked else True,
            )
            .order_by(Episode.id)
            .limit(100)
        )
        candidates = list(r.scalars().all())
        logger.info("cluster_episodes_candidates", count=len(candidates))
        
        assigned_ids = set()
        created = 0
        total_considered = len(candidates)

        for ep in candidates:
            if ep.id in assigned_ids:
                continue
            
            # Find similar episodes using vector distance (threshold 0.20)
            similar_r = await db.execute(
                select(Episode)
                .where(
                    Episode.tenant_id == tid,
                    Episode.reviewer_state == "approved",
                    Episode.embedding.is_not(None),
                    Episode.id.not_in(tuple(linked)) if linked else True,
                    Episode.id.not_in(tuple(assigned_ids)) if assigned_ids else True,
                    Episode.embedding.cosine_distance(ep.embedding) < 0.20
                )
            )
            cluster = list(similar_r.scalars().all())

            # Allow single-episode patterns when no similar pair found —
            # better to create a pattern than silently drop a valid approved episode.
            if len(cluster) == 0:
                cluster = [ep]
            
            # Form a pattern using AI synthesis for high-fidelity results
            try:
                # Fetch steps for the cluster episodes to provide context to the LLM
                synth_r = await db.execute(
                    select(Episode)
                    .where(Episode.id.in_([e.id for e in cluster]))
                    .options(selectinload(Episode.steps))
                )
                cluster_with_steps = list(synth_r.scalars().all())
                
                # Prepare data structure for the pattern extractor
                ep_data = [
                    {
                        "title": ep_obj.title,
                        "root_cause_summary": ep_obj.root_cause_summary,
                        "final_outcome": ep_obj.final_outcome,
                        "steps": [{"text": s.text} for s in ep_obj.steps]
                    }
                    for ep_obj in cluster_with_steps
                ]

                # Call AI to synthesize pattern from the cluster
                synthesis = await synthesize_pattern(ep_data, tenant_id=tid, db=db)
                
                await create_pattern_from_episodes(
                    db,
                    tenant_id=tid,
                    domain_id=did,
                    title=synthesis.get("title") or f"Synthesized: {cluster[0].title[:50]}",
                    episode_ids=[e.id for e in cluster_with_steps],
                    confidence=float(synthesis.get("confidence") or 0.8),
                    description=synthesis.get("description"),
                    trigger_conditions=synthesis.get("trigger_conditions"),
                    core_entities=synthesis.get("core_entities"),
                    observed_errors=synthesis.get("observed_errors"),
                    root_causes=synthesis.get("root_causes"),
                    resolution_steps=synthesis.get("resolution_steps"),
                    evidence_summary=synthesis.get("evidence_summary"),
                )
            except Exception as e:
                # Robust fallback to basic pattern creation if AI synthesis fails
                logger.warning("pattern_synthesis_failed_falling_back", error=str(e))
                title_seed = cluster[0].title[:60] if cluster[0].title else "Semantic cluster"
                await create_pattern_from_episodes(
                    db,
                    tenant_id=tid,
                    domain_id=did,
                    title=f"Auto: {title_seed}",
                    episode_ids=[e.id for e in cluster],
                    confidence=0.75,
                )
            created += 1
            assigned_ids.update(e.id for e in cluster)
            
        return {"patterns_created": created, "episodes_considered": total_considered, "embeddings_repaired": repaired}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("pattern.cluster_failed", domain_id=domain_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="pattern.generate_playbook_candidate",
)
def generate_playbook_candidate(self, pattern_id: str, tenant_id: str):
    """Generate a playbook candidate from a pattern and persist playbook + version."""

    async def work(db):
        tid = uuid.UUID(tenant_id)
        pid = uuid.UUID(pattern_id)
        pr = await db.execute(select(Pattern).where(Pattern.id == pid, Pattern.tenant_id == tid))
        pattern = pr.scalar_one_or_none()
        if not pattern:
            return {"status": "skipped", "reason": "pattern_not_found"}

        existing = await db.execute(
            select(Playbook).where(Playbook.tenant_id == tid, Playbook.pattern_id == pid)
        )
        if existing.scalar_one_or_none():
            return {"status": "skipped", "reason": "playbook_already_exists"}

        lr = await db.execute(
            select(PatternEvidenceLink).where(
                PatternEvidenceLink.pattern_id == pid,
                PatternEvidenceLink.episode_id.is_not(None),
            )
        )
        links = lr.scalars().all()
        ep_ids = [ln.episode_id for ln in links if ln.episode_id]
        if not ep_ids:
            return {"status": "skipped", "reason": "no_episode_links"}

        er = await db.execute(select(Episode).where(Episode.id.in_(ep_ids)))
        episodes = list(er.scalars().all())
        summaries = [
            {
                "title": ep.title,
                "root_cause": ep.root_cause_summary,
                "outcome": ep.final_outcome,
            }
            for ep in episodes[:12]
        ]

        nk_r = await db.execute(
            select(NegativeKnowledgeItem).where(
                NegativeKnowledgeItem.tenant_id == tid,
                NegativeKnowledgeItem.domain_id == pattern.domain_id,
            ).limit(20)
        )
        neg = [
            f"{row.step_text} ({row.failure_reason or 'no reason'})"
            for row in nk_r.scalars().all()
        ]

        llm = await playbook_generator.generate_playbook_candidate(
            pattern_title=pattern.title,
            pattern_description=pattern.description,
            episode_count=len(episodes),
            episode_summaries=summaries,
            negative_knowledge=neg,
            tenant_id=tid,
            db=db,
        )

        ur = await db.execute(select(User).where(User.tenant_id == tid).limit(1))
        owner = ur.scalar_one_or_none()
        if not owner:
            return {"status": "failed", "reason": "no_user_for_owner"}

        stable_key = f"pb-{uuid.uuid4().hex[:12]}"
        playbook = Playbook(
            tenant_id=tid,
            domain_id=pattern.domain_id,
            stable_key=stable_key,
            title=str(llm.get("title") or pattern.title)[:500],
            description=(llm.get("description") or pattern.description),
            risk_tier=str(llm.get("risk_tier") or "medium")[:20],
            automation_mode="suggest_only",
            owner_user_id=owner.id,
            pattern_id=pattern.id,
            lifecycle_state="candidate",
        )
        db.add(playbook)
        await db.flush()

        version_data = {
            "semantic_version": "0.1.0",
            "trigger_conditions": llm.get("trigger_conditions") or {},
            "branching_logic": llm.get("branching_logic") or {},
            "inputs": llm.get("inputs") or [],
            "outputs": llm.get("outputs") or [],
            "steps": llm.get("steps") or [],
            "rollback_notes": llm.get("rollback_notes"),
            "playbook_confidence": float(llm.get("playbook_confidence") or 0.5),
            "execution_confidence_guidance": llm.get("execution_confidence_guidance"),
        }
        await create_playbook_version(db, playbook, version_data)
        identity_ids = []
        for episode in episodes:
            identity_ids.extend(identity_ids_from_refs(episode.entity_refs))
        await link_node_to_identities(
            db,
            tid,
            "playbook",
            playbook.id,
            identity_ids,
            edge_type="references_identity",
            domain_id=pattern.domain_id,
        )
        await ensure_edge(
            db, tid,
            "playbook", playbook.id,
            "pattern", pattern.id,
            "derived_from",
            domain_id=pattern.domain_id,
        )
        await db.refresh(playbook)
        return {"status": "ok", "playbook_id": str(playbook.id), "stable_key": stable_key}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("playbook.generate_failed", pattern_id=pattern_id, error=str(exc))
        raise self.retry(exc=exc) from exc
