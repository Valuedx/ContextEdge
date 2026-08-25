# ContextEdge - Runbook

Operational guide for running, maintaining, and troubleshooting ContextEdge after initial setup.

For first-time local installation, use [SETUP_GUIDE.md](SETUP_GUIDE.md). For architecture, see [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md). For HTTP behavior and auth semantics, see [API.md](API.md).

*Verified against the working tree on 2026-08-19. Load-bearing statements carry a `file:line` citation. If a citation and the code disagree, the code wins — fix the doc.*

---

## 1. Scope

This runbook covers:

- environment and configuration checks
- Docker and host-run operational commands
- migrations and seed data
- workers, queues, and the beat schedule
- the pipeline stages an operator has to reason about (chunking, correlation, episodes, AI review, signatures, patterns)
- health, logs, retention, and troubleshooting

It does not repeat the full onboarding flow from [SETUP_GUIDE.md](SETUP_GUIDE.md).

---

## 2. Prerequisites

- Docker with Compose support
- Python 3.12+
- Node.js 20+
- A populated `.env` file at the repo root

If you are starting from scratch, go to [SETUP_GUIDE.md](SETUP_GUIDE.md) first.

---

## 3. Configuration Checklist

Settings are loaded through `contextedge.config.Settings`, which reads the repo-root `.env` then `backend/.env` and ignores unknown keys (`backend/src/contextedge/config.py:10-15`). Both files are read; the later one (`backend/.env`) wins on any key set in both, which is worth remembering before you spend an hour on a value that will not change.

| Area | Representative variables |
| --- | --- |
| Database | `DATABASE_URL` (asyncpg), `DATABASE_URL_SYNC` (used by Alembic) |
| Redis / Celery | `REDIS_URL` (db 0), `CELERY_BROKER_URL` (db 1), `CELERY_RESULT_BACKEND` (db 2) |
| Object storage | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET` |
| Auth | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `SERVICE_TOKENS_JSON` |
| Crypto | `FERNET_KEY` |
| AI routing | `DEFAULT_LLM_PROVIDER`, `DEFAULT_CLASSIFICATION_MODEL`, `DEFAULT_EXTRACTION_MODEL`, `PATTERN_MODEL`, `PLAYBOOK_MODEL`, `DEFAULT_EMBEDDING_MODEL`, `LLM_FALLBACK_MODEL` |
| AI cost containment | `LLM_NUM_RETRIES` (2), `LLM_MAX_OUTPUT_TOKENS` (4096), `LLM_TASK_OUTPUT_TOKENS`, `LLM_THINKING_BUDGETS`, `EMBEDDING_MAX_BATCH_SIZE` (64), `DEFAULT_DAILY_TOKEN_LIMIT` (2,000,000), `DEFAULT_DAILY_COST_CAP_USD` (25.0), `DEFAULT_BUDGET_ACTION_ON_EXCEED` (`block`) |
| Pipeline gates | `EPISODE_RESOLUTION_GATE` (`off` \| `cluster`), `EPISODE_AI_REVIEW` (`off` \| `advisory` \| `auto_approve`), `REDACTION_ENABLED` (true), `DOCUMENT_VISION_ENABLED` (true) |
| Retention | `RETENTION_PURGE_MODE` (`soft_purge`), `RETENTION_DEFAULT_DAYS` (365) |
| Notifications | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `NOTIFICATION_WEBHOOK_URL` — all empty by default, so those channels are explicit no-ops |
| Prompt A/B | `TENANT_PROMPT_VARIANTS_JSON` — `{"<tenant-uuid>": {"relevance": "v2"}}` |
| App | `APP_ENV`, `APP_DEBUG`, `APP_LOG_LEVEL`, `APP_CORS_ORIGINS`, `FRONTEND_URL` |

Two settings fail fast at import time rather than misbehaving later, both when `APP_ENV` is not `development`: the default `JWT_SECRET_KEY` raises `RuntimeError` (`config.py:248-252`), and a missing or placeholder `FERNET_KEY` does the same, because source credentials encrypted under a throwaway key are unrecoverable (`config.py:254-264`).

Compose note:

- `docker-compose.dev.yml` overrides service URLs to use container hostnames such as `postgres`, `redis`, and `minio`.
- Host-run workflows should use `localhost` values from `.env`.

---

## 4. Starting and Stopping Services

### Infrastructure only

```bash
make up
```

Equivalent:

```bash
docker compose up -d
```

### Full Docker development stack

```bash
make dev
```

Equivalent:

```bash
docker compose -f docker-compose.dev.yml up --build
```

### Stop stacks

Infrastructure:

```bash
make down
```

Full Docker dev stack:

```bash
make dev-down
```

Logs:

```bash
make logs
```

---

## 5. Database Migrations

Revisions live in `backend/alembic/versions/`.

**The standing rule: never trust a revision number written in a document.** The chain moves most weeks. Run `alembic heads` and believe its output; if the API or a worker complains about the schema, compare that against `alembic current` before you debug application code.

Operationally notable revisions, for when you need to know *why* something exists:

| Revision | Why an operator cares |
| --- | --- |
| `0001_initial_schema` | Builds from model metadata, not a frozen DDL snapshot — fresh installs never reproduce historical drift bugs. See [MIGRATIONS.md](MIGRATIONS.md). |
| `0007_fts_gin_indexes` | Stored `search_tsvector` columns and GIN indexes on `evidence_items` and `playbooks`. No FTS results without it. |
| `0008_resolution_sessions` | `resolution_sessions` + `decision_trace_events`. Sessions API 404s without it. |
| `0010_operational_events` | The append-only event ledger carrying request / correlation / causation ids. This is where `llm.usage` lives. |
| `0023_tenant_llm_budgets` | Per-tenant daily spend caps. Without a row, deployment defaults apply. |
| `0026_dedup_uniqueness` | Unique indexes on `(tenant_id, content_hash)` and `(tenant_id, source_a_ref, source_b_ref)`. **Pre-migration dedupe required** on older deployments — §5.1. |
| `0027_playbook_evidence_link_fk` | FK `playbook_evidence_links.evidence_id → evidence_items(id) ON DELETE SET NULL`. **Pre-migration NULLing required** if past hard-deletes left dangling ids — §5.1. |
| `0030_evidence_chunks` | The `evidence_chunks` sibling table plus the partial index `ix_evidence_items_chunked_at_null` that a future backfill will drive. |
| `0032_halfvec_hnsw_indexes` | The **real** vector indexes: HNSW *expression* indexes over `(embedding::halfvec(3072))` on `evidence_items`, `evidence_chunks`, `decisions`, `episodes`. Requires the pgvector server extension at 0.7+. See §7.7. |
| `0045` | `issue_signatures` + `episode_issue_signatures` — recurrence fingerprints (§7.6). |
| `0067` / `0068` | `knowledge_state` on evidence; `case_state` and `source_facets`. Rows ingested before these stay NULL until their next sync. |
| `0069_sync_run_control` | `sync_runs.control` and `celery_task_id` — the columns behind cooperative pause / cancel / resume (§7.9). |
| `0070_episode_ai_review` | `episodes.ai_review` — where the AI reviewer stamps its verdict (§7.5). |

Apply all pending migrations:

```bash
make migrate
```

Equivalent:

```bash
cd backend
alembic upgrade head
```

Create a new migration:

```bash
make migrate-new msg="short description"
```

Rollback one revision:

```bash
make migrate-down
```

Important notes:

- Prefer Alembic for shared environments; do not rely on ad hoc table creation.
- Before running anything online, `alembic/env.py` widens the legacy `alembic_version.version_num` column to `VARCHAR(255)` on a **separate bootstrap connection** with its own commit (`backend/alembic/env.py:70-72`). Doing it on the migration connection made Alembic see a transaction it did not start and `alembic upgrade` reported success while changing nothing. Older databases created that column as `VARCHAR(32)`, and several revision ids in this chain are longer, so without the widening the *stamp* fails with "value too long" and looks like a broken migration. Offline `--sql` mode has no connection to widen, so this does not apply there.
- If the API reports missing tables or columns, verify the current Alembic head before debugging application code. Both the API (`/ready`) and every worker (at startup) refuse to serve against a schema behind the code — §8 and §7.2.

### 5.1 Pre-migration dedupe for `0026` / `0027`

Deployments that ran under the old check-then-insert race (before migration `0026` shipped the unique indexes) may have accumulated duplicate rows that would cause `CREATE UNIQUE INDEX` to fail. Run this once, per tenant or across the DB, before `alembic upgrade`:

```sql
-- Evidence dedup — keep the oldest by ingested_at
WITH dups AS (
  SELECT id, ROW_NUMBER() OVER (
    PARTITION BY tenant_id, content_hash ORDER BY ingested_at ASC, id ASC
  ) AS rn
  FROM evidence_items
  WHERE content_hash IS NOT NULL
)
DELETE FROM evidence_items WHERE id IN (SELECT id FROM dups WHERE rn > 1);

-- Contradictions dedup — keep oldest by created_at
WITH dups AS (
  SELECT id, ROW_NUMBER() OVER (
    PARTITION BY tenant_id, source_a_ref, source_b_ref ORDER BY created_at ASC, id ASC
  ) AS rn
  FROM contradictions
)
DELETE FROM contradictions WHERE id IN (SELECT id FROM dups WHERE rn > 1);
```

For migration `0027` (FK addition to `playbook_evidence_links.evidence_id`), NULL any orphaned references first so the new FK doesn't reject them:

```sql
UPDATE playbook_evidence_links
SET evidence_id = NULL
WHERE evidence_id IS NOT NULL
  AND evidence_id NOT IN (SELECT id FROM evidence_items);
```

Fresh deployments that never ran under the old model can skip this step — the indexes / FKs apply cleanly.

---

## 6. Seed Data

Run:

```bash
make seed
```

Equivalent:

```bash
cd backend
python dev.py seed
```

Seeded development users:

Accounts are stored in the database. Create them in Settings, or set `SEED_*`
environment variables when running seed. Passwords are hashed; they are not
printed or hardcoded in the app.

The seed script is idempotent for the default tenant slug and will skip if the default tenant already exists. The destructive variants (`reset_db_and_seed`, `demo_maf_seed`) TRUNCATE tenant-global tables and are refused unless `APP_ENV=development` or `CONTEXTEDGE_ALLOW_DB_RESET=1` (`backend/src/contextedge/seed_guard.py:20-60`).

---

## 7. Host-Run Services

With infrastructure running:

| Goal | Command |
| --- | --- |
| API reload server | `make backend-dev` |
| Celery worker (all queues) | `make celery-dev` |
| Celery beat | `make celery-beat-dev` |
| Frontend dev server | `make frontend-dev` |

Equivalent direct commands:

- API: `cd backend && python dev.py api`
- Worker: `cd backend && python dev.py worker`
- Beat: `cd backend && python dev.py beat`
- Frontend: `cd frontend && npm run dev`

The `python dev.py ...` launcher adds `backend/src` to `PYTHONPATH` automatically and uses the active interpreter (`backend/dev.py:19-26`). If `contextedge` still cannot be imported, return to [SETUP_GUIDE.md](SETUP_GUIDE.md) and verify the interpreter path, Python version, and dependency install before retrying.

### 7.1 Queues — all eight of them

```
default   sync   hydration   extraction   correlation   embedding   pattern   evaluation
```

`python dev.py worker` consumes every one of them by default (`backend/dev.py:16`). The routing table is `backend/src/contextedge/workers/celery_app.py:226-279` and is **matched in order**, so an earlier specific key beats a later wildcard. Anything the table does not match falls to `task_default_queue="default"` (`celery_app.py:280`).

| Route key | Queue | Why it is separate |
| --- | --- | --- |
| `sync.*` | `sync` | Isolation from the extraction backlog. |
| `hydration.*` | `hydration` | Source-side rate limits are per-connector, so this lane is the one you throttle. |
| `extraction.classify_relevance` | `default` | Fast lane. A ~2.5 s gate call must not queue behind 20-60 s episode work; 500 classifications once starved ~40 minutes inside extraction's FIFO. |
| `extraction.correlate_evidence`, `extraction.reconstruct_episode`, `extraction.compute_evidence_baseline` | `correlation` | Graph lane, added 2026-08-17. When these shared `extraction`, the extraction queue grew ~70 tasks/minute at 8,255 deep and correlation was dispatched but never once received: 193 evidence items in, zero episodes, patterns, or playbooks out. |
| `extraction.chunk_evidence`, `extraction.embed_chunks_batch` | `embedding` | Retrieval lane, same root cause, worse symptom. 1,879 chunks existed with 289 embedded (15%) while 309 embed tasks waited behind 10,226 normalizations. Nothing errored — the evidence was ingested and silently unsearchable. |
| `extraction.*`, `artifact.*` | `extraction` | Normalization and attachment parsing. |
| `pattern.*` | `pattern` | Clustering, playbook generation, dedup — all whole-graph operations, deliberately serialized (§7.2). |
| `evaluation.*` | `evaluation` | Drift, contradictions, retention, verification, AI review, issue signatures. |
| `review_queue.*` | `default` | Explicit so the short-named task is not swallowed by the module-path catch-all. |
| `contextedge.workers.*` | `default` | Fallback for tasks still using full module paths. |

Two families use short names with no matching route and therefore land on the default queue: `identity.*` (daily identity reconciliation) and `maintenance.*`. If a document tells you identity reconciliation runs on `evaluation`, it is wrong.

> **If you take one thing from this section:** a worker fleet started with a hand-written `-Q extraction,hydration,default` will normalize evidence forever and never build a graph or embed a chunk, and nothing will report an error. Older revisions of this runbook contained exactly that command. Always list all eight, or let `dev.py` do it.

### 7.2 Worker topology (Windows)

Run **two worker groups** with different roles, plus exactly one beat.

Pool constraints, both measured, both non-obvious:

- **Prefork is unusable on Windows.** This is a Celery/Windows fact, not a ContextEdge one.
- **`-P threads` is unusable for any lane that makes LLM calls.** litellm holds asyncio locks bound to the loop that created them, so a threads pool raises "Lock is bound to a different event loop" on every enrichment call, which trips the provider circuit breaker and fails the run near-silently. Measured on a live backfill, 2026-08-16. Parallelism therefore has to come from **processes**, each with its own event loop.

```powershell
# Worker A — the parallel one. Ticket processing is ~95% waiting on the LLM,
# so process parallelism is close to linear. Start N solo workers with
# distinct node names. Note the queue list: correlation and embedding are
# in it, because those two lanes are where the graph and the search index
# actually get built.
1..4 | ForEach-Object {
  Start-Process python -ArgumentList "-m","celery","-A","contextedge.workers.celery_app",`
    "worker","-l","INFO","-n","workerA$_@%h",`
    "-Q","extraction,correlation,embedding,hydration,default","-P","solo"
}

# Worker B — the serialized one. Clustering and playbook generation operate on
# the whole graph and have no advisory lock (unlike sync), so two concurrent
# runs could mint duplicate patterns. Solo costs nothing here — these tasks
# are rare, and the hourly dedup sweep deliberately rides the same queue so it
# serializes behind clustering.
python -m celery -A contextedge.workers.celery_app worker -l INFO -n workerB@%h -Q sync,pattern,evaluation -P solo

# Beat — ONE instance only. A second beat double-dispatches every scheduled task.
python -m celery -A contextedge.workers.celery_app beat -l INFO
```

Via the launcher (`dev.py` defers to a caller-supplied `-P` / `-Q`):

```powershell
python dev.py worker -Q extraction,correlation,embedding,hydration,default -P solo   # Worker A, run 2-4 of these
python dev.py worker -Q sync,pattern,evaluation                                      # Worker B (solo default on Windows)
python dev.py beat
```

**Why this split is safe:**

- Every task body runs through `run_async`, which creates a **fresh NullPool engine and session per task**, commits on success, rolls back on exception, then closes and disposes the engine (`backend/src/contextedge/workers/asyncio_runner.py:10-34`). No event loop and no connection is ever shared across tasks. This is also why the API side uses a pooled engine and the workers do not.
- Syncs take a per-source-object Postgres advisory lock, so a second worker skips rather than racing a checkpoint (§7.9).
- `task_acks_late=True` means a crashed worker's task is re-delivered rather than lost (`workers/celery_app.py:192-200`).
- The broker connection retries forever with keepalive and 30-second health checks (`workers/celery_app.py:216-224`). On the Windows dev box the broker is reached through WSL's port relay, which drops TCP connections under concurrent load; one blip previously killed four of eight workers silently. Now a blip pauses a worker instead of ending it.

**Limits to respect before adding processes:** 8 concurrent Gemini calls is roughly 60-120 requests/minute against the Vertex quota. Concurrent hydration hits the source connector's rate limits — if Zoho starts returning 429s, move `hydration` onto Worker B. NullPool means each running task holds its own DB connections, so total connections scale with the number of concurrently running tasks, not with a pool size.

### 7.3 Workers refuse to start behind the schema

On `worker_ready`, each worker resolves the bundled Alembic head, reads `alembic_version.version_num`, and calls `SystemExit` on a definite mismatch — including the case where the table does not exist at all, which is treated as the most definite mismatch of all (`backend/src/contextedge/workers/celery_app.py:83-139`). Transient DB errors and installed layouts without the alembic directory are skipped with `worker.migration_check_skipped` rather than blocking startup.

If a supervisor is restart-looping your workers, read the log line before assuming a crash: `worker.migration_mismatch_refusing_to_start` means run `make migrate`. This check exists because a worker consuming the normalize queue against a stale schema corrupts ingestion mid-transaction.

### 7.4 Celery beat schedule (all 14 entries)

One beat process only. Every fan-out task takes the literal sentinel `"all"` and iterates tenants internally with per-tenant try/except and rollback, so one bad tenant never blocks a sweep.

| Beat entry | Task | Every | Queue | What it does |
| --- | --- | --- | --- | --- |
| `trigger-syncs-every-15m` | `sync.trigger_scheduled_syncs` | 15 min | sync | Selects every `source_objects` row with `approved_for_sync`, dispatches one `sync.run_incremental_sync` per object. |
| `verify-executions-every-15m` | `evaluation.verify_executions` | 15 min | evaluation | Sweeps completed execution runs with `verification_status IS NULL`, up to 50 per tenant. |
| `detect-fleet-groups` | `evaluation.detect_fleet_groups` | 30 min | evaluation | Deterministic fleet-group detector; suggestions are idempotent per change reference and rejections are permanent. |
| `deduplicate-knowledge-hourly` | `pattern.deduplicate_knowledge` | 1 hour | pattern | Merges duplicate evidence, episodes, patterns, playbooks (§7.8). Defers per tenant while ingest is active. |
| `ai-review-episodes-hourly` | `evaluation.ai_review_episodes` | 1 hour | evaluation | The episode AI reviewer (§7.5). Returns `{"status": "disabled"}` instantly while `EPISODE_AI_REVIEW=off`; scheduled unconditionally so enabling the setting needs no beat restart. |
| `detect-drift-every-6h` | `evaluation.detect_drift` | 6 hours | evaluation | Per tenant → `drift_service.check_playbook_drift`. |
| `reconcile-graph-relationships-every-6h` | `evaluation.reconcile_graph_relationships` | 6 hours | evaluation | Streams relational rows into `graph_edges` in batches of 500. Idempotent — the edge writer is ON CONFLICT-safe. |
| `scan-contradictions-every-12h` | `evaluation.scan_contradictions_task` | 12 hours | evaluation | LLM-bearing. Watch cost as the KB grows (§13). |
| `reconcile-identities-daily` | `identity.reconcile_identities` | daily | **default** | Cross-set identity pass. **Proposes merges only** — a human decides on `/identities`. |
| `calibrate-decision-confidence-daily` | `evaluation.calibrate_decision_confidence` | daily | evaluation | Recalibrates decision confidence against recorded outcomes. |
| `mine-decision-patterns-daily` | `evaluation.mine_decision_patterns` | daily | evaluation | Deliberately tenant-wide; emits counts into operational events rather than synthesized content. |
| `cleanup-hard-deleted-daily` | `evaluation.cleanup_hard_deleted_evidence` | daily | evaluation | Orphan sweep after a hard-delete purge (§7.10). |
| `retention-archive-daily` | `evaluation.apply_retention_archive` | daily | evaluation | Marks aged evidence `archived` per memory class (§7.10). |
| `retention-purge-weekly` | `evaluation.purge_archived` | weekly | evaluation | Purges archived evidence in `RETENTION_PURGE_MODE`, default `soft_purge` (§7.10). |

Source: `backend/src/contextedge/workers/celery_app.py:281-384`.

**The shared ingest-activity gate.** Both the hourly dedup sweep and the hourly AI-review sweep call `tenant_pipeline_active` before doing anything (`backend/src/contextedge/workers/pattern_tasks.py:748-785`). A tenant counts as mid-flight when, in the last 10 minutes, either more than 50 evidence rows arrived **or** more than 30 episodes were created (`DEDUP_ACTIVITY_WINDOW_MINUTES`, `DEDUP_ACTIVITY_THRESHOLD`, `EPISODE_ACTIVITY_THRESHOLD` at `pattern_tasks.py:736-745`). Those tenants are deferred to the next tick and counted as `deferred_tenants` rather than churning. The episode threshold exists because watching evidence inflow alone missed the reconstruction tail — a 12:29 sweep once retired 446 drafts mid-tail and some clusters re-paid full synthesis.

### 7.5 Episode AI review (`EPISODE_AI_REVIEW`)

Episode drafts land in `pending_review` and wait for a human. The AI reviewer is an optional stage that reads a draft, forms a verdict, and either records that verdict for the human (advisory) or approves the draft itself (auto_approve).

Modes are exactly three: `off`, `advisory`, `auto_approve` (`backend/src/contextedge/config.py:185-187`). Nothing else is a mode. In particular, any marker you find written into `episodes.ai_review` that is not produced by this code path came from a one-off operational script, not a feature.

- **off** (default) — the hourly task returns `{"status": "disabled"}` and spends nothing.
- **advisory** — every reviewed draft gets a verdict stamped on `episodes.ai_review`; nothing is approved. This is the safe way to measure the reviewer before trusting it.
- **auto_approve** — a draft is approved only if it clears the model verdict **and** every deterministic floor below. `reviewer_user_id` deliberately stays NULL, so an AI approval is permanently distinguishable from a human one.

Auto-approve floors, all of which must pass (`backend/src/contextedge/services/episode_review_service.py:42-44,89-101`):

| Floor | Value | Reason |
| --- | --- | --- |
| `MIN_EVIDENCE` | 2 evidence items | Grounding a one-message story is vacuous. |
| `MIN_OUTCOME_CHARS` | 20 chars of `final_outcome` | Resolution is what downstream learns from. |
| verdict | must be exactly `approve` | `hold` is never overridden. |
| `MIN_VERDICT_CONFIDENCE` | 0.8 | Below that the model itself is unsure. |

Sweep mechanics worth knowing when you are watching it run (`backend/src/contextedge/workers/evaluation_tasks.py:129-358`):

- A per-dispatch `mode_override` can only **downgrade** (advisory under a configured auto_approve). It can never escalate.
- Drafts are selected as `reviewer_state = 'pending_review' AND ai_review IS NULL`, ordered by the same priority score the human review queue uses, default 100 per tenant.
- It commits **per episode, before any dispatch**. A batch-end commit made every verdict hostage to the last one; one deadlock re-paid 50 LLM calls.
- Transient failures (provider outage, budget block) persist nothing, so the draft is retried next sweep. **Five consecutive transient failures abort that tenant's batch** with `episode_ai_review.aborting_sweep_provider_down`.
- After a commit lands, it dispatches `evaluation.extract_issue_signature` per approval, and one `pattern.cluster_episodes` **per domain** that had approvals. Passing a null domain clustered nothing, because the global pass only sees NULL-domain episodes.
- A crash-recovery mop-up re-dispatches signature extraction for up to 20 auto-approved episodes that have no signature row, covering process death between commit and broker send.

On-demand dispatch: `POST /api/v1/episodes/ai-review?limit=&advisory=` (role `knowledge_manager`, `backend/src/contextedge/api/v1/episodes.py:556`).

**Caveat you must not gloss over:** episodes built from clusters larger than 20 evidence items are synthesized in multiple LLM calls, and the step lists from each call currently stack rather than merge — several hundred live episodes carry corrupted timelines. Their row-level fields (root cause, outcome) are clean. See `codewiki/KNOWN_GAPS.md` before making quality claims about episode timelines.

### 7.6 Issue signatures and recurrence

When an episode is approved — by a human or by auto-approve — one LLM call distils it into a generalized problem fingerprint, and identical fingerprints across episodes form a recurrence chain.

- Task: `evaluation.extract_issue_signature`, queue `evaluation`, 2 retries at 30 s (`backend/src/contextedge/workers/signature_tasks.py:24`).
- Service: `extract_issue_signature` (`backend/src/contextedge/services/issue_signature_service.py:89`). It gates on the episode existing, matching the tenant, and being `approved`; a second run for the same episode is a no-op.
- The signature key is `capability|component|failure_mode`, each slugged to lowercase with non-alphanumerics collapsed to `_`, truncated at 240 chars (`issue_signature_service.py:76-86`). Trigger, environment, and scope are recorded but are **not** part of identity, so the same failure triggered differently still recurs under one key.
- Rows land in `issue_signatures` (unique key per tenant, `episode_count` incremented) and `episode_issue_signatures`, plus an `episode -[has_signature]-> issue_signature` graph edge. The edge write is fail-soft; the relational rows are authoritative.
- **Recurrence linking** runs only when the signature already existed (`issue_signature_service.py:249-312`). It finds the most recent other episode on that signature, finds that episode's primary case, and gives the new episode's first evidence item a `recurrence` membership pointing at the old case, at confidence 0.6 (`RECURRENCE_CONFIDENCE`, line 36).
- **The invariant that makes this safe:** the episode cluster resolver explicitly refuses to expand through `recurrence` memberships. Recurrence means "similar problem, never the same occurrence". It exists for precedent retrieval, not for merging clusters.

Worked example: the Acme VPN incident `INC0010427` on `vpn-gw-east-01` approves as an episode and extracts roughly `remote_access|tls_certificate|certificate_expired`. Six months later the same failure mints a second episode under the same key, and its first evidence item gains a `recurrence` pointer back to the original case — so a responder sees the precedent without the two incidents being merged into one story.

If a signature never appears: check that the episode is actually `approved`, then look for `issue_signature.invalid_draft` in the logs. A draft that fails the schema gate returns normally rather than raising, so Celery does **not** retry it (`issue_signature_service.py:145-155`).

### 7.7 Evidence chunking, embedding, and the vector index

**Why chunking exists.** Parent-evidence embedding only covers `title + body[:8000]`, so anything past roughly 8,000 characters used to be invisible to semantic search. Chunking splits a body into small pieces, embeds each one, and lets search find the exact paragraph inside a 40 KB post-mortem.

**Dispatch (`_dispatch_chunking`, `backend/src/contextedge/workers/extraction_tasks.py:73-119`).** A body is chunked *inline* inside `normalize_evidence` when it is under `INLINE_CHUNK_BUDGET_BYTES = 16 * 1024` **and** its source is on `INLINE_CHUNK_SOURCE_ALLOWLIST` — currently `jira_sm`, `servicenow`, `gmail`, `teams`, `sapphireims`, `zoho_desk` (`extraction_tasks.py:54,60-62`). Everything larger or unfamiliar dispatches the async task so a slow parser cannot stall ingest. The whole block is wrapped in try/except: a chunker bug degrades retrieval, it never fails ingestion.

**The two tasks, both on the `embedding` queue** (not `extraction` — older docs say otherwise):

- `extraction.chunk_evidence` (`backend/src/contextedge/workers/chunk_tasks.py:210`, 3 retries / 60 s). Idempotent on `chunker_version`: replaying it on evidence whose existing chunks already match the resolved chunker's version is a no-op. It reloads the raw payload; if the payload was offloaded to MinIO without a storage key it degrades to body-text-only chunking rather than failing.
- `extraction.embed_chunks_batch` (`chunk_tasks.py:238`, 3 retries / 30 s). Filters `embedding IS NULL`, then embeds in batches of `EMBED_BATCH_SIZE = 32` (`chunk_tasks.py:51`). Per-tenant budget enforcement fires per batch, not per chunk. On a batch failure it breaks without raising, leaving NULL rows for the next replay.

**Which chunker runs** is decided by record shape first, then source type (`get_chunker`, `services/chunkers/registry.py:116-143`), in this order: `evidence_type == "kb_article"` → the document chunker (or the attachment chunker if the document one failed to register); ticket sources (`jira_sm`, `servicenow`, `sapphireims`, `zoho_desk`) → the ticket chunker; `gmail`/`teams` → the thread chunker; `evidence_type == "attachment"` → the attachment chunker; everything else → fallback. Shape wins over source because one source emits more than one shape — a Zoho Desk source produces both tickets and KB articles, and an article's headings are the meaningful split boundaries. `get_chunker` always returns something; the fallback chunker is the floor, and it never returns `None`.

**The vector index (§ this is the part that silently doesn't work).** pgvector's HNSW caps the plain `vector` type at 2,000 dimensions and this application stores 3,072, which means migration `0021`'s indexes never actually existed and every similarity query was a sequential scan. Real ANN indexing landed in `0032` as HNSW **expression** indexes over `(embedding::halfvec(3072))`. Consequences for operators:

- The server needs the pgvector extension at **0.7 or newer**. `docker-compose.yml` pins `pgvector/pgvector:pg16`. An environment stamped at an older revision of that migration file never re-executes it and stays on sequential scans forever.
- Query code must go through `halfvec_cosine_distance` (`backend/src/contextedge/search/vector_ops.py:40-45`). Any direct `column.cosine_distance(...)` ordering bypasses the index.
- Before any tenant-filtered ANN query, callers run `SET LOCAL hnsw.ef_search = 200` (`vector_ops.py:31-37`). The indexes are global across tenants while every query post-filters by `tenant_id`; at the default `ef_search = 40` a small tenant's rows can be missing from the candidate set entirely and the query quietly returns fewer rows than asked for.

**Operational caveats:**

- There is a brief window after chunks are written where `EvidenceChunk.embedding IS NULL`. The chunk vector query skips NULL-embedding rows naturally. If chunks stay NULL past your expected window, suspect a tripped per-tenant LLM budget: `check_budget` raises `TenantBudgetExceeded` *before* the call is made, so there is **no `llm.usage` event to grep for** — the tenant's spend line simply goes flat. The visible trace is a `chunk_embedding_failed` structlog warning whose `error` names `TenantBudgetExceeded` (`workers/chunk_tasks.py:172-181`); the batch loop `break`s and returns normally, so nothing raises. Confirm with `GET /api/v1/admin/tenant-budget/status`.
- Chunks **are** read at query time now. Semantic search runs an oversampled chunk ANN pass, diversifies with MMR, rolls up to one hit per parent, and merges a parent-embedding pass so unchunked evidence still surfaces. Documentation that says chunks are written but never read is stale.
- A backfill task for legacy `EvidenceItem` rows with `chunked_at IS NULL` **has still not landed**. Until it does, only newly normalized evidence gets chunked. The partial index `ix_evidence_items_chunked_at_null` is in place to drive it cheaply when it does.
- Identity and decision extraction still run once on the parent body, not per chunk.

See [codewiki/CHUNKING_DESIGN.md](../codewiki/CHUNKING_DESIGN.md) for the full pipeline narrative.

### 7.8 Pattern clustering, playbook candidates, and the dedup sweep

**Clustering has no beat entry.** It is event-driven and manual (`backend/src/contextedge/workers/pattern_tasks.py:422`, queue `pattern`, 2 retries / 120 s). It is dispatched by:

1. `POST /api/v1/episodes/{id}/approve` and `POST /api/v1/episodes/bulk-approve`, once per affected domain, after the commit.
2. The hourly AI review sweep, once per domain that had auto-approvals.
3. `POST /api/v1/patterns/cluster` (role `domain_admin`). With a `domain_id` it runs one pass; without, one pass per tenant domain **plus** a global NULL-domain pass.

A domain pass sees only episodes in that domain; the global pass sees only episodes with a NULL domain. They never mix, because whichever pass ran first would otherwise capture the NULL rows arbitrarily.

Inside one pass (`_cluster`, `pattern_tasks.py:153`): approved episodes missing an embedding are repaired first; then each unlinked candidate is matched against the pattern owning its **single nearest member episode** within `PATTERN_MATCH_MAX_DISTANCE = 0.30` (`pattern_tasks.py:50`, `ORDER BY member_distance ASC LIMIT 1` at `:243-256`) and that one candidate is adjudicated by an LLM; on no match, similar unlinked episodes within `CLUSTER_GROUP_MAX_DISTANCE = 0.27` (`:60`, applied at `:308-309`) form a cluster, and one LLM call synthesizes a pattern from it. Candidates are capped at 100 per run (`:214`). If synthesis fails for any reason, a basic fallback pattern is still created at confidence 0.75 with no synthesized fields (`:390`).

The `ORDER BY` is the point, not a detail. Everything in this corpus is an AutomationEdge support incident, so embeddings bunch: measured 2026-08-19, the pairwise episode distance spread was min 0.157 / p01 0.257 / median 0.409, which meant an unordered `LIMIT 1` handed the validator an essentially arbitrary qualifying pattern. Asking about the *nearest* pattern took the validator's accept rate from 12% to 40% on the same corpus. If you see these numbers written as `0.35` and `0.20` anywhere, that document predates the change.

Creating a pattern enqueues `pattern.generate_playbook_candidate` for it, and growing a pattern's membership re-enqueues the same task — but the send goes through `services/deferred_dispatch.dispatch_after_commit` (`services/pattern_service.py:192,247`), which holds it until the transaction commits. Sending inside the transaction failed in both directions: a rolled-back clustering pass once left 65 queued tasks naming patterns that never existed, and on the success path a worker could read "not found" before the commit landed and silently skip a pattern's playbook.

**Two operational facts about clustering:** the adjudication call **fails open** — during a provider outage or a budget block it returns `is_match: true`, so the 0.30 embedding probe alone decides membership. And a full 100-episode pass has been observed running 25 minutes inside a *single* database transaction with roughly 156 LLM calls; a late failure rolls back every row while the spend stays spent, and `patterns` reads zero the whole time.

**The dedup sweep** (`pattern.deduplicate_knowledge`, `pattern_tasks.py:834`, 1 retry / 600 s) runs hourly on beat, rides along at the end of every clustering pass, and is also exposed at `POST /api/v1/patterns/deduplicate`. It merges, in this order: duplicate evidence items, episodes by title (split into evidence-overlap components so a shared label never merges different incidents), episodes whose evidence set is a strict subset of another's, semantically similar episodes at cosine ≥ 0.85 **that share evidence**, then patterns, then playbooks. Episodes are never hard-deleted — the loser is marked `superseded`.

The "shares evidence" requirement on the semantic pass is not a tuning knob: pairs above 0.85 with disjoint evidence are exactly the recurrence case, and merging them would destroy the signal §7.6 depends on.

### 7.9 Sync controls: single-flight, pause, cancel, resume

**Single-flight.** Each sync job takes a transaction-scoped Postgres advisory lock, `pg_try_advisory_xact_lock(hashtext('sync:<object_id>'))` (`acquire_sync_lock`, `backend/src/contextedge/services/sync_worker_service.py:379-395`). A second worker on the same source object returns `{"status": "skipped_locked"}` rather than racing the checkpoint (backfill at `:427-433`, incremental at `:532-538`). Being transaction-scoped, the lock releases on commit or rollback, so a crashed worker cannot leak it. Retries: `sync.run_backfill` 3 attempts at 120 s, `sync.run_incremental_sync` 5 attempts at 30 s.

**Incremental with no checkpoint is skipped, not escalated.** A scheduled incremental run against an object that has never been backfilled completes with status `skipped_no_checkpoint` and an explanatory error blob. It never silently turns into a first full pull.

**Cooperative pause / cancel / resume** (migration `0069`): `POST /api/v1/sources/{source_id}/sync/control` with `{action: pause|resume|cancel, source_object_id?}`, gated on `domain_admin` (`backend/src/contextedge/api/v1/sources.py:295-312`). Pause and cancel set a gate on the source object and, if a run is active, write `sync_runs.control`. The running job polls that column **on a fresh connection** — its own transaction predates the operator's write and cannot see it. How often depends on the connector: `BaseConnector` supplies `set_control_check` / `_check_control` with a no-op default and each connector chooses where to call it (`connectors/base.py:94-107`). Zoho Desk, the one exercised against a live instance, checks once per page and every 25 detail records (`CONTROL_CHECK_EVERY = 25`, `connectors/zoho_desk/connector.py:128,818,946`); a connector that never calls the hook simply runs to completion. The check never raises: a failing control channel must not kill a sync. Both stops persist everything already fetched, with a checkpoint; cancel is not a rollback. `resume` only clears the gate — the paused run already ended, and the next run continues from the checkpoint. `sync_runs.celery_task_id` is the escape hatch for a genuinely wedged worker: revoke by id. Every control action is audited as `sync.<action>`.

### 7.10 Retention and data lifecycle

Runtime memory assembly and retention windows both flow through `backend/src/contextedge/services/memory_service.py`.

Memory classes:

- `short_term` — active sessions, current case context, recent evidence
- `long_term` — validated patterns, approved playbooks, KB-style evidence, canonicalized identities
- `reasoning` — decision traces, execution steps, approvals, tool/action history

Retention takes the tenant policy's `retention_days` as a base (falling back to `RETENTION_DEFAULT_DAYS`, 365) and expands it by class (`memory_service.py:64-70`):

- `short_term`: base window
- `reasoning`: `max(base × 3, 90 days)`
- `long_term`: `max(base × 6, 180 days)`

An evidence item is `long_term` if its type is `kb_article` / `sop` / `documentation`, or if it resolved any identities; otherwise `short_term` (`memory_service.py:73-79`).

**Archive (daily).** `evaluation.apply_retention_archive` (`backend/src/contextedge/workers/retention_tasks.py:72`) → `apply_retention_policy` (`backend/src/contextedge/services/retention_service.py:71`) sets `relevance_state = "archived"` on evidence past its class window. Legal hold is excluded in the SQL WHERE clause, never post-filtered. A boolean `retention_days` in a policy config is explicitly rejected, because `true` is an `int` subclass in Python and a config typo would silently mean a one-day window.

**Purge (weekly).** `evaluation.purge_archived` (`retention_tasks.py:104`) → `purge_archived_evidence` (`retention_service.py:139`). Candidates are archived rows older than a 30-day grace period (`DEFAULT_ARCHIVE_GRACE_DAYS`, `retention_service.py:66`), not under legal hold, oldest first so a backlog drains across ticks, capped per tick.

- `soft_purge` (the default) NULLs `embedding`, `body_text`, `body_summary`, `canonical_entity_refs` and `raw_object_ref`, sets the title to `[purged]`, and then explicitly deletes the row's `evidence_chunks` — chunks carry the same content and embeddings, and the FK cascade does not fire while the parent row survives.
- `hard_delete` deletes the row; FKs cascade to attachments, correlation edges, and contradiction scan state, while `playbook_evidence_links.evidence_id` is ON DELETE SET NULL so the link survives as an audit record.
- `dry_run` returns candidate counts without mutating anything — this is what the admin preview uses.

**Orphan cleanup (daily).** `evaluation.cleanup_hard_deleted_evidence` (`backend/src/contextedge/workers/cleanup_tasks.py:165`) reaps what hard-delete deliberately leaves behind: `raw_evidence_objects` rows and their MinIO blobs that no `evidence_items.raw_object_ref` points at, and `graph_edges` whose evidence-typed endpoint no longer exists (edge node ids are plain UUIDs with no FK). A blob delete failure leaves the DB row for the next day's retry. Artifact blobs are a documented stub returning 0 — once the rows are gone there is no way to find them by DB scan, so run an S3 lifecycle rule on the `artifacts/` prefix.

**The gap to plan around:** offloaded raw payloads belonging to *live* evidence have no TTL or garbage collection in code. Blob retention for those depends on an external bucket lifecycle rule.

### 7.11 Raw payload offload (the 32 KB rule)

Any raw payload whose JSON serialization exceeds `OFFLOAD_THRESHOLD_BYTES = 32_768` is uploaded to MinIO at `raw/{tenant_id}/{raw_id}.json`, and the `raw_payload` column keeps only the stub `{"_offloaded": true, "size_bytes": N}` (`backend/src/contextedge/services/ingestion_persistence.py:16,84-87`).

This is invisible until it bites you, so it is worth stating plainly: **every SQL query that filters or reads `raw_evidence_objects.raw_payload` silently skips the biggest rows.** Known places this already matters:

- Ingest-priority ordering reads `thread_count` / `resolution` from the payload, so offloaded rows sort as zero-thread, no-resolution — the longest conversations are exactly the ones prioritization cannot see.
- Reply-inheritance reconciliation reads `reply_to_id` in SQL and explicitly skips offloaded rows.
- Any ad-hoc backfill you write over payload fields will skip the longest articles and tickets. This is why `knowledge_state` and `source_facets` on pre-`0067`/`0068` rows are left to fill in on the next sync rather than backfilled with SQL.

The MinIO client is deliberately impatient — 1-second connect and read timeouts, one attempt — so a slow object store fails fast instead of stalling a worker.

### 7.12 Onboarding a new tenant / bulk backfill

Measured on a live 84-ticket Zoho backfill (thread-heavy corpus): a cold-start ingest burned through the deployment-default daily budget (2M tokens) in roughly two hours, and the `block` action froze the pipeline mid-run for 9.5 minutes until an operator intervened.

Before any bulk backfill:

1. **Provision a `tenant_llm_budgets` row** for the onboarding tenant — size it at roughly 100k tokens per thread-heavy ticket for a cold start — or set its action to `warn` for the ingest window and restore afterwards. Use `GET/PUT /api/v1/admin/tenant-budget`.
2. **Set connector filters with that connector's own key.** `module_filters` for Zoho Desk, `table_filters` for ServiceNow. The wrong key is silently ignored and the whole modified window syncs.
3. **Consider `EPISODE_RESOLUTION_GATE=cluster`** for corpora where many tickets carry no resolution — open queues, chat exports. Episode synthesis dominates cold-start spend (~73% on the measured run); with the gate on, clusters with no solution signal anywhere defer synthesis at zero LLM cost and are re-checked as new evidence joins. Deferred, not dropped — see `codewiki/07-episodes-patterns-playbooks.md`. For closed-ticket backfills, the connector's own status filter is the better gate.
4. **Make sure the fleet consumes all eight queues** (§7.1) before you start, not after. Discovering mid-backfill that `correlation` had no consumer means re-running the graph half of the pipeline.

### 7.13 Attachment extraction

Deterministic artifact extraction runs on the `extraction` queue as `artifact.extract_attachment` (`backend/src/contextedge/workers/artifact_tasks.py:15`, 3 retries / 60 s).

- Text-ish formats: `text/*`, `.txt`, `.text`, `.md`, `.csv`, `.log`, `.out`, `.err`, `.json`, `.jsonl`, `.ndjson`, `.srt`, `.vtt`, `.transcript` (`backend/src/contextedge/services/artifact_extraction_service.py:51-56`).
- **Documents:** PDF and DOCX are parsed natively (`pdf_native` via pdfplumber, `docx_native`), registered lazily so a deployment without the document extras degrades to "unsupported format" instead of failing at import (`backend/src/contextedge/services/documents/registry.py:20-38`).
- Budgets differ by kind, deliberately: log-ish attachments are capped at 4,000 chars of extracted text and 16,000 chars of combined body, while documents get 200,000 / 400,000, because applying the log caps to a 60-page SOP truncated it to roughly its title page (`backend/src/contextedge/services/artifact_extraction_service.py:26-46`).
- A vision pass interprets figures in documents that produced figure elements needing it, gated by `DOCUMENT_VISION_ENABLED` (default true).
- Artifact binaries live in object storage under `artifacts/<tenant>/<evidence>/<artifact>/...`.
- Extracted text is merged back into `evidence_items.body_text` with `[Attachment: …]` provenance markers, and the merged body is **re-redacted** — this is the one place attachment-derived text gets caught before it is persisted and embedded.
- When extraction finishes, the task dispatches re-classification (on the real content this time), correlation, and baseline computation.

---

## 8. Health and Observability

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Pure liveness — the process is up and serving. Dependency checks live in `/ready` on purpose, so a database blip does not get the process killed by an orchestrator. |
| `GET /ready` | Readiness: database `SELECT 1`, Alembic head comparison, Redis ping — each with a 5-second timeout. Any failure returns **503** with a per-check dict. Object storage is reported as `ok` or `degraded` but does **not** gate readiness (`backend/src/contextedge/main.py:179-210`). |
| `GET /metrics` | Prometheus scrape target — HTTP request metrics from the FastAPI instrumentator (`main.py:168`). |
| `GET /api/v1/admin/pipeline-health` | The operator view of the pipeline (§8.1). |
| `GET /api/v1/admin/llm-usage`, `GET /api/v1/admin/tenant-budget/status` | Spend and budget state per tenant. |

Local URLs:

- `http://localhost:8000/health`
- `http://localhost:8000/ready`
- `http://localhost:8000/metrics`

Logging:

- API and workers use structlog.
- In debug mode logs are developer-friendly console output.
- Docker logs are available through `make logs`.
- **Request ids follow work into the workers.** Middleware mints or parses `x-request-id`, `x-correlation-id`, and `x-causation-id`, binds them to a context variable, and echoes the first two on the response. When a handler calls `task.delay(...)`, a `before_task_publish` signal copies those three ids into the task headers; `task_prerun` rebinds them on the worker for the task's duration (`backend/src/contextedge/workers/celery_app.py:25-80`). Everything written to `operational_events` inherits them, which means one id joins an operator's click to the LLM spend it caused.

### 8.1 Pipeline health — the first place to look

`GET /api/v1/admin/pipeline-health` → `pipeline_health_service.get_pipeline_health` (`backend/src/contextedge/services/pipeline_health_service.py:87`), rendered at `/admin/pipeline` for `tenant_admin`.

It reports two things side by side:

1. **Queue depth per lane**, read straight from Redis with `LLEN` over the queues in pipeline order, plus `HLEN unacked` for in-flight work (`_queue_depths`, `pipeline_health_service.py:58-84`; the lane list is `QUEUES` at `:43-52`). The unacked count matters more than it sounds: during the reconstruction phase, 5,800 debounced reconstruct tasks churned for hours while every queue length read zero. Backlog alert threshold is `BACKLOG_ALERT_DEPTH = 500` (`:55`).
2. **Stage counts along the graph chain** in one SQL read — evidence → embedded → identities → and onward. **The first zero in that sequence is the diagnosis.**

It never raises on broker failure; it returns empty depths instead. The module exists because of a specific incident: every per-task metric said "healthy" while `correlate_evidence` starved behind 8,000 normalizations and episodes stayed at zero.

---

## 9. Testing and Lint

| Scope | Command |
| --- | --- |
| Backend tests | `make test-backend` or `cd backend && python -m pytest -v` |
| Frontend tests | `cd frontend && npm test` |
| Combined | `make test` |
| Lint | `make lint` (ruff for backend, `npm run lint` for frontend) |
| Format | `make format` |

Current state:

- The backend suite is large: **173 test files** holding roughly **1,900 test functions** before parametrization. Do not quote a fixed pass count in a document — run the suite and record the number it prints (the repo convention is to put that number in the commit message).
- Coverage spans security hardening (RBAC, JSON parsing, config validation), evidence FTS and semantic search, chunking and chunk rollup, retention and legal hold, async episode reconstruction and review, identity resolution and candidacy, correlation and case bridging, contradiction detection, governed execution, graph edge-type registry and projection, ORM/migration column parity, and connector behaviour.
- `npm test` runs `vitest run` against real frontend unit tests — seven files today: role predicates, the graph API client, graph constants, graph query controls, and the applicability, playbook-step, and thread-conversation components. Older revisions of this runbook described it as a placeholder; that is no longer true.

---

## 10. Operational Caveats

- **Sync is single-flight per source object.** A per-object Postgres advisory lock makes the second worker skip rather than race (§7.9). This is a change from older revisions of this runbook, which said no such guard existed. What is still true: pausing and cancelling are *cooperative*, so a stop takes effect at the next page or 25-record boundary, not instantly.
- **Evidence dedupe is enforced by the database.** Migration `0026` added a unique index on `(tenant_id, content_hash)`; the normalize worker catches the `IntegrityError`, adopts the winning row, and returns `{"deduped": true, "raced": true}` without re-spending LLM calls. The application-layer hash check still runs first, as a cheap pre-filter.
- **Raw payloads over 32 KB are not in Postgres.** See §7.11 before writing any SQL over `raw_payload`.
- **Role bindings carry a scope that is not enforced.** `RoleBinding.scope_type` / `scope_id` exist, but login reads only role names and `has_role` is a pure name check, so a domain admin bound to one domain holds that role tenant-wide on every `require_role` route (`backend/src/contextedge/deps.py:37-51`). Finer scoping comes only from token claims that individual routes consult. Single-domain tenants are unaffected; multi-domain tenants must treat grants as tenant-wide.
- **Nav visibility is not authorization.** The backend treats `platform_super_admin`, `tenant_admin`, and `admin` as super-roles; the frontend treats only `platform_super_admin` that way. A `tenant_admin` therefore sees fewer nav items than the API would actually authorize them for. Sidebar filtering is UX, and the 401/403 from the API is the real boundary.
- **Service tokens without `allowed_domain_ids` are tenant-wide** for runtime access. Set an explicit allowlist when you want least-privilege behaviour.
- **Notification channels are opt-in.** With no SMTP host or webhook URL configured, email and webhook delivery are explicit no-ops that log `notification.email_skipped_unconfigured` / `notification.webhook_skipped_unconfigured`. All delivery is best-effort and never raises into the triggering flow.
- **Audit writes never fail a request.** `RequestAuditMiddleware` inserts `audit_logs` rows on its own sync engine, off-thread, and swallows its own failures as `audit_db_error`. Unauthenticated 401 probes never resolve a tenant, so they exist only in structlog — alert on `http.mutating_request` with status 401 if you care about them.
- **Poison messages survive a database rebuild.** Celery messages live in Redis db 1, so `docker compose down -v` (which drops the Redis volume) is the reset that clears them; dropping only the Postgres volume does not.

---

## 11. Common Issues

| Symptom | What to check |
| --- | --- |
| Login fails or API returns 401 | JWT secret mismatch, expired token, backend restart required after env change |
| Backend crashes on startup with `RuntimeError` about JWT_SECRET_KEY or FERNET_KEY | Set a real value, or set `APP_ENV=development` for local work (`config.py:248-264`) |
| Worker exits immediately at startup | Read the log line. `worker.migration_mismatch_refusing_to_start` means the DB is behind the code — run `make migrate` (§7.3) |
| `/ready` returns 503 | Read the returned `checks` dict — `database`, `migrations`, and `redis` are reported separately |
| Runtime returns 403 | Caller risk tier cap, playbook/domain mismatch, or service-token domain allowlist |
| Runtime explain returns 404 | Redis cache expired (1 hour TTL) or there was no previous `POST /runtime/match` |
| Missing tables or columns | Run migrations and verify against `alembic heads` — the chain moves frequently, so trust the command, not a number written in a doc |
| FTS queries return no results | Verify migration `0007_fts_gin_indexes` was applied and `search_tsvector` columns exist |
| **Evidence is ingested but no episodes ever appear** | Almost always no worker consuming `correlation`. Check `GET /api/v1/admin/pipeline-health` — the first zero in the stage chain is the diagnosis (§8.1) |
| **Chunks exist but search cannot find them** | No worker consuming `embedding`, or a tripped tenant budget. A block writes no `llm.usage` event at all — check `GET /api/v1/admin/tenant-budget/status` and grep worker logs for `chunk_embedding_failed` |
| Semantic search returns fewer rows than `limit` | pgvector extension below 0.7 (so `0032`'s halfvec HNSW indexes never built), or a query path bypassing `halfvec_cosine_distance` (§7.7) |
| `ModuleNotFoundError: No module named 'contextedge'` | Start host-run services with `cd backend && python dev.py ...` so `src/` is added automatically. If it still fails, check `python -c "import sys; print(sys.executable); print(sys.version)"` and verify a Python 3.12+ backend virtualenv with dependencies installed |
| Celery tasks do not execute | Worker not running, Redis misconfigured, broker URL mismatch — **or the task routes to a queue nobody is consuming** (§7.1) |
| Celery workers die silently under load on Windows | You are probably on `-P threads` with LLM-bearing lanes. Use multiple `-P solo` processes (§7.2) |
| MinIO failures | Endpoint, credentials, bucket name, host vs container hostname |
| Object-store offload not working | Verify MinIO is reachable from the worker; check `MINIO_ENDPOINT` and credentials. Client timeouts are 1 s with one attempt, so failures are fast and loud |
| Attachment extraction stays `pending` or `failed` | Verify the `extraction` worker is running, the artifact object exists in MinIO/S3, and the format is supported. PDF/DOCX need the document extras installed — look for `document_parser.register_failed` at startup |
| Evidence chunking is not running (`chunked_at IS NULL`) | Verify a worker consumes the **`embedding`** queue. For rows ingested before `0030`, the backfill task is still not wired — only newly normalized evidence chunks today. Check structlog `chunking_failed` in the normalize worker for chunker bugs |
| Chunks persist with `embedding IS NULL` past expected window | Likely a tripped per-tenant LLM budget. There is no `llm.usage` row for a blocked call — use `GET /admin/tenant-budget/status` (`allowed: false`, `reason` of `token_limit_exceeded` or `cost_cap_exceeded`) and the `chunk_embedding_failed` log line; raise the cap or wait for the daily reset. The next replay of the batch task is idempotent and picks up `embedding IS NULL` rows automatically |
| Episodes approve but no signature row appears | Look for `issue_signature.invalid_draft` — a schema-gate failure returns normally, so Celery does not retry (§7.6) |
| Patterns never form | Clustering has no beat entry. It fires on episode approval and from `POST /api/v1/patterns/cluster`. If nothing is approving episodes, nothing clusters (§7.8) |
| Frontend cannot reach API | `NEXT_PUBLIC_API_URL`, backend port, and `APP_CORS_ORIGINS` |
| Contradiction scan is slow or expensive | Reduce scan frequency in the `celery_app.py` beat schedule, or limit to specific domains |

---

## 12. Local Reset

To rebuild local state from scratch:

```bash
docker compose down -v
docker compose -f docker-compose.dev.yml down -v
make up
make migrate
make seed
```

This removes Docker volumes for Postgres, Redis, and MinIO data — including the Celery broker, which is why it also clears any stuck task messages.

---

## 13. Production-Oriented Notes

- Replace all default secrets before any shared deployment. The backend refuses to start with the default `JWT_SECRET_KEY` or a placeholder `FERNET_KEY` when `APP_ENV` is not `development`.
- Run the API behind TLS and a real reverse proxy.
- Treat `SERVICE_TOKENS_JSON` as a secrets-bearing config surface.
- Scale Celery workers by queue characteristics rather than as one undifferentiated pool — and make sure every one of the eight queues has a consumer somewhere in the fleet.
- Run exactly one beat process across the whole deployment.
- Back up Postgres and object storage independently, and add a bucket lifecycle rule for the `artifacts/` prefix (the code's artifact-blob cleanup is a documented stub) and for offloaded raw payloads of live evidence (no TTL exists in code).
- MinIO bucket is auto-created on startup if missing; verify credentials and endpoint when running against a shared or production S3-compatible store.
- Access policies filter evidence and playbook results at retrieval time. Admin roles (`platform_super_admin`, `tenant_admin`, `domain_admin`) bypass access-policy filtering. Non-admin roles will not see evidence or playbooks attached to restricted access policies.
- **Budget every tenant explicitly.** A tenant with no `tenant_llm_budgets` row inherits the deployment defaults (2M tokens/day, $25/day, action `block`), which is a ceiling, not a plan. Usage is measured by summing the current UTC day's `llm.usage` operational events, with a 60-second cache, so at most one over-cap call slips through per minute and cross-worker races are not yet fully bounded.
- Contradiction scanning and the episode AI reviewer both make LLM calls on a schedule. Monitor cost and latency; adjust the intervals in `celery_app.py` as the KB grows.

---

## 14. Maintenance Rules

Update this runbook when:

- Make targets change
- Docker compose services change
- **Worker queues or the routing table change** — and check the PowerShell block in §7.2 explicitly, since a stale `-Q` list there is a silent-failure generator
- The beat schedule gains or loses an entry
- Pipeline stages are added, renamed, or re-routed
- Operational commands or troubleshooting steps change

Do **not** update the migration table in §5 with every revision — it is deliberately a "why an operator cares" list, not a changelog. `alembic heads` is the source of truth.

Update [SETUP_GUIDE.md](SETUP_GUIDE.md) when onboarding steps change. Update [API.md](API.md) when HTTP behavior changes. Check [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md) before asserting any feature works end to end.
