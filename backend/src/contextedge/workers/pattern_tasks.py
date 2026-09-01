import uuid

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from contextedge.ai.extractors.pattern_extractor import (
    synthesize_pattern,
    validate_pattern_match,
)
from contextedge.ai.generators import playbook_generator
from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY
from contextedge.ai.provider import generate_embedding
from contextedge.graph.builder import ensure_edge, link_node_to_identities
from contextedge.models.episode import Episode
from contextedge.models.pattern import NegativeKnowledgeItem, Pattern, PatternEvidenceLink
from contextedge.models.playbook import Playbook
from contextedge.models.tenant import User
from contextedge.services.identity_service import identity_ids_from_refs
from contextedge.services.pattern_service import (
    add_episode_to_pattern,
    create_pattern_from_episodes,
)
from contextedge.services.episode_service import (
    evidence_ids_for_episodes,
    playbook_episode_summaries,
)
from contextedge.services.playbook_service import create_playbook_version
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()

RISK_TIERS = ("low", "medium", "high")

# Minimum pattern confidence before a playbook candidate is generated. See
# the gate in generate_playbook_candidate for how this was calibrated.
PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE = 0.5

# Clustering distance thresholds, calibrated 2026-08-19 against the live
# corpus rather than carried over from defaults. Both are cosine distances,
# and both are only meaningful relative to how THIS corpus is distributed:
# two randomly chosen approved episodes sit at p01 0.257, p10 0.342, median
# 0.409 (min 0.157, max 0.524). Everything is an AutomationEdge support
# incident, so the embeddings bunch and absolute thresholds tuned elsewhere
# do not discriminate here. Re-measure both if the corpus mix changes.

# Prefilter for "could this episode belong to an existing pattern?" — the
# LLM validator makes the actual call. Kept just below the random-pair p10
# so it stays a cheap filter, not the decision. The distance to a
# candidate's NEAREST pattern member has median 0.243 and p75 0.269, so
# 0.30 admits ~93% of episodes to the validator while skipping the tail
# that the validator rejects almost every time.
PATTERN_MATCH_MAX_DISTANCE = 0.30

# How close two unlinked episodes must be to form a NEW cluster. Raised
# from 0.20, which sat below the random-pair p01 and was so strict that
# 126 of 150 probed episodes could group with nothing and became
# single-episode "patterns". Measured singletons / mean cluster size over
# the same 150: 0.20 -> 126 / 2.3, 0.23 -> 105 / 2.9, 0.25 -> 83 / 3.3,
# 0.27 -> 50 / 3.8, 0.30 -> 20 / 6.3, 0.40 -> 0 / 66.2. The 0.40 figure is
# the corpus collapsing into one blob, which is the failure mode on the
# other side. 0.27 is the knee: real groups of ~4, no runaway merge.
CLUSTER_GROUP_MAX_DISTANCE = 0.27

# Deterministic risk floor per step safety class. The LLM's suggested tier
# may only raise risk above this floor, never lower it — risk assessment is
# a policy decision, not a model output.
_SAFETY_CLASS_RISK_FLOOR = {
    "read_only": "low",
    "low_side_effect": "medium",
    "high_side_effect": "high",
    "destructive": "high",
}


def _effective_risk_tier(llm_risk_tier, steps) -> str:
    floor = "low"
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        step_floor = _SAFETY_CLASS_RISK_FLOOR.get(
            str(step.get("safety_class") or "read_only"), "high"
        )
        if RISK_TIERS.index(step_floor) > RISK_TIERS.index(floor):
            floor = step_floor
    suggested = str(llm_risk_tier or "").strip().lower()
    if suggested not in RISK_TIERS:
        # Unknown / missing model suggestion: fall back to the floor, but
        # never below medium — an ungraded generated playbook should not
        # look low-risk by default.
        return floor if RISK_TIERS.index(floor) >= RISK_TIERS.index("medium") else "medium"
    if RISK_TIERS.index(suggested) > RISK_TIERS.index(floor):
        return suggested
    return floor


# Re-export: tests and older callers imported this from the worker.
_evidence_ids_for_episodes = evidence_ids_for_episodes


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


def _domain_predicate(did: uuid.UUID | None):
    """Strict per-domain mining scope. A domain pass sees ONLY that
    domain's episodes; the global pass (did=None) sees ONLY tenant-global
    (NULL-domain) episodes. NULL episodes are deliberately NOT folded
    into domain passes: whichever domain's pass ran first would capture
    them into its pattern (episodes link once), making the tagging
    arbitrary — the exact bug this replaces."""
    return Episode.domain_id == did if did is not None else Episode.domain_id.is_(None)


async def _cluster(db, tid: uuid.UUID, did: uuid.UUID | None) -> dict:
        logger.info(
            "cluster_episodes_started",
            tenant_id=str(tid),
            domain_id=str(did) if did else "global",
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
                # Review F-03: pass tenant_id + db so embedding spend
                # shows up in /admin/cost and the budget gate fires.
                ep.embedding = await generate_embedding(
                    emb_text, tenant_id=tid, db=db,
                )
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

        # 2. Fetch unlinked approved episodes with embeddings IN THIS
        # DOMAIN SCOPE only. Mining used to run tenant-wide and stamp the
        # result with the requested domain — which surfaced domain B's
        # episode content in a domain-A pattern through the projection's
        # domain predicate. Patterns are synthesized CONTENT, so the
        # cluster must be domain-homogeneous (create_pattern_from_episodes
        # enforces the same rule as a second layer).
        r = await db.execute(
            select(Episode)
            .where(
                Episode.tenant_id == tid,
                _domain_predicate(did),
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

            # Does this episode belong to an EXISTING pattern? Take the
            # pattern owning the single NEAREST member.
            #
            # The ORDER BY is the whole point and used to be missing:
            # `LIMIT 1` on an unordered set returns an arbitrary qualifying
            # row. That is not a rare edge case on this corpus — measured
            # 2026-08-19, EVERY unlinked episode has some pattern member
            # within 0.35, because 0.35 is roughly the 10th percentile of
            # the distance between two RANDOM episodes (pairwise spread:
            # min 0.157, p01 0.257, median 0.409, max 0.524 — everything is
            # an AutomationEdge support incident, so the embeddings bunch).
            # So the gate admitted everyone and then handed the validator a
            # near-random pattern, which it correctly rejected: 8 of 65
            # episodes joined, and the other 88% went off to mint singleton
            # patterns. Asking about the nearest pattern instead took the
            # validator's accept rate from 12% to 40% on the same corpus.
            member_distance = Episode.embedding.cosine_distance(ep.embedding)
            match_r = await db.execute(
                select(PatternEvidenceLink.pattern_id)
                .join(Episode, Episode.id == PatternEvidenceLink.episode_id)
                .join(Pattern, Pattern.id == PatternEvidenceLink.pattern_id)
                .where(
                    Pattern.tenant_id == tid,
                    _domain_predicate(did),
                    Episode.embedding.is_not(None),
                    member_distance < PATTERN_MATCH_MAX_DISTANCE,
                )
                .order_by(member_distance.asc())
                .limit(1)
            )
            matched_pattern_id = match_r.scalar_one_or_none()

            if matched_pattern_id:
                try:
                    pat = await db.get(Pattern, matched_pattern_id)
                    ep_info = {
                        "title": ep.title,
                        "root_cause_summary": ep.root_cause_summary,
                        "final_outcome": ep.final_outcome,
                    }
                    pat_info = {
                        "title": pat.title if pat else "",
                        "description": pat.description if pat else "",
                        "root_causes": pat.root_causes if pat else [],
                        "resolution_steps": pat.resolution_steps if pat else [],
                    }
                    # Validate match with AI before associating
                    ai_val = await validate_pattern_match(ep_info, pat_info, tenant_id=tid, db=db)

                    if ai_val.get("is_match"):
                        await add_episode_to_pattern(db, tid, matched_pattern_id, ep.id)
                        assigned_ids.add(ep.id)
                        logger.info(
                            "episode_matched_existing_pattern_ai_validated",
                            episode_id=str(ep.id),
                            pattern_id=str(matched_pattern_id),
                            confidence=ai_val.get("confidence"),
                        )
                        continue
                    else:
                        logger.info(
                            "episode_pattern_match_rejected_by_ai",
                            episode_id=str(ep.id),
                            pattern_id=str(matched_pattern_id),
                            reason=ai_val.get("reason"),
                        )
                except Exception as exc:
                    logger.warning("add_episode_to_pattern_failed", error=str(exc))

            # Find similar episodes to form a NEW cluster with — same domain
            # scope as the candidates: similarity must never pull another
            # domain's episode into this cluster.
            similar_r = await db.execute(
                select(Episode)
                .where(
                    Episode.tenant_id == tid,
                    _domain_predicate(did),
                    Episode.reviewer_state == "approved",
                    Episode.embedding.is_not(None),
                    Episode.id.not_in(tuple(linked)) if linked else True,
                    Episode.id.not_in(tuple(assigned_ids)) if assigned_ids else True,
                    Episode.embedding.cosine_distance(ep.embedding)
                    < CLUSTER_GROUP_MAX_DISTANCE,
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
                syn_title = synthesis.get("title") or f"Synthesized: {cluster[0].title[:50]}"
                syn_title_lower = syn_title.strip().lower()

                # Gate: skip persisting if LLM determined there is no
                # operational incident/pattern
                if any(
                    p in syn_title_lower
                    for p in [
                        "no incident",
                        "no pattern",
                        "no operational pattern",
                        "no recurring pattern",
                    ]
                ):
                    logger.info(
                        "pattern_synthesis_rejected_no_incident",
                        title=syn_title,
                        episodes=[str(e.id) for e in cluster_with_steps],
                    )
                    assigned_ids.update(e.id for e in cluster)
                    continue

                await create_pattern_from_episodes(
                    db,
                    tenant_id=tid,
                    domain_id=did,
                    title=syn_title,
                    episode_ids=[e.id for e in cluster_with_steps],
                    confidence=float(synthesis.get("confidence") or 0.8),
                    description=synthesis.get("description"),
                    trigger_conditions=synthesis.get("trigger_conditions"),
                    core_entities=synthesis.get("core_entities"),
                    observed_errors=synthesis.get("observed_errors"),
                    root_causes=synthesis.get("root_causes"),
                    resolution_steps=synthesis.get("resolution_steps"),
                    evidence_summary=synthesis.get("evidence_summary"),
                    generation_provenance=synthesis.get(GENERATION_PROVENANCE_KEY),
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

        # The dedup sweep is housekeeping riding on the clustering task —
        # it must never fail the clustering itself (and clustering runs
        # on the solo pattern queue, so the sweep is serialised there;
        # the API-triggered sweep in patterns.py can still overlap it,
        # which the merge logic tolerates via its existence checks).
        from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks
        try:
            dedup_stats = await deduplicate_patterns_and_playbooks(db, tid)
        except Exception as dedup_exc:  # noqa: BLE001
            logger.warning("pattern_dedup_sweep_failed", error=str(dedup_exc))
            dedup_stats = {}
        # No explicit commit: run_async owns the commit/rollback contract
        # for every worker task.

        return {
            "status": "success",
            "patterns_created": created,
            "episodes_considered": total_considered,
            "embeddings_repaired": repaired,
            "dedup_merged": dedup_stats,
        }


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="pattern.cluster_episodes",
)
def cluster_episodes(self, domain_id: str | None, tenant_id: str):
    """Group approved episodes into semantic patterns, strictly within one
    domain scope. ``domain_id=None`` runs the global pass over
    tenant-global (NULL-domain) episodes and produces NULL-domain
    patterns."""
    tid = uuid.UUID(tenant_id)
    did = uuid.UUID(domain_id) if domain_id else None
    try:
        return run_async(lambda db: _cluster(db, tid, did))
    except Exception as exc:
        logger.exception(
            "pattern.cluster_failed",
            domain_id=domain_id or "global",
            error=str(exc),
        )
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="pattern.generate_playbook_candidate",
)
def generate_playbook_candidate(self, pattern_id: str, tenant_id: str, force: bool = False):
    """Generate a playbook candidate from a pattern and persist playbook + version.

    When ``force`` is true, confidence and pre-generation blockers are logged but
    do not skip generation — used by corpus refresh to replace every gap pattern.
    """

    async def work(db):
        tid = uuid.UUID(tenant_id)
        pid = uuid.UUID(pattern_id)
        pr = await db.execute(select(Pattern).where(Pattern.id == pid, Pattern.tenant_id == tid))
        pattern = pr.scalar_one_or_none()
        if not pattern:
            return {"status": "skipped", "reason": "pattern_not_found"}

        existing = await db.execute(
            select(Playbook).where(
                Playbook.tenant_id == tid,
                Playbook.lifecycle_state.notin_(("retired", "deprecated")),
                or_(
                    Playbook.pattern_id == pid,
                    func.lower(Playbook.title) == pattern.title.strip().lower(),
                ),
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "skipped", "reason": "playbook_already_exists"}

        # Evidence floor. Reviewing 37 generated playbooks showed the corpus
        # splitting cleanly on pattern confidence: below ~0.5 the generator
        # produced structured-but-hollow procedures ("check logs", "review
        # firewall rules", escalate) for administrative requests and
        # one-off anecdotes — half the corpus was generation the model itself
        # distrusted, and every one of those costs reviewer attention and
        # dilutes trust in the good ones. pattern_type cannot gate this
        # (everything is recurring_issue), so confidence is the signal.
        #
        # Skipping is not a dead end, but it is not automatic either:
        # generation is auto-dispatched only at pattern creation
        # (create_pattern_from_episodes), so a pattern that crosses the floor
        # later needs the manual POST /playbooks/generate route — which
        # bypasses this worker entirely and stays available for exactly that
        # case, and for a human who disagrees with the floor.
        pattern_confidence = float(pattern.confidence or 0.0)
        if pattern_confidence < PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE:
            if not force:
                logger.info(
                    "playbook.generation_skipped_low_confidence",
                    tenant_id=str(tid),
                    pattern_id=str(pid),
                    confidence=pattern_confidence,
                    floor=PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE,
                )
                return {
                    "status": "skipped",
                    "reason": "pattern_confidence_below_floor",
                    "confidence": pattern_confidence,
                }
            logger.warning(
                "playbook.generation_forced_low_confidence",
                tenant_id=str(tid),
                pattern_id=str(pid),
                confidence=pattern_confidence,
                floor=PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE,
            )

        lr = await db.execute(
            select(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pid)
        )
        links = lr.scalars().all()
        ep_ids = [ln.episode_id for ln in links if ln.episode_id]
        if not ep_ids:
            return {"status": "skipped", "reason": "no_episode_links"}
        # Evidence provenance for the generated version: every evidence item
        # the pattern was clustered from. Without these refs a generated
        # playbook has no traceable grounding.
        #
        # Resolved through episode_evidence_links (migration 0037), NOT
        # through PatternEvidenceLink.evidence_id. That column exists but
        # create_pattern_from_episodes never populates it — it writes
        # episode membership only — so this set was silently empty for
        # every auto-generated pattern, which is every pattern the
        # clustering worker produces. The 0037 table is the maintained
        # per-episode grounding and is the correct source of truth.
        evidence_ref_ids = await _evidence_ids_for_episodes(db, tid, ep_ids)

        er = await db.execute(select(Episode).where(Episode.id.in_(ep_ids)))
        episodes = list(er.scalars().all())
        summaries = await playbook_episode_summaries(db, tid, episodes)

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

        # Approved KB/SOP content for this pattern. Retrieved here, after
        # episodes are reconstructed, because the pattern's own vocabulary
        # (root causes, outcomes) is a far better retrieval fingerprint
        # than an incident title — "Laptop Wi-Fi not working" matches
        # nothing useful; "Intel AX201 Code 10 driver rollback" matches
        # the article that documents it.
        from contextedge.services.knowledge_applicability_service import (
            ticket_version_custom_fields,
        )
        from contextedge.services.knowledge_retrieval_service import (
            knowledge_refs_payload,
            persist_knowledge_links,
            retrieve_knowledge_for_pattern,
        )
        from contextedge.services.quality_contract_service import prepare_playbook_generation

        version_fields = await ticket_version_custom_fields(db, tid, evidence_ref_ids)
        retrieval_failed = False
        try:
            knowledge = await retrieve_knowledge_for_pattern(
                db,
                tid,
                pattern_title=pattern.title,
                pattern_description=pattern.description,
                episode_summaries=summaries,
                custom_fields=version_fields or None,
            )
        except Exception:
            retrieval_failed = True
            knowledge = []
            logger.exception(
                "playbook.knowledge_retrieval_failed",
                tenant_id=str(tid),
                pattern_id=str(pid),
            )

        prep = prepare_playbook_generation(
            pattern=pattern,
            episode_summaries=summaries,
            knowledge=knowledge,
            negative_knowledge=neg,
            retrieval_failed=retrieval_failed,
        )
        if prep.should_block:
            if not force:
                logger.info(
                    "playbook.generation_blocked_pregeneration",
                    tenant_id=str(tid),
                    pattern_id=str(pid),
                    outcome=str(prep.gate.outcome),
                    reasons=prep.gate.reasons[:5],
                )
                return {
                    "status": "skipped",
                    "reason": str(prep.gate.outcome),
                    "pregeneration": prep.gate.as_dict(),
                }
            logger.warning(
                "playbook.generation_forced_past_pregeneration",
                tenant_id=str(tid),
                pattern_id=str(pid),
                outcome=str(prep.gate.outcome),
                reasons=prep.gate.reasons[:5],
            )

        knowledge = prep.filtered_knowledge
        links_written = await persist_knowledge_links(
            db, tid, pid, knowledge, domain_id=pattern.domain_id
        )
        logger.info(
            "playbook.knowledge_retrieved",
            tenant_id=str(tid),
            pattern_id=str(pid),
            documents=len(knowledge),
            sections=sum(len(k.sections) for k in knowledge),
            links_written=links_written,
            ticket_version=(version_fields or {}).get("version"),
            pregeneration_outcome=str(prep.gate.outcome),
        )

        llm = await playbook_generator.generate_playbook_candidate(
            pattern_title=pattern.title,
            pattern_description=pattern.description,
            episode_count=len(episodes),
            episode_summaries=summaries,
            negative_knowledge=neg,
            knowledge_sources=knowledge,
            quality_contract_prompt=prep.contract_prompt_block,
            tenant_id=tid,
            db=db,
        )

        ur = await db.execute(select(User).where(User.tenant_id == tid).limit(1))
        owner = ur.scalar_one_or_none()
        if not owner:
            return {"status": "failed", "reason": "no_user_for_owner"}

        steps = llm.get("steps") or []
        if not steps:
            # A playbook with no steps is not a playbook. This was
            # persisted as a candidate and reported {"status": "ok"} —
            # a truncated response had lost the steps array, the repair
            # path salvaged the complete prefix, and title/description/
            # risk_tier all arrived intact, so nothing downstream had
            # any reason to suspect the artifact was empty.
            #
            # Failing here rather than storing it: an empty playbook in
            # the review queue costs a reviewer's time to discover it is
            # worthless, and it looks like the generator's considered
            # opinion rather than a dropped response.
            logger.warning(
                "playbook.no_steps_generated",
                tenant_id=str(tid),
                pattern_id=str(pid),
                returned_keys=sorted(llm.keys()),
            )
            return {"status": "failed", "reason": "no_steps_generated"}

        risk_tier = _effective_risk_tier(llm.get("risk_tier"), steps)

        stable_key = f"pb-{uuid.uuid4().hex[:12]}"
        playbook = Playbook(
            tenant_id=tid,
            domain_id=pattern.domain_id,
            stable_key=stable_key,
            title=str(llm.get("title") or pattern.title)[:500],
            description=(llm.get("description") or pattern.description),
            risk_tier=risk_tier,
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
            "steps": steps,
            "rollback_notes": llm.get("rollback_notes"),
            "evidence_refs": {
                "evidence_ids": evidence_ref_ids,
                "episode_ids": [str(eid) for eid in ep_ids],
                "pattern_id": str(pattern.id),
                "quality_contract": prep.evidence_refs_quality(),
                **knowledge_refs_payload(
                    knowledge,
                    ticket_version=(version_fields or {}).get("version"),
                ),
            },
            "quality_contract_hash": prep.contract_hash,
            "source_snapshot_hash": prep.source_snapshot_hash,
            # Where the documented procedure and observed practice
            # disagree. Persisted rather than resolved: preferring the
            # SOP ignores verified runs that did something else,
            # preferring practice quietly deletes a safeguard. The
            # reviewer decides.
            "conflicts": llm.get("conflicts") or [],
            "playbook_confidence": float(llm.get("playbook_confidence") or 0.5),
            "execution_confidence_guidance": llm.get("execution_confidence_guidance"),
            # F5: version_data is assembled field by field, so the generator's
            # stamp has to be carried across explicitly — it does not ride
            # along the way it does on the API path that forwards the
            # candidate dict whole.
            GENERATION_PROVENANCE_KEY: llm.get(GENERATION_PROVENANCE_KEY),
        }
        version = await create_playbook_version(db, playbook, version_data, origin="generation")
        # Semantic fingerprint so the agent seed resolver can match this
        # playbook by meaning, not just title words. Best-effort.
        from contextedge.services.playbook_embedding import embed_playbook

        await embed_playbook(db, playbook, version)
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
        result: dict[str, object] = {
            "status": "ok",
            "playbook_id": str(playbook.id),
            "stable_key": stable_key,
        }
        if force and prep.should_block:
            result["forced_past_pregeneration"] = str(prep.gate.outcome)
        if force and pattern_confidence < PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE:
            result["forced_low_confidence"] = pattern_confidence
        return result

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("playbook.generate_failed", pattern_id=pattern_id, error=str(exc))
        raise self.retry(exc=exc) from exc


# Fresh evidence per tenant, in the last window, that marks a bulk ingest
# still landing. Hourly dedup during a backfill retires drafts that the very
# next message burst regrows — pure churn — so the sweep steps aside and the
# next hourly tick catches up once the tenant is quiet. 50 rows in ten
# minutes is far above steady-state (a handful per sync tick) and far below
# backfill rates (hundreds), so the guard cannot flap on normal traffic.
DEDUP_ACTIVITY_WINDOW_MINUTES = 10
DEDUP_ACTIVITY_THRESHOLD = 50
# Episodes minted per tenant in the same window. Evidence inflow alone
# missed the RECONSTRUCTION phase: the tail of a bulk ingest creates
# episodes for hours after the last evidence row lands, and the 12:29
# sweep retired 446 drafts mid-tail because it only watched evidence —
# some of those clusters then paid a full re-synthesis. 30/10min is
# several times steady-state (a handful per settle window) and well
# below any tail (40-70 observed).
EPISODE_ACTIVITY_THRESHOLD = 30


async def tenant_pipeline_active(db, tenant_id, window_start) -> tuple[bool, dict]:
    """Is this tenant's pipeline mid-flight? Shared by every sweep that
    must not churn work another stage is still producing (dedup, AI
    review). Active means EITHER fresh evidence is landing (ingest) OR
    episodes are being minted (reconstruction tail)."""
    from contextedge.models.episode import Episode
    from contextedge.models.evidence import EvidenceItem

    recent_evidence = (
        await db.execute(
            select(func.count())
            .select_from(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.ingested_at >= window_start,
            )
        )
    ).scalar() or 0
    recent_episodes = (
        await db.execute(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.tenant_id == tenant_id,
                Episode.created_at >= window_start,
            )
        )
    ).scalar() or 0
    active = (
        recent_evidence > DEDUP_ACTIVITY_THRESHOLD
        or recent_episodes > EPISODE_ACTIVITY_THRESHOLD
    )
    return active, {
        "recent_evidence": recent_evidence,
        "recent_episodes": recent_episodes,
    }


async def _deduplicate_knowledge(db, tenant_id: str) -> dict:
    """Scheduled sweep of the shared dedup entry point.

    Beat passes the literal string ``all`` to sweep every tenant. The
    entry point is the same one the dedup API and the pattern task call
    (`deduplicate_patterns_and_playbooks`), so scheduling adds no second
    dedup mechanism — only a clock for the existing one.
    """
    from datetime import UTC, datetime, timedelta

    from contextedge.models.tenant import Tenant
    from contextedge.services.pattern_service import (
        deduplicate_patterns_and_playbooks,
    )

    if tenant_id == "all":
        r = await db.execute(select(Tenant.id))
        tids = [row[0] for row in r.all()]
    else:
        tids = [uuid.UUID(tenant_id)]

    window_start = datetime.now(UTC) - timedelta(
        minutes=DEDUP_ACTIVITY_WINDOW_MINUTES
    )
    swept: dict[str, dict] = {}
    deferred = 0
    for tid in tids:
        active, activity = await tenant_pipeline_active(db, tid, window_start)
        if active:
            deferred += 1
            logger.info(
                "knowledge_dedup.deferred_ingest_active",
                tenant_id=str(tid),
                **activity,
            )
            continue
        result = await deduplicate_patterns_and_playbooks(db, tid)
        if any(result.values()):
            logger.info("knowledge_dedup.swept", tenant_id=str(tid), **result)
        swept[str(tid)] = result

    return {"tenants": len(tids), "deferred": deferred, "results": swept}


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=600,
    name="pattern.deduplicate_knowledge",
)
def deduplicate_knowledge(self, tenant_id: str):
    """Hourly hygiene sweep; `pattern.*` routes to the pattern queue, so it
    serializes behind clustering and playbook generation on the solo worker
    instead of racing them."""

    async def work(db):
        return await _deduplicate_knowledge(db, tenant_id)

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("knowledge_dedup.failed", error=str(exc))
        raise self.retry(exc=exc) from exc
