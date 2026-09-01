import json
import uuid
from datetime import datetime

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.classifiers.message_function import classify_message_function
from contextedge.ai.classifiers.relevance import classify_relevance as run_relevance_classifier
from contextedge.ai.embeddings import embed_evidence
from contextedge.config import settings
from contextedge.models.episode import Episode, EpisodeStep
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.source import Source
from contextedge.models.tenant import Domain
from contextedge.services.artifact_extraction_service import (
    load_raw_payload,
    register_attachment_artifacts,
)
from contextedge.services.case_state import derive_case_state
from contextedge.services.decision_service import link_evidence_decisions
from contextedge.services.evidence_chunk_service import write_chunks
from contextedge.services.evidence_normalization import (
    ensure_thread_for_evidence,
    evidence_body_from_payload,
    evidence_content_hash_from_payload,
    evidence_title_from_payload,
    sync_related_ticket_facets,
)
from contextedge.services.evidence_typing import (
    KNOWLEDGE_EVIDENCE_TYPES,
    derive_evidence_type,
)
from contextedge.services.identity_service import link_evidence_identities
from contextedge.services.knowledge_lifecycle import derive_knowledge_state
from contextedge.services.message_filter import (
    MESSAGE_FILTER_VERSION,
    is_hydrated_message,
    message_noise_reason,
)
from contextedge.services.redaction_service import redact, redact_evidence_fields
from contextedge.services.source_facets import applicability_from_facets, derive_all_facets
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app
from contextedge.workers.chunk_tasks import chunk_evidence_task, embed_chunks_batch_task
from contextedge.workers.correlation_tasks import correlate_evidence

logger = structlog.get_logger()


# Bodies under this threshold (post-redaction, UTF-8 bytes) are chunked
# inline inside ``_normalize`` for predictable card-render latency.
# Bodies above it dispatch to ``chunk_evidence_task`` so the critical
# normalize path stays bounded for big attachments / long threads.
# Tunable; revisit once production p50/p95 body sizes are measured.
INLINE_CHUNK_BUDGET_BYTES = 16 * 1024

# Source types whose chunkers are ready for inline dispatch. Bodies
# from sources outside this set always go async so a slow / unfamiliar
# parser cannot stall ingest. Add a key here once the corresponding
# chunker has been load-tested at typical body sizes.
INLINE_CHUNK_SOURCE_ALLOWLIST = frozenset(
    {"jira_sm", "servicenow", "gmail", "teams", "sapphireims", "zoho_desk"}
)


async def _ensure_embedding(db: AsyncSession, evidence: EvidenceItem) -> bool:
    if evidence.embedding is not None:
        return False
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
    await db.flush()
    return True


async def _dispatch_chunking(
    db: AsyncSession,
    *,
    raw: RawEvidenceObject,
    ev: EvidenceItem,
    payload: dict,
    tenant_id: uuid.UUID,
) -> None:
    """Chunk the evidence inline or hand it off to the async task.

    Side-effect-only — caller wraps in try/except. Stamps
    ``ev.source_type`` from the parent ``Source`` row when missing so
    the chunker registry has a connector key to dispatch on (the
    column was added by migration ``0029_ae_ops_concept_alignment``
    but no other code path stamps it yet).

    Inline path embeds chunks via the batch task (one Celery message
    per evidence). Async path defers chunking entirely so big
    attachments don't block the normalize transaction.
    """
    if not ev.source_type:
        src = await db.get(Source, raw.source_id)
        if src is not None:
            ev.source_type = src.source_type
            await db.flush()

    body_size = len((ev.body_text or "").encode("utf-8"))
    inline_eligible = (
        body_size < INLINE_CHUNK_BUDGET_BYTES
        and (ev.source_type or "") in INLINE_CHUNK_SOURCE_ALLOWLIST
    )

    if inline_eligible:
        chunks = await write_chunks(
            db,
            tenant_id=tenant_id,
            evidence=ev,
            payload=payload,
            source_type=ev.source_type,
        )
        if chunks:
            embed_chunks_batch_task.delay(
                [str(c.id) for c in chunks],
                str(tenant_id),
            )
    else:
        chunk_evidence_task.delay(str(ev.id), str(tenant_id))


async def _normalize(db: AsyncSession, raw_object_id: str, tenant_id: uuid.UUID) -> dict:
    rid = uuid.UUID(raw_object_id)
    raw = await db.get(RawEvidenceObject, rid)
    if not raw or raw.tenant_id != tenant_id:
        return {"error": "raw_not_found"}

    try:
        payload = await load_raw_payload(raw)
    except ValueError:
        return {"error": "raw_payload_offloaded_without_storage_key"}

    # Thread messages are the volume problem: hydration turns 1,515 tickets
    # into ~18,900 message rows, and each one that continues past this point
    # costs a relevance classification at minimum. Measured on the live
    # corpus, 47% of them are coordination — "Hi Team, Any update?", meeting
    # invitations, signature-only replies, delivery failures — and paying a
    # model to discover that is ~4M tokens spent rejecting chatter.
    #
    # Dropped before any model call and before an evidence row exists. The
    # raw object is untouched and the message remains part of its hydrated
    # thread; only its promotion to standalone evidence is refused.
    # The filter version travels with every rejection. A dropped message
    # leaves no evidence row, so without it there is no way to tell a
    # message nothing ever looked at from one an older rule rejected —
    # and no way to know what a rule change should re-examine.
    if is_hydrated_message(payload):
        noise = message_noise_reason(payload)
        if noise is not None:
            logger.info(
                "normalize.skipped_noise_message",
                raw_object_id=raw_object_id,
                reason=noise,
                filter_version=MESSAGE_FILTER_VERSION,
            )
            return {
                "status": "skipped_noise_message",
                "reason": noise,
                "filter_version": MESSAGE_FILTER_VERSION,
            }

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
    closed_str = payload.get("closedTime") or payload.get("closed_time")
    if closed_str:
        try:
            source_ts = datetime.fromisoformat(str(closed_str).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    elif payload.get("_source_timestamp"):
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
        # Refreshed on every re-ingest, unlike the fields below it. The
        # content hash covers the BODY, and retiring an article does not
        # rewrite its body — so a state change is precisely the case that
        # lands here rather than creating a new row. Only ever set it when
        # the source says something: a payload that has stopped carrying the
        # field must not silently republish a retired article.
        # A ticket's status is the field most likely to change without its
        # body changing — closing one rarely rewrites the description — so it
        # is refreshed here for the same reason knowledge_state is.
        # Facets are filled in as the ticket is worked — the root cause is
        # typically typed when it is resolved, long after first ingest, and
        # without touching the description the content hash covers.
        refreshed_facets = derive_all_facets(
            payload, (getattr(await db.get(Source, raw.source_id), "config", None) or {}).get(
                "facet_fields"
            ) if raw.source_id else None
        )
        quality_stale_needed = False
        if refreshed_facets and refreshed_facets != (existing.source_facets or {}):
            existing.source_facets = {**(existing.source_facets or {}), **refreshed_facets}
            quality_stale_needed = True
        refreshed_case = derive_case_state(payload)
        if refreshed_case is not None and refreshed_case != existing.case_state:
            logger.info(
                "evidence.case_state_changed",
                evidence_id=str(existing.id),
                was=existing.case_state,
                now=refreshed_case,
            )
            existing.case_state = refreshed_case
        refreshed_state = derive_knowledge_state(payload)
        if refreshed_state is not None and refreshed_state != existing.knowledge_state:
            logger.info(
                "evidence.knowledge_state_changed",
                evidence_id=str(existing.id),
                was=existing.knowledge_state,
                now=refreshed_state,
            )
            existing.knowledge_state = refreshed_state
            quality_stale_needed = True
        if quality_stale_needed:
            try:
                from contextedge.services.quality_staleness_hooks import (
                    signal_stale_for_evidence,
                )

                await signal_stale_for_evidence(
                    db,
                    tenant_id,
                    [existing.id],
                    origin="evidence_reingest",
                )
            except Exception as stale_exc:  # noqa: BLE001
                logger.warning(
                    "quality_stale_signal_failed",
                    evidence_id=str(existing.id),
                    error=str(stale_exc)[:200],
                )
        if existing.created_at_source is None and source_ts:
            existing.created_at_source = source_ts
        if existing.thread_id is None:
            await ensure_thread_for_evidence(
                db, tenant_id=tenant_id, evidence=existing, payload=payload,
            )
        await sync_related_ticket_facets(db, tenant_id, existing)
        try:
            embedded = await _ensure_embedding(db, existing)
        except Exception as embed_exc:
            logger.warning("embedding_failed", evidence_id=str(existing.id), error=str(embed_exc))
            embedded = False
        identity_count = None
        has_identities = (existing.canonical_entity_refs or {}).get("identities")
        if not has_identities and identity_content.strip():
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
        has_decisions = (existing.canonical_entity_refs or {}).get("decisions")
        if not has_decisions and identity_content.strip():
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

    # Scope is copied from the SOURCE the evidence came through, at ingest.
    # New evidence used to land with workspace_id/domain_id both NULL — and
    # the graph layer treats a NULL domain as eligible under *every*
    # domain-scoped query, because NULL is the deliberate encoding for
    # reviewed tenant-global knowledge. Unassigned ingest riding that
    # convention meant a domain-limited agent could see evidence nobody had
    # scoped yet. The source's workspace always applies; its domain applies
    # when unambiguous (exactly one configured). A multi-domain source's
    # evidence stays domain-NULL — that case genuinely is tenant-wide until
    # a human or the correlation layer narrows it.
    # getattr, not attribute access: normalization must degrade to unscoped
    # evidence on any unexpected source shape, never crash the ingest.
    src = await db.get(Source, raw.source_id) if raw.source_id else None
    source_domain_ids = list(getattr(src, "domain_ids", None) or [])
    facets = derive_all_facets(payload, (getattr(src, "config", None) or {}).get("facet_fields"))
    ev = EvidenceItem(
        tenant_id=tenant_id,
        source_id=raw.source_id,
        source_object_id=raw.source_object_id,
        raw_object_ref=raw.id,
        workspace_id=getattr(src, "workspace_id", None),
        domain_id=(
            uuid.UUID(str(source_domain_ids[0]))
            if len(source_domain_ids) == 1
            else None
        ),
        # Derived from what the connector actually fetched, not read off
        # the payload with a "message" default — no connector but
        # zoho_desk ever set the field, so a ServiceNow KB article and a
        # Teams line were indistinguishable downstream. See
        # services/evidence_typing.py for why this is central.
        evidence_type=derive_evidence_type(payload),
        # What the source system says about this article's currency. NULL for
        # every source without a knowledge lifecycle, which serves normally.
        knowledge_state=derive_knowledge_state(payload),
        # Resolved / cancelled, from the source's own status field.
        case_state=derive_case_state(payload),
        # Whatever the source already states about cause, environment and
        # version — recorded rather than re-inferred.
        source_facets=facets,
        title=title[:500],
        body_text=body,
        content_hash=h,
        relevance_state="unclassified",
        created_at_source=source_ts,
    )
    db.add(ev)
    try:
        await db.flush()
    except IntegrityError:
        # Review L-02: a concurrent normalize worker raced us on the
        # same (tenant_id, content_hash). Migration 0026 added the
        # unique index that made this an error instead of a silent
        # duplicate row. Roll back the attempted insert, re-fetch the
        # winning row, and return a minimal "raced to dedup" result.
        # The winning worker already did the enrichment (identity /
        # decision / embedding) — re-running it here would be wasted
        # LLM spend. Downstream post-processing (correlate / baseline)
        # still fires via the task wrapper.
        await db.rollback()
        winner = (
            await db.execute(
                select(EvidenceItem).where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.content_hash == h,
                )
            )
        ).scalar_one()
        logger.info(
            "normalize.dedup_race_resolved",
            tenant_id=str(tenant_id),
            raw_object_id=raw_object_id,
            evidence_id=str(winner.id),
        )
        return {
            "evidence_id": str(winner.id),
            "deduped": True,
            "raced": True,
            "embedded": winner.embedding is not None,
            "identity_count": None,
            "decision_count": None,
            "attachment_ids": [],
        }
    await ensure_thread_for_evidence(
        db, tenant_id=tenant_id, evidence=ev, payload=payload,
    )
    await sync_related_ticket_facets(db, tenant_id, ev)
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
        # Operational summary from the same call (relevance prompt v2,
        # roadmap A2) — the hydrator projects body_summary, which until
        # now only attachment extraction ever populated. Never overwrite
        # an existing summary (attachment extraction owns its value).
        if cls.get("summary") and not ev.body_summary:
            ev.body_summary = cls["summary"]
        await db.flush()
        # Claims from the same call (v3, roadmap A4) — land unverified,
        # invisible to the projection until validated. Fail-soft.
        if cls.get("claims"):
            try:
                from contextedge.services.claim_service import (
                    persist_extracted_claims,
                )

                await persist_extracted_claims(db, tenant_id, ev, cls["claims"])
            except Exception as claim_exc:
                logger.warning(
                    "claim_persistence_failed",
                    evidence_id=str(ev.id),
                    error=str(claim_exc),
                )
        # Applicability, on the INGEST path. This used to run only from the
        # manual `classify_relevance` task, so an article that arrived
        # through a normal sync — which is every article — never got one:
        # 7 of 133 on the live corpus carried applicability, and those 7
        # were re-classified by hand. The feature that makes knowledge
        # retrieval version- and environment-aware was effectively dead for
        # ingested content, and silently, because it degrades to lexical
        # matching rather than failing.
        #
        # Belongs here rather than in the caller: it is per-evidence
        # follow-up on the same object the classifier just read, it is
        # skipped for non-knowledge types, and it never raises.
        await _extract_applicability(db, ev, tenant_id, payload)
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

    # Message function (A1): what a conversational message is DOING —
    # feeds the dissociation veto, correction supersession, and the
    # negative-evidence store. Conversational sources only, and only
    # for items that passed the relevance gate (noise never earns a
    # second LLM call). Fail-soft: an unlabeled message just means the
    # downstream consumers use their deterministic floors.
    if not skip_extraction and (ev.source_type or "") in MESSAGE_FUNCTION_SOURCE_TYPES:
        try:
            mf = await classify_message_function(
                ev.title or "",
                ev.body_text or "",
                ev.source_type or "unknown",
                tenant_id=tenant_id,
                db=db,
                evidence_id=ev.id,
            )
            ev.message_function = mf["function"]
            ev.message_function_confidence = mf["confidence"]
            await db.flush()
        except Exception as mf_exc:
            logger.warning(
                "message_function_classification_failed",
                evidence_id=str(ev.id),
                error=str(mf_exc),
            )

    # Error-signature fingerprints (diagnosis roadmap D1). Deterministic
    # regex normalization — no LLM — so it runs on every item, including
    # ones the relevance gate skips: a confidently-irrelevant thread can
    # still carry a pasted stack trace worth indexing.
    try:
        from contextedge.services.error_signature_service import fingerprint_evidence

        fp_counts = await fingerprint_evidence(db, tenant_id, ev)
        if fp_counts["signatures"]:
            logger.info(
                "error_signature.fingerprinted",
                evidence_id=str(ev.id),
                **fp_counts,
            )
    except Exception as fp_exc:
        logger.warning(
            "error_signature_fingerprint_failed",
            evidence_id=str(ev.id),
            error=str(fp_exc),
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

        # Chunking runs *after* the parent embedding so a chunker bug
        # cannot regress today's retrieval. The whole block is wrapped
        # in try/except for the same reason — chunk quality is a
        # Phase-4 concern, the critical-path ingest must not depend on
        # it.
        try:
            await _dispatch_chunking(db, raw=raw, ev=ev, payload=payload, tenant_id=tenant_id)
        except Exception as chunk_exc:
            logger.warning(
                "chunking_failed",
                evidence_id=str(ev.id),
                error=str(chunk_exc),
            )
    elif skip_extraction:
        logger.info(
            "normalize.skipped_extraction_not_relevant",
            evidence_id=str(ev.id),
            confidence=classification_confidence,
        )

    # Thread metadata for auto-hydration dispatch by the celery wrapper.
    # Only populated for evidence types that carry a _thread_id (tickets,
    # email), so conversational sources get their threads hydrated
    # automatically rather than waiting for a manual "Hydrate thread" click.
    #
    # Only the PARENT record may ask for hydration. Hydration stamps
    # `_thread_id` onto every message it writes, so keying the dispatch on
    # "payload carries a thread id" makes each hydrated message re-hydrate
    # the thread it just came from: 341 rows carry one across 34 threads on
    # the current graph, a 10x amplification, and 50x on the largest ticket.
    #
    # It terminates — the re-fetch finds no new raw objects, so nothing
    # recurses further — but every redundant pass still costs a /threads
    # list call, up to THREAD_FETCH_LIMIT detail calls and a /comments call
    # against an API that answers throttling with EMPTY RESULTS rather than
    # an error. That is the failure mode that stored 11 of 20 tickets as
    # empty while reporting success.
    # Same predicate the noise gate at the top of this function uses — one
    # definition, in `message_filter`, rather than a local copy that can
    # drift from it (and that used to shadow the import by name).
    thread_ext_id = (
        None if is_hydrated_message(payload) else (payload or {}).get("_thread_id")
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
        "_thread_external_id": thread_ext_id,
        "_source_id": str(raw.source_id),
        # Carried so the caller can skip dispatching hydration for evidence
        # that has no conversation to hydrate. See the auto-hydration block.
        "_evidence_type": ev.evidence_type,
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
        # Review F-02: manual re-classification must also land in
        # /admin/cost and respect the tenant budget gate; mirror the
        # inline call at L206.
        tenant_id=tenant_id,
        db=db,
    )
    label = out.get("classification", "not_relevant")
    ev.relevance_state = label.replace(" ", "_")
    ev.relevance_score = float(out.get("confidence", 0.0))
    # Same summary persistence as the inline normalize call — manual
    # re-classification is how stale items get their summary refreshed.
    if out.get("summary") and not ev.body_summary:
        ev.body_summary = out["summary"]
    if out.get("claims"):
        try:
            from contextedge.services.claim_service import persist_extracted_claims

            await persist_extracted_claims(db, tenant_id, ev, out["claims"])
        except Exception as claim_exc:
            logger.warning(
                "claim_persistence_failed",
                evidence_id=str(ev.id),
                error=str(claim_exc),
            )

    payload: dict = {}
    if ev.raw_object_ref:
        raw = await db.get(RawEvidenceObject, ev.raw_object_ref)
        if raw is not None:
            try:
                payload = await load_raw_payload(raw) or {}
            except ValueError:
                payload = {}

    await _extract_applicability(db, ev, tenant_id, payload)

    await db.flush()
    return {
        "evidence_id": evidence_id,
        "classification": ev.relevance_state,
        # An item that classified relevant but was never chunked was
        # skipped by an earlier (v1, head-truncated) verdict — the caller
        # dispatches the retrieval fan-out it missed. Post-commit only:
        # the wrapper dispatches, never this function, so a worker can't
        # read pre-commit state.
        "needs_fanout": (
            ev.relevance_state in ("operational", "possibly_relevant")
            and getattr(ev, "chunked_at", None) is None
        ),
    }


def _merge_kb_platform_version(ev: EvidenceItem) -> None:
    """Stamp the AE platform version from title/Affected Version onto the
    article so playbook retrieval can rank and label version-wise from
    stored facets. Fetch itself stays embedding-based.
    """
    from contextedge.services.knowledge_applicability_service import (
        PLATFORM_KEY,
        extract_platform_versions,
    )

    platform = extract_platform_versions(ev.title, ev.body_text)
    if not platform:
        return
    payload = dict(ev.applicability or {})
    products = dict(payload.get("product_versions") or {})
    for key, version in platform.items():
        products.setdefault(key, version)
    payload["product_versions"] = products
    ev.applicability = payload
    version = platform.get(PLATFORM_KEY) or next(iter(platform.values()))
    facets = dict(ev.source_facets or {})
    if version and not facets.get("version"):
        facets["version"] = version
        ev.source_facets = facets


async def _extract_applicability(
    db: AsyncSession, ev: EvidenceItem, tenant_id: uuid.UUID, payload: dict | None = None
) -> None:
    """Read and persist where a knowledge article applies.

    Ingest time is the only place this is affordable: one call per
    article for the life of the article, rather than one per candidate
    article per playbook generation. Knowledge evidence only — a ticket
    does not have an applicability, it has an environment.

    Never raises. Applicability is an enhancement to ranking, and an
    article that fails extraction must still be ingested, chunked and
    retrievable; retrieval falls back to the lexical extractor for it.
    """
    from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES
    from contextedge.services.knowledge_applicability_service import (
        extract_applicability_llm,
    )

    if ev.evidence_type not in KNOWLEDGE_EVIDENCE_TYPES:
        return

    # Official catalog pages list many releases on purpose. Running the
    # article extractor on them would invent a single product_version
    # (often the docs-portal latest) and then retrieval would mismatch
    # every ticket that is not on that line.
    payload = payload or {}
    if payload.get("catalog_key") or payload.get("_connector_object_type") == "official_catalog":
        if not ev.applicability:
            ev.applicability = {"extracted_by": "catalog", "product_versions": {}}
        facets = dict(ev.source_facets or {})
        if payload.get("catalog_key") and not facets.get("catalog_key"):
            facets["catalog_key"] = payload["catalog_key"]
            ev.source_facets = facets
        return

    if not ev.applicability:
        stated = applicability_from_facets(getattr(ev, "source_facets", None))
        if stated:
            ev.applicability = stated
            logger.info(
                "applicability.from_source_facets",
                evidence_id=str(ev.id),
                facets=sorted(stated),
            )
        else:
            try:
                facets = await extract_applicability_llm(
                    ev.title, ev.body_text, tenant_id=tenant_id, db=db
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "applicability.extract_failed",
                    evidence_id=str(ev.id),
                    error_type=type(exc).__name__,
                )
                facets = None
            if facets is not None:
                ev.applicability = facets.to_payload()

    _merge_kb_platform_version(ev)


# Reconstruction debounce: in a busy incident channel every message
# grows the cluster; synthesizing per message would burn one LLM call
# each and churn drafts. Dispatch is delayed by this window, and the
# task re-checks settlement at run time — only the task that fires
# after the cluster has been QUIET for the window proceeds; earlier
# ones no-op on SQL alone. The starvation guard bounds the wait: a
# never-quiet channel still gets its first synthesis within
# MAX_SYNTHESIS_DELAY of the cluster's oldest evidence — a long live
# incident is exactly when episodes matter most.
RECONSTRUCT_DEBOUNCE_SECONDS = 180

# Smallest cluster the AUTOMATIC path will narrate. 3, not 2: the live
# backfill showed message-seeded pairs are overwhelmingly sub-cluster
# fragments of a fuller ticket story (58% of a day's drafts, near-all
# retired by containment dedup). A genuine 2-evidence incident still gets
# told — when its cluster stops growing at 2 it eventually merges nowhere,
# and a reviewer or the per-ticket manual path (settle=False) narrates it
# deliberately; what this floor removes is paying per-fragment while the
# full cluster is still assembling.
MIN_AUTO_SYNTHESIS_CLUSTER = 3

# How much bigger a cluster must be than the account already written for it
# before re-telling is worth a model call.
#
# Set at 50% because the failure was arithmetic, not marginal: a thread
# delivers its messages one at a time, so without a floor every single
# message re-narrates the whole incident. Ten messages arriving on a
# ten-evidence cluster meant ten full syntheses at ~12,700 tokens each, of
# which dedup then retired nine.
#
# A ratio rather than a count, because "one more message" means something
# different to a 3-evidence cluster than to a 30-evidence one: the first
# genuinely changes the story, the second barely moves it.
#
# This governs only the AUTOMATIC path. A reviewer asking for
# reconstruction (`settle=False`) always gets a fresh account — an explicit
# request is not a duplicate.
MIN_RESYNTHESIS_GROWTH = 0.5


async def _largest_covered_episode(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> tuple[int, uuid.UUID] | None:
    """The biggest live episode whose evidence this cluster already covers.

    Containment, not overlap: an episode built from evidence the cluster does
    NOT contain is about different material, and re-telling this cluster does
    not supersede it. Returns (size, id) or None.
    """
    if not evidence_ids:
        return None

    # Containment is asked of `evidence_ids` directly, NOT via
    # `episode_evidence_links`. The link table is not reliably populated —
    # 1,489 of 2,111 live episodes carry evidence_ids with no link rows at
    # all — so a join against it silently matched nothing and this returned
    # None every time, leaving the growth gate dead. `evidence_ids` is the
    # authoritative membership; `<@` ("is contained by") is exactly the
    # question, and Postgres answers it without loading every episode.
    ids = json.dumps([str(e) for e in evidence_ids])
    # Raw `<@` rather than the ORM's `contained_by`: `evidence_ids` is
    # already jsonb, and the ORM expression built a cast chain that silently
    # matched nothing — including an episode against its own evidence set,
    # which must always match. Written as SQL so the operator is the one
    # that was verified against the database.
    # Fail OPEN on anything unexpected: this helper exists to save money,
    # and the worst failure mode is the inverse — an error here suppressing
    # synthesis and silently stopping the graph from forming. A miss costs
    # one redundant synthesis that dedup then retires.
    try:
        row = (
            await db.execute(
                text("""
                    select id, jsonb_array_length(evidence_ids) as n
                    from episodes
                    where tenant_id = :t
                      and reviewer_state = 'pending_review'
                      and jsonb_array_length(evidence_ids) > 0
                      and evidence_ids <@ cast(:ids as jsonb)
                    order by n desc
                    limit 1
                """),
                {"t": str(tenant_id), "ids": ids},
            )
        ).first()
        if row is None:
            return None
        return (int(row[1]), row[0])
    except (TypeError, ValueError, IndexError) as exc:
        logger.warning(
            "episode_reconstruct.growth_gate_check_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )
        return None
MAX_SYNTHESIS_DELAY_SECONDS = 1_800

# Sources whose messages get a message-function classification during
# normalize (A1): the same set the ticket bridge treats as
# conversational — where "what is this message doing" carries linking
# semantics (dissociation, correction).
from contextedge.services.ticket_bridge_service import (  # noqa: E402
    CONVERSATIONAL_SOURCE_TYPES as MESSAGE_FUNCTION_SOURCE_TYPES,
)

# source_type → the role synthesis should treat it as. The prompt's
# field-authority rules (episode v3) key off these labels. A tenant can
# override the role per SOURCE via Source.config["synthesis_role"] —
# e.g. a Teams channel that receives alert webhooks is really a
# "monitoring" feed, and an ops mailbox may be a ticket intake. Unknown
# override values are ignored (fall back to the type default) so a
# config typo degrades to today's behavior instead of poisoning
# authority resolution.
SOURCE_ROLE_MAP = {
    "servicenow": "ticket",
    "jira_sm": "ticket",
    "sapphireims": "ticket",
    "zoho_desk": "ticket",
    "teams": "working_discussion",
    "gmail": "external_communication",
    "local_file": "document",
}
SYNTHESIS_ROLES = (
    "ticket",
    "working_discussion",
    "external_communication",
    "monitoring",
    "document",
)

# evidence_type → role, checked before the source-type default. A single
# source emits more than one kind of record: ServiceNow serves incidents
# and the KB from the same connector, and a Zoho Desk source produces
# both tickets and articles. A knowledge article carries *document*
# authority, not ticket authority — without this, a general "how the VPN
# works" page outranks the actual incident record on incident-specific
# fields during synthesis.
EVIDENCE_TYPE_ROLE_MAP = {
    "kb_article": "document",
    "sop": "document",
    "documentation": "document",
    "alert": "monitoring",
    # Explicit so a ticket from a source with no SOURCE_ROLE_MAP entry
    # still resolves to ticket rather than the generic "evidence".
    "ticket": "ticket",
}


def resolve_synthesis_role(
    source_type: str,
    source_config: dict | None,
    evidence_type: str | None = None,
) -> str:
    override = (source_config or {}).get("synthesis_role")
    if isinstance(override, str) and override in SYNTHESIS_ROLES:
        return override
    if evidence_type and evidence_type in EVIDENCE_TYPE_ROLE_MAP:
        return EVIDENCE_TYPE_ROLE_MAP[evidence_type]
    return SOURCE_ROLE_MAP.get(source_type, "evidence")


async def _reconcile_reply_inheritance(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cluster_evidence_ids: list[uuid.UUID],
    source_types: dict[uuid.UUID, str],
    loaded_evidence: dict[uuid.UUID, "EvidenceItem"],
) -> dict:
    """A10: replies that correlated before their parent gained a case
    membership never retried inheritance. Reconstruction is the natural
    retry point — it fires after a burst settles, and the cluster
    already names every message in the conversation. For each teams
    member with no active non-mentioned membership, re-attempt
    inheritance through the shared ``inherit_reply_membership`` (which
    carries all the shipped guards: single-case parent, dissociation
    veto, thread negation). Fail-soft: reconciliation must never break
    reconstruction."""
    counts = {"attempted": 0, "inherited": 0}
    teams_ids = [
        eid for eid in cluster_evidence_ids if source_types.get(eid) == "teams"
    ]
    if not teams_ids:
        return counts
    try:
        from contextedge.models.case_bridge import EvidenceCaseMembership
        from contextedge.services.ticket_bridge_service import (
            inherit_reply_membership,
        )

        anchored = set(
            (
                await db.execute(
                    select(EvidenceCaseMembership.evidence_id).where(
                        EvidenceCaseMembership.tenant_id == tenant_id,
                        EvidenceCaseMembership.evidence_id.in_(tuple(teams_ids)),
                        EvidenceCaseMembership.status == "active",
                        EvidenceCaseMembership.relationship_type != "mentioned_only",
                    )
                )
            )
            .scalars()
            .all()
        )
        unanchored = [eid for eid in teams_ids if eid not in anchored]
        if not unanchored:
            return counts

        # reply_to_id straight from the stored payload in SQL — loading
        # full payloads (possibly offloaded to object storage) for a
        # reconciliation sweep would be disproportionate. Offloaded rows
        # have a NULL inline payload and are simply skipped.
        reply_rows = (
            await db.execute(
                select(
                    EvidenceItem.id,
                    RawEvidenceObject.raw_payload["reply_to_id"].astext,
                    RawEvidenceObject.raw_payload["is_bot"].astext,
                )
                .join(
                    RawEvidenceObject,
                    EvidenceItem.raw_object_ref == RawEvidenceObject.id,
                )
                .where(
                    EvidenceItem.tenant_id == tenant_id,
                    RawEvidenceObject.tenant_id == tenant_id,
                    EvidenceItem.id.in_(tuple(unanchored)),
                )
            )
        ).all()
        for evidence_id, reply_to, is_bot in reply_rows:
            ev = loaded_evidence.get(evidence_id)
            if ev is None or not reply_to:
                continue
            counts["attempted"] += 1
            result = await inherit_reply_membership(
                db,
                tenant_id,
                ev,
                {"reply_to_id": reply_to, "is_bot": is_bot == "true"},
            )
            counts["inherited"] += result.get("inherited", 0)
        if counts["inherited"]:
            logger.info(
                "reply_reconciliation.applied",
                tenant_id=str(tenant_id),
                **counts,
            )
    except Exception as exc:
        logger.warning(
            "reply_reconciliation_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
    return counts


async def _cluster_has_observational_evidence(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_ids: list[uuid.UUID]
) -> bool:
    """Does this cluster contain anything that actually happened?

    Fail-OPEN on error or on an unclassifiable cluster: the cost of
    wrongly allowing synthesis is one reviewable draft, and the cost of
    wrongly blocking it is a real incident that silently never becomes an
    episode. Only a cluster positively identified as knowledge-only is
    refused.
    """
    from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES

    if not evidence_ids:
        return True
    try:
        rows = (
            await db.execute(
                select(EvidenceItem.evidence_type)
                .where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.id.in_(tuple(evidence_ids)),
                )
                .distinct()
            )
        ).scalars().all()
        # Only real type strings count as an answer. Anything else — an
        # empty result, a row whose type is NULL, a stand-in session in a
        # test — means we did not learn what this cluster is made of, and
        # "did not learn" must read as allow, not as knowledge-only.
        kinds = [row for row in rows if isinstance(row, str)]
    except Exception:  # noqa: BLE001 — see fail-open note above
        return True
    if not kinds:
        return True
    return any(kind not in KNOWLEDGE_EVIDENCE_TYPES for kind in kinds)


async def _reconstruct(
    db: AsyncSession,
    cluster_id: str,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    settle: bool = True,
) -> dict:
    """``cluster_id`` carries seed evidence UUIDs (comma-separated for
    caller compatibility). The FULL connected cluster — case links +
    correlation edges, visibility- and time-fenced — is resolved before
    reconstruction; the seeds are only the entry point."""
    seed_ids = [uuid.UUID(x.strip()) for x in cluster_id.split(",") if x.strip()]
    if len(seed_ids) < 1:
        return {"error": "no_evidence_ids"}

    from contextedge.services.episode_cluster_service import resolve_episode_cluster

    cluster = await resolve_episode_cluster(db, tenant_id, seed_ids)
    if not cluster.evidence_ids:
        return {"error": "no_evidence_found"}

    if settle and len(cluster.evidence_ids) < MIN_AUTO_SYNTHESIS_CLUSTER:
        # Automatic synthesis needs a story worth telling. Singletons have
        # no timeline at all, and PAIRS proved nearly as bad on the live
        # Zoho backfill: message-seeded dispatch resolves whatever
        # sub-cluster the seed's sparse correlations reach, and 2,450 of
        # one day's 4,189 drafts (58%) were 1-2 evidence fragments that
        # containment dedup then retired against fuller accounts — paid
        # narration of material another synthesis already covered.
        # Deferred, not dropped: the evidence and its correlations persist,
        # and when the fragment's cluster merges into the case's fuller
        # cluster, that dispatch tells the whole story once. Manual
        # triggers (settle=False) still reconstruct anything deliberately.
        return {
            "status": "skipped_below_min_cluster",
            "cluster_size": len(cluster.evidence_ids),
        }

    if settle and settings.episode_resolution_gate == "cluster":
        # Resolution gate: a cluster with no solution signal ANYWHERE
        # defers synthesis instead of paying for it. Deferred, not
        # dropped — evidence, embeddings, and case links all persist, so
        # when a resolution-bearing item joins the cluster (possibly
        # from a different source days later), the next dispatch passes.
        # Fail-open: a gate error must never block synthesis.
        try:
            from contextedge.services.resolution_signal_service import (
                cluster_has_resolution_signal,
            )

            if not await cluster_has_resolution_signal(
                db, tenant_id, list(cluster.evidence_ids)
            ):
                logger.info(
                    "episode_reconstruct.deferred_unresolved",
                    tenant_id=str(tenant_id),
                    cluster_size=len(cluster.evidence_ids),
                )
                return {"status": "deferred_unresolved"}
        except Exception as gate_exc:  # noqa: BLE001
            logger.warning(
                "resolution_gate_failed_open", error=str(gate_exc)
            )

    # Per-cluster advisory lock, same pattern as acquire_sync_lock: the
    # threads pool runs reconstructs concurrently, and the fingerprint
    # dedup below this point is read-then-act — 8 concurrent tasks for
    # one cluster raced past it and minted 8 identical episodes
    # (measured live: 8 duplicates in 46 seconds). Losers skip without
    # spending an LLM call; the winner's draft idempotency then works.
    from sqlalchemy import text as _text

    lock_key = f"episode_reconstruct:{tenant_id}:{cluster.fingerprint}"
    got_lock = (
        await db.execute(
            _text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": lock_key},
        )
    ).scalar()
    if not got_lock:
        logger.info(
            "episode_reconstruct.skipped_locked",
            tenant_id=str(tenant_id),
            cluster_size=len(cluster.evidence_ids),
        )
        return {"status": "skipped_locked"}

    if settle:
        # Debounce settlement check: if anything in this cluster was
        # ingested within the window, a later-scheduled task (from that
        # newer evidence) will handle synthesis — this one steps aside
        # without spending an LLM call. Manual reviewer triggers pass
        # settle=False to bypass.
        from datetime import UTC, datetime, timedelta

        bounds = (
            await db.execute(
                select(
                    func.min(EvidenceItem.ingested_at),
                    func.max(EvidenceItem.ingested_at),
                ).where(EvidenceItem.id.in_(tuple(cluster.evidence_ids)))
            )
        ).first()
        oldest_ingested, newest_ingested = (bounds or (None, None))
        if newest_ingested is not None:
            now = datetime.now(UTC)
            if newest_ingested.tzinfo is None:
                newest_ingested = newest_ingested.replace(tzinfo=UTC)
            if oldest_ingested is not None and oldest_ingested.tzinfo is None:
                oldest_ingested = oldest_ingested.replace(tzinfo=UTC)
            unsettled = now - newest_ingested < timedelta(
                seconds=RECONSTRUCT_DEBOUNCE_SECONDS
            )
            overdue = oldest_ingested is not None and (
                now - oldest_ingested
                >= timedelta(seconds=MAX_SYNTHESIS_DELAY_SECONDS)
            )
            if unsettled and not overdue:
                return {
                    "status": "deferred_unsettled",
                    "cluster_fingerprint": cluster.fingerprint,
                    "cluster_size": len(cluster.evidence_ids),
                }

    # Draft idempotency: the same cluster re-processed must not create a
    # duplicate draft. Reviewers see one evolving draft, not four
    # near-duplicates as sources trickle in.
    existing_draft = (
        await db.execute(
            select(Episode.id).where(
                Episode.tenant_id == tenant_id,
                Episode.cluster_fingerprint == cluster.fingerprint,
                Episode.reviewer_state == "pending_review",
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_draft is not None:
        return {
            "status": "duplicate_cluster",
            "cluster_fingerprint": cluster.fingerprint,
            "episode_ids": [str(existing_draft)],
        }

    # An operational episode is an account of something that HAPPENED, so it
    # needs at least one observational source. A cluster made only of
    # knowledge — KB articles, SOPs — describes what a document says works,
    # and narrating it as an episode converts "this article claims X
    # resolves it" into "an engineer did X and it worked". That invents an
    # observation, and everything downstream then treats it as one: the
    # playbook prompt tells the model episode outcomes are empirical
    # evidence a step works, patterns count them as recurrence, and the
    # agent cites them as [ep-N].
    #
    # Found live after a knowledge backfill took the corpus from 53 articles
    # to 629: 299 episodes had all-knowledge evidence, 8 of them from before
    # the backfill, so this predates it and was simply too rare to notice.
    #
    # Knowledge still correlates, still embeds, still reaches the graph and
    # still seeds patterns — this gates episode SYNTHESIS only, not
    # participation. An all-knowledge cluster's structured content belongs
    # in a knowledge case, which is a separate object.
    #
    # Placed here, immediately before the synthesis this protects, rather
    # than at the top: every cheaper exit above — too small, unsettled, no
    # resolution signal, locked, duplicate fingerprint — short-circuits
    # first, so this query is only paid by a cluster that would otherwise
    # go on to spend an LLM call.
    if not await _cluster_has_observational_evidence(
        db, tenant_id, cluster.evidence_ids
    ):
        logger.info(
            "episode.skipped_knowledge_only_cluster",
            tenant_id=str(tenant_id),
            cluster_size=len(cluster.evidence_ids),
        )
        return {
            "status": "skipped_knowledge_only_cluster",
            "cluster_size": len(cluster.evidence_ids),
        }

    # The same guard, for the case the fingerprint cannot see.
    #
    # The check above asks "has this EXACT membership been synthesized", and
    # a fingerprint is derived from membership — so one more thread message
    # yields a new fingerprint, the check misses, and a full synthesis runs.
    # That is how a single ticket accumulated 44 accounts of one incident:
    # the idempotency guard was defeated by exactly the thing it exists to
    # prevent. Measured on the live corpus, re-running 207 messages produced
    # 111 episodes of which 112 were retired by dedup minutes later — about
    # 1.4M tokens, nearly the entire cost of the run, spent writing accounts
    # that were immediately superseded.
    #
    # So: if a live episode already covers this material, only re-tell it
    # when the cluster has actually grown enough to say something new.
    # Dedup still runs behind this as the safety net, but it should not be
    # the mechanism — cleaning up afterwards recovers the graph, never the
    # money.
    if settle:
        prior = await _largest_covered_episode(db, tenant_id, cluster.evidence_ids)
        if prior is not None:
            prior_size, prior_id = prior
            if len(cluster.evidence_ids) < prior_size * (1 + MIN_RESYNTHESIS_GROWTH):
                logger.info(
                    "episode_reconstruct.skipped_insufficient_growth",
                    tenant_id=str(tenant_id),
                    cluster_size=len(cluster.evidence_ids),
                    prior_size=prior_size,
                    episode_id=str(prior_id),
                )
                return {
                    "status": "skipped_insufficient_growth",
                    "cluster_fingerprint": cluster.fingerprint,
                    "cluster_size": len(cluster.evidence_ids),
                    "prior_size": prior_size,
                    "episode_ids": [str(prior_id)],
                }

    if domain_id is None:
        # Resolve a default domain for the tenant if not provided
        dr = await db.execute(select(Domain.id).where(Domain.tenant_id == tenant_id).limit(1))
        domain_id = dr.scalar_one_or_none()

    # Real source types + roles: join each evidence to its Source. The
    # flattened "source_type": "evidence" this replaces made a ticket, a
    # chat, and a transcript indistinguishable to synthesis.
    source_types: dict[uuid.UUID, str] = {}
    source_roles: dict[uuid.UUID, str] = {}
    rows = (
        await db.execute(
            select(
                EvidenceItem.id,
                Source.source_type,
                Source.config,
                EvidenceItem.evidence_type,
            )
            .join(Source, EvidenceItem.source_id == Source.id)
            .where(EvidenceItem.id.in_(tuple(cluster.evidence_ids)))
        )
    ).all()
    for row in rows:
        source_types[row[0]] = row[1] or "unknown"
        source_roles[row[0]] = resolve_synthesis_role(
            row[1] or "unknown",
            row[2] if isinstance(row[2], dict) else None,
            row[3],
        )

    items = []
    loaded_evidence: dict[uuid.UUID, EvidenceItem] = {}
    for eid in cluster.evidence_ids:
        ev = await db.get(EvidenceItem, eid)
        # Cluster membership is tenant-verified upstream; this is belt-
        # and-braces against a resolver regression.
        if ev is None or ev.tenant_id != tenant_id:
            continue
        loaded_evidence[ev.id] = ev
        source_type = source_types.get(ev.id, "unknown")
        items.append({
            "title": ev.title,
            "body": ev.body_text,
            "source_type": source_type,
            "source_role": source_roles.get(ev.id, "evidence"),
            "timestamp": str(ev.created_at_source or ev.ingested_at),
            "evidence_id": str(ev.id),
        })
    items.sort(key=lambda item: item["timestamp"])

    if not items:
        return {"error": "no_evidence_found"}

    reply_reconciliation = await _reconcile_reply_inheritance(
        db, tenant_id, cluster.evidence_ids, source_types, loaded_evidence
    )

    # Supersede-on-growth: a pending draft built from a SUBSET of this
    # cluster is an older view of the same incident. Mark it superseded
    # (invisible to the agent surface, which requires "approved") so the
    # reviewer sees one current draft.
    superseded = 0
    pending = (
        await db.execute(
            select(Episode).where(
                Episode.tenant_id == tenant_id,
                Episode.reviewer_state == "pending_review",
                Episode.cluster_fingerprint.is_not(None),
            ).limit(50)
        )
    ).scalars().all()
    cluster_id_strings = {str(eid) for eid in cluster.evidence_ids}
    for draft in pending:
        draft_ids = set(draft.evidence_ids or [])
        if draft_ids and draft_ids < cluster_id_strings:
            draft.reviewer_state = "superseded"
            superseded += 1
            # Draft lineage: reviewers and audits can follow one evolving
            # draft chain instead of guessing why an episode vanished.
            from contextedge.services.event_log_service import (
                append_operational_event,
            )

            await append_operational_event(
                db,
                tenant_id=tenant_id,
                entity_type="episode",
                entity_id=draft.id,
                event_type="episode.draft_superseded",
                payload={
                    "old_cluster_fingerprint": draft.cluster_fingerprint,
                    "new_cluster_fingerprint": cluster.fingerprint,
                    "new_cluster_size": len(cluster.evidence_ids),
                },
            )

    from contextedge.services.episode_service import create_episodes_from_evidence

    created_episodes = await create_episodes_from_evidence(
        db,
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_items=items,
        evidence_ids=[uuid.UUID(i["evidence_id"]) for i in items],
        cluster_fingerprint=cluster.fingerprint,
        cluster_reasons=cluster.reasons,
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
        "cluster_size": len(cluster.evidence_ids),
        "cluster_fingerprint": cluster.fingerprint,
        "superseded_drafts": superseded,
        "reply_reconciliation": reply_reconciliation,
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
            attachment_ids = [
                artifact_id for artifact_id in (res.get("attachment_ids") or []) if artifact_id
            ]
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

            # Auto-hydrate thread when a ticket/email is first normalized.
            # Previously threads stayed in "pending" status until the user
            # clicked "Hydrate thread" in the UI, which meant conversation
            # messages were never ingested as separate EvidenceItems and the
            # ThreadConversation component showed "not yet hydrated".
            thread_ext_id = res.get("_thread_external_id")
            source_id = res.get("_source_id")
            # Knowledge has no conversation to hydrate. A KB article's body,
            # fetched at sync time, IS its content — the Zoho connector says
            # so explicitly and returns `hydration: not_applicable` without
            # making a call, as do the SapphireIMS connector and ServiceNow
            # alert rollups.
            #
            # The connector short-circuit means dispatching anyway was never
            # WRONG, just pointless, which is why nothing ever failed: a
            # 630-article backfill queued 578 tasks that each did nothing.
            # Harmless per task, and it puts hundreds of no-ops in a shared
            # lane where real hydration then waits behind them. Cheaper not
            # to ask.
            hydratable = res.get("_evidence_type") not in KNOWLEDGE_EVIDENCE_TYPES
            if thread_ext_id and source_id and not res.get("deduped") and hydratable:
                from contextedge.workers.hydration_tasks import hydrate_thread

                hydrate_thread.delay(thread_ext_id, source_id, tenant_id)
                logger.info(
                    "normalize.auto_hydration_dispatched",
                    thread_ref=thread_ext_id,
                    evidence_id=res["evidence_id"],
                )
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
        # Retrieval fan-out for items a stale verdict skipped (roadmap
        # A3). Dispatched here — after run_async committed — mirroring
        # normalize_evidence's post-commit dispatch pattern.
        if isinstance(res, dict) and res.get("needs_fanout"):
            from contextedge.workers.evidence_baseline_tasks import (
                compute_evidence_baseline_task,
            )

            chunk_evidence_task.delay(evidence_id, tenant_id)
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
def reconstruct_episode_task(
    self,
    correlation_cluster_id: str,
    tenant_id: str,
    domain_id: str | None = None,
    settle: bool = True,
):
    tid = uuid.UUID(tenant_id)
    did = uuid.UUID(domain_id) if domain_id else None

    async def work(db):
        return await _reconstruct(db, correlation_cluster_id, tid, did, settle=settle)

    try:
        return run_async(work)
    except Exception as exc:
        raise self.retry(exc=exc) from exc
