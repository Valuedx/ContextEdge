import json
import uuid
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.classifiers.relevance import classify_relevance as run_relevance_classifier
from contextedge.ai.embeddings import embed_evidence
from contextedge.config import settings
from contextedge.models.episode import EpisodeStep
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
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
from contextedge.services.redaction_service import redact, redact_evidence_fields
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
    # Hash the pre-redaction payload so two ingests of the same upstream
    # row still dedupe correctly — redaction is non-deterministic
    # across placeholder-format changes and we don't want to break
    # dedup when we tune the regex rules.
    h = evidence_content_hash_from_payload(payload)

    # Redact PII / secrets before anything downstream sees the text.
    # Classifier, embedder, identity / decision extractors, and DB
    # storage all read from ``title`` / ``body`` after this point.
    title, body, redaction_counts = redact_evidence_fields(
        title, body, enabled=settings.redaction_enabled,
    )
    if redaction_counts:
        logger.info(
            "evidence.redacted",
            tenant_id=str(tenant_id),
            raw_object_id=raw_object_id,
            counts=redaction_counts,
        )

    identity_content = "\n".join(
        part for part in [
            title or "",
            body or "",
            json.dumps(payload, default=str)[:2000] if payload else "",
        ]
        if part and part.strip()
    )
    # The raw payload JSON can contain PII fields the title/body
    # extractors missed (e.g. nested custom fields in Jira issues).
    # Re-redact the composed blob before it ships to the identity /
    # decision LLM extractors.
    identity_content, _ = redact(
        identity_content, enabled=settings.redaction_enabled,
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
    attachments = await register_attachment_artifacts(
        db,
        tenant_id=tenant_id,
        evidence=ev,
        payload=payload,
    )

    # Classify BEFORE expensive downstream work. At typical IT inbox noise
    # rates (~60-70% non-operational), this short-circuits the embed +
    # identity + decision extraction path for the majority of items.
    # Irrelevant items still exist as EvidenceItem rows (audit trail) but
    # don't contribute tokens to embeddings or extraction LLM calls.
    classification_label: str | None = None
    classification_confidence: float | None = None
    try:
        cls = await run_relevance_classifier(
            ev.title or "",
            ev.body_text or "",
            "unknown",
            ev.evidence_type,
            tenant_id=tenant_id,
            db=db,
        )
        classification_label = cls.get("classification", "not_relevant")
        classification_confidence = float(cls.get("confidence", 0.0))
        ev.relevance_state = classification_label.replace(" ", "_")
        ev.relevance_score = classification_confidence
        await db.flush()
    except Exception as cls_exc:
        # Classifier failure shouldn't block ingestion — fall through to the
        # full path as the pre-flip behaviour did.
        logger.warning(
            "relevance_classification_failed",
            evidence_id=str(ev.id),
            error=str(cls_exc),
        )

    # Gate: skip expensive fan-out for confidently-irrelevant items.
    # Threshold kept conservative (0.75) so ambiguous items still get the
    # full treatment — false-negative cost (miss a real incident) is much
    # higher than the false-positive cost of extracting on noise.
    skip_extraction = (
        classification_label == "not_relevant"
        and classification_confidence is not None
        and classification_confidence >= 0.75
    )

    identity_count = 0
    decision_count = 0
    embedded = False
    if not skip_extraction and identity_content.strip():
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
        try:
            embedded = await _ensure_embedding(db, ev)
        except Exception as embed_exc:
            logger.warning("embedding_failed", evidence_id=str(ev.id), error=str(embed_exc))
            embedded = False
    elif skip_extraction:
        logger.info(
            "normalize.skipped_extraction_not_relevant",
            evidence_id=str(ev.id),
            confidence=classification_confidence,
        )

    return {
        "evidence_id": str(ev.id),
        "deduped": False,
        "embedded": embedded,
        "identity_count": identity_count,
        "decision_count": decision_count,
        "relevance_state": ev.relevance_state,
        "skipped_extraction": skip_extraction,
        "attachment_ids": [str(artifact.id) for artifact in attachments],
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


async def _reconstruct(db: AsyncSession, cluster_id: str, tenant_id: uuid.UUID, domain_id: uuid.UUID | None = None) -> dict:
    """`cluster_id` is treated as a comma-separated list of evidence UUIDs for MVP wiring."""
    ids = [uuid.UUID(x.strip()) for x in cluster_id.split(",") if x.strip()]
    if len(ids) < 1:
        return {"error": "no_evidence_ids"}

    if domain_id is None:
        # Resolve a default domain for the tenant if not provided
        dr = await db.execute(select(Domain.id).where(Domain.tenant_id == tenant_id).limit(1))
        domain_id = dr.scalar_one_or_none()

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
        domain_id=domain_id,
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
                from contextedge.workers.evidence_baseline_tasks import (
                    compute_evidence_baseline_task,
                )

                # Classification is now done inline in _normalize before
                # the embed/identity/decision fan-out so we can short-circuit
                # expensive work on irrelevant items. classify_relevance_task
                # remains available for manual re-classification from the
                # admin UI but is no longer part of the default fan-out.
                correlate_evidence.delay(res["evidence_id"], tenant_id)
                compute_evidence_baseline_task.delay(res["evidence_id"], tenant_id)
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


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="extraction.reconstruct_episode",
)
def reconstruct_episode_task(self, correlation_cluster_id: str, tenant_id: str, domain_id: str | None = None):
    tid = uuid.UUID(tenant_id)
    did = uuid.UUID(domain_id) if domain_id else None

    async def work(db):
        return await _reconstruct(db, correlation_cluster_id, tid, did)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
