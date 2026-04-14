# Known gaps and caveats

Short list of implementation gaps and operational caveats called out in the codewiki and root documentation. Use this when the product surface looks more complete in the architecture than it does in the current UI or environment.

## Adding a new connector type

Built-in types `teams`, `gmail`, `servicenow`, and `jira_sm` are registered in `backend/src/contextedge/connectors/registry.py`. New vendors still need a class under `connectors/` and an entry in the registry map.

## Sync requires a worker on the `sync` queue

`run_backfill` and `run_incremental_sync` in `workers/sync_tasks.py` route to the `sync` Celery queue. Local development includes `sync` in `DEFAULT_QUEUES` in `backend/dev.py`. Custom workers that omit `sync` will leave retry and backfill tasks stuck.

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

## Object storage blobs are not lifecycle-managed in-app

Raw payloads above the offload threshold are stored in S3-compatible object storage (MinIO) and referenced by `RawEvidenceObject.object_storage_key`. The application currently uploads and reads these blobs but does not delete them (no TTL, lifecycle policy enforcement, or garbage collection job in code). In practice, blob retention relies on external bucket lifecycle rules or manual cleanup.

## Graph Explorer is read-only

The Graph Explorer page (`/graph-explorer`) provides interactive visualization and traversal of the context graph — statistics, subgraph rendering via React Flow, and BFS neighbor browsing — but does not yet support creating, editing, or deleting graph edges from the UI. All graph mutations happen through backend services: builder functions called from pattern discovery, playbook generation, contradiction scans, identity linking, decision extraction, and episode graph construction.

## Decision extraction depends on LLM quality

AI-extracted decisions (Tier 1) rely on `decision_extractor.py` prompting an LLM to identify operational actions from evidence text. Decision types are open-ended labels, not a fixed enum, which means analytics and filtering may require normalization or fuzzy matching across label variations. The extractor truncates input to 4,000 characters; decisions mentioned later in long evidence items may be missed. Governed decision edges (Tier 2) from execution service are high-fidelity and not subject to this limitation.

## Decision and identity linking order in normalization

The normalization worker runs `link_evidence_identities` before `link_evidence_decisions`. Both write to `evidence.canonical_entity_refs` non-destructively (using separate keys: `identities` and `decisions`). If either step fails, the other's data is preserved. However, if identity linking is re-run after decisions have been written, the merge logic in `link_evidence_identities` preserves existing keys — but a full re-normalization should be monitored to ensure both keys remain intact.

## Thread hydration requires normalization to run first

`Thread` rows are created during normalization via `ensure_thread_for_evidence`. If normalization has not yet processed a raw evidence object, the corresponding `Thread` row will not exist and the hydration API will return 404. This is by design (threads are created lazily), but operators should be aware that hydration depends on normalization completing first.

## Gmail backfill checkpoint seeds history_id for incremental

Gmail's `backfill` fetches the mailbox `historyId` when the last page completes and stores it in the checkpoint. This bridges backfill to incremental sync. If a backfill is interrupted before the final page, only a `page_token` checkpoint exists and incremental sync will fail until backfill finishes.

## Historical note: sync Celery tasks

Sync tasks were previously commented out in `sync_tasks.py`, which broke imports used by `api/v1/sync.py` and `api/v1/sources.py`. They are implemented again; use a worker configuration that consumes `sync` as described above.

## Resolved: backfill-to-incremental checkpoint bridging

All four connectors (Gmail, Teams, ServiceNow, Jira SM) now seed a checkpoint on the final backfill page so incremental sync can start without manual intervention. Previously, the last page returned `new_checkpoint=None`, breaking the incremental flow.

## Resolved: sync retry dispatch by run_type

`POST /sync/{run_id}/retry` now checks `run.run_type` and dispatches to `run_backfill.delay(...)` for backfill runs or `run_incremental_sync.delay(...)` for incremental runs. Previously, all retries were dispatched as incremental sync regardless of the original run type.

## Resolved: title/body extraction for all connectors

`evidence_title_from_payload` and `evidence_body_from_payload` now cover field names from all connectors (`summary`, `short_description`, `description`, `text`, `snippet`) in addition to the previously handled `title`/`subject`/`body`/`body_text`.

## Resolved: Teams hydrate_thread includes root message

Teams `hydrate_thread` now fetches the root message first via `/messages/{message_id}` before fetching replies, so the parent message body and author are included in the hydrated thread.

## Resolved: dead code removed

- `generate_embeddings` Celery task removed (embeddings are now inline during normalization)
- `discover_source` Celery task removed (discovery runs directly via API and `discover_source_objects`)
- `validate_service_account_token` stub removed from `middleware/auth.py`
- Unused `symptoms` parameter removed from `rank_playbooks`

## Retention service not scheduled (intentional gap)

`apply_retention_policy` exists in `retention_service.py` but is not called from a scheduled job. Tenant retention defaults have no effect until a cron trigger or operator script is wired.

## Resolved: Correlation now auto-triggers episode reconstruction

`correlate_evidence` (Celery task) now enqueues `reconstruct_episode_task` when new correlation edges are created. Episode reconstruction LLM failures are caught and logged in `create_episodes_from_evidence` so they do not crash the task.

## `workspace_id` and `domain_id` not set during normalization

New `EvidenceItem` rows do not have `workspace_id` or `domain_id` set during normalization. These are intended to be populated by domain-assignment logic after the evidence item exists.

## `body_summary` only via artifact path

Evidence `body_summary` is only populated when attachment artifact extraction runs (via `process_attachment_artifact`). Direct normalization does not generate a summary.

## Semantic search not exposed in evidence API

The evidence list API supports FTS-based search but does not expose the semantic (vector) search path. Semantic search is used internally by the hybrid ranker for playbook ranking.

## `evidence_quality` placeholder in ranker

The hybrid ranker uses a hard-coded `quality_score = 0.5` for all playbooks. A proper evidence-quality signal has not been implemented.
