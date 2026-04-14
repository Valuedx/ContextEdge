# Known gaps and caveats

Short list of implementation gaps and operational caveats called out in the codewiki and root documentation. Use this when the product surface looks more complete in the architecture than it does in the current UI or environment.

## Adding a new connector type

Built-in types `teams`, `gmail`, `servicenow`, and `jira_sm` are registered in `backend/src/contextedge/connectors/registry.py`. New vendors still need a class under `connectors/` and an entry in the registry map.

## Sync requires a worker on the `sync` queue

`discover_source`, `run_backfill`, and `run_incremental_sync` in `workers/sync_tasks.py` route to the `sync` Celery queue. Local development includes `sync` in `DEFAULT_QUEUES` in `backend/dev.py`. Custom workers that omit `sync` will leave retry and backfill tasks stuck.

Fix direction: include `sync` in consumed queues and verify worker routing against [`docs/RUNBOOK.md`](../docs/RUNBOOK.md).

## Evidence deduplication and sync overlap

From the root [`README.md`](../README.md) known constraints:

- Evidence dedupe is application-layer and hash-based; there is not yet a database uniqueness constraint that hard-prevents duplicate `EvidenceItem` rows under concurrency.
- Sync scheduling is not single-flight per source object yet; avoid overlapping manual backfills or retries for the same object.

## JWT secret in non-development

Production-like environments must set a real `JWT_SECRET_KEY` when `APP_ENV` is not `development`.

## Role bindings are stored, but login currently flattens roles

`RoleBinding` stores `scope_type` and `scope_id`, but the login flow in `api/v1/auth.py` currently selects only `RoleBinding.role` values when it builds the JWT. In practice, most route enforcement is role-name based, with finer scope coming from token claims such as `allowed_domain_ids` or `workspace_ids`, not from dynamic resolution of every role binding on each request.

## Frontend source onboarding is local-file first

The Add Source dialog is strongest for local directory ingest through `/sources/local-ingest`. The backend contains connector modules for Gmail, Teams, ServiceNow, and Jira Service Management, but the current dialog does not expose a full credential and connector-configuration experience for those cloud connectors.

## Admin console coverage is partial

The Settings page can show tenant data, list users, and create workspaces or domains, but it is not yet a complete admin console. User creation, role binding CRUD, edit or deactivate flows for workspaces and domains, and the retention console remain mostly API-led or placeholder UI.

## Policy assignment UI is partial

The dashboard currently surfaces source retention and classification assignment plus evidence access assignment. Generic policy-assignment listing and playbook approval-policy assignment exist in the backend, but they do not yet have a dedicated first-class dashboard workflow.

## Notifications are lightweight UI only

The current frontend notification experience is the header dropdown in `AppHeader`, backed by polling `/notifications` every 60 seconds. There is not yet a dedicated inbox page, live push transport, or workflow-routing console, even though the notification service abstraction already includes email and webhook channels.

## Operational events and retention jobs

`apply_retention_policy` in `retention_service.py` is intended to be invoked from a scheduled job or operator script. If nothing calls it, tenant retention defaults have no effect yet.

## Graph Explorer is read-only

The Graph Explorer page (`/graph-explorer`) provides interactive visualization and traversal of the context graph — statistics, subgraph rendering via React Flow, and BFS neighbor browsing — but does not yet support creating, editing, or deleting graph edges from the UI. All graph mutations happen through backend services: builder functions called from pattern discovery, playbook generation, contradiction scans, identity linking, and episode graph construction.

## Historical note: sync Celery tasks

Sync tasks were previously commented out in `sync_tasks.py`, which broke imports used by `api/v1/sync.py` and `api/v1/sources.py`. They are implemented again; use a worker configuration that consumes `sync` as described above.
