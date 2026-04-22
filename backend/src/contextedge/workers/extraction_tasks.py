import json
import uuid
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.classifiers.relevance import classify_relevance as run_relevance_classifier
from contextedge.ai.embeddings import embed_evidence
from contextedge.models.episode import EpisodeStep
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject, Thread
from contextedge.models.tenant import Domain
from contextedge.services.artifact_extraction_service import (
    load_raw_payload,
    register_attachment_artifacts,
)
from contextedge.services.evidence_normalization import (
    ensure_thread_for_evidence,
    evidence_body_from_payload,
    evidence_content_hash_from_payload,
    evidence_title_from_payload,
)
from contextedge.services.decision_service import link_evidence_decisions
from contextedge.services.identity_service import link_evidence_identities
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app
from contextedge.workers.correlation_tasks import correlate_evidence

logger = structlog.get_logger()


async def _ensure_embedding(db: AsyncSession, evidence: EvidenceItem) -> bool:
    if evidence.embedding is not None:
        return False
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
    await db.flush()
    return True


async def _normalize(db: AsyncSession, raw_object_id: str, tenant_id: uuid.UUID) -> dict:
    rid = uuid.UUID(raw_object_id)
    raw = await db.get(RawEvidenceObject, rid)
    if not raw or raw.tenant_id != tenant_id:
        return {"error": "raw_not_found"}

    try:
        payload = await load_raw_payload(raw)
    except ValueError:
        return {"error": "raw_payload_offloaded_without_storage_key"}

    title = evidence_title_from_payload(payload)
    body = evidence_body_from_payload(payload)
    h = evidence_content_hash_from_payload(payload)
    identity_content = "\n".join(
        part for part in [
            title or "",
            body or "",
            json.dumps(payload, default=str)[:2000] if payload else "",
        ]
        if part and part.strip()
    )

    source_ts = None
    if payload.get("_source_timestamp"):
        try:
            source_ts = datetime.fromisoformat(payload["_source_timestamp"])
        except (ValueError, TypeError):
            pass

    existing = (
        await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.content_hash == h,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.created_at_source is None and source_ts:
            existing.created_at_source = source_ts
        if existing.thread_id is None:
            await ensure_thread_for_evidence(
                db, tenant_id=tenant_id, evidence=existing, payload=payload,
            )
        try:
            embedded = await _ensure_embedding(db, existing)
        except Exception as embed_exc:
            logger.warning("embedding_failed", evidence_id=str(existing.id), error=str(embed_exc))
            embedded = False
        identity_count = None
        if not ((existing.canonical_entity_refs or {}).get("identities")) and identity_content.strip():
            try:
                refs = await link_evidence_identities(
                    db,
                    tenant_id=tenant_id,
                    evidence=existing,
                    content=identity_content,
                    source_id=raw.source_id,
                    source_metadata={"raw_object_id": str(raw.id)},
                )
                identity_count = len(refs)
            except Exception as exc:
                logger.warning(
                    "identity_resolution_failed",
                    tenant_id=str(tenant_id),
                    raw_object_id=str(raw.id),
                    evidence_id=str(existing.id),
                    error=str(exc),
                )
        decision_count = None
        if not ((existing.canonical_entity_refs or {}).get("decisions")) and identity_content.strip():
            try:
                decision_refs = await link_evidence_decisions(
                    db,
                    tenant_id=tenant_id,
                    evidence=existing,
                    content=identity_content,
                    source_id=raw.source_id,
                )
                decision_count = len(decision_refs)
            except Exception as exc:
                logger.warning(
                    "decision_extraction_failed",
                    tenant_id=str(tenant_id),
                    raw_object_id=str(raw.id),
                    evidence_id=str(existing.id),
                    error=str(exc),
                )
        attachments = await register_attachment_artifacts(
            db,
            tenant_id=tenant_id,
            evidence=existing,
            payload=payload,
        )
        return {
            "evidence_id": str(existing.id),
            "deduped": True,
            "embedded": existing.embedding is not None,
            "embedding_repaired": embedded,
            "identity_count": identity_count,
            "decision_count": decision_count,
            "attachment_ids": [str(artifact.id) for artifact in attachments],
        }

    ev = EvidenceItem(
        tenant_id=tenant_id,
        source_id=raw.source_id,
        source_object_id=raw.source_object_id,
        raw_object_ref=raw.id,
        evidence_type=payload.get("evidence_type", "message"),
        title=title[:500],
        body_text=body,
        content_hash=h,
        relevance_state="unclassified",
        created_at_source=source_ts,
    )
    db.add(ev)
    await db.flush()
    await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=ev, payload=payload,
    )
    identity_count = 0
    if identity_content.strip():
        try:
            refs = await link_evidence_identities(
                db,
                tenant_id=tenant_id,
                evidence=ev,
                content=identity_content,
                source_id=raw.source_id,
                source_metadata={"raw_object_id": str(raw.id)},
            )
            identity_count = len(refs)
        except Exception as exc:
            logger.warning(
                "identity_resolution_failed",
                tenant_id=str(tenant_id),
                raw_object_id=str(raw.id),
                evidence_id=str(ev.id),
                error=str(exc),
            )
    decision_count = 0
    if identity_content.strip():
        try:
            decision_refs = await link_evidence_decisions(
                db,
                tenant_id=tenant_id,
                evidence=ev,
                content=identity_content,
                source_id=raw.source_id,
            )
            decision_count = len(decision_refs)
        except Exception as exc:
            logger.warning(
                "decision_extraction_failed",
                tenant_id=str(tenant_id),
                raw_object_id=str(raw.id),
                evidence_id=str(ev.id),
                error=str(exc),
            )
    attachments = await register_attachment_artifacts(
        db,
        tenant_id=tenant_id,
        evidence=ev,
        payload=payload,
    )
    try:
        embedded = await _ensure_embedding(db, ev)
    except Exception as embed_exc:
        logger.warning("embedding_failed", evidence_id=str(ev.id), error=str(embed_exc))
        embedded = False
    return {
        "evidence_id": str(ev.id),
        "deduped": False,
        "embedded": embedded,
        "identity_count": identity_count,
        "decision_count": decision_count,
        "attachment_ids": [str(artifact.id) for artifact in attachments],
    }


async def _classify(db: AsyncSession, evidence_id: str, tenant_id: uuid.UUID) -> dict:
    eid = uuid.UUID(evidence_id)
    ev = await db.get(EvidenceItem, eid)
    if not ev or ev.tenant_id != tenant_id:
        return {"error": "evidence_not_found"}

    # If already classified by another process, return early
    if ev.relevance_state != "unclassified":
        return {"evidence_id": evidence_id, "classification": ev.relevance_state}

    # Thread-level optimization: check if the thread is already classified
    if ev.thread_id:
        thread = await db.get(Thread, ev.thread_id)
        if thread and thread.relevance_state != "unclassified":
            ev.relevance_state = thread.relevance_state
            ev.relevance_score = 1.0  # Inherited
            # Inherit metadata if available
            if not ev.canonical_entity_refs:
                ev.canonical_entity_refs = {}
            ev.canonical_entity_refs["classification_meta"] = {
                "inherited": True,
                "thread_id": str(thread.id)
            }
            await db.flush()
            return {"evidence_id": evidence_id, "classification": ev.relevance_state, "cached": True}

    # Perform LLM classification
    out = await run_relevance_classifier(
        ev.title or "",
        ev.body_text or "",
        "unknown",
        ev.evidence_type,
    )
    
    label = out.get("classification", "NOT_RELEVANT")
    # Map to internal states
    if label == "OPERATIONAL_INCIDENT":
        state = "operational"
    elif label == "POSSIBLY_RELEVANT":
        state = "possibly_relevant"
    else:
        state = "discarded"
    
    ev.relevance_state = state
    ev.relevance_score = float(out.get("confidence", 0.0))
    
    # Store extended metadata
    metadata = {
        "issue_type": out.get("issue_type"),
        "affected_system": out.get("affected_system"),
        "user_impact": out.get("user_impact"),
        "urgency_level": out.get("urgency_level"),
        "stage2_confidence": out.get("confidence")
    }
    
    if not ev.canonical_entity_refs:
        ev.canonical_entity_refs = {}
    ev.canonical_entity_refs["classification_meta"] = metadata
    
    # Also update the parent thread if it exists
    if ev.thread_id:
        thread = await db.get(Thread, ev.thread_id)
        if thread:
            thread.relevance_state = state
            
    await db.flush()
    return {"evidence_id": evidence_id, "classification": ev.relevance_state}


async def _reconstruct(
    db: AsyncSession,
    cluster_id: str,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    target_episode_id: uuid.UUID | None = None,
) -> dict:
    """`cluster_id` is treated as a comma-separated list of evidence UUIDs for MVP wiring."""
    ids = [uuid.UUID(x.strip()) for x in cluster_id.split(",") if x.strip()]
    if len(ids) < 1:
        return {"error": "no_evidence_ids"}

    if domain_id is None:
        # Resolve a default domain for the tenant if not provided
        dr = await db.execute(select(Domain.id).where(Domain.tenant_id == tenant_id).limit(1))
        domain_id = dr.scalar_one_or_none()

    # --- Step 1: Load all seed evidence items and collect their thread_ids ---
    seen_ids: set[uuid.UUID] = set()
    thread_ids: set[uuid.UUID] = set()
    seed_items: list[EvidenceItem] = []

    for eid in ids:
        ev = await db.get(EvidenceItem, eid)
        if ev and ev.tenant_id == tenant_id:
            seen_ids.add(ev.id)
            seed_items.append(ev)
            if ev.thread_id:
                thread_ids.add(ev.thread_id)

    if not seed_items:
        return {"error": "no_evidence_found"}

    # --- Step 2: Expand cluster by fetching ALL siblings from the same threads ---
    # This ensures every email in the trail is included, not just the correlated subset.
    if thread_ids:
        thread_res = await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.thread_id.in_(thread_ids),
                EvidenceItem.id.not_in(seen_ids),
            )
        )
        for sibling in thread_res.scalars().all():
            seed_items.append(sibling)
            seen_ids.add(sibling.id)

    # --- Step 2.1: Secondary Fallback Expansion (by Subject/Title) ---
    # If we have very few items and thread_id expansion didn't yield much, try matching by title.
    if len(seed_items) < 3:
        from contextedge.services.episode_service import _normalize_title
        
        # Collect normalized titles of current items
        norm_titles = set()
        for ev in seed_items:
            if ev.title:
                norm_titles.add(_normalize_title(ev.title))
        
        if norm_titles:
            # We look for evidence items with similar normalized titles in the last 7 days
            from datetime import timedelta, UTC
            since = datetime.now(UTC) - timedelta(days=7)
            
            # This is a broad search but limited by tenant and time
            # We filter in Python for exact normalized title match to be safe
            potential_res = await db.execute(
                select(EvidenceItem).where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.id.not_in(seen_ids),
                    EvidenceItem.ingested_at >= since
                )
            )
            for pot in potential_res.scalars().all():
                if pot.title and _normalize_title(pot.title) in norm_titles:
                    seed_items.append(pot)
                    seen_ids.add(pot.id)

    # --- Step 3: Sort by timestamp ascending so the LLM sees events in order ---
    from datetime import UTC
    min_dt = datetime.min.replace(tzinfo=UTC)
    seed_items.sort(key=lambda ev: (ev.created_at_source or ev.ingested_at or min_dt))

    items = [
        {
            "title": ev.title,
            "body": ev.body_text,
            "source_type": "evidence",
            "timestamp": str(ev.created_at_source or ev.ingested_at),
            "evidence_id": str(ev.id),
            "thread_id": str(ev.thread_id) if ev.thread_id else None,
        }
        for ev in seed_items
    ]

    logger.info(
        "reconstruct.expanded_cluster",
        seed_count=len(ids),
        total_count=len(items),
        thread_count=len(thread_ids),
        fallback_active=len(seed_items) > len(ids) and not thread_ids
    )

    from contextedge.services.episode_service import create_episodes_from_evidence

    created_episodes = await create_episodes_from_evidence(
        db,
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_items=items,
        evidence_ids=list(seen_ids),
        target_episode_id=target_episode_id,
    )
    await db.flush()

    total_steps = 0
    for episode in created_episodes:
        res = await db.execute(select(EpisodeStep).where(EpisodeStep.episode_id == episode.id))
        total_steps += len(res.scalars().all())

    return {
        "episode_ids": [str(ep.id) for ep in created_episodes],
        "count": len(created_episodes),
        "total_steps": total_steps,
        "evidence_count": len(items),
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.normalize_evidence",
)
def normalize_evidence(self, raw_object_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _normalize(db, raw_object_id, tid)

    try:
        res = run_async(work)
        if res and "evidence_id" in res:
            attachment_ids = [artifact_id for artifact_id in (res.get("attachment_ids") or []) if artifact_id]
            if attachment_ids:
                from contextedge.workers.artifact_tasks import extract_attachment_artifact

                for artifact_id in attachment_ids:
                    extract_attachment_artifact.delay(artifact_id, tenant_id)
            else:
                # Chain classification. Correlation and baseline only run if relevance is operational
                classify_relevance_task.delay(res["evidence_id"], tenant_id)
        return res
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="extraction.classify_relevance",
)
def classify_relevance_task(self, evidence_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _classify(db, evidence_id, tid)

    try:
        res = run_async(work)
        if res and res.get("classification") in ["operational", "possibly_relevant"]:
            from contextedge.workers.correlation_tasks import correlate_evidence
            from contextedge.workers.evidence_baseline_tasks import (
                compute_evidence_baseline_task,
            )

            correlate_evidence.delay(evidence_id, tenant_id)
            compute_evidence_baseline_task.delay(evidence_id, tenant_id)
        return res
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.reconstruct_episode",
)
def reconstruct_episode_task(self, correlation_cluster_id: str, tenant_id: str, domain_id: str | None = None, target_episode_id: str | None = None):
    tid = uuid.UUID(tenant_id)
    did = uuid.UUID(domain_id) if domain_id else None
    teid = uuid.UUID(target_episode_id) if target_episode_id else None

    async def work(db):
        return await _reconstruct(db, correlation_cluster_id, tid, did, teid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
