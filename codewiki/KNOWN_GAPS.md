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

`apply_retention_policy` and `purge_archived_evidence` in `retention_service.py` are production-ready services (legal-hold safe, dry-run preview, `limit`/`limit_reached` cross-tick drain), but neither is wired into Celery Beat. Tenant retention defaults have no effect until a cron trigger or operator script calls them. See [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) for the two-phase archive → purge model; see "Scheduled jobs that need wiring" below for the tracked deferrals.

## Scheduled jobs that need wiring

These tasks are coded, tested, and safe to run — they're just not yet in `celery_app.beat_schedule`:

- **Retention archive (`apply_retention_policy`)** and **purge (`purge_archived_evidence`)** — per-tenant memory-class archive, then hard-delete or soft-purge past the configured `archive_grace_days`. Wire when the customer confirms their desired cadence (typical: archive daily, purge weekly).
- **Weekly golden eval regression** — `backend/evals/run_regression.py` runs today manually or in CI; the weekly Beat entry is deferred until the customer signs off on what pass bar (absolute accuracy? week-over-week delta?) should trip an alert.

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

## Resolved: LLM cost observability + Week-1-2 cost wins

Four issues flagged in [`ENTERPRISE_ARCHITECTURE_REVIEW.md`](../ENTERPRISE_ARCHITECTURE_REVIEW.md)'s Weeks 1–2 roadmap are now shipped:

1. **Prompt caching** — `ai/provider.py::llm_complete` splits messages into a stable system block (marked `cache_control: {"type": "ephemeral"}` via `ai/observability.build_messages`) and a dynamic user block. OpenAI's automatic prefix cache and Anthropic's ephemeral cache both hit once the system prompt warms per worker. Classifier prompt rewritten accordingly.
2. **Classify-before-embed** — `workers/extraction_tasks._normalize` runs relevance classification inline before embedding + identity + decision extraction. Items scoring `not_relevant` with confidence ≥ 0.75 skip the downstream LLM fan-out entirely. `classify_relevance_task` is no longer part of the default fan-out (still available for manual re-classification from the admin UI / attachment extraction path).
3. **Per-call token + cache logging** — new `ai/observability.py` emits Prometheus counters (`contextedge_llm_tokens_total`, `contextedge_llm_requests_total`) tagged with tenant/model/task/token-type/outcome, a structured `llm.usage` log line per call, and an `OperationalEvent(event_type="llm.usage")` for historical dashboard queries. Both `llm_complete`/`llm_complete_json` and `generate_embedding`/`generate_embeddings_batch` instrumented.
4. **HNSW indexes on embedding columns** — migration `0021_hnsw_vector_indexes` builds cosine-ops HNSW indexes on `evidence_items.embedding` and `decisions.embedding` with `CONCURRENTLY`, resolving the full-scan problem flagged in the architecture review. Requires `pgvector>=0.5`.

Also shipped in the same slice: `GET /api/v1/admin/llm-usage` + `/admin/cost` reviewer UI that renders per-tenant spend, cache-hit rate, and top-N model×task breakdown. Gated to `tenant_admin` / `platform_super_admin`. Refetches every 60 seconds.

## Resolved: Weeks 3-4 — quadratic scanner / retention / episode chunking

- **Contradiction scanner redesign.** `services/contradiction_service.scan_contradictions` now uses HNSW top-K KB candidates + incremental cursor (new `contradiction_scan_state` table, migration `0022`) + explicit `max_llm_calls` budget. Result dict reports `llm_calls_used` / `token_skips` / `cursor_skips` / `budget_skips` / `budget_exhausted`. Expected 80-95% LLM-call reduction on warm tenants.
- **Retention hard-delete + soft-purge.** New `purge_archived_evidence(mode="hard_delete"|"soft_purge", dry_run, limit)` in `services/retention_service.py`. Hard-delete cascades via FK to `attachment_artifacts` / `correlation_edges` / `contradiction_scan_state`; soft-purge NULLs embedding + body and replaces title with `"[purged]"`. Legal hold is in the SQL `WHERE` clause (never post-filtered). Beat scheduling deferred — see "Scheduled jobs that need wiring" above.
- **Episode extractor chunking.** `ai/extractors/episode_extractor.reconstruct_episode` now splits clusters larger than `MAX_ITEMS_PER_CALL=20` into per-chunk LLM calls; per-item body truncated at `PER_ITEM_CHAR_LIMIT=2000`. Logs `episode_extractor.chunked` on the split path so oversize clusters are observable.

## Resolved: Weeks 5-6 — enterprise gates

- **Shadow automation_mode.** `models/playbook.AUTOMATION_MODES` is now a validated enum `("suggest_only", "shadow", "human_confirmed", "supervised", "full_auto")`. `record_tool_invocation` detects shadow runs and tags outputs with `shadow: True`, forces status to `shadow_executed`, and fires `tool.shadow_executed` events so analytics can separate dry-runs from real outcomes.
- **Correlation-ID propagation.** Celery `before_task_publish` / `task_prerun` / `task_postrun` handlers in `workers/celery_app.py` thread `request_id` / `correlation_id` / `causation_id` through the HTTP → worker boundary. `llm.usage` structlog line also enriched so a single reviewer action is greppable across HTTP → Celery → LLM log lines.
- **Ingest-time redaction.** `services/redaction_service.py` regex MVP covers EMAIL / PHONE / SSN / CREDIT_CARD / AWS_ACCESS_KEY / AWS_SECRET_KEY / PRIVATE_KEY blocks. Wired into `_normalize` before the classifier / embedder / identity / decision extractor see anything. `content_hash` computed on the pre-redaction payload so future regex tuning doesn't break dedup. Gated by `settings.redaction_enabled` (default True).

## Resolved: Weeks 7-9 — scale foundations

- **Per-tenant LLM budget enforcement.** New `tenant_llm_budgets` table (migration `0023`) + `services/tenant_budget_service` + pre-call gate in `llm_complete` that raises `TenantBudgetExceeded` on `block` or emits `llm.budget_warning` on `warn`. Admin API `GET/PUT /admin/tenant-budget` + `GET /admin/tenant-budget/status` + `BudgetPanel` UI on `/admin/cost`.
- **Schema-validated LLM JSON.** `ai/provider.llm_complete_json_validated(prompt, schema)` accepts a Pydantic model, validates the parsed JSON, and on failure sends exactly one repair call with the raw prior response + validation errors + JSON Schema. Retry budget hard-capped at 1.
- **Evidence-table scale indexes** (migration `0024`): BRIN on `(tenant_id, ingested_at)`, partial B-tree on `(tenant_id, relevance_state)` for the reviewer queue, partial B-tree on `(tenant_id, updated_at)` for the retention purge sweep. All `CREATE INDEX CONCURRENTLY`. Full partition-conversion runbook deferred in `codewiki/04-evidence-normalization-and-storage.md` until customer volume numbers land.

## Resolved: Weeks 10-12 — agent quality

- **Decision calibration + pattern mining on Beat.** `evaluation.calibrate_decision_confidence` and `evaluation.mine_decision_patterns` (in `workers/decision_tasks.py`) accept the `"all"` sentinel for per-tenant fan-out with isolated exception handling and are scheduled daily.
- **Prompt versioning + per-tenant A/B.** New `ai/prompts/` package with `Prompt` dataclass + `register_prompt` / `get_prompt` / `resolve_version`. Per-tenant variants via `settings.tenant_prompt_variants_json`. `prompt_name` + `prompt_version` threaded through `llm_complete` into `llm.usage` events. All seven LLM prompt families register a `v1` default at import time: `relevance`, `episode`, `decision`, `identity`, `pattern`, `playbook`, `contradiction`.
- **Golden eval scaffold.** `backend/evals/` with `golden.jsonl` format + `run_regression.py` CLI (confusion matrix, non-zero exit on failure). Weekly Beat deferred — see "Scheduled jobs that need wiring".

## Resolved: Watch-list JSONB indexes

The two JSONB hot spots flagged in `ENTERPRISE_ARCHITECTURE_REVIEW.md` §5 are now indexed (migration `0025_jsonb_gin_indexes`):

- `ix_graph_edges_metadata_extra_gin` — future `metadata.reason = X` edge traversals hit an index.
- `ix_evidence_items_canonical_entity_refs_gin` — identity / decision / correlation filters that hit the JSONB blob are indexed, whole-column rather than `->'identities'` so `decisions` filters benefit too.

Both are `jsonb_path_ops` GIN indexes (smaller, faster for the `@>` containment operator current code uses) built `CONCURRENTLY`. Other JSONB columns (`context_snapshot`, `evidence_summary`, `baseline_ref`, `modification_diff`) remain un-indexed by design — add targeted GIN only when a specific filter path shows up.

## Resolved: Frontend production build unblocked

Two pre-existing SSR issues on `/review` and `/decisions` (both called `useSearchParams()` without a Suspense boundary) blocked `next build` once the `add-source-dialog.tsx` type error was fixed. Both pages now follow the standard Next.js 16 pattern: a thin default export that renders `<Suspense fallback={…}>` wrapping a `*PageContent` component that owns the hook call. `npm run build` is green end-to-end; both routes render as `○ (Static)`.

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
