import uuid

import structlog

from contextedge.services.contradiction_service import scan_contradictions
from contextedge.services.drift_service import check_playbook_drift
from contextedge.services.evaluation_service import execute_evaluation_run
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="evaluation.run_evaluation",
)
def run_evaluation(self, evaluation_run_id: str, tenant_id: str):
    """Run evaluation replay against historical dataset."""

    async def work(db):
        return await execute_evaluation_run(
            db,
            uuid.UUID(evaluation_run_id),
            uuid.UUID(tenant_id),
        )

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("evaluation.run_failed", run_id=evaluation_run_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.detect_drift",
)
def detect_drift(self, tenant_id: str):
    """Check approved playbooks for drift, staleness, and contradictions.

    Celery Beat passes the literal string ``all`` to scan every tenant.
    """
    from sqlalchemy import select

    from contextedge.models.tenant import Tenant

    async def work(db):
        if tenant_id == "all":
            r = await db.execute(select(Tenant.id))
            tids = [row[0] for row in r.all()]
            merged: list[dict] = []
            total_expired = 0
            for tid in tids:
                pack = await check_playbook_drift(db, tid)
                merged.extend(pack["alerts"])
                total_expired += int(pack["expired_transition_count"])
            return {
                "tenants": len(tids),
                "alerts": merged,
                "alert_count": len(merged),
                "expired_transition_count": total_expired,
            }
        tid = uuid.UUID(tenant_id)
        pack = await check_playbook_drift(db, tid)
        return {
            "tenants": 1,
            "alerts": pack["alerts"],
            "alert_count": pack["alert_count"],
            "expired_transition_count": pack["expired_transition_count"],
        }

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("drift.check_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    name="evaluation.scan_contradictions_task",
)
def scan_contradictions_task(self, tenant_id: str):
    """Scan approved playbooks against KB evidence for contradictions."""
    from sqlalchemy import select

    from contextedge.models.tenant import Tenant

    async def work(db):
        if tenant_id == "all":
            r = await db.execute(select(Tenant.id))
            tids = [row[0] for row in r.all()]
            totals = {
                "tenants": len(tids),
                "playbooks_scanned": 0,
                "kb_items_scanned": 0,
                "candidate_pairs_scanned": 0,
                "contradictions_created": 0,
                "contradictions_updated": 0,
            }
            for tid in tids:
                pack = await scan_contradictions(db, tid)
                for key in totals:
                    if key == "tenants":
                        continue
                    totals[key] += int(pack.get(key, 0))
            return totals

        return {"tenants": 1, **(await scan_contradictions(db, uuid.UUID(tenant_id)))}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("contradiction.scan_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=600,
    name="evaluation.ai_review_episodes",
)
def ai_review_episodes(
    self,
    tenant_id: str,
    limit: int = 100,
    mode_override: str | None = None,
):
    """AI first-pass review sweep over pending episode drafts.

    Runs hourly from beat (no-op while EPISODE_AI_REVIEW=off) and on
    demand from the API. ``mode_override`` may only DOWNGRADE the
    configured mode (run advisory under auto_approve) — a dispatch
    argument must never escalate a governance setting.

    Reviews in review-priority order, the same order the human queue
    shows, so machine attention and human attention agree on what
    matters first. Skips drafts that already carry an assessment —
    the sweep never pays twice for the same draft. Defers per tenant
    while a bulk ingest is landing, for the dedup sweep's reason:
    assessing drafts the next message burst will supersede is spend
    with no beneficiary.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from contextedge.config import settings
    from contextedge.models.episode import Episode
    from contextedge.models.tenant import Tenant
    from contextedge.services.episode_review_service import (
        REVIEW_MODES,
        ai_review_episode,
        review_priority_expression,
    )
    from contextedge.workers.pattern_tasks import (
        DEDUP_ACTIVITY_WINDOW_MINUTES,
        tenant_pipeline_active,
    )

    configured = settings.episode_ai_review
    if configured == "off" and mode_override is None:
        return {"status": "disabled"}
    mode = configured
    if mode_override in REVIEW_MODES:
        # Downgrade only: advisory under auto_approve is allowed; a
        # dispatch cannot turn advisory (or off) into auto_approve.
        if mode_override == "advisory" or configured == "off":
            mode = "advisory" if configured == "off" else mode_override
    if mode == "off":
        mode = "advisory"

    async def work(db):
        if tenant_id == "all":
            r = await db.execute(select(Tenant.id))
            tids = [row[0] for row in r.all()]
        else:
            tids = [uuid.UUID(tenant_id)]

        window_start = datetime.now(UTC) - timedelta(
            minutes=DEDUP_ACTIVITY_WINDOW_MINUTES
        )
        totals = {"reviewed": 0, "approved": 0, "held": 0, "deferred_tenants": 0}
        for tid in tids:
            active, activity = await tenant_pipeline_active(db, tid, window_start)
            if active:
                totals["deferred_tenants"] += 1
                logger.info(
                    "episode_ai_review.deferred_ingest_active",
                    tenant_id=str(tid),
                    **activity,
                )
                continue

            # Crash recovery for the post-commit dispatch below: an
            # auto-approved episode whose signature dispatch was lost
            # (process death between commit and send, broker outage)
            # is approved in the DB but never minted a signature. Small
            # bounded re-dispatch each sweep; the signature task is
            # idempotent. Scoped to auto-approvals only — widening to
            # every approved-without-signature episode would surprise-
            # backfill the pre-signature era at one LLM call apiece.
            from contextedge.models.issue_signature import EpisodeIssueSignature

            orphaned = (
                await db.execute(
                    select(Episode.id)
                    .where(
                        Episode.tenant_id == tid,
                        Episode.reviewer_state == "approved",
                        Episode.ai_review["auto_approved"].as_boolean().is_(True),
                        ~select(EpisodeIssueSignature.id)
                        .where(EpisodeIssueSignature.episode_id == Episode.id)
                        .exists(),
                    )
                    .limit(20)
                )
            ).scalars().all()
            for orphan_id in orphaned:
                try:
                    from contextedge.workers.signature_tasks import (
                        extract_issue_signature_task,
                    )

                    extract_issue_signature_task.delay(str(orphan_id), str(tid))
                except Exception:
                    logger.warning(
                        "issue_signature.redispatch_failed", episode_id=str(orphan_id)
                    )

            drafts = (
                await db.execute(
                    select(Episode)
                    .where(
                        Episode.tenant_id == tid,
                        Episode.reviewer_state == "pending_review",
                        Episode.ai_review.is_(None),
                    )
                    .order_by(review_priority_expression().desc())
                    .limit(limit)
                )
            ).scalars().all()

            approved_domains: set = set()
            consecutive_transient = 0
            for episode in drafts:
                try:
                    outcome = await ai_review_episode(
                        db, tid, episode, mode=mode
                    )
                    # Commit PER EPISODE, before any dispatch. Durability
                    # first: a batch-end commit made every verdict in the
                    # batch hostage to the last one (one deadlock = 50
                    # LLM calls re-paid), and it held the review-service
                    # row lock for minutes instead of milliseconds.
                    await db.commit()
                except Exception as exc:  # one bad draft never ends the sweep
                    await db.rollback()
                    logger.warning(
                        "episode_ai_review.failed",
                        episode_id=str(episode.id),
                        error=str(exc),
                    )
                    continue
                if outcome.get("skipped_state_changed"):
                    totals["skipped_state_changed"] = (
                        totals.get("skipped_state_changed", 0) + 1
                    )
                    continue
                if outcome.get("transient_failure"):
                    # Provider outage / budget block: NOTHING was persisted,
                    # so the draft stays eligible for the next sweep. A run
                    # of these means the provider is down for everyone —
                    # stop burning the batch instead of holding 100 drafts.
                    totals["transient_failures"] = totals.get("transient_failures", 0) + 1
                    consecutive_transient += 1
                    if consecutive_transient >= 5:
                        logger.warning(
                            "episode_ai_review.aborting_sweep_provider_down",
                            tenant_id=str(tid),
                        )
                        break
                    continue
                consecutive_transient = 0
                totals["reviewed"] += 1
                if outcome["approved"]:
                    totals["approved"] += 1
                    approved_domains.add(outcome.get("domain_id"))
                    # The commit above already landed: the signature task
                    # can only ever observe the approved state, and a
                    # rollback can no longer orphan this dispatch.
                    try:
                        from contextedge.workers.signature_tasks import (
                            extract_issue_signature_task,
                        )

                        extract_issue_signature_task.delay(
                            outcome["episode_id"], str(tid)
                        )
                    except Exception:  # broker down: mop-up re-dispatches
                        logger.warning(
                            "issue_signature.dispatch_failed",
                            episode_id=outcome["episode_id"],
                        )
                else:
                    totals["held"] += 1

            # One clustering dispatch PER DOMAIN with approvals — passing
            # None here clustered nothing: the global pass deliberately
            # sees only NULL-domain episodes (domain-safe mining), and on
            # the live graph every episode is domain-scoped. Found by
            # external review 2026-08-18.
            for domain_id in approved_domains:
                try:
                    from contextedge.workers.pattern_tasks import cluster_episodes

                    # domain_id is already a string (or None) from the
                    # service's post-lock read; every approval feeding
                    # this set committed before we got here.
                    cluster_episodes.delay(domain_id, str(tid))
                except Exception as exc:
                    logger.warning(
                        "episode_ai_review.cluster_dispatch_failed", error=str(exc)
                    )
        return {"mode": mode, **totals}

    try:
        return run_async(work)
    except Exception as exc:
        logger.exception("episode_ai_review.sweep_failed", error=str(exc))
        raise self.retry(exc=exc) from exc
