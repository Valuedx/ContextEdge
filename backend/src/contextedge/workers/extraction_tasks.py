import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.classifiers.relevance import classify_relevance as run_relevance_classifier
from contextedge.ai.embeddings import embed_evidence
from contextedge.models.episode import EpisodeStep
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.services.evidence_normalization import (
    evidence_body_from_payload,
    evidence_content_hash_from_payload,
    evidence_title_from_payload,
)
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app


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

    payload = raw.raw_payload or {}
    title = evidence_title_from_payload(payload)
    body = evidence_body_from_payload(payload)
    h = evidence_content_hash_from_payload(payload)

    existing = (
        await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.content_hash == h,
            )
        )
    ).scalar_one_or_none()
    if existing:
        embedded = await _ensure_embedding(db, existing)
        return {
            "evidence_id": str(existing.id),
            "deduped": True,
            "embedded": existing.embedding is not None,
            "embedding_repaired": embedded,
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
    )
    db.add(ev)
    await db.flush()
    embedded = await _ensure_embedding(db, ev)
    return {
        "evidence_id": str(ev.id),
        "deduped": False,
        "embedded": embedded,
    }


async def _classify(db: AsyncSession, evidence_id: str, tenant_id: uuid.UUID) -> dict:
    eid = uuid.UUID(evidence_id)
    ev = await db.get(EvidenceItem, eid)
    if not ev or ev.tenant_id != tenant_id:
        return {"error": "evidence_not_found"}

    out = await run_relevance_classifier(
        ev.title or "",
        ev.body_text or "",
        "unknown",
        ev.evidence_type,
    )
    label = out.get("classification", "not_relevant")
    ev.relevance_state = label.replace(" ", "_")
    ev.relevance_score = float(out.get("confidence", 0.0))
    await db.flush()
    return {"evidence_id": evidence_id, "classification": ev.relevance_state}


async def _embed(db: AsyncSession, evidence_id: str, tenant_id: uuid.UUID) -> dict:
    eid = uuid.UUID(evidence_id)
    ev = await db.get(EvidenceItem, eid)
    if not ev or ev.tenant_id != tenant_id:
        return {"error": "evidence_not_found"}

    vec = await embed_evidence(ev.title, ev.body_text)
    ev.embedding = vec
    await db.flush()
    return {"evidence_id": evidence_id, "dimensions": len(vec)}


async def _reconstruct(db: AsyncSession, cluster_id: str, tenant_id: uuid.UUID) -> dict:
    """`cluster_id` is treated as a comma-separated list of evidence UUIDs for MVP wiring."""
    ids = [uuid.UUID(x.strip()) for x in cluster_id.split(",") if x.strip()]
    if len(ids) < 1:
        return {"error": "no_evidence_ids"}

    items = []
    for eid in ids:
        ev = await db.get(EvidenceItem, eid)
        if ev and ev.tenant_id == tenant_id:
            items.append({
                "title": ev.title,
                "body": ev.body_text,
                "source_type": "evidence",
                "timestamp": str(ev.created_at_source or ev.ingested_at),
                "evidence_id": str(ev.id),
            })

    if not items:
        return {"error": "no_evidence_found"}

    from contextedge.services.episode_service import create_episodes_from_evidence

    created_episodes = await create_episodes_from_evidence(
        db,
        tenant_id=tenant_id,
        domain_id=None,
        evidence_items=items,
        evidence_ids=[uuid.UUID(i["evidence_id"]) for i in items],
    )
    await db.flush()
    
    total_steps = 0
    for episode in created_episodes:
        res = await db.execute(select(EpisodeStep).where(EpisodeStep.episode_id == episode.id))
        total_steps += len(res.scalars().all())

    return {
        "episode_ids": [str(ep.id) for ep in created_episodes],
        "count": len(created_episodes),
        "total_steps": total_steps
    }


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def normalize_evidence(self, raw_object_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _normalize(db, raw_object_id, tid)

    try:
        res = run_async(work)
        if res and "evidence_id" in res:
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
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def generate_embeddings(self, evidence_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _embed(db, evidence_id, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.reconstruct_episode",
)
def reconstruct_episode_task(self, correlation_cluster_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)

    async def work(db):
        return await _reconstruct(db, correlation_cluster_id, tid)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
