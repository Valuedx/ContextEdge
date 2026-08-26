import uuid

import structlog
from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun, worker_ready

from contextedge.config import settings
from contextedge.middleware.request_context import (
    bind_request_context,
    current_causation_id,
    current_correlation_id,
    current_request_id,
    reset_request_context,
)

# ContextVar token per worker task, indexed by the task's celery request id.
# We cannot rely on a module-level variable because Celery can run tasks
# concurrently inside one worker process (gevent / eventlet pools) — each
# task must own its own reset token.
_WORKER_CONTEXT_TOKENS: dict[str, object] = {}

_CORRELATION_HEADER_KEYS = ("request_id", "correlation_id", "causation_id")


@before_task_publish.connect
def _inject_correlation_headers(sender=None, headers=None, **_) -> None:
    """Attach the current HTTP request's correlation IDs to outgoing task
    messages so the worker can rebind them on prerun. Celery propagates
    the ``headers`` dict through the broker protocol; nothing else needs
    to change at the enqueue site (``task.delay(...)`` just works)."""
    if headers is None:
        return
    values = {
        "request_id": current_request_id(),
        "correlation_id": current_correlation_id(),
        "causation_id": current_causation_id(),
    }
    for key, value in values.items():
        if value is None:
            continue
        # Don't clobber anything the caller already set explicitly.
        headers.setdefault(key, str(value))


@task_prerun.connect
def _bind_worker_context(task_id=None, task=None, **_) -> None:
    """Rebind correlation IDs from task headers into the worker's
    ContextVar so logs + operational_events emitted by service code
    running under this task inherit them automatically."""
    if task is None or task_id is None:
        return
    request = getattr(task, "request", None)
    if request is None:
        return
    raw_headers = getattr(request, "headers", None) or {}
    values: dict[str, uuid.UUID] = {}
    for key in _CORRELATION_HEADER_KEYS:
        raw = raw_headers.get(key)
        if not raw:
            continue
        try:
            values[key] = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return
    token = bind_request_context(**values)
    _WORKER_CONTEXT_TOKENS[task_id] = token


@task_postrun.connect
def _release_worker_context(task_id=None, **_) -> None:
    token = _WORKER_CONTEXT_TOKENS.pop(task_id, None) if task_id else None
    if token is not None:
        try:
            reset_request_context(token)
        except Exception:
            # A different task may have already reset it (pool recycling);
            # don't let postrun crash the worker on cleanup.
            pass


@worker_ready.connect
def _require_migrations_at_head(sender=None, **_) -> None:
    """Refuse to consume when the DB schema is behind the code.

    The API's /ready gate holds HTTP traffic on a migration mismatch, but
    workers would happily consume the normalize queue against a stale
    schema — every task then fails mid-transaction (e.g. identity columns
    from 0033 missing), corrupting ingestion until someone notices.
    Exiting lets the supervisor restart-loop until migrations run.
    Transient DB errors and installed layouts without the alembic
    directory are skipped — this gate only fires on a *definite* mismatch.
    """
    logger = structlog.get_logger()
    try:
        from pathlib import Path

        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine
        from sqlalchemy import text as sa_text

        import contextedge

        alembic_dir = Path(contextedge.__file__).resolve().parents[2] / "alembic"
        if not alembic_dir.is_dir():
            return
        from sqlalchemy.exc import ProgrammingError

        expected = ScriptDirectory(str(alembic_dir)).get_current_head()
        engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                try:
                    row = conn.execute(
                        sa_text("SELECT version_num FROM alembic_version")
                    ).first()
                except ProgrammingError:
                    # No alembic_version table = never-migrated database —
                    # the MOST definite mismatch, not a transient error.
                    row = None
        finally:
            engine.dispose()
        current = row[0] if row else None
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning("worker.migration_check_skipped", error=str(exc))
        return
    if current != expected:
        logger.error(
            "worker.migration_mismatch_refusing_to_start",
            database_revision=current,
            expected_revision=expected,
        )
        raise SystemExit(
            f"Database at revision {current!r}, code expects {expected!r} — "
            "run 'alembic upgrade head' before starting workers."
        )


celery_app = Celery(
    "contextedge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "contextedge.workers.sync_tasks",
        "contextedge.workers.hydration_tasks",
        "contextedge.workers.extraction_tasks",
        "contextedge.workers.artifact_tasks",
        "contextedge.workers.correlation_tasks",
        "contextedge.workers.pattern_tasks",
        "contextedge.workers.evaluation_tasks",
        # Merged-in modules from ForAEOpsSupport. The baseline task has an
        # explicit name="extraction.compute_evidence_baseline" so the short-name
        # routing below catches it. The review-queue prefetch task uses its
        # default module-path name and falls into the `contextedge.workers.*`
        # fallback route → default queue, which is fine for light prefetch work.
        "contextedge.workers.evidence_baseline_tasks",
        "contextedge.workers.review_queue_tasks",
        # Decision analytics: pattern mining + confidence calibration.
        # Tasks register under ``evaluation.*`` names so they hit the
        # evaluation queue via the routing rule below.
        "contextedge.workers.decision_tasks",
        # Gated semantic correlation suggestions (evaluation.* name →
        # evaluation queue via the routing rule below).
        "contextedge.workers.suggestion_tasks",
        # Issue-signature extraction on episode approval (B3).
        "contextedge.workers.signature_tasks",
        # Fleet-group detection sweep (B6).
        "contextedge.workers.fleet_tasks",
        # Operator-dispatched maintenance sweeps (roadmap A3) —
        # module-path fallback route -> default queue.
        "contextedge.workers.maintenance_tasks",
        # Post-hard-delete orphan sweeps (review F-18 / F-20).
        "contextedge.workers.cleanup_tasks",
        "contextedge.workers.copilot_tasks",
        # Relational-to-graph edge reconciliation for post-0031 rows.
        "contextedge.workers.graph_tasks",
        # Post-merge JSONB identity snapshot repair.
        "contextedge.workers.identity_tasks",
        # Scheduled retention archive + purge.
        "contextedge.workers.retention_tasks",
        # Ad-hoc playbook embedding backfill (0035).
        "contextedge.workers.playbook_tasks",
        # Demand-driven CMDB topology cache warming.
        "contextedge.workers.cmdb_tasks",
        # Post-action verification sweep (0036).
        "contextedge.workers.verification_tasks",
        "contextedge.workers.ranking_calibration_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Survive a broker connection reset instead of dying on it.
    #
    # On this Windows dev box the broker is reached through WSL's port relay
    # (`wslrelay.exe` owns 127.0.0.1:6379, which beats Docker's 0.0.0.0 bind),
    # and that relay drops TCP connections intermittently under concurrent
    # load. Without these settings a single reset raises
    # `kombu.exceptions.OperationalError` and the worker EXITS — measured
    # 2026-08-17, when four of eight workers died to one blip and throughput
    # halved with nothing reporting a failure. Silent capacity loss is worse
    # than a crash: the queue simply drains slower and looks healthy.
    #
    # `max_retries=None` means retry forever rather than give up after 100
    # attempts: a broker outage should pause a worker, never terminate it.
    # The keepalive keeps idle connections from being reaped by the relay in
    # the first place, which is the cheaper half of the fix.
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    broker_transport_options={
        "socket_keepalive": True,
        "retry_on_timeout": True,
        "health_check_interval": 30,
    },
    redis_socket_keepalive=True,
    redis_retry_on_timeout=True,
    task_routes={
        "sync.*": {"queue": "sync"},
        "hydration.*": {"queue": "hydration"},
        # Fast lane (e2e finding): a ~2.5s gate call must never queue
        # behind 20-60s episode/extraction tasks — during the bulk
        # ingest, 500 classifications starved ~40 minutes in extraction's
        # FIFO. Routes are matched in order, so this wins the wildcard.
        "extraction.classify_relevance": {"queue": "default"},
        # Graph lane — the same starvation, one link further down.
        #
        # `normalize_evidence` -> `correlate_evidence` -> (if it created
        # correlations) `reconstruct_episode` is the chain that turns a pile
        # of evidence into a context graph. Every link was routed to
        # `extraction`, so each one queued BEHIND the bulk normalization that
        # produces it — and thread hydration means normalization feeds itself:
        # one ticket hydrates into ~41 message rows, each of which is another
        # normalize task. Measured on the live Zoho backfill (2026-08-17), the
        # extraction queue was GROWING by ~70 tasks/minute at 8,255 deep with
        # ~55,000 message tasks still to come, while the head of the queue was
        # 60/60 normalize. Correlation had been dispatched and never once been
        # received; episodes, patterns and playbooks were all still zero after
        # 193 evidence items.
        #
        # FIFO makes this unbounded, not merely slow: the graph could not
        # begin forming until the entire ingest finished, so the product's
        # whole downstream half waited on its own upstream.
        #
        # A separate lane is the fix already proven for `classify_relevance`
        # directly above. Baseline computation rides along because it is
        # per-evidence follow-up work with the same starvation profile.
        "extraction.correlate_evidence": {"queue": "correlation"},
        "extraction.reconstruct_episode": {"queue": "correlation"},
        "extraction.compute_evidence_baseline": {"queue": "correlation"},
        # Retrieval lane, starved by the same FIFO and with a worse symptom.
        # Chunking and chunk embedding are what make evidence findable: an
        # unembedded chunk is invisible to vector search, so an agent asking
        # the graph a question simply does not see it. Measured in the same
        # run: 1,879 chunks existed and 289 were embedded (15%), with 309
        # `embed_chunks_batch` tasks queued behind 10,226 normalizations.
        # Nothing reported an error — the evidence was ingested, and silently
        # unretrievable.
        "extraction.chunk_evidence": {"queue": "embedding"},
        "extraction.embed_chunks_batch": {"queue": "embedding"},
        "extraction.*": {"queue": "extraction"},
        "artifact.*": {"queue": "extraction"},
        "pattern.*": {"queue": "pattern"},
        "evaluation.*": {"queue": "evaluation"},
        # Lightweight cache-warming after session creation. Routes to
        # default explicitly so the short-name task isn't silently
        # picked up by the catch-all below (review C-03).
        "review_queue.*": {"queue": "default"},
        # Fallback for any tasks still using full module paths
        "contextedge.workers.*": {"queue": "default"},
    },
    task_default_queue="default",
    beat_schedule={
        "detect-drift-every-6h": {
            "task": "evaluation.detect_drift",
            "schedule": 21600.0,
            "args": ("all",),
        },
        "scan-contradictions-every-12h": {
            "task": "evaluation.scan_contradictions_task",
            "schedule": 43200.0,
            "args": ("all",),
        },
        "trigger-syncs-every-15m": {
            "task": "sync.trigger_scheduled_syncs",
            "schedule": 900.0,
        },
        # Daily, because the duplicates this finds accrue one mention at
        # a time and only become visible as a set — and because its
        # output is a review queue, which nobody wants refilled hourly.
        "reconcile-identities-daily": {
            "task": "identity.reconcile_identities",
            "schedule": 86400.0,
            "args": ("all",),
        },
        # Decision analytics beats — fanning out across tenants in a
        # single task keeps the beat simple and lets the worker
        # parallelise per-tenant iteration internally. Daily cadence is
        # fine for both: calibration and pattern mining both operate on
        # completed decisions, which accrue on a per-incident basis.
        "calibrate-decision-confidence-daily": {
            "task": "evaluation.calibrate_decision_confidence",
            "schedule": 86400.0,
            "args": ("all",),
        },
        "recalibrate-ranking-nightly": {
            "task": "evaluation.recalibrate_ranking",
            "schedule": 86400.0,
            "args": ("all",),
        },
        "mine-decision-patterns-daily": {
            "task": "evaluation.mine_decision_patterns",
            "schedule": 86400.0,
            "args": ("all",),
        },
        # Reap orphaned MinIO blobs + dangling graph_edges after
        # retention hard-deletes. Cheap when there's nothing to do.
        "cleanup-hard-deleted-daily": {
            "task": "evaluation.cleanup_hard_deleted_evidence",
            "schedule": 86400.0,
            "args": ("all",),
        },
        # Materialize claim / fix-pattern / case-outcome / execution
        # relationships created since migration 0031's one-time backfill.
        # ensure_edge is ON CONFLICT-safe, so the sweep is idempotent.
        "reconcile-graph-relationships-every-6h": {
            "task": "evaluation.reconcile_graph_relationships",
            "schedule": 21600.0,
            "args": ("all",),
        },
        # Retention: archive daily, purge weekly (mode from
        # settings.retention_purge_mode; soft_purge by default).
        "retention-archive-daily": {
            "task": "evaluation.apply_retention_archive",
            "schedule": 86400.0,
            "args": ("all",),
        },
        "retention-purge-weekly": {
            "task": "evaluation.purge_archived",
            "schedule": 604800.0,
            "args": ("all",),
        },
        # Post-action verification: re-check completed executions against
        # operational reality (new incidents / alert activity on the
        # session's CIs). 15-minute cadence — verdicts persist, so each
        # run is swept at most a handful of times before leaving the queue.
        "verify-executions-every-15m": {
            "task": "evaluation.verify_executions",
            "schedule": 900.0,
            "args": ("all",),
        },
        "detect-fleet-groups": {
            "task": "evaluation.detect_fleet_groups",
            "schedule": 1800.0,
        },
        # Knowledge dedup on a clock. The passes were correct and called by
        # nothing on a schedule — the graph re-inflated between manual runs
        # (measured: 643 -> 2,869 pending drafts across one bulk-ingest
        # night, consolidated to ~950 by one manual sweep). Hourly because
        # the passes are idempotent and near-free when there is nothing to
        # do; the task itself defers per-tenant while a bulk ingest is
        # landing (see DEDUP_ACTIVITY_THRESHOLD) so it never churns drafts
        # that the next message burst would regrow.
        "deduplicate-knowledge-hourly": {
            "task": "pattern.deduplicate_knowledge",
            "schedule": 3600.0,
            "args": ("all",),
        },
        # AI first-pass episode review (EPISODE_AI_REVIEW). Scheduled
        # unconditionally so enabling the setting needs no beat restart;
        # the task returns "disabled" instantly while the setting is off.
        # Offset from the dedup sweep's cadence matters less than order:
        # both defer while ingest is active, and review after dedup means
        # the model reads consolidated drafts, not about-to-be-superseded
        # fragments.
        "ai-review-episodes-hourly": {
            "task": "evaluation.ai_review_episodes",
            "schedule": 3600.0,
            "args": ("all",),
        },
        "backfill-playbook-embeddings-nightly": {
            "task": "evaluation.backfill_playbook_embeddings",
            "schedule": 86400.0,
            "args": ("all", 200, True),
        },
        "purge-copilot-message-bodies-daily": {
            "task": "evaluation.purge_copilot_message_bodies",
            "schedule": 86400.0,
            "args": ("all",),
        },
    },
)
