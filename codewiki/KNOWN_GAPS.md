# Known gaps and caveats

Short list of **implementation gaps** and **operational caveats** called out in the codewiki and root documentation. Use this when something “should work” but does not in your environment.

## Adding a new connector type

Built-in types `teams`, `gmail`, `servicenow`, and `jira_sm` are registered in `backend/src/contextedge/connectors/registry.py`. New vendors still need a class under `connectors/` and an entry in `CONNECTOR_CLASSES.update(...)` (or equivalent registration).

## Sync requires a worker on the `sync` queue

`discover_source`, `run_backfill`, and `run_incremental_sync` in `workers/sync_tasks.py` route to the **sync** Celery queue (`celery_app` `task_routes`). Local dev (`make celery-dev` / `python dev.py worker`) includes `sync` in `DEFAULT_QUEUES` in `backend/dev.py`. Custom production workers that omit `sync` from `-Q` will leave retry/backfill tasks **stuck**.

**Fix direction:** Include `sync` in consumed queues (see [`docs/RUNBOOK.md`](../docs/RUNBOOK.md)).

## Evidence deduplication and sync overlap

From the root [`README.md`](../README.md) **Known Constraints**:

- Evidence dedupe is **application-layer** and hash-based; there is not yet a database uniqueness constraint that hard-prevents duplicate `EvidenceItem` rows under concurrency.
- Sync scheduling is not **single-flight** per source object yet; avoid overlapping manual backfills or retries for the same object.

## JWT secret in non-development

Production-like environments must set a real `JWT_SECRET_KEY` when `APP_ENV` is not `development` (root README).

## Operational events and retention jobs

`apply_retention_policy` in `retention_service.py` is intended to be invoked from a **scheduled job** or operator script. If nothing calls it, retention defaults on tenants have no effect until wired.

## Historical note: sync Celery tasks

Sync tasks were previously commented out in `sync_tasks.py`, which broke imports used by `api/v1/sync.py` (`retry_sync_run`) and `api/v1/sources.py` (`trigger_backfill`). They are **implemented** again; use a worker configuration that consumes **sync** as described above.
