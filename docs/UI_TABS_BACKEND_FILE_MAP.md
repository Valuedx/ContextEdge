# ContextEdge UI Tabs - Backend File Map

This report shows which backend files support each UI tab. Use it when explaining where each screen gets its data from.

Line numbers were re-verified against the working tree on **2026-08-19**. If a file has moved since, trust the symbol name over the number.

## Quick Rule

Frontend page = what user sees.

Backend API file = controller/router that receives requests from UI.

Service file = business logic behind the API.

Worker file = the Celery task that does the slow work in the background. Most screens only *read* what a worker already wrote, so if a tab looks empty the worker is usually the thing to check, not the API.

Model file = database tables used by that feature.

## How to read a request

Every `/api/v1/*` call goes through the same four layers before it reaches a service:

1. `backend/src/contextedge/middleware/request_context.py:74` - mints `X-Request-ID` / `X-Correlation-ID` / `X-Causation-ID` and reads the JWT or `X-Service-Token` into `request.state`.
2. `backend/src/contextedge/deps.py:72` (`get_current_user`) - service token wins over JWT; `has_role` short-circuits to true for `platform_super_admin`, `tenant_admin`, and `admin` (deps.py:37).
3. `backend/src/contextedge/database.py:29` (`get_db`) - one async session per request, commit on success. Handlers `flush()`; the dependency commits.
4. `backend/src/contextedge/middleware/request_audit.py:25` - after the response, every mutating `/api/v1` call is written to `audit_logs`.

Those same correlation ids ride into Celery through `before_task_publish` / `task_prerun` hooks (`backend/src/contextedge/workers/celery_app.py:25`), so one id joins a button click to the LLM spend it caused.

## Quick Line Map

Use this table when someone asks: "Where is this tab code?"

`file:line` means the code starts around that line.

| Tab | Frontend page code | Backend API code | Main database/model code |
| --- | --- | --- | --- |
| Overview | `frontend/src/app/(dashboard)/overview/page.tsx:107` | `backend/src/contextedge/api/v1/sources.py:38`, `backend/src/contextedge/api/v1/evidence.py:29`, `backend/src/contextedge/api/v1/episodes.py:40`, `backend/src/contextedge/api/v1/playbooks.py:81` | `backend/src/contextedge/models/source.py:11`, `backend/src/contextedge/models/evidence.py:47`, `backend/src/contextedge/models/episode.py:213`, `backend/src/contextedge/models/playbook.py:48` |
| Sources | `frontend/src/app/(dashboard)/sources/page.tsx:119` | `backend/src/contextedge/api/v1/sources.py:38` (list), `:80` (create), `:204` (discover), `:295` (pause/resume/cancel), `:418` (rotate credentials), `:456` (local ingest) | `backend/src/contextedge/models/source.py:11`, `backend/src/contextedge/models/source.py:55`, `backend/src/contextedge/models/source.py:89` |
| Sync Operations | `frontend/src/app/(dashboard)/sync/page.tsx:94` | `backend/src/contextedge/api/v1/sync.py:13` (runs), `:43` (retry), `:64` (purge), `backend/src/contextedge/api/v1/sources.py:368` (runs for one source), `backend/src/contextedge/api/v1/sources.py:386` (backfill) | `backend/src/contextedge/models/source.py:128` (SyncRun), `backend/src/contextedge/models/source.py:111` (SyncCheckpoint) |
| Evidence | `frontend/src/app/(dashboard)/evidence/page.tsx:214` | `backend/src/contextedge/api/v1/evidence.py:29`, `:98`, `:238`, `:318`, `:530`, `backend/src/contextedge/api/v1/threads.py:68` (hydrate) | `backend/src/contextedge/models/evidence.py:47`, `backend/src/contextedge/models/evidence.py:173` (EvidenceChunk), `backend/src/contextedge/models/evidence.py:223` (Thread) |
| Sessions | `frontend/src/app/(dashboard)/sessions/page.tsx:628` | `backend/src/contextedge/api/v1/sessions.py:26`, `:45`, `:76`, `:102`, `:139` | `backend/src/contextedge/models/session.py:11`, `backend/src/contextedge/models/session.py:101` |
| Runtime | `frontend/src/app/(dashboard)/runtime/page.tsx:197` | `backend/src/contextedge/api/v1/runtime.py:89` (match), `:249` (explain), `:270` (published version), `:352` (feedback) | `backend/src/contextedge/models/playbook.py:125` (PlaybookVersion), `backend/src/contextedge/models/session.py:11`, `backend/src/contextedge/models/evaluation.py:42` (RetrievalFeedback) |
| Review Queue | `frontend/src/app/(dashboard)/review/page.tsx:111` | `backend/src/contextedge/api/v1/review_queue.py:30`, `backend/src/contextedge/api/v1/decisions.py:159`, `backend/src/contextedge/api/v1/decisions.py:304` (reject), `backend/src/contextedge/api/v1/execution.py:259` (decide), `backend/src/contextedge/api/v1/execution.py:291` (modify) | `backend/src/contextedge/models/decision.py:75`, `backend/src/contextedge/models/execution.py:173` (ApprovalRequest), `backend/src/contextedge/models/session.py:11` |
| Execution | `frontend/src/app/(dashboard)/execution/page.tsx:123` | `backend/src/contextedge/api/v1/execution.py:65` (start run), `:324` (pending approvals), `:135` (record tool invocation), `:179` (complete step) | `backend/src/contextedge/models/execution.py:33` (ExecutionRun), `backend/src/contextedge/models/execution.py:86` (ExecutionStepRun), `backend/src/contextedge/models/execution.py:173` |
| Decisions | `frontend/src/app/(dashboard)/decisions/page.tsx:152` | `backend/src/contextedge/api/v1/decisions.py:159` (list), `:228` (detail), `:268` (chain) | `backend/src/contextedge/models/decision.py:75`, `backend/src/contextedge/models/decision.py:175` (DecisionOption), `backend/src/contextedge/models/decision.py:208` (DecisionOutcome) |
| Episodes | `frontend/src/app/(dashboard)/episodes/page.tsx:207` | `backend/src/contextedge/api/v1/episodes.py:40`, `:230` (approve), `:282` (bulk approve), `:342` (reconstruct), `:556` (AI review) | `backend/src/contextedge/models/episode.py:213` (Episode), `backend/src/contextedge/models/episode.py:269` (EpisodeStep), `backend/src/contextedge/models/episode.py:292` (EpisodeEvidenceLink) |
| Patterns | `frontend/src/app/(dashboard)/patterns/page.tsx:190` | `backend/src/contextedge/api/v1/patterns.py:41`, `:133` (approve), `:304` (discover), `:412` (cluster) | `backend/src/contextedge/models/pattern.py:23`, `backend/src/contextedge/models/pattern.py:60` (PatternEvidenceLink) |
| Playbooks | `frontend/src/app/(dashboard)/playbooks/page.tsx:72` | `backend/src/contextedge/api/v1/playbooks.py:81`, `:465` (transition), `:515` (create version), `:613` (rollback), `:654` (generate) | `backend/src/contextedge/models/playbook.py:48`, `backend/src/contextedge/models/playbook.py:125`, `backend/src/contextedge/models/playbook.py:177` |
| Negative Knowledge | `frontend/src/app/(dashboard)/negative-knowledge/page.tsx:174` | `backend/src/contextedge/api/v1/negative_knowledge.py:17` | `backend/src/contextedge/models/pattern.py:87` (NegativeKnowledgeItem) |
| Identities | `frontend/src/app/(dashboard)/identities/page.tsx:199` | `backend/src/contextedge/api/v1/identities.py:32`, `:162` (merge), `:186` (merge proposals), `:250` (decide proposal) | `backend/src/contextedge/models/episode.py:48` (CanonicalIdentity), `backend/src/contextedge/models/episode.py:91` (IdentityAlias), `backend/src/contextedge/models/episode.py:330` (IdentityMergeProposal) |
| Correlations | `frontend/src/app/(dashboard)/correlations/page.tsx:314` | `backend/src/contextedge/api/v1/correlations.py:26`, `:51`, `:263` (accept/reject/split/merge) | `backend/src/contextedge/models/episode.py:187` (CorrelationEdge), `backend/src/contextedge/models/session.py:148` (CaseLink) |
| Review Queues (suggestions) | `frontend/src/app/(dashboard)/suggestions/page.tsx:36` | `backend/src/contextedge/api/v1/correlations.py:172` (semantic suggestions), `:70` (fleet suggestions), `backend/src/contextedge/api/v1/identities.py:32` (`resolution_state=needs_review`) | `backend/src/contextedge/models/correlation_suggestion.py:24`, `backend/src/contextedge/models/fleet_group.py:15`, `backend/src/contextedge/models/episode.py:48` |
| Graph Explorer | `frontend/src/app/(dashboard)/graph-explorer/page.tsx:1` (data access via `frontend/src/lib/graph-api.ts:20`) | `backend/src/contextedge/api/v1/graph.py:190` (neighbors), `:220` (subgraph), `:242` (stats), `:120` (edge proposals) | `backend/src/contextedge/models/pattern.py:174` (GraphEdge) |
| Contradictions | `frontend/src/app/(dashboard)/contradictions/page.tsx:144` | `backend/src/contextedge/api/v1/contradictions.py:17`, `:37` (status) | `backend/src/contextedge/models/pattern.py:105` (Contradiction), `backend/src/contextedge/models/pattern.py:123` (ContradictionScanState) |
| Drift | `frontend/src/app/(dashboard)/drift/page.tsx:96` | `backend/src/contextedge/api/v1/drift.py:19` | `backend/src/contextedge/models/playbook.py:48`, `backend/src/contextedge/models/pattern.py:23`, `backend/src/contextedge/models/decision.py:75` |
| Evaluations | `frontend/src/app/(dashboard)/evaluations/page.tsx:183` | `backend/src/contextedge/api/v1/evaluations.py:50`, `:60`, `:86` | `backend/src/contextedge/models/evaluation.py:11`, `backend/src/contextedge/models/evaluation.py:25` |
| Policies | `frontend/src/app/(dashboard)/policies/page.tsx:356` | `backend/src/contextedge/api/v1/policies.py:57`, `backend/src/contextedge/api/v1/policy_assignments.py:66` | `backend/src/contextedge/models/policy.py:31` (TenantPolicy), `backend/src/contextedge/models/policy.py:70` (PolicyCheck) |
| Audit Log | `frontend/src/app/(dashboard)/audit/page.tsx:84` | `backend/src/contextedge/api/v1/audit.py:14` (mounted at `/api/v1/audit-logs`) | `backend/src/contextedge/models/audit.py:11` |
| LLM Cost | `frontend/src/app/(dashboard)/admin/cost/page.tsx:583` | `backend/src/contextedge/api/v1/admin_cost.py:33`, `:102`, `:113`, `:137` | `backend/src/contextedge/models/events.py:13` (OperationalEvent), `backend/src/contextedge/models/tenant.py:116` (TenantLLMBudget) |
| Pipeline Health | `frontend/src/app/(dashboard)/admin/pipeline/page.tsx:140` | `backend/src/contextedge/api/v1/admin_cost.py:166` | reads Redis queue depths + one SQL roll-up, no dedicated table |
| Settings | `frontend/src/app/(dashboard)/settings/page.tsx:245` | `backend/src/contextedge/api/v1/tenants.py:14`, `backend/src/contextedge/api/v1/workspaces.py:14`, `backend/src/contextedge/api/v1/domains.py:14`, `backend/src/contextedge/api/v1/users.py:22` | `backend/src/contextedge/models/tenant.py:12`, `:30`, `:48`, `:68`, `:88` |
| Entity Inventory (no sidebar entry) | `frontend/src/app/(dashboard)/inventory/[id]/page.tsx:56` | `backend/src/contextedge/api/v1/sources.py:219` (objects), `:237` (approve), `:386` (backfill) | `backend/src/contextedge/models/source.py:55` (SourceObject) |

## Quick Task Map

Screens are thin. The pipeline logic lives in Celery tasks. Use this when someone asks "what actually produced this row?"

Routing rules are matched in order at `backend/src/contextedge/workers/celery_app.py:226`; the beat schedule is at `celery_app.py:281`.

| Celery task | Defined at | Queue | Produces what you see on |
| --- | --- | --- | --- |
| `sync.trigger_scheduled_syncs` | `backend/src/contextedge/workers/sync_tasks.py:14` | sync | Sync Operations (fires every 900s) |
| `sync.run_backfill` | `backend/src/contextedge/workers/sync_tasks.py:39` | sync | Sync Operations, Evidence |
| `sync.run_incremental_sync` | `backend/src/contextedge/workers/sync_tasks.py:68` | sync | Sync Operations, Evidence |
| `hydration.hydrate_thread` | `backend/src/contextedge/workers/hydration_tasks.py:189` | hydration | Evidence detail (thread messages) |
| `extraction.normalize_evidence` | `backend/src/contextedge/workers/extraction_tasks.py:1313` | extraction | Evidence |
| `extraction.classify_relevance` | `backend/src/contextedge/workers/extraction_tasks.py:1370` | default | Evidence relevance badge |
| `extraction.chunk_evidence` | `backend/src/contextedge/workers/chunk_tasks.py:210` | embedding | Evidence search quality |
| `extraction.embed_chunks_batch` | `backend/src/contextedge/workers/chunk_tasks.py:238` | embedding | Evidence search quality, Runtime |
| `artifact.extract_attachment` | `backend/src/contextedge/workers/artifact_tasks.py:15` | extraction | Evidence attachments |
| `extraction.correlate_evidence` | `backend/src/contextedge/workers/correlation_tasks.py:16` | correlation | Correlations, Graph Explorer |
| `extraction.compute_evidence_baseline` | `backend/src/contextedge/workers/evidence_baseline_tasks.py:26` | correlation | Correlations |
| `extraction.reconstruct_episode` | `backend/src/contextedge/workers/extraction_tasks.py:1400` | correlation | Episodes |
| `evaluation.ai_review_episodes` | `backend/src/contextedge/workers/evaluation_tasks.py:129` | evaluation | Episodes (`ai_review` verdict badge) |
| `evaluation.extract_issue_signature` | `backend/src/contextedge/workers/signature_tasks.py:24` | evaluation | recurrence links behind Episodes / Graph Explorer |
| `pattern.cluster_episodes` | `backend/src/contextedge/workers/pattern_tasks.py:418` | pattern | Patterns |
| `pattern.generate_playbook_candidate` | `backend/src/contextedge/workers/pattern_tasks.py:442` | pattern | Playbooks (candidates) |
| `pattern.deduplicate_knowledge` | `backend/src/contextedge/workers/pattern_tasks.py:830` | pattern | Episodes / Patterns / Playbooks (hourly tidy-up) |
| `evaluation.detect_drift` | `backend/src/contextedge/workers/evaluation_tasks.py:41` | evaluation | Drift |
| `evaluation.scan_contradictions_task` | `backend/src/contextedge/workers/evaluation_tasks.py:88` | evaluation | Contradictions |
| `evaluation.run_evaluation` | `backend/src/contextedge/workers/evaluation_tasks.py:18` | evaluation | Evaluations |
| `evaluation.verify_executions` | `backend/src/contextedge/workers/verification_tasks.py:112` | evaluation | Execution (verification status) |
| `evaluation.reconcile_graph_relationships` | `backend/src/contextedge/workers/graph_tasks.py:33` | evaluation | Graph Explorer |
| `evaluation.generate_correlation_suggestions` | `backend/src/contextedge/workers/suggestion_tasks.py:26` | evaluation | Review Queues (suggestions) |
| `evaluation.detect_fleet_groups` | `backend/src/contextedge/workers/fleet_tasks.py:41` | evaluation | Review Queues (fleet suggestions) |
| `evaluation.apply_retention_archive` / `evaluation.purge_archived` | `backend/src/contextedge/workers/retention_tasks.py:72` / `:104` | evaluation | Evidence (archived / purged rows) |
| `evaluation.cleanup_hard_deleted_evidence` | `backend/src/contextedge/workers/cleanup_tasks.py:165` | evaluation | orphan cleanup behind Evidence |
| `evaluation.warm_cmdb_topology` | `backend/src/contextedge/workers/cmdb_tasks.py:74` | evaluation | Graph Explorer (CI topology) |
| `evaluation.calibrate_decision_confidence` / `evaluation.mine_decision_patterns` | `backend/src/contextedge/workers/decision_tasks.py:130` / `:34` | evaluation | Decisions |
| `identity.reconcile_identities` | `backend/src/contextedge/workers/identity_tasks.py:147` | default | Identities (merge proposals) |
| `extraction.rebuild_identity_snapshots` | `backend/src/contextedge/workers/identity_tasks.py:72` | extraction | Identities (after a merge) |
| `review_queue.prefetch_review_context` | `backend/src/contextedge/workers/review_queue_tasks.py:33` | default | Review Queue (warm cache) |
| `maintenance.reclassify_stale_evidence` | `backend/src/contextedge/workers/maintenance_tasks.py:71` | default | Evidence |

Eight queues exist: `default, sync, hydration, extraction, correlation, embedding, pattern, evaluation` (`backend/dev.py:16`). A worker fleet that does not consume **correlation** and **embedding** will still ingest evidence, but Correlations, Episodes, Patterns and semantic search all stay empty - that is the single most common "nothing is happening" cause.

## 1. Overview

**Frontend page:**
- `frontend/src/app/(dashboard)/overview/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sources.py`
- `backend/src/contextedge/api/v1/evidence.py`
- `backend/src/contextedge/api/v1/episodes.py`
- `backend/src/contextedge/api/v1/playbooks.py`

**Main database/model files:**
- `backend/src/contextedge/models/source.py`
- `backend/src/contextedge/models/evidence.py`
- `backend/src/contextedge/models/episode.py`
- `backend/src/contextedge/models/playbook.py`

**Simple meaning:**
Overview combines counts and health from multiple backend areas. It is one `Promise.all` of four plain list endpoints (`overview/page.tsx:109-112`) - there is no dedicated overview API, and each call asks for at most 200 rows (`overview/page.tsx:25`), so every number on this page is "first 200 rows of that list", not a database-wide count. The page says so itself. For true queue and pipeline health use the Pipeline Health tab instead.

## 2. Sources

**Frontend page:**
- `frontend/src/app/(dashboard)/sources/page.tsx`
- `frontend/src/app/(dashboard)/sources/[id]/page.tsx`
- `frontend/src/components/sources/add-source-dialog.tsx` (create form; reads `/sources/types` at line 167, posts local folders at line 476)

**Backend files:**
- `backend/src/contextedge/api/v1/sources.py` - list `:38`, catalog `:57`, create `:80`, discover `:204`, per-object approval `:237`, sync control `:295`, sync runs `:368`, backfill `:386`, credential rotation `:418`, local ingest `:456`, probe config `:661`
- `backend/src/contextedge/api/v1/policy_assignments.py:66`

**Service files:**
- `backend/src/contextedge/connectors/registry.py:113` - `get_connector` maps a `source_type` string to a connector class. Seven classes are registered at `connectors/registry.py:100`: `teams`, `gmail`, `servicenow`, `jira_sm`, `manageengine`, `sapphireims`, `zoho_desk`. The picker reads `source_type_catalog` (`connectors/registry.py:69`), which walks the hand-maintained label table `_SOURCE_TYPE_LABELS` (`connectors/registry.py:37`) and marks each entry `available` or `planned` depending on whether a connector is registered for it - so `confluence` / `sharepoint` / `exchange` show up as `planned` (`connectors/registry.py:63`).
- `backend/src/contextedge/connectors/base.py:78` - the five methods every connector implements (`validate_credentials`, `discover_objects`, `backfill`, `fetch_changes`, `hydrate_thread`).
- `backend/src/contextedge/services/source_service.py:87` - `discover_source_objects` decrypts credentials, calls the connector, upserts `SourceObject` rows, and sets `auth_status` / `discovery_status`.
- `backend/src/contextedge/services/sync_control_service.py:64` - writes the pause/cancel signal.
- `backend/src/contextedge/services/policy_assignment.py:12`

**Main database/model files:**
- `backend/src/contextedge/models/source.py:11` (Source), `:55` (SourceObject), `:89` (SourceCredential)
- `backend/src/contextedge/models/policy.py:31`

**Simple meaning:**
Sources backend stores where data comes from and how it should be synced. Credentials are Fernet-encrypted; outside development a missing `FERNET_KEY` raises at import rather than minting a throwaway key (`backend/src/contextedge/config.py:254`).

## 3. Sync Operations

**Frontend page:**
- `frontend/src/app/(dashboard)/sync/page.tsx` (reads `/sync-runs`, `/sources`)
- Per-object backfill lives on `frontend/src/app/(dashboard)/inventory/[id]/page.tsx:91`

**Backend files:**
- `backend/src/contextedge/api/v1/sync.py` - list `:13`, detail `:32`, retry `:43`, purge `:64`, delete `:74`
- `backend/src/contextedge/api/v1/sources.py:386` (start a backfill), `:295` (pause / resume / cancel)

**Worker/service files:**
- `backend/src/contextedge/workers/sync_tasks.py:14` / `:39` / `:68` - the three sync tasks.
- `backend/src/contextedge/services/sync_worker_service.py:419` (`run_backfill_job`) and `:526` (`run_incremental_job`) - the actual job bodies. Both take a transaction-scoped Postgres advisory lock first (`acquire_sync_lock`, `sync_worker_service.py:379`), so a second worker returns `skipped_locked` instead of racing the checkpoint.
- `backend/src/contextedge/services/ingestion_persistence.py:19` - `persist_ingestion_events` writes the `raw_evidence_objects` rows and dedupes on `(tenant_id, source_id, external_id, content_hash)`.
- `backend/src/contextedge/services/object_store.py:50` - raw payloads over `OFFLOAD_THRESHOLD_BYTES = 32_768` (`ingestion_persistence.py:16`) go to MinIO at `raw/{tenant_id}/{raw_id}.json` and the DB keeps a `{"_offloaded": true, "size_bytes": N}` stub.
- `backend/src/contextedge/services/sync_ingestion_queue.py:16` - after the commit, one `normalize_evidence.delay(...)` per new raw id.
- `backend/src/contextedge/services/ingest_priority.py:36` - optional ordering of that hand-off (`resolution_first`, `threads_desc`, `threads_asc`).

**Main database/model files:**
- `backend/src/contextedge/models/source.py:111` (SyncCheckpoint), `:128` (SyncRun)
- `backend/src/contextedge/models/evidence.py:25` (RawEvidenceObject)

**Simple meaning:**
Sync backend runs imports from sources and tracks success/failure counts. Two behaviours are worth knowing before debugging a "nothing imported" ticket: an incremental run with no checkpoint completes as `skipped_no_checkpoint` rather than doing a surprise full pull (`sync_worker_service.py:571`), and a failed hand-off marks the run `failed` and parks the un-queued raw ids on `source_objects.metadata_extra["pending_normalize_raw_ids"]` so the next run drains them (`sync_worker_service.py:325`).

## 4. Evidence

**Frontend page:**
- `frontend/src/app/(dashboard)/evidence/page.tsx`
- `frontend/src/app/(dashboard)/evidence/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/evidence.py` - list/search `:29`, detail `:98`, attachments `:238`, access policy `:261`, relevance override `:297`, bulk delete `:318`, purge `:409`, delete `:494`, context `:530`
- `backend/src/contextedge/api/v1/threads.py` - list `:14`, detail `:33`, thread evidence `:57`, hydrate `:68`

**Service files:**
- `backend/src/contextedge/search/pg_fts.py:13` - `search_evidence_fts`: `plainto_tsquery` over the generated `search_tsvector` column, OR-ed with a ticket-number lookup into `raw_evidence_objects` and a `title ILIKE` fallback, so `INC0010427` is findable by its number. It applies the **same** visibility predicates as vector search, from the same helper (`pg_fts.py:10`, `_visibility_predicates`), so legal-hold and pending-redaction rows can no longer come back through the lexical path.
- `backend/src/contextedge/search/vector_search.py:204` - chunk-aware semantic search (see Runtime below).
- `backend/src/contextedge/search/access_control.py:15` - `resolve_excluded_access_policy_ids`; admins are exempt (`ADMIN_ROLES`, `access_control.py:12`), everyone else is filtered by active `access` policies marked `restricted`.
- `backend/src/contextedge/services/evidence_normalization.py:14` - title/body extraction and the pre-redaction content hash.
- `backend/src/contextedge/services/redaction_service.py:179` - `redact_evidence_fields`; secrets run before numeric rules on purpose.
- `backend/src/contextedge/services/message_filter.py:174` - `message_noise_reason`, the deterministic noise gate for hydrated thread messages; its four verdicts are listed at `message_filter.py:81`.
- `backend/src/contextedge/services/evidence_typing.py:118`, `backend/src/contextedge/services/case_state.py:89`, `backend/src/contextedge/services/knowledge_lifecycle.py:98`, `backend/src/contextedge/services/source_facets.py:63` - the four pure derivations stamped at insert.
- `backend/src/contextedge/services/evidence_chunk_service.py:43` - `write_chunks`.
- `backend/src/contextedge/services/chunkers/registry.py:116` - picks ticket / thread / document / attachment / fallback chunker.
- `backend/src/contextedge/services/artifact_extraction_service.py:349` - attachment registration and text merge.

**Worker files:**
- `backend/src/contextedge/workers/extraction_tasks.py:1313` (`extraction.normalize_evidence`) - the body is `_normalize` at `extraction_tasks.py:122`.
- `backend/src/contextedge/workers/chunk_tasks.py:210` / `:238` - chunk and embed.
- `backend/src/contextedge/workers/hydration_tasks.py:189` - pulls the rest of a thread.

**Main database/model files:**
- `backend/src/contextedge/models/evidence.py:25` (RawEvidenceObject), `:47` (EvidenceItem), `:173` (EvidenceChunk), `:223` (Thread), `:248` (AttachmentArtifact)
- `backend/src/contextedge/models/source.py:11`
- `backend/src/contextedge/models/policy.py:31`

**Simple meaning:**
Evidence backend stores logs, tickets, notes, files, and searchable facts. Since the chunking work landed, one evidence row can also own many `evidence_chunks` rows; the chunk is what semantic search actually matches, and the parent row is what the UI shows.

## 5. Sessions

**Frontend page:**
- `frontend/src/app/(dashboard)/sessions/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sessions.py` - list `:26`, create `:45`, add trace event `:76`, history `:102`, close `:139`

**Service files:**
- `backend/src/contextedge/services/session_service.py:139` - `append_trace_event` writes a `decision_trace_events` row plus an operational event.
- `backend/src/contextedge/services/decision_trace_service.py:51` - `create_decision`.
- `backend/src/contextedge/services/memory_service.py:82` - `build_runtime_memory_context` (short-term / long-term / reasoning memory).
- `backend/src/contextedge/workers/review_queue_tasks.py:33` - warms the reviewer cache when a session is created.

**Main database/model files:**
- `backend/src/contextedge/models/session.py:11` (ResolutionSession), `:101` (DecisionTraceEvent), `:148` (CaseLink)
- `backend/src/contextedge/models/decision.py:75`

**Simple meaning:**
Sessions backend stores the full case file for one issue.

## 6. Runtime

**Frontend page:**
- `frontend/src/app/(dashboard)/runtime/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/runtime.py` - match `:89`, explain `:249`, published playbook `:270`, feedback `:352` / `:372`

**Service/search files:**
- `backend/src/contextedge/search/hybrid_ranker.py:213` - `rank_playbooks`. Weighted signals: keyword 0.25, semantic 0.30, graph 0.15, evidence quality 0.10, identity 0.05, recency 0.10, freshness 0.05, minus a negative-knowledge penalty of 0.05 (`hybrid_ranker.py:22`).
- `backend/src/contextedge/search/vector_search.py:246` - `search_evidence_semantic_for_playbook`, the per-playbook evidence pass.
- `backend/src/contextedge/search/chunk_rollup.py:79` - MMR diversification at chunk level (lambda 0.7), then one hit per parent.
- `backend/src/contextedge/search/vector_ops.py:40` - `halfvec_cosine_distance`, and `tune_ann_recall` at `:34` which issues `SET LOCAL hnsw.ef_search = 200` for the current transaction (`ANN_EF_SEARCH`, `vector_ops.py:31`).
- `backend/src/contextedge/search/risk_policy.py:3` - risk-tier cap ordering.
- `backend/src/contextedge/services/memory_service.py:82` and `backend/src/contextedge/services/decision_trace_service.py:51`.
- `backend/src/contextedge/services/runtime_service.py:23` - the same ranker behind a service call.

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py:48` / `:125` / `:177`
- `backend/src/contextedge/models/evidence.py:47`, `:173`
- `backend/src/contextedge/models/pattern.py:23`
- `backend/src/contextedge/models/session.py:11`
- `backend/src/contextedge/models/evaluation.py:42` (RetrievalFeedback)

**Simple meaning:**
Runtime backend is the recommendation engine. It searches evidence, past cases, patterns, and playbooks. Two operational notes: the explain payload is cached in Redis for one hour (`MATCH_CACHE_TTL_SEC = 3600`, `api/v1/runtime.py:29`), so `GET /runtime/explain/{id}` 404s after that; and the ranker deliberately returns an empty list rather than a weak guess when every candidate scores below `MIN_RECOMMENDATION_SCORE = 0.35` (`hybrid_ranker.py:171`).

## 7. Review Queue

**Frontend page:**
- `frontend/src/app/(dashboard)/review/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/review_queue.py:30` - one bundled read for a whole session.
- `backend/src/contextedge/api/v1/decisions.py:159` (queue list, `status=pending&sort=confidence_desc`), `:93` (similar-decision aggregate), `:304` (reject)
- `backend/src/contextedge/api/v1/execution.py:259` (approve/deny), `:291` (modify)

**Service files:**
- `backend/src/contextedge/services/review_queue_service.py` - `build_review_context`, read-through cached on Redis key `review_queue:{tenant_id}:{session_id}`.
- `backend/src/contextedge/services/execution_service.py` - `decide_approval` and the modify path.
- `backend/src/contextedge/services/approval_policy_service.py:106` / `:119` / `:127` - automation-mode cap, minimum safety class, and the decider check (approver roles, self-approval ban).
- `backend/src/contextedge/services/policy_check_service.py:34` - `record_policy_check` writes one append-only row per evaluation, including denials.
- `backend/src/contextedge/services/decision_trace_service.py:586` - `reject_decision`.

**Main database/model files:**
- `backend/src/contextedge/models/decision.py:75`, `:175`, `:208`
- `backend/src/contextedge/models/execution.py:173` (ApprovalRequest)
- `backend/src/contextedge/models/policy.py:70` (PolicyCheck)
- `backend/src/contextedge/models/session.py:11`

**Simple meaning:**
Review Queue backend shows pending human approvals and decision context. The queue is session-shaped, not decision-shaped: the page pulls pending decisions and dedupes them by `session_id` so one case appears once (`review/page.tsx:122`).

## 8. Execution

**Frontend page:**
- `frontend/src/app/(dashboard)/execution/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/execution.py` - start run `:65`, list runs `:90`, run detail `:110`, record tool invocation `:135`, complete step `:179`, abort `:216`, complete run `:233`, decide approval `:259`, modify approval `:291`, pending approvals `:324`

**Service files:**
- `backend/src/contextedge/services/execution_service.py` - `start_execution`, `request_approval`, `record_tool_invocation`, `record_step_completion`.
- `backend/src/contextedge/services/action_policy_service.py` - per-step verdict by scope filter, specificity, then conflict resolution (default `most_restrictive`).
- `backend/src/contextedge/services/artifact_binding_service.py` - the approval-to-artifact hash re-check.
- `backend/src/contextedge/services/idempotency_service.py` - the duplicate-step guard.
- `backend/src/contextedge/services/execution_verification_service.py:56` - post-action verification, swept by `evaluation.verify_executions`.

**Main database/model files:**
- `backend/src/contextedge/models/execution.py:33` (ExecutionRun), `:86` (ExecutionStepRun), `:145` (ToolInvocation), `:173` (ApprovalRequest)
- `backend/src/contextedge/models/attempt.py:53` (ExecutionAttempt)
- `backend/src/contextedge/models/playbook.py:48`
- `backend/src/contextedge/models/session.py:11`

**Simple meaning:**
Execution backend starts, tracks, approves, modifies, denies, and completes playbook runs. Be precise in a demo: **ContextEdge has no executor on this branch.** `execution_service` is a governed ledger that an external caller drives through the HTTP routes above; nothing in this repo dispatches a real remediation action (`codewiki/KNOWN_GAPS.md:34`).

## 9. Decisions

**Frontend page:**
- `frontend/src/app/(dashboard)/decisions/page.tsx`
- `frontend/src/components/decisions/decision-chain.tsx`, `frontend/src/components/decisions/decision-detail.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/decisions.py` - similar `:50`, similar aggregate `:93`, effectiveness `:135`, list `:159`, create `:196`, detail `:228`, record outcome `:240`, chain `:268`, provenance `:278`, reject `:304`

**Service files:**
- `backend/src/contextedge/services/decision_trace_service.py:51` (`create_decision`), `:246` (`record_outcome`), `:387` (`get_decision_chain`), `:517` (`find_similar_decisions`), `:733` (`get_decision_provenance`)
- `backend/src/contextedge/services/decision_service.py:21` - the ingest-time extractor that mines decisions out of evidence text.
- `backend/src/contextedge/graph/builder.py:328` - the `based_on` / `considered` / `chose` / `applied_policy` edge writers.

**Main database/model files:**
- `backend/src/contextedge/models/decision.py:75` (Decision), `:175` (DecisionOption), `:208` (DecisionOutcome)
- `backend/src/contextedge/models/session.py:101`

**Simple meaning:**
Decisions backend stores what was decided, why, confidence, evidence, and outcomes. Note for a demo: the page uses list, detail and chain only. `/decisions/{id}/provenance` and `/decisions/effectiveness` are built and tested but have no frontend caller yet.

## 10. Episodes

**Frontend page:**
- `frontend/src/app/(dashboard)/episodes/page.tsx`
- `frontend/src/app/(dashboard)/episodes/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/episodes.py` - list `:40`, detail `:91`, patch `:156`, patch step `:189`, approve `:230`, bulk approve `:282`, reconstruct `:342`, add/remove evidence `:414` / `:459`, delete `:510`, AI review `:556`

**Service/worker files:**
- `backend/src/contextedge/workers/extraction_tasks.py:1400` (`extraction.reconstruct_episode`); the body `_reconstruct` is at `extraction_tasks.py:1008` and runs the gates in order: cluster resolve, minimum cluster size (`MIN_AUTO_SYNTHESIS_CLUSTER = 3`, `extraction_tasks.py:769`), optional resolution gate, per-cluster advisory lock, debounce settle re-check (`RECONSTRUCT_DEBOUNCE_SECONDS = 180`, `:759`, with the `MAX_SYNTHESIS_DELAY_SECONDS = 1_800` starvation guard at `:847`), draft idempotency, growth gate (`MIN_RESYNTHESIS_GROWTH = 0.5`, `:787`), source-role resolution, supersede-on-growth, then synthesis.
- `backend/src/contextedge/services/episode_cluster_service.py:108` - `resolve_episode_cluster` materialises the connected component over `case_links` + `correlation_edges` with `MAX_CLUSTER_SIZE = 50`, `MAX_HOPS = 3`, and a 30-day window from the nearest seed (`episode_cluster_service.py:47`).
- `backend/src/contextedge/ai/extractors/episode_extractor.py:167` - `reconstruct_episode`; 20 evidence items per LLM call, 2,000 chars each (`:44`, `:48`).
- `backend/src/contextedge/ai/extractors/episode_schema.py:118` - `validate_episode`, strict on structure and lenient on vocabulary.
- `backend/src/contextedge/services/episode_service.py:114` - `create_episodes_from_evidence` writes `episodes`, `episode_steps`, `episode_evidence_links`.
- `backend/src/contextedge/services/episode_review_service.py:174` - `ai_review_episode`, the advisory / auto-approve reviewer.
- `backend/src/contextedge/workers/evaluation_tasks.py:129` - the hourly sweep that calls it.
- `backend/src/contextedge/services/issue_signature_service.py:89` - turns an approved episode into a reusable problem fingerprint.

**Main database/model files:**
- `backend/src/contextedge/models/episode.py:213` (Episode - note `cluster_fingerprint` at `:244`, `generation_provenance` at `:254`, `ai_review` at `:261`), `:269` (EpisodeStep), `:292` (EpisodeEvidenceLink)
- `backend/src/contextedge/models/issue_signature.py:30` / `:66`
- `backend/src/contextedge/models/evidence.py:47`

**Simple meaning:**
Episodes backend reconstructs incident stories from evidence. Review mode is a setting with exactly three values - `off`, `advisory`, `auto_approve` (`backend/src/contextedge/config.py:185`). Advisory stamps a verdict on `episodes.ai_review` and approves nothing; auto-approve also needs deterministic floors (at least 2 evidence items, a 20+ char outcome, verdict `approve`, confidence >= 0.8 - `episode_review_service.py:42`). An auto-approved episode keeps `reviewer_user_id` NULL forever, so it is always distinguishable from a human approval.

## 11. Patterns

**Frontend page:**
- `frontend/src/app/(dashboard)/patterns/page.tsx`
- `frontend/src/app/(dashboard)/patterns/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/patterns.py` - list `:41`, detail `:95`, approve `:133`, graph `:163`, evidence links `:190`, discover `:304`, cluster `:412`, deduplicate `:31`, domain audit `:22`

**Service/worker files:**
- `backend/src/contextedge/workers/pattern_tasks.py:418` (`pattern.cluster_episodes`); the body `_cluster` is at `pattern_tasks.py:153`. There is **no beat entry** for clustering - it is dispatched after episode approval (`api/v1/episodes.py:270` and `:330`), after the AI-review sweep's auto-approvals (`workers/evaluation_tasks.py:335`), or manually from `/patterns/cluster`.
- `backend/src/contextedge/ai/extractors/pattern_extractor.py:56` - `validate_pattern_match` (adjudicates "is this episode the same pattern?", and fails open to `is_match=True` at confidence 0.75 during a provider outage - `pattern_extractor.py:112`).
- `backend/src/contextedge/ai/extractors/pattern_extractor.py:18` - `synthesize_pattern`.
- `backend/src/contextedge/services/pattern_service.py:63` - `create_pattern_from_episodes`, including the domain-safety assertion at `:22`. Playbook generation is enqueued through `dispatch_after_commit` (`pattern_service.py:192`, and again on `add_episode_to_pattern` at `:247`), so the task is only sent once the pattern is durable - dispatching inside the transaction used to send tasks naming rows another connection could not yet see.
- `backend/src/contextedge/graph/builder.py:477` - `persist_pattern_enrichment_edges` (trigger / entity / error / root-cause concept nodes).

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py:23` (Pattern), `:60` (PatternEvidenceLink)
- `backend/src/contextedge/models/episode.py:213`

**Simple meaning:**
Patterns backend groups repeated incidents and finds recurring root causes. Two named distance thresholds matter, both cosine and both re-calibrated against the live corpus on 2026-08-19 (`pattern_tasks.py:36-60`):

- `PATTERN_MATCH_MAX_DISTANCE = 0.30` (`pattern_tasks.py:50`) is a **prefilter**: the query orders by distance and takes the pattern owning the single **nearest** member, then the adjudicator decides (`pattern_tasks.py:252-257`). The `ORDER BY` is the point - without it the query returned an arbitrary qualifying pattern and the adjudicator rejected almost all of them.
- `CLUSTER_GROUP_MAX_DISTANCE = 0.27` (`pattern_tasks.py:60`) decides which unlinked episodes group into a **new** pattern (`pattern_tasks.py:309`). An empty group is allowed and becomes a single-episode pattern (`pattern_tasks.py:316`).

## 12. Playbooks

**Frontend page:**
- `frontend/src/app/(dashboard)/playbooks/page.tsx`
- `frontend/src/app/(dashboard)/playbooks/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/playbooks.py` - list `:81`, create `:206`, detail `:239`, references `:250`, patch `:403`, transition `:465`, versions `:505`, create version `:515`, version diff `:544`, rollback `:613`, generate `:654`

**Service/AI files:**
- `backend/src/contextedge/workers/pattern_tasks.py:442` (`pattern.generate_playbook_candidate`) - the governed generation path.
- `backend/src/contextedge/ai/generators/playbook_generator.py:17` - `generate_playbook_candidate`; prompt assembly starts at `:40`. Three deterministic passes run over the model's JSON before anything is persisted (`playbook_generator.py:91-93`): `validate_source_refs` (`:331`) drops citations the model invented, `classify_step_grounding` (`:256`) forces any step without a surviving citation to `best_practice`, and `sanitize_branching_logic` (`:154`) drops decision points that cannot execute - targets naming steps that do not exist, branches whose true and false paths are identical, and steps no path can reach. The last one repairs rather than rejects: the steps are usually fine and only `decision_points` is junk.
- `backend/src/contextedge/ai/prompts/playbook.py:415` - current default prompt version **v6** (`version="v6"` at `:418`, registered with `default=True`).
- `backend/src/contextedge/services/knowledge_retrieval_service.py:226` - `retrieve_knowledge_for_pattern`, the RAG step that pulls KB articles and SOPs into the prompt.
- `backend/src/contextedge/services/playbook_service.py:360` - `create_playbook_version` (step-binding validation, evidence link materialisation, `current_version_id` repoint).
- `backend/src/contextedge/services/playbook_embedding.py:79` - `embed_playbook`, the semantic fingerprint used by Runtime and the agent seed resolver; the text it embeds is built at `:54`.

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py:48` (Playbook), `:125` (PlaybookVersion), `:177` (PlaybookEvidenceLink), `:211` (PlaybookApproval)
- `backend/src/contextedge/models/pattern.py:23`

**Simple meaning:**
Playbooks backend stores approved recovery steps and generated candidate playbooks. The manual `POST /playbooks/generate` route is deliberately leaner than the worker path: it skips knowledge retrieval, the pattern-confidence floor, the deterministic risk floor (it takes the model's `risk_tier` and defaults to `medium`), the empty-steps guard and playbook embedding (`api/v1/playbooks.py:654`). It also builds its episode summaries without ids, so every `[ep-N]` citation is dropped as unresolvable. Use the worker path when demonstrating grounded generation - and note that the Drift tab's "Verify & Regenerate" button posts to this same manual route (`frontend/src/app/(dashboard)/drift/page.tsx:23`).

## 13. Negative Knowledge

**Frontend page:**
- `frontend/src/app/(dashboard)/negative-knowledge/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/negative_knowledge.py` - list `:17`, create `:36`, patch `:58`, delete `:83`

**Related files:**
- `backend/src/contextedge/search/hybrid_ranker.py:140` - `_negative_penalty_for_playbook` subtracts score for `contradicts` edges and for domain negative-knowledge count.
- `backend/src/contextedge/workers/pattern_tasks.py:537` - up to 20 entries for the pattern's domain are read into the playbook-generation prompt.

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py:87` (NegativeKnowledgeItem)

**Simple meaning:**
Negative Knowledge backend stores what not to do, and it is consumed in two places: it lowers a playbook's ranking score, and it is written into the generation prompt so new candidates avoid the same step.

## 14. Identities

**Frontend page:**
- `frontend/src/app/(dashboard)/identities/page.tsx`
- Needs-review items also surface on `frontend/src/app/(dashboard)/suggestions/page.tsx:206`

**Backend files:**
- `backend/src/contextedge/api/v1/identities.py` - list `:32`, patch `:71`, merge `:162`, merge proposals `:186`, decide proposal `:250`

**Service/worker files:**
- `backend/src/contextedge/services/identity_service.py:810` - `link_evidence_identities`, the ingest entry point.
- `backend/src/contextedge/services/identity_service.py:616` - `resolve_extracted_entities`, the four resolution layers: strong identifier (1.0), typed exact alias (0.95), LLM adjudication (auto-links only at or above `AUTO_LINK_THRESHOLDS`, `identity_service.py:58` - person 0.95, everything else 0.9), then a `provisional` new identity at 0.5.
- `backend/src/contextedge/services/identity_candidacy.py:179` - `identity_rejection_reason`, the gate that rejects non-names and facet-shaped values before any LLM call.
- `backend/src/contextedge/services/identity_normalizer.py:81` - normalisation, including the rule that turns a single-token device name like `vpn-gw-east-01` into a `hostname` strong identifier (`identity_normalizer.py:134`).
- `backend/src/contextedge/services/identity_promotion.py:72` - promotes a provisional identity to `resolved` once at least 2 and at most 5 distinct evidence items cite it (`CORROBORATION_DEGREE_MIN` at `:58`, `RARE_DEGREE_MAX` at `:65`).
- `backend/src/contextedge/services/identity_reconciliation_service.py:306` - `reconcile_identities`, the daily pass; it **proposes** merges only, at confidence >= 0.95 (`MIN_CONFIDENCE`, `:68`). `decide_proposal` at `:386` is what a human calls.
- `backend/src/contextedge/workers/identity_tasks.py:147` (daily reconcile), `:72` (rebuild cached refs after a merge).

**Main database/model files:**
- `backend/src/contextedge/models/episode.py:48` (CanonicalIdentity), `:91` (IdentityAlias), `:152` (EvidenceIdentityLink), `:330` (IdentityMergeProposal)

**Simple meaning:**
Identities backend connects different names for the same real user, system, mailbox, workflow, or device. Nothing merges automatically - the daily job files a proposal and a human decides.

## 15. Correlations

**Frontend page:**
- `frontend/src/app/(dashboard)/correlations/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/correlations.py` - list `:26`, create `:51`, decision (accept/reject/split/merge) `:263`, delete `:330`

**Service/worker files:**
- `backend/src/contextedge/workers/correlation_tasks.py:16` (`extraction.correlate_evidence`); when it creates edges it schedules `extraction.reconstruct_episode` with a 180-second countdown, after the commit (`correlation_tasks.py:48`).
- `backend/src/contextedge/services/correlation_service.py:197` - `correlate_evidence_item`, two tiers: deterministic case links at confidence 1.0 (`extract_case_link_candidates`, `correlation_service.py:116`) and gated identity co-occurrence (7-day window, hub-degree guard, rare-entity boost - constants at `correlation_service.py:36`).
- `backend/src/contextedge/services/ticket_bridge_service.py:324` - `_add_membership`, the ticket-number bridge that puts a quoting email into the right case.
- `backend/src/contextedge/services/servicenow_reference_service.py`, `jira_reference_service.py`, `sapphireims_reference_service.py`, `zoho_desk_reference_service.py` - typed reference enrichment, each in its own SAVEPOINT so a failure loses enrichment and never the correlation.

**Main database/model files:**
- `backend/src/contextedge/models/episode.py:187` (CorrelationEdge)
- `backend/src/contextedge/models/session.py:148` (CaseLink)
- `backend/src/contextedge/models/case_bridge.py:32` (CaseIdentifier), `:60` (EvidenceCaseMembership), `:92` (PendingIdentifierMention)
- `backend/src/contextedge/models/evidence.py:47`

**Simple meaning:**
Correlations backend links related evidence items.

## 16. Review Queues (suggestions)

**Frontend page:**
- `frontend/src/app/(dashboard)/suggestions/page.tsx` - three panels: semantic correlation suggestions, fleet-group suggestions, and identities in `needs_review`.

**Backend files:**
- `backend/src/contextedge/api/v1/correlations.py:172` (list), `:212` / `:223` (accept / reject), `:152` (stats)
- `backend/src/contextedge/api/v1/correlations.py:70` (fleet suggestions), `:125` / `:138` (accept / reject)
- `backend/src/contextedge/api/v1/identities.py:32` with `resolution_state=needs_review`, `:71` to resolve one

**Service/worker files:**
- `backend/src/contextedge/services/correlation_suggestion_service.py`
- `backend/src/contextedge/workers/suggestion_tasks.py:26` - dispatched after chunk embeddings land (`workers/chunk_tasks.py:261`).
- `backend/src/contextedge/services/fleet_group_service.py` and `backend/src/contextedge/workers/fleet_tasks.py:41` (every 1,800s).

**Main database/model files:**
- `backend/src/contextedge/models/correlation_suggestion.py:24`
- `backend/src/contextedge/models/fleet_group.py:15`
- `backend/src/contextedge/models/episode.py:48`

**Simple meaning:**
This tab is the "machine is not sure" inbox. Nothing here has been applied to the graph yet; accepting a row is what writes the edge or resolves the identity. Every route on it calls `require_role("knowledge_manager")`, and `has_role` short-circuits to true for `platform_super_admin`, `tenant_admin` and `admin` (`deps.py:37`) - so those three get in as well. `domain_admin` does **not**: it is not one of the short-circuit roles, so a domain admin needs an explicit `knowledge_manager` binding.

## 17. Graph Explorer

**Frontend page:**
- `frontend/src/app/(dashboard)/graph-explorer/page.tsx`
- `frontend/src/lib/graph-api.ts:20` (stats), `:24` (subgraph), `:44` (neighbors)
- `frontend/src/components/graph/*` - including `edge-proposals.tsx:31`, the review surface for agent-proposed dependencies

**Backend files:**
- `backend/src/contextedge/api/v1/graph.py` - agent subsets `:18`, CMDB topology `:34`, fix applicability `:79`, change risk `:100`, edge proposals `:120` / `:142` / `:167`, neighbors `:190`, subgraph `:220`, stats `:242`

**Graph/service files:**
- `backend/src/contextedge/graph/queries.py:20` - `get_neighbors`, bounded BFS to `MAX_TRAVERSAL_DEPTH = 3`; subgraph payloads cap at 250 nodes / 500 edges (`queries.py:16`).
- `backend/src/contextedge/graph/builder.py:50` - `ensure_edge`, the idempotent writer (SELECT, then `INSERT ... ON CONFLICT DO NOTHING` against `uq_graph_edges_active_logical`). `weight` means traversal importance; `confidence` means belief.
- `backend/src/contextedge/graph/edge_types.py:137` - `EDGE_TYPES`, the union of five semantic groups holding 69 valid edge types; `require_registered` (`edge_types.py:186`) refuses anything else.
- `backend/src/contextedge/graph/temporal.py:29` - `edge_valid_at` for point-in-time reads.
- `backend/src/contextedge/graph/agent/materializer.py:107` - `reconcile_tenant`, the relational-to-graph projection run every 6 hours (class at `materializer.py:54`).
- `backend/src/contextedge/graph/agent/service.py:108` (`project`), `repository.py:169` (`resolve_seeds`), `selector.py:28` (traversal and budget), `hydrators.py:118` (`node_is_visible`, fail-closed per node type), `profiles.py:179` (the `maf.v1` profile).

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py:174` (GraphEdge)
- `backend/src/contextedge/models/entity.py:65` (Entity)
- plus every node-type table listed in `backend/src/contextedge/graph/agent/hydrators.py:33`

**Simple meaning:**
Graph Explorer backend shows relationships between evidence, sessions, decisions, playbooks, identities, and policies. Worth stating in a review: `/graph/agent-subsets` builds a fully scoped, budgeted projection, while `/graph/neighbors`, `/graph/subgraph` and `/graph/stats` scope by tenant plus an optional caller-supplied `domain_id` query parameter, and nothing checks that parameter against the caller's own scope (`api/v1/graph.py:190`, `:220`, `:242`). `codewiki/KNOWN_GAPS.md:56` records the same shape as open item P1-6 for the CMDB topology / change-risk / fix-applicability routes.

One nuance in the agent projection that is easy to state backwards: episode visibility is **not** approved-only. `AGENT_VISIBLE_EPISODE_STATES` admits `approved` and `pending_review` (`hydrators.py:108`), an unapproved draft is relabelled `[UNAPPROVED DRAFT]` and carries an `agent_caveat` (`hydrators.py:448`, `:463`), and drafts get their own two seed slots at a 0.8 relevance multiplier so they can never evict an approved precedent (`repository.py:111`, `:117`).

## 18. Contradictions

**Frontend page:**
- `frontend/src/app/(dashboard)/contradictions/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/contradictions.py` - list `:17`, status `:37`

**Service/worker files:**
- `backend/src/contextedge/services/contradiction_service.py` - `scan_contradictions`
- `backend/src/contextedge/workers/evaluation_tasks.py:88` - the beat sweep, every 12 hours. It makes LLM calls, so it is a real cost line.

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py:105` (Contradiction), `:123` (ContradictionScanState)
- `backend/src/contextedge/models/playbook.py:48`
- `backend/src/contextedge/models/evidence.py:47`

**Simple meaning:**
Contradictions backend finds conflicting evidence or playbook claims.

## 19. Drift

**Frontend page:**
- `frontend/src/app/(dashboard)/drift/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/drift.py:19`

**Service files:**
- `backend/src/contextedge/services/drift_service.py` - `check_playbook_drift`
- `backend/src/contextedge/workers/evaluation_tasks.py:41` - the beat sweep, every 6 hours

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py:48`
- `backend/src/contextedge/models/pattern.py:23`
- `backend/src/contextedge/models/decision.py:75`
- `backend/src/contextedge/models/evaluation.py:42` (negative retrieval feedback)

**Simple meaning:**
Drift backend detects when old playbooks or patterns may no longer be safe/current. The freshness signal it shares with Runtime is in `backend/src/contextedge/search/hybrid_ranker.py:382`: expired scores 0, never-validated scores 0.5, otherwise it decays linearly over 180 days.

## 20. Evaluations

**Frontend page:**
- `frontend/src/app/(dashboard)/evaluations/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/evaluations.py` - datasets `:50` / `:60`, runs `:75` / `:86` / `:105`

**Service/worker files:**
- `backend/src/contextedge/services/evaluation_service.py:134` - replays each case through the same `rank_playbooks` the live Runtime tab uses.
- `backend/src/contextedge/workers/evaluation_tasks.py:18`

**Main database/model files:**
- `backend/src/contextedge/models/evaluation.py:11` (EvaluationDataset), `:25` (EvaluationRun)

**Simple meaning:**
Evaluations backend tests quality of retrieval, recommendations, and generated results. Because it calls the production ranker, an evaluation run is the honest way to check a ranking change.

## 21. Policies

**Frontend page:**
- `frontend/src/app/(dashboard)/policies/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/policies.py` - grouped list `:57`, create `:83`, patch `:120`, delete `:148`
- `backend/src/contextedge/api/v1/policy_assignments.py` - list `:66`, assign `:119`, unassign `:141`
- `backend/src/contextedge/api/v1/action_policies.py` (mounted at `/api/v1/action-policies`) - the per-action policy table used by execution

**Service files:**
- `backend/src/contextedge/services/policy_assignment.py:12` - `assert_policy_assignment` validates type and tenant.
- `backend/src/contextedge/services/approval_policy_service.py:12` - the four enforced keys: `approver_roles`, `forbid_self_approval`, `require_approval_min_safety_class`, `max_automation_mode`.
- `backend/src/contextedge/services/action_policy_service.py` - scope filter, specificity, conflict resolution.
- `backend/src/contextedge/services/policy_check_service.py:34` - the append-only evaluation record.

**Main database/model files:**
- `backend/src/contextedge/models/policy.py:31` (TenantPolicy - `policy_type` is one of retention / classification / access / approval), `:70` (PolicyCheck)
- `backend/src/contextedge/models/action_policy.py:54` (ActionPolicy)

**Simple meaning:**
Policies backend controls access, retention, classification, and approval rules. `TenantPolicy.version` bumps only when `config` changes, never on a rename or a deactivate, because the version tracks rules rather than labels (`api/v1/policies.py:133`).

## 22. Audit Log

**Frontend page:**
- `frontend/src/app/(dashboard)/audit/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/audit.py:14` (mounted at `/api/v1/audit-logs`)

**Service/middleware files:**
- `backend/src/contextedge/middleware/request_audit.py:25` - automatic capture of every mutating `/api/v1` request, with outcome `success` / `denied` / `failed`. The insert runs on a separate sync engine off-thread and swallows its own errors, so auditing can never break a request.
- `backend/src/contextedge/middleware/audit.py:10` - `log_audit_event`, the explicit call used where a business action deserves its own row (for example sync pause/cancel at `api/v1/sources.py:354`).
- `backend/src/contextedge/services/event_log_service.py:32` - `append_operational_event`, the separate machine-readable event stream (`llm.usage`, `correlation.case_linked`, `episode.ai_approved`, and so on).

**Main database/model files:**
- `backend/src/contextedge/models/audit.py:11` (AuditLog)
- `backend/src/contextedge/models/events.py:13` (OperationalEvent)

**Simple meaning:**
Audit backend records who did what and when. Unauthenticated 401 probes never resolve a tenant, so they exist only in the structured log line `http.mutating_request`, not in the table.

## 23. LLM Cost

**Frontend page:**
- `frontend/src/app/(dashboard)/admin/cost/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/admin_cost.py` - usage `:33`, read budget `:102`, write budget `:113`, budget status `:137`

**Service files:**
- `backend/src/contextedge/services/admin_cost_service.py:64` (`_estimate_cost`), `:75` (`get_llm_usage` - totals, per model/task breakdown, cache-hit rate, reasoning share)
- `backend/src/contextedge/services/tenant_budget_service.py:234` - `check_budget`, called before every LLM and embedding call.
- `backend/src/contextedge/ai/observability.py:133` - `record_llm_usage`, the single recorder: Prometheus counters, one `llm.usage` log line, and one operational event.

**Main database/model files:**
- `backend/src/contextedge/models/events.py:13` (OperationalEvent - `llm.usage` rows are the only source of truth for spend)
- `backend/src/contextedge/models/tenant.py:116` (TenantLLMBudget)

**Simple meaning:**
LLM Cost backend tracks AI token usage, cost, and budget limits. A tenant with no budget row still gets the deployment defaults - 2,000,000 tokens/day, $25/day, action `block` (`backend/src/contextedge/config.py:194-198`). Cost numbers are estimates for dashboard use; the provider's bill is authoritative.

## 24. Pipeline Health

**Frontend page:**
- `frontend/src/app/(dashboard)/admin/pipeline/page.tsx:140`

**Backend files:**
- `backend/src/contextedge/api/v1/admin_cost.py:166`

**Service files:**
- `backend/src/contextedge/services/pipeline_health_service.py:87` - `get_pipeline_health`: a Redis `LLEN` per queue in pipeline order plus `HLEN unacked` for in-flight work, and one SQL roll-up counting the chain end to end (evidence -> embedded -> identities -> correlations -> episodes -> patterns -> playbooks). Backlog alert threshold is 500 (`pipeline_health_service.py:55`).

**Main database/model files:**
- none of its own; it reads Redis and aggregates existing tables

**Simple meaning:**
Pipeline Health is the operator view of the eight queues. Read it as "find the first zero in the chain" - that is where the pipeline stopped. In-flight work matters as much as queue depth: debounced episode reconstructions sit in `unacked` for minutes while every queue reads zero. `tenant_admin` only.

## 25. Settings

**Frontend page:**
- `frontend/src/app/(dashboard)/settings/page.tsx` - tabs are General, Workspaces, Domains, Users, Retention (`settings/page.tsx:280`)

**Backend files:**
- `backend/src/contextedge/api/v1/tenants.py:14`
- `backend/src/contextedge/api/v1/workspaces.py:14`
- `backend/src/contextedge/api/v1/domains.py:14`
- `backend/src/contextedge/api/v1/users.py:22` (users), `:111` (assign role), `:140` (list roles), `:151` (remove role)

**Main database/model files:**
- `backend/src/contextedge/models/tenant.py:12` (Tenant), `:30` (Workspace), `:48` (Domain), `:68` (User), `:88` (RoleBinding)

**Simple meaning:**
Settings backend manages tenant, workspace, domain, and user configuration. Two honest caveats for a KT session: the **Retention** tab is a pointer, not a console - it says retention is managed through the policies API and backend defaults (`settings/page.tsx:398`); and `RoleBinding.scope_type` / `scope_id` are stored but not enforced, because `has_role` is a name check (`backend/src/contextedge/deps.py:37`). A domain admin bound to one domain effectively holds that role tenant-wide. Narrower scope exists only through service-token `allowed_domain_ids` on routes that consult it.

## 26. Entity Inventory (no sidebar entry)

**Frontend page:**
- `frontend/src/app/(dashboard)/inventory/[id]/page.tsx` - reached from the source detail page (`sources/[id]/page.tsx:216`), not from the sidebar

**Backend files:**
- `backend/src/contextedge/api/v1/sources.py:219` (list source objects), `:237` (approve for backfill / sync), `:386` (start a backfill)
- `backend/src/contextedge/api/v1/inventory.py` (mounted at `/api/v1/inventory`)

**Main database/model files:**
- `backend/src/contextedge/models/source.py:55` (SourceObject - `approved_for_backfill`, `approved_for_sync`, `last_checkpoint_at`, `metadata_extra`)

**Simple meaning:**
This is where a discovered object (a ServiceNow table, a Zoho module, a mailbox) is approved for backfill or scheduled sync. Nothing syncs until it is approved here - which is exactly why the Zoho `articles` module is discovered on the live tenant but has never ingested a single article (`codewiki/KNOWN_GAPS.md:36`).

## Demo Explanation

Use this short line:

Each tab has a frontend page for display and a backend API file for data. The API file calls service logic, and service logic reads or writes database model files. The slow work - syncing, normalising, chunking, correlating, narrating, clustering, generating - happens in Celery tasks, so when a screen looks empty, check the queue before you check the API.
