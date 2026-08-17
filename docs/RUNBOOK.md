# ContextEdge - Runbook

Operational guide for running, maintaining, and troubleshooting ContextEdge after initial setup.

For first-time local installation, use [SETUP_GUIDE.md](SETUP_GUIDE.md). For architecture, see [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md). For HTTP behavior and auth semantics, see [API.md](API.md).

---

## 1. Scope

This runbook covers:

- environment and configuration checks
- Docker and host-run operational commands
- migrations and seed data
- workers and queues
- health, logs, and troubleshooting

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

Settings are loaded through `contextedge.config`.

| Area | Representative variables |
| --- | --- |
| Database | `DATABASE_URL`, `DATABASE_URL_SYNC` |
| Redis / Celery | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| Object storage | `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET` |
| Auth | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `SERVICE_TOKENS_JSON` |
| Crypto | `FERNET_KEY` |
| AI | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_*`, default model names |
| App | `APP_DEBUG`, `APP_LOG_LEVEL`, `APP_CORS_ORIGINS`, `FRONTEND_URL` |

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

| Revision | Purpose |
| --- | --- |
| `0001_initial_schema` | Base schema bootstrap |
| `0002_tenant_policies` | Tenant policy table |
| `0003_source_policy_fks` | Source -> policy foreign keys |
| `0004_evidence_access_policy_fk` | Evidence -> access policy foreign key |
| `0005_playbook_version_semantic_unique` | Per-playbook semantic version uniqueness and legacy duplicate cleanup |
| `0007_fts_gin_indexes` | Stored `search_tsvector` columns and GIN indexes on `evidence_items` and `playbooks` |
| `0008_resolution_sessions` | `resolution_sessions` and `decision_trace_events` tables for runtime audit trails |
| `0009_case_links` | `case_links` table for cross-source case correlation |
| `0010_operational_events` | Append-only event ledger with request, correlation, and causation ids |
| `0011_execution_governance` | Governed execution runs, step runs, tool invocations, and approval requests |
| `0012_evidence_identity_links` | Active evidence-to-identity links for normalization, retrieval, and correlation |
| `0013_attachment_processing` | Attachment parser metadata, extraction status, and extracted-at timestamps |
| `0014_notifications_and_playbook_approval_policy` | Notification tables and playbook approval policy |
| `0015_graph_edges_domain_id` | `domain_id` column and composite index on `graph_edges` |
| `0016`–`0024` | First-class decisions, rejection codes, playbook step metadata, evidence baselines, decision embeddings, HNSW vector indexes, contradiction scan state, tenant LLM budgets, evidence scale indexes. See [MIGRATIONS.md](MIGRATIONS.md). |
| `0025_jsonb_gin_indexes` | GIN indexes on `graph_edges.metadata_extra` + `evidence_items.canonical_entity_refs`. `CREATE INDEX CONCURRENTLY`; no pre-step needed. |
| `0026_dedup_uniqueness` | Unique indexes on `(tenant_id, content_hash)` + `(tenant_id, source_a_ref, source_b_ref)`. **Pre-migration dedupe required** on deployments that previously ran under the race window — see §5.1 below. |
| `0027_playbook_evidence_link_fk` | FK `playbook_evidence_links.evidence_id → evidence_items(id) ON DELETE SET NULL`. **Pre-migration dedupe required** if prior hard-delete runs left dangling evidence_ids — see §5.1. |
| `0028_orm_ddl_drift_alignment` | Adds `ON DELETE CASCADE` to three FKs that drifted ORM-only (`attachment_artifacts.evidence_id`, `correlation_edges.source_evidence_id`, `correlation_edges.target_evidence_id`). Idempotent (`DROP CONSTRAINT IF EXISTS` + re-add). |

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
- `0001_initial_schema` is not a frozen DDL snapshot. See [MIGRATIONS.md](MIGRATIONS.md).
- If the API reports missing tables or columns, verify the current Alembic head before debugging application code.

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

- `admin@contextedge.local` / `admin123`
- `analyst@contextedge.local` / `analyst123`

The seed script is idempotent for the default tenant slug and will skip if the default tenant already exists.

---

## 7. Host-Run Services

With infrastructure running:

| Goal | Command |
| --- | --- |
| API reload server | `make backend-dev` |
| Celery worker | `make celery-dev` |
| Celery beat | `make celery-beat-dev` |
| Frontend dev server | `make frontend-dev` |

Equivalent direct commands:

- API: `cd backend && python dev.py api`
- Worker: `cd backend && python dev.py worker`
- Beat: `cd backend && python dev.py beat`
- Frontend: `cd frontend && npm run dev`

The `python dev.py ...` launcher adds `backend/src` to `PYTHONPATH` automatically and uses the active interpreter. If `contextedge` still cannot be imported, return to [SETUP_GUIDE.md](SETUP_GUIDE.md) and verify the interpreter path, Python version, and dependency install before retrying.

Worker queues currently used:

- `default`
- `sync`
- `hydration`
- `extraction` (also handles artifact extraction, correlation tasks, **chunk_evidence_task**, and **embed_chunks_batch_task**)
- `pattern`
- `evaluation` (also handles contradiction scans)

### Worker topology (Windows)

Run **two** workers with different pools, plus exactly one beat:

```powershell
# Worker A — the parallel one. Ticket processing is ~95% waiting on the LLM,
# so parallelism gives near-linear speedup. Prefork is not usable on Windows,
# and `-P threads` is NOT usable either: litellm holds asyncio locks bound to
# the loop that created them, so a threads pool raises "Lock is bound to a
# different event loop" on every enrichment call, which trips the provider
# circuit breaker and fails the whole run silently-ish. Measured on a live
# backfill 2026-08-16.
#
# Parallelism therefore has to come from PROCESSES, each with its own loop:
# start N solo workers with distinct node names.
1..4 | ForEach-Object {
  Start-Process python -ArgumentList "-m","celery","-A","contextedge.workers.celery_app",`
    "worker","-l","INFO","-n","workerA$_@%h","-Q","extraction,hydration,default","-P","solo"
}

# Worker B — the serialized one. Clustering and playbook generation operate on
# the whole graph and have no advisory lock (unlike sync), so two concurrent
# runs could mint duplicate patterns. Solo costs nothing here — these tasks are rare.
python -m celery -A contextedge.workers.celery_app worker -l INFO -n workerB@%h -Q sync,pattern,evaluation -P solo

# Beat — ONE instance only. A second beat double-dispatches every scheduled task.
python -m celery -A contextedge.workers.celery_app beat -l INFO
```

### Onboarding a new tenant / bulk backfill

Measured on a live 84-ticket Zoho backfill (thread-heavy corpus): a
cold-start ingest burned through the deployment-default daily budget
(2M tokens) in roughly two hours and the `block` action froze the
pipeline mid-run for 9.5 minutes until an operator intervened. Before
any bulk backfill, **provision a `tenant_llm_budgets` row** for the
onboarding tenant (sized ~100k tokens per thread-heavy ticket,
cold-start) or set its action to `warn` for the ingest window, then
restore. Also set connector filters with the connector's own key
(`module_filters` for Zoho Desk, `table_filters` for ServiceNow) — the
wrong key is silently ignored and the whole modified window syncs.

Episode synthesis dominates cold-start spend (~73% on the measured
run). For backfills where many tickets carry no resolution — open
queues, chat exports — consider `EPISODE_RESOLUTION_GATE=cluster`:
clusters with no solution signal anywhere defer synthesis at zero LLM
cost and are re-checked as new evidence joins (deferred, not dropped;
see `codewiki/07-episodes-patterns-playbooks.md`). For closed-ticket
backfills the connector status filter is the right gate instead.

Via the launcher (`dev.py` defers to a caller-supplied `-P`/`-Q`):

```powershell
python dev.py worker -Q extraction,hydration,default -P threads -c 8   # Worker A
python dev.py worker -Q sync,pattern,evaluation                        # Worker B (solo default)
python dev.py beat
```

Why this split is safe: every task runs `asyncio.run` with its own fresh NullPool engine (`workers/asyncio_runner.py`) — no loop or connection is shared across threads; syncs take a per-source Postgres advisory lock (`acquire_sync_lock`) so concurrent workers skip rather than race a checkpoint; `task_acks_late=True` re-delivers a crashed thread's task.

Limits to respect before raising `-c`: 8 concurrent Gemini calls ≈ 60–120 requests/min against the Vertex quota, and concurrent hydration hits the source connector's rate limits (move `hydration` to Worker B if Zoho starts returning 429s). NullPool means each running task holds its own DB connections (~2–3 × `-c` total).

Celery beat scheduled tasks:

| Task | Frequency | Queue |
| --- | --- | --- |
| `evaluation.detect_drift` | Every 6 hours | evaluation |
| `evaluation.scan_contradictions_task` | Every 12 hours | evaluation |
| `sync.trigger_scheduled_syncs` | Every 15 minutes | sync |
| `evaluation.calibrate_decision_confidence` | Daily | evaluation |
| `evaluation.mine_decision_patterns` | Daily | evaluation |
| `evaluation.cleanup_hard_deleted_evidence` | Daily | evaluation |

The `cleanup_hard_deleted_evidence` task reaps orphans left by `purge_archived_evidence(mode="hard_delete")` — MinIO raw blobs that no `EvidenceItem.raw_object_ref` points at, plus `graph_edges` rows whose source / target node was an evidence id that has been deleted. It is cheap when there is nothing to do; per-tenant exception isolation means one bad tenant doesn't block the beat. Accepts the literal `"all"` sentinel when invoked directly to sweep every tenant.

### Memory classes and retention

Runtime memory assembly now flows through `backend/src/contextedge/services/memory_service.py`.

- `short_term`: active sessions, current case context, and recent evidence
- `long_term`: validated patterns, approved playbooks, KB-style evidence, and canonicalized identities
- `reasoning`: decision traces, execution steps, approvals, and tool/action history

Retention uses the source policy days as the base window and expands by memory class:

- `short_term`: base retention window
- `reasoning`: `max(base_days * 3, 90 days)`
- `long_term`: `max(base_days * 6, 180 days)`

`retention_service.py` applies those windows while still honoring legal hold exclusions.

### Attachment extraction

Deterministic artifact extraction is available on the `extraction` queue through `artifact.extract_attachment`.

- Supported first-stage formats: `text/plain`, `.log`, `.txt`, `.json`, `.jsonl`, `.ndjson`, `.srt`, and `.vtt`
- Artifact binaries are stored in object storage under `artifacts/<tenant>/<evidence>/<artifact>/...`
- Extracted text is persisted on `attachment_artifacts` and merged back into `evidence_items.body_text` with attachment provenance markers so FTS, embeddings, and episode reconstruction see it
- OCR-heavy document/image formats are still deferred; this stage is intentionally deterministic

### Evidence chunking

Evidence chunking runs on the `extraction` queue alongside normalization. Two task names land here:

- `extraction.chunk_evidence` — async path for large items (post-mortem attachments, long Teams threads). Idempotent on `chunker_version` — replaying the task on the same evidence is a no-op when the parent's existing chunks already match the resolved chunker's version.
- `extraction.embed_chunks_batch` — fans out chunk embeddings in batches of 32 via `generate_embeddings_batch`. Per-tenant LLM budget enforcement (migration `0023`) fires per batch, not per chunk. Idempotent via the `embedding IS NULL` filter — replaying on the same chunk IDs only embeds what's still pending.

Operational caveats:

- After `chunk_evidence_task` writes rows, there is a brief window where `EvidenceChunk.embedding IS NULL` until `embed_chunks_batch_task` completes. The chunk-level vector query handles this naturally (the `<=> :q ORDER BY` skips NULL-embedding rows). If a chunk persists with `embedding IS NULL` past expected SLO, check the `extraction` worker's `llm.usage` events for `outcome = budget_exceeded` — a tripped per-tenant LLM budget cap will block embedding without a hard error.
- Inline chunking is bounded to bodies under 16 KB on the source allowlist (`jira_sm`, `servicenow`, `gmail`, `teams`); larger or unfamiliar sources go async via `chunk_evidence_task`. Tunable via `INLINE_CHUNK_BUDGET_BYTES` and `INLINE_CHUNK_SOURCE_ALLOWLIST` in `workers/extraction_tasks.py`.
- The HNSW index `ix_evidence_chunks_embedding_hnsw` is built `CONCURRENTLY` so the `0030_evidence_chunks` migration does not lock writers; on a backfilled deployment with millions of evidence rows the index build can take minutes.
- A backfill task for legacy `EvidenceItem` rows with `chunked_at IS NULL` is **not yet wired** — see `codewiki/KNOWN_GAPS.md` "Resolved: Evidence chunking foundation" for the deferred follow-up list. Until that ships, only newly-normalized evidence is chunked.

See [codewiki/CHUNKING_DESIGN.md](../codewiki/CHUNKING_DESIGN.md) for the full pipeline narrative.

---

## 8. Health and Observability

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Basic liveness |
| `GET /ready` | Readiness-style stub |
| `GET /metrics` | Prometheus scrape target |

Local URLs:

- `http://localhost:8000/health`
- `http://localhost:8000/ready`
- `http://localhost:8000/metrics`

Logging:

- API uses structlog
- In debug mode logs are developer-friendly console output
- Docker logs are available through `make logs`

---

## 9. Testing and Lint

| Scope | Command |
| --- | --- |
| Backend tests | `make test-backend` or `cd backend && pytest -v` |
| Frontend tests | `cd frontend && npm test` |
| Combined | `make test` |
| Lint | `make lint` |
| Format | `make format` |

Current state:

- Backend tests cover security hardening (RBAC, JSON parse, config validation), evidence FTS search, retention legal-hold, async episode reconstruction, FTS tsvector usage, object-store helpers, runtime sessions/traces, case correlation, contradiction detection, access-aware retrieval, governed execution, identity resolution, memory lifecycle handling, and deterministic artifact extraction. 72 tests currently pass on the implementation branch.
- Frontend `npm test` is a placeholder script and does not run a real unit-test suite.

---

## 10. Operational Caveats

- Do not trigger overlapping backfills or retries for the same `SourceObject`. Sync recovery is bounded, but there is no single-flight guard that serializes manual sync requests per object.
- Evidence dedupe is application-layer and based on normalized content hash. If you are stress-testing sync or recovery behavior, verify duplicates in `evidence_items` rather than assuming the database will reject them.
- Service tokens without `allowed_domain_ids` are tenant-wide for runtime access. Set an explicit allowlist when you want least-privilege behavior.
- Attachment extraction is deterministic-only in this stage. Do not expect OCR or binary document parsing until a later rollout.

---

## 11. Common Issues

| Symptom | What to check |
| --- | --- |
| Login fails or API returns 401 | JWT secret mismatch, expired token, backend restart required after env change |
| Backend crashes on startup with `RuntimeError` about JWT_SECRET_KEY | Set `JWT_SECRET_KEY` to a non-default value, or set `APP_ENV=development` for local work |
| Runtime returns 403 | Caller risk tier cap, playbook/domain mismatch, or service-token domain allowlist |
| Runtime explain returns 404 | Redis cache expired or there was no previous `POST /runtime/match` |
| Missing tables or columns | Run migrations and verify the Alembic head matches `alembic heads` output — the chain moves frequently, so trust the command, not a number written in a doc |
| FTS queries return no results | Verify migration `0007_fts_gin_indexes` was applied and `search_tsvector` columns exist |
| `ModuleNotFoundError: No module named 'contextedge'` | Start host-run services with `cd backend && python dev.py ...` so `src/` is added automatically. If it still fails, check `python -c "import sys; print(sys.executable); print(sys.version)"` and verify you are using a Python 3.12+ backend virtualenv with dependencies installed |
| Celery tasks do not execute | Worker not running, Redis misconfigured, broker URL mismatch |
| MinIO failures | Endpoint, credentials, bucket name, host vs container hostname |
| Object-store offload not working | Verify MinIO is reachable from the worker, check `MINIO_ENDPOINT` and credentials |
| Attachment extraction stays `pending` or `failed` | Verify the extraction worker is running, the artifact object exists in MinIO/S3, and the attachment is a supported deterministic format |
| Evidence chunking is not running (chunks not appearing, `chunked_at IS NULL`) | Verify `extraction` queue worker is consuming `chunk_evidence_task` / `embed_chunks_batch_task`. For legacy rows ingested before `0030`, the backfill task is not yet wired — only newly normalized evidence chunks today. Check structlog `chunking_failed` lines in the normalize worker for chunker bugs. |
| Chunks persist with `embedding IS NULL` past expected window | Likely a tripped per-tenant LLM budget cap (`tenant_llm_budgets`). Check `llm.usage` events for `outcome = budget_exceeded` and `GET /admin/tenant-budget/status`; raise the cap or wait for the daily reset. The next replay of `embed_chunks_batch_task` is idempotent and picks up `embedding IS NULL` rows automatically. |
| Frontend cannot reach API | `NEXT_PUBLIC_API_URL`, backend port, and `APP_CORS_ORIGINS` |
| Contradiction scan is slow or expensive | Reduce scan frequency in `celery_app.py` beat schedule, or limit to specific domains |

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

This removes Docker volumes for Postgres, Redis, and MinIO data.

---

## 13. Production-Oriented Notes

- Replace all default secrets before any shared deployment. The backend will refuse to start with the default `JWT_SECRET_KEY` when `APP_ENV` is not `development`.
- Run the API behind TLS and a real reverse proxy.
- Treat `SERVICE_TOKENS_JSON` as a secrets-bearing config surface.
- Scale Celery workers by queue characteristics rather than as one undifferentiated pool.
- Back up Postgres and object storage independently.
- MinIO bucket is auto-created on startup if missing; verify credentials and endpoint when running against a shared or production S3-compatible store.
- Access policies filter evidence and playbook results at retrieval time. Admin roles (`platform_super_admin`, `tenant_admin`, `domain_admin`) bypass access policy filtering. Non-admin roles will not see evidence or playbooks attached to restricted access policies.
- Contradiction scanning uses LLM calls. Monitor cost and latency for the 12-hour beat schedule; adjust the cron interval or batch size in `celery_app.py` as the KB grows.

---

## 14. Maintenance Rules

Update this runbook when:

- Make targets change
- Docker compose services change
- worker queues change
- migration filenames change
- operational commands or troubleshooting steps change

Update [SETUP_GUIDE.md](SETUP_GUIDE.md) when onboarding steps change. Update [API.md](API.md) when HTTP behavior changes.
