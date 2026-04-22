import uuid

import structlog

from contextedge.services.correlation_service import correlate_evidence_item
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="extraction.correlate_evidence",
)
def correlate_evidence(self, evidence_id: str, tenant_id: str):
    tid = uuid.UUID(tenant_id)
    eid = uuid.UUID(evidence_id)

    async def work(db):
        from sqlalchemy import select
        from contextedge.models.evidence import EvidenceItem
        
        result = await correlate_evidence_item(db, tid, eid)
        
        cluster_ids = []
        domain_id = None
        if result and result.get("status") == "ok":
            canonical_case_id = result.get("canonical_case_id")
            if canonical_case_id:
                # Fetch all evidence IDs in this case cluster via CaseLink
                from contextedge.models.session import CaseLink
                from contextedge.models.episode import CorrelationEdge
                from contextedge.models.evidence import EvidenceItem
                from sqlalchemy import or_
                
                cl_res = await db.execute(
                    select(CaseLink.evidence_id).where(
                        CaseLink.tenant_id == tid,
                        CaseLink.canonical_case_id == uuid.UUID(canonical_case_id),
                        CaseLink.evidence_id.is_not(None)
                    )
                )
                cluster_ids = [str(vid) for vid in cl_res.scalars().all()]
                
                # ALSO fetch all evidence IDs linked via CorrelationEdge
                ce_res = await db.execute(
                    select(CorrelationEdge).where(
                        CorrelationEdge.tenant_id == tid,
                        or_(
                            CorrelationEdge.source_evidence_id == eid,
                            CorrelationEdge.target_evidence_id == eid
                        )
                    )
                )
                for edge in ce_res.scalars().all():
                    cluster_ids.append(str(edge.source_evidence_id))
                    cluster_ids.append(str(edge.target_evidence_id))
                
                # Fetch domain_id from evidence if not provided
                res = await db.execute(select(EvidenceItem.domain_id).where(EvidenceItem.id == eid))
                domain_id = res.scalar_one_or_none()
            
        return result, cluster_ids, domain_id

    try:
        result, cluster_ids, domain_id = run_async(work)
        # We trigger reconstruction if we have any correlations OR if we identify a cluster
        if result and result.get("status") == "ok" and cluster_ids:
            from contextedge.workers.extraction_tasks import reconstruct_episode_task

            cluster_id_str = ",".join(set(cluster_ids)) # Deduplicate and join
            reconstruct_episode_task.delay(cluster_id_str, tenant_id, domain_id=str(domain_id) if domain_id else None)
            logger.info(
                "correlation.episode_reconstruction_enqueued",
                evidence_id=evidence_id,
                cluster_size=len(cluster_ids),
                correlations_created=result.get("correlations_created", 0),
            )
        return result
    except Exception as exc:
        logger.exception("correlation.failed", evidence_id=evidence_id, error=str(exc))
        raise self.retry(exc=exc) from exc
