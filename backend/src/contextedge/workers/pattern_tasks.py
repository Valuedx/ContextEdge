import uuid

import structlog
from sqlalchemy import select

from contextedge.ai.generators import playbook_generator
from contextedge.models.episode import Episode
from contextedge.models.pattern import NegativeKnowledgeItem, Pattern, PatternEvidenceLink
from contextedge.models.playbook import Playbook
from contextedge.models.tenant import User
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


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def cluster_episodes(self, domain_id: str, tenant_id: str):
    """Group approved episodes in a domain into coarse patterns (MVP batching)."""

    async def work(db):
        tid = uuid.UUID(tenant_id)
        did = uuid.UUID(domain_id)
        linked = await _linked_episode_ids(db, tid)
        r = await db.execute(
            select(Episode)
            .where(
                Episode.tenant_id == tid,
                Episode.domain_id == did,
                Episode.reviewer_state == "approved",
            )
            .order_by(Episode.id)
            .limit(80)
        )
        episodes = [e for e in r.scalars().all() if e.id not in linked]
        created = 0
        for i in range(0, len(episodes), 4):
            chunk = episodes[i : i + 4]
            if len(chunk) < 2:
                continue
            title_seed = chunk[0].title[:60] if chunk[0].title else "Episode cluster"
            await create_pattern_from_episodes(
                db,
                tenant_id=tid,
                domain_id=did,
                title=f"Auto: {title_seed}",
                episode_ids=[e.id for e in chunk],
                confidence=0.55,
            )
            created += 1
        return {"patterns_created": created, "episodes_considered": len(episodes)}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("pattern.cluster_failed", domain_id=domain_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
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
        await create_playbook_version(db, playbook, version_data, owner.id)
        await db.refresh(playbook)
        return {"status": "ok", "playbook_id": str(playbook.id), "stable_key": stable_key}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("playbook.generate_failed", pattern_id=pattern_id, error=str(exc))
        raise self.retry(exc=exc) from exc
