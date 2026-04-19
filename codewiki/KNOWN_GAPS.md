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

AI-extracted decisions (Tier 1) rely on `decision_extractor.py` prompting an LLM to identify operational actions from evidence text. Decision types are open-ended labels, not a fixed enum, which means analytics and filtering may require normalization or fuzzy matching across label variations. The extractor truncates input to 4,000 characters; decisions mentioned later in long evidence items may be missed. Governed decision edges (Tier 2) from execution service are high-fidelity and not subject to this limitation. First-class decision traces (Tier 3) provide the richest representation — see [16-decision-traces.md](./16-decision-traces.md).

## Resolved: Human-in-the-loop rejection now uses structured reason codes

Previously, `DecisionOption.rejection_reason` was free-text only, which meant rejection signal was unaggregatable and `get_decision_effectiveness` couldn't break out failure modes. Migration `0017_rejection_modification_codes` adds:

- `decision_options.rejection_code` (one of `REJECTION_REASON_CODES`: `wrong_diagnosis`, `plan_incomplete`, `needs_human_judgment`, `user_context_missing`, `policy_violation`, `other`)
- `decision_outcomes.feedback_code` (same enum) and extends `OUTCOME_RESULTS` with `"rejected"` so analytics can separate reviewer-rejected from executed-and-failed decisions.
- `approval_requests.modification_diff` JSONB + `modification_reason_code` for the Modify branch of the Approve / Modify / Reject flow.

A new `POST /decisions/{id}/reject` endpoint (`services.decision_trace_service.reject_decision`) writes the structured code, creates a `DecisionOutcome(execution_result="rejected")` with a `resulted_in` graph edge, flips `decision.status="superseded"` + `human_override=true`, and emits a `decision.rejected` operational event. The free-text `rejection_reason` / `feedback_received` fields remain for the `other` + write-in case. See [16-decision-traces.md](./16-decision-traces.md) for the structured-code walkthrough.

## Resolved: Reviewer console bundle endpoint

`GET /api/v1/review-queue/{session_id}/context` (`services.review_queue_service.build_review_context`) composes session + top-pending decision + similar-decision aggregate + scoped decisions / execution runs / operational events into a single response (`ReviewQueueContext`) so the reviewer UI renders in one round trip instead of fanning out. `top_decision_badge.level` is derived server-side (`green >= 0.8`, `amber 0.5–0.8`, `red < 0.5`) so thresholds can't drift between consumers. Paired with `GET /decisions` confidence filter/sort (`min_confidence`, `max_confidence`, `sort=confidence_desc|confidence_asc|created_desc`) this unlocks queue-based prioritization.

## Resolved: Review-queue bundle prefetched to Redis on session creation

The bundle endpoint is read-through cached (`review_queue:{tenant_id}:{session_id}`, TTL 300s) and pre-warmed by the `prefetch_review_context` Celery task enqueued from `create_resolution_session`. This closes the sub-2s first-render budget for the reviewer console — the click-to-render round trip hits Redis, not Postgres. Cache is shape-safe (default limits only, custom limits bypass), tenant-scoped, corrupt-entry tolerant, and can be bypassed per-request with `?no_cache=true`. Enqueue failures are logged and swallowed so a degraded Celery broker never blocks session creation.

## Partial: Reviewer console — Phase 5

`/review` route renders zones 2 (ticket header), 3 (raw user message), 5 (ranked hypotheses with ruled-out reasons + similar-decisions aggregate), and 7 (**Approve / Modify / Reject** — all three verbs live). Queue pane consumes `/decisions?status=pending&sort=confidence_desc` with confidence-badge color levels (`green ≥ 0.8`, `amber 0.5–0.8`, `red < 0.5`) matching the server-side thresholds.

**Modify flow** opens a dialog pre-filled with the pending approval's current step inputs as editable JSON. Reviewer provides a required summary (becomes `modification_diff.summary`, which the backend uses as the modified step's action label on the new `Decision` option), optional free-text comment, and a reason code from the same 6-code enum as reject. Submission POSTs `{modification_diff: {inputs, summary}, modification_reason_code, comment}` to `/execution/runs/{run_id}/approvals/{approval_id}/modify`. TanStack Query invalidation refetches the bundle; the backend's `invalidate_review_context` (wired into `decide_approval`/`modify_approval` transitively via `create_decision`) drops the Redis cache too.

**Known limitation of the Modify UI:** uses a raw JSON textarea for the `inputs` editor. This preserves the backend's schema-less flexibility (any step shape can be modified) but is a rough reviewer UX. Typed per-step forms — keyed on `PlaybookStep.tool_ref` or `step_title` — are a clean follow-up. For reviewers working on well-known step shapes (cert renewal, password reset), these forms would be materially faster.

**Still deferred:**
- **Zone 4 evidence cards** — bundle does not carry evidence; needs a `/decisions/{id}/provenance` fetch per top-decision rendered with `delta_signal` color and `baseline_ref.comparison_label`.
- **Zone 6 plan steps** — needs joining `PlaybookVersion.steps` (the M2 schema) + `verification_policy` so reviewers see reversibility, time estimate, per-step safety class, and the auto-close-on-recheck commitment. Requires a playbook-version fetch (or a dedicated "step detail" endpoint).
- **Bulk approve** — described in the design doc (filter to confidence > 0.85, select-all, one-click approve with condensed preview modal). Backend supports it today via the existing Approve endpoint; UI affordance is not built.
- **Keyboard shortcuts** — `A`/`M`/`R` for verbs, `J`/`K` for queue navigation.
- **Typed Modify forms** — see limitation above.
- **Frontend tests** — no test runner is configured for the frontend package (`npm test` stubs out). Add one alongside the next slice.

## Resolved: Semantic similar-decision retrieval

`Decision.embedding` (Vector(3072)) is populated inline during `create_decision` from `decision_type + compact_trace + rationale_summary`. `find_similar_decisions` and `find_similar_decisions_aggregate` accept `query_decision_id` (uses that decision's stored embedding) or `query_text` (embedded on the fly) and order results by `embedding <=> query` cosine distance. JSONB containment on `workflow` / `environment` / `impacted_dependency` remains as a structural pre-filter in both paths so structural scoping still works with semantic ordering. When no query embedding resolves (neither param passed, or provider failure), retrieval falls back to the pre-C3 `created_at DESC` ordering — no caller breakage. Embedding write failures at `create_decision` are swallowed; the decision lands with `embedding = NULL` and participates in structural retrieval until re-embedded.

**Not yet done:** no HNSW / IVFFlat index on `decisions.embedding` — matches the existing `evidence_items.embedding` pattern (also unindexed) since the full-table scan is fine at current scale. Add an index when decision row counts warrant it. Similarly, no back-fill task exists to embed pre-C3 decisions; they'll stay embedding-null until re-written or a dedicated `reembed_decisions` task is built.

## Resolved: Cache invalidation on downstream mutations

`services.review_queue_service.invalidate_review_context(tenant_id, session_id)` is called from every mutation that changes a session's review state: `create_decision`, `record_outcome`, `reject_decision` (decision service), and `close_resolution_session` (session service). `decide_approval` and `modify_approval` in the execution service embed `create_decision`, so they invalidate transitively through that call — no duplicate wire-in.

The helper opens a short-lived `aioredis` client via `settings.redis_url`, deletes the key, and swallows transport errors (a degraded Redis never bubbles into mutation code paths). `session_id=None` is a no-op so call sites can invoke unconditionally.

**Known caveat:** invalidation fires post-flush but pre-commit, so a narrow race window exists where a concurrent bundle read could re-populate the cache with the pre-commit snapshot. The 300s TTL backstops the race. A `SQLAlchemy after_commit` hook is the cleanest fix if real-time correctness ever matters more than the current simplicity.

## Resolved: Evidence baseline / delta signal for Zone 4 cards

`EvidenceItem.baseline_ref` (JSONB) and `EvidenceItem.delta_signal` (`neutral` / `amber` / `red`) added in migration `0019_evidence_baseline`. Post-normalize, `compute_evidence_baseline_task` (`workers/evidence_baseline_tasks.py`, `extraction` queue) fans out alongside `classify_relevance_task` and `correlate_evidence`, matches prior evidence on tenant + evidence_type + source_object_id within a 7-day window, and records a relationship-only baseline: "last seen N days ago" or "first observation in 7d window". `delta_signal` defaults to `neutral`; connector-stamped richer signals are preserved.

**By design:** numeric deltas ("74% → 32% disk free") come from connectors that know the metric semantics — the generic worker only does relationship baselines. The JSONB shape is open-ended so connector-side and worker-side baselines coexist on the same column.

**Not yet wired:** no IT-telemetry connectors populate numeric baselines yet — the Intune / CrowdStrike / AD / Entra connectors are part of Phase 4. Until they land, Zone 4 cards render the relationship-only label, not numeric deltas.

## Resolved: Playbook step metadata — reversibility, time estimate, verification flag

`PlaybookStep` (`schemas/playbook.py`) adds per-step `reversible`, `time_estimate_sec`, `verification`, `rollback_hint`, `safety_class`, and `tool_ref`. All fields are optional with defaults so pre-M2 JSONB payloads keep validating, and `extra="allow"` preserves vendor-specific keys. Storage is the existing `PlaybookVersion.steps` JSONB — no column change.

Migration `0018_playbook_step_metadata` adds `playbook_versions.verification_policy JSONB` for the reviewer console's "auto-close on successful recheck" commitment (`VerificationPolicy`: `auto_close_on_success`, `recheck_after_sec`, `recheck_metric`, `recheck_source`). This backs the UI's trust-building promise that the agent closes its own loop rather than fire-and-forget.

**Not yet wired:** the execution engine does not yet honour `verification_policy` — the scheduler + recheck worker that re-evaluates `recheck_metric` after `recheck_after_sec` and auto-closes the session on success is a follow-up. Today the fields are descriptive only; the reviewer UI can render them but the backend does not act on them.

## Resolved: Approve / Modify / Reject flow — Modify endpoint is live

`POST /api/v1/execution/runs/{run_id}/approvals/{approval_id}/modify` (`services.execution_service.modify_approval`) accepts an `ApprovalModificationRequest` with `modification_diff`, `modification_reason_code` (same enum as reject), and optional `comment`. It flips the `ApprovalRequest.status` to `modified`, merges `modification_diff["inputs"]` into the step's inputs JSONB, transitions the run + step back to `running`, emits an `approval.modified` operational event, adds a `modified_by` graph edge, and creates a first-class `Decision(decision_type="modify")` with two options — original (`selected=False`, `rejection_code=<reason>`) and modified (`selected=True`) — keeping the graph's `considered`/`chose` invariant intact. `DECISION_TYPES` now includes `"modify"`.

## Resolved: Decision traces are now first-class graph citizens

Previously, decision traces were flat `DecisionTraceEvent` rows with no graph connectivity or structured option/outcome tracking. This has been addressed: `Decision`, `DecisionOption`, and `DecisionOutcome` models are fully integrated into the context graph with typed edges (`based_on`, `considered`, `chose`, `applied_policy`, `required_approval`, `resulted_in`, `followed_by`). The execution service creates first-class decisions at every key lifecycle point (playbook start, approval/denial, completion). A dedicated `/decisions` API and frontend page provide full CRUD, chain navigation, similarity search, and effectiveness analytics. The flat `DecisionTraceEvent` is retained for backward compatibility as a compact audit trail.

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
