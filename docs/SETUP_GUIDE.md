# ContextEdge - Setup Guide

This is the canonical local onboarding guide for ContextEdge.

Use this document for first-time setup. Use [RUNBOOK.md](RUNBOOK.md) after the system is already installed and you need operational commands or troubleshooting. Use [API.md](API.md) for endpoint behavior and [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) for architecture.

*Verified against the working tree on 2026-08-19.*

---

## 1. Choose a Workflow

| Workflow | Best for | Runs in Docker |
| --- | --- | --- |
| Host-run app + Docker infrastructure | Default development flow | Postgres, Redis, MinIO |
| Full Docker development stack | Fewer host processes | Postgres, Redis, MinIO, backend, worker, beat, frontend |
| Backend-only local work | API and workers only | Postgres, Redis, MinIO |

The recommended workflow is **host-run app + Docker infrastructure**.

---

## 2. Prerequisites

- Docker Desktop or Docker Engine with Compose support
- Python 3.12+ (the `dev.py` launcher refuses to run on anything older — `backend/dev.py:11,45-56`)
- Node.js 20+
- npm

Recommended:

- `make`
- Git

Windows note:

- If `make` is not available, run the underlying `docker compose`, `uvicorn`, `celery`, and `npm` commands directly. Equivalent commands are shown below.

---

## 3. Prepare the Environment

Copy the environment template:

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

Settings are loaded by one pydantic-settings class, `contextedge.config.Settings`, which reads the repo-root `.env` and then `backend/.env`, ignoring unknown keys (`backend/src/contextedge/config.py:10-15`). With two files listed, the **later** one wins, so a key set in `backend/.env` overrides the same key in the repo-root `.env`. Keep one file if you can — a value that "will not change" is usually being overridden by the other file.

Important variables in [`.env.example`](../.env.example):

- `DATABASE_URL` (asyncpg) and `DATABASE_URL_SYNC` (psycopg, used by Alembic)
- `REDIS_URL` (db 0), `CELERY_BROKER_URL` (db 1), `CELERY_RESULT_BACKEND` (db 2) — three different Redis databases on purpose (`backend/src/contextedge/config.py:26-28`)
- `MINIO_ENDPOINT`, `MINIO_BUCKET` (default `contextedge-evidence`)
- `JWT_SECRET_KEY`
- `FERNET_KEY`
- `OPENAI_API_KEY` / Vertex AI credentials, depending on which provider you point LiteLLM at
- `SERVICE_TOKENS_JSON`

Minimum changes to make before running locally:

- Replace `JWT_SECRET_KEY` with a real random value. The backend raises a `RuntimeError` at import time if the default is still in place and `APP_ENV` is not `development` (`backend/src/contextedge/config.py:248-252`).
- Generate and set `FERNET_KEY`. Same fail-fast rule (`config.py:254-264`), and for a good reason: this key encrypts stored source credentials, so a missing or rotated key turns them into unrecoverable garbage.
- Keep host-run URLs on `localhost`.
- Ensure `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` match your Docker Compose configuration.

Generate a Fernet key:

```powershell
@'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
'@ | python -
```

### Defaults worth knowing before your first ingest

You do not need to change these to get started, but they explain what the system will
and will not do on day one:

| Variable | Default | What it means |
| --- | --- | --- |
| `EPISODE_AI_REVIEW` | `off` | The hourly AI pre-review of episode drafts does nothing. Values are exactly `off`, `advisory`, `auto_approve` (`config.py:185-187`). |
| `EPISODE_RESOLUTION_GATE` | `off` | Episode synthesis runs for every settled cluster, including clusters with no fix in them. `cluster` defers those instead (`config.py:175`). |
| `REDACTION_ENABLED` | `true` | Regex redaction runs before anything is embedded or sent to a model (`config.py:236`). |
| `DEFAULT_DAILY_TOKEN_LIMIT` / `DEFAULT_DAILY_COST_CAP_USD` | 2,000,000 tokens / $25 | Applied to any tenant with no `tenant_llm_budgets` row, with `action_on_exceed = block` (`config.py:194-198`). A cold-start backfill can hit this. |
| `RETENTION_PURGE_MODE` | `soft_purge` | Weekly purge scrubs text, summaries, embeddings and blob pointers rather than deleting rows (`config.py:215`). |
| `DEFAULT_EMBEDDING_MODEL` | `text-embedding-3-small` | Whatever you set must return **exactly 3072 dimensions** — `generate_embedding` raises otherwise (`backend/src/contextedge/ai/provider.py:787-793`). |

---

## 4. Recommended Setup: Host-Run App + Docker Infrastructure

### Step 1: Start infrastructure

```bash
make up
```

Equivalent:

```bash
docker compose up -d
```

This starts:

- PostgreSQL with pgvector on `localhost:5432` — image `pgvector/pgvector:pg16`, because the vector index in migration `0032` needs the pgvector server extension at 0.7 or newer
- Redis on `localhost:6379`
- MinIO API on `localhost:9000`
- MinIO console on `localhost:9001`

### Step 2: Install backend dependencies

```bash
cd backend
pip install -e ".[dev]"
cd ..
```

### Step 3: Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### Step 4: Apply migrations

```bash
make migrate
```

Equivalent:

```bash
cd backend
alembic upgrade head
cd ..
```

This walks the whole revision chain in `backend/alembic/versions/`. The chain moves
frequently, so **do not** trust any revision number written in a document — run
`alembic heads` and believe that. What you get on a fresh database includes the stored
`search_tsvector` columns and their GIN indexes (`0007`), resolution sessions and
decision traces (`0008`), case links (`0009`), evidence chunks (`0030`), the halfvec
HNSW vector indexes (`0032`), and everything since.

Two mechanical details that save debugging time later:

- Before running anything, `alembic/env.py` widens the legacy `alembic_version.version_num` column to `VARCHAR(255)` on a **separate bootstrap connection** (`backend/alembic/env.py:70-72`). Old databases created the column as `VARCHAR(32)` and six revision ids in this chain are longer than that, so without the widening the *stamp* fails with "value too long" and it looks like a broken migration.
- If a migration fails on a fresh database, read [MIGRATIONS.md](MIGRATIONS.md) — `0001_initial_schema` builds from model metadata rather than being a frozen DDL snapshot.

### Step 5: Seed development data

```bash
make seed
```

Equivalent:

```bash
cd backend
python dev.py seed
cd ..
```

Seeded users:

- `admin@contextedge.local` / `admin123`
- `analyst@contextedge.local` / `analyst123`

The seed is idempotent for the default tenant slug. The destructive variants
(`reset_db_and_seed`, `demo_maf_seed`) truncate tenant-global tables and are blocked
unless `APP_ENV=development` or `CONTEXTEDGE_ALLOW_DB_RESET=1`
(`backend/src/contextedge/seed_guard.py:20-60`).

### Step 6: Start the backend API

```bash
make backend-dev
```

Equivalent:

```bash
cd backend
python dev.py api
```

### Step 7: Start the Celery worker

```bash
make celery-dev
```

Equivalent:

```bash
cd backend
python dev.py worker
```

This one process consumes **all eight queues** —
`default,sync,hydration,extraction,correlation,embedding,pattern,evaluation`
(`backend/dev.py:16`) — and on Windows it defaults to `-P solo` unless you pass your own
pool (`backend/dev.py:116-124`). That is the right setup for reading code and clicking
through the UI. It is the wrong setup for a real backfill: see "Worker topology" in
[RUNBOOK.md](RUNBOOK.md), where parallelism comes from running several solo **processes**
rather than raising concurrency inside one.

If the worker exits immediately with `worker.migration_mismatch_refusing_to_start`, your
database is behind the code. Run `make migrate`. This check is deliberate — a worker
running against a stale schema corrupts ingestion mid-transaction
(`backend/src/contextedge/workers/celery_app.py:83-139`).

### Step 8: Start Celery beat

```bash
make celery-beat-dev
```

Equivalent:

```bash
cd backend
python dev.py beat
```

Run **exactly one** beat process. A second one double-dispatches every scheduled task.
Beat is what drives scheduled syncs every 15 minutes, drift and contradiction scans,
retention, the hourly dedup sweep, and the hourly episode AI-review sweep — 14 entries
in total (`backend/src/contextedge/workers/celery_app.py:281-384`).

### Step 9: Start the frontend

```bash
make frontend-dev
```

Equivalent:

```bash
cd frontend
npm run dev
```

### Step 10: Verify the stack

Open:

- Frontend: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Liveness: `http://localhost:8000/health`
- Readiness: `http://localhost:8000/ready`
- Metrics: `http://localhost:8000/metrics`
- MinIO console: `http://localhost:9001`

`/health` only says "the process is up". `/ready` is the one that actually tells you the
stack is wired: it checks the database, compares `alembic_version` against the bundled
scripts' head, and pings Redis, each with a 5-second timeout, and returns 503 with a
per-check dict if any of them fail. Object storage is reported as `ok` or `degraded` but
does not gate readiness (`backend/src/contextedge/main.py:179-210`).

---

## 5. Alternative Setup: Full Docker Development Stack

Start everything:

```bash
make dev
```

Equivalent:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Then run migrations inside the backend container:

```bash
docker compose -f docker-compose.dev.yml exec backend alembic upgrade head
```

Seed development data:

```bash
docker compose -f docker-compose.dev.yml exec backend python -m contextedge.seed
```

Available endpoints:

- Frontend: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- MinIO console: `http://localhost:9001`

Stop the stack:

```bash
make dev-down
```

Equivalent:

```bash
docker compose -f docker-compose.dev.yml down
```

Note: `docker-compose.dev.yml` overrides service URLs to container hostnames
(`postgres`, `redis`, `minio`). Host-run workflows must use the `localhost` values from
`.env`. Mixing the two is the most common "MinIO failures" cause.

---

## 6. Backend-Only Setup

If you do not need the frontend:

1. Start infrastructure with `make up`
2. Install backend dependencies
3. Run `make migrate`
4. Run `make seed`
5. Start `make backend-dev`
6. Start `make celery-dev`
7. Start `make celery-beat-dev` if you want scheduled syncs, dedup, or retention to run

---

## 7. Service Token Setup

To test integration-style runtime access, define `SERVICE_TOKENS_JSON` in `.env`. It is a
JSON map from token string to principal, parsed at startup
(`backend/src/contextedge/security_tokens.py:12-36`).

Example:

```json
{
  "svc-dev-token": {
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "user_id": "00000000-0000-0000-0000-000000000010",
    "email": "svc@contextedge.local",
    "roles": ["service_account"],
    "allowed_domain_ids": ["00000000-0000-0000-0000-000000000003"]
  }
}
```

Use it with:

```http
X-Service-Token: svc-dev-token
```

The service-token header wins over a Bearer JWT when both are present, and an invalid
service token is a 403 rather than a fallback to JWT
(`backend/src/contextedge/deps.py:72-114`). If `allowed_domain_ids` is omitted, the token
gets full-tenant runtime access. If it is an empty list, the token is limited to
tenant-wide playbooks only.

---

## 8. Local Development Caveats

- Set `allowed_domain_ids` explicitly on service tokens unless tenant-wide runtime access is intended.
- **Role bindings are stored with a scope but the scope is not enforced.** `RoleBinding.scope_type` / `scope_id` exist in the schema, but login only reads role *names* and `has_role` is a pure name check, so a "domain admin for one domain" holds that role across the whole tenant on every `require_role` route (`backend/src/contextedge/deps.py:37-51`). Finer scoping comes only from token claims that individual routes consult. Single-domain tenants are unaffected; multi-domain tenants must treat grants as tenant-wide. Tracked in `codewiki/KNOWN_GAPS.md`.
- **Concurrent syncs for one source object are now serialized**, which is a change from older versions of this guide. Each sync job takes a transaction-scoped Postgres advisory lock (`pg_try_advisory_xact_lock(hashtext('sync:<object_id>'))`, `backend/src/contextedge/services/sync_worker_service.py:379-395`) and a second worker returns `{"status": "skipped_locked"}` instead of racing the checkpoint (`sync_worker_service.py:427-433`). You can still create confusing state by triggering a backfill and an incremental at the same time, but you will not corrupt a checkpoint.
- **Evidence dedupe is now enforced by the database too.** There is still an application-layer content-hash check, but migration `0026` added a unique index on `(tenant_id, content_hash)`, and the normalize worker catches the `IntegrityError`, rolls back, adopts the winning row, and returns `{"deduped": true, "raced": true}` without re-spending LLM calls (`backend/src/contextedge/workers/extraction_tasks.py:374-409`).
- **Raw payloads over 32 KB are not in the database.** They are uploaded to MinIO at `raw/{tenant}/{raw_id}.json` and the `raw_payload` column keeps only `{"_offloaded": true, "size_bytes": N}` (`backend/src/contextedge/services/ingestion_persistence.py:16,84-87`). Any SQL you write that filters on `raw_evidence_objects.raw_payload` will silently skip the biggest records — which are exactly the longest tickets and articles.
- MinIO bucket is auto-created on startup. If MinIO is unreachable the backend still starts but logs a warning and reports `object_store: degraded` on `/ready`; raw payload offload will fail until the store is available.
- **The correlation and embedding stages need their own queues.** Start `make celery-dev` (which listens on all eight) plus `make celery-beat-dev`. A worker started with a hand-written `-Q extraction,hydration,default` will normalize evidence forever and never build a single episode or embed a single chunk, with no error anywhere.
- Access policy filtering is active on the evidence list, evidence search, and evidence detail endpoints (`api/v1/evidence.py:42,100,240`), on runtime playbook ranking (`search/hybrid_ranker.py:235`), and on the agent graph projection (`graph/agent/repository.py:860`). Non-admin roles will not see items attached to restricted access policies; `platform_super_admin`, `tenant_admin`, and `domain_admin` bypass the filter (`backend/src/contextedge/search/access_control.py:12-39`).

---

## 9. Common Commands

| Goal | Command |
| --- | --- |
| Start infrastructure | `make up` |
| Stop infrastructure | `make down` |
| Full Docker stack | `make dev` |
| Start backend | `make backend-dev` |
| Start worker (all eight queues) | `make celery-dev` |
| Start beat (one instance only) | `make celery-beat-dev` |
| Start frontend | `make frontend-dev` |
| Apply migrations | `make migrate` |
| Create migration | `make migrate-new msg="..."` |
| Roll back one revision | `make migrate-down` |
| Seed data | `make seed` |
| Lint | `make lint` |
| Backend tests | `make test-backend` |
| Frontend tests | `cd frontend && npm test` |

Notes:

- `make test` runs the backend suite (`python -m pytest`) and then the frontend suite.
- The backend suite is large — 173 test files holding roughly 1,900 test functions before parametrization. Do not quote a fixed pass count in a document; run the suite and read the number it prints.
- `npm test` runs `vitest run` against the frontend unit tests (roles, graph API client, graph constants, graph query controls, and the applicability, playbook-step, and thread-conversation components). Older docs describe this as a placeholder script; that is no longer true.

---

## 10. Troubleshooting Setup

| Problem | Check |
| --- | --- |
| API cannot connect to Postgres | `DATABASE_URL`, Postgres container health, port `5432` |
| Backend crashes with JWT_SECRET_KEY RuntimeError | Set a non-default `JWT_SECRET_KEY` or set `APP_ENV=development` (`config.py:248-252`) |
| Backend crashes with a FERNET_KEY RuntimeError | Generate a real Fernet key; the placeholder is refused outside development (`config.py:254-264`) |
| Worker exits at startup with `worker.migration_mismatch_refusing_to_start` | The DB is behind the code's Alembic head — run `make migrate` (`workers/celery_app.py:83-139`) |
| `/ready` returns 503 | Read the `checks` dict it returns: `database`, `migrations`, `redis` are named individually (`main.py:179-210`) |
| Celery is idle | `CELERY_BROKER_URL`, Redis reachability, worker process — and confirm the worker is listening on the queue the task routes to |
| Evidence is ingested but no episodes ever appear | The worker is probably not consuming `correlation`. Check `GET /api/v1/admin/pipeline-health` |
| Chunks exist but stay unsearchable | The worker is probably not consuming `embedding`, or the tenant is over its LLM budget |
| Frontend cannot call API | `NEXT_PUBLIC_API_URL`, backend port, CORS (`APP_CORS_ORIGINS`) |
| MinIO errors | `MINIO_ENDPOINT`, credentials, bucket name, and host vs container hostname |
| Login fails after env edits | Restart backend to reload settings |
| Migration failure on fresh DB | Read [MIGRATIONS.md](MIGRATIONS.md) for the `0001` bootstrap caveat |
| FTS returns empty results | Verify `0007_fts_gin_indexes` is applied (`alembic current`) |
| Semantic search returns far fewer rows than expected | Confirm the pgvector server extension is 0.7+, so migration `0032`'s halfvec HNSW indexes actually built |
| Sessions API returns 404 | Verify `0008_resolution_sessions` migration is applied |

---

## 11. Reset Local State

To rebuild from scratch:

```bash
docker compose down -v
docker compose -f docker-compose.dev.yml down -v
make up
make migrate
make seed
```

This removes Docker volumes for Postgres, Redis, and MinIO data.

One thing this does **not** clear on its own: Celery task messages live in Redis db 1,
so a poison message on a queue survives a Postgres rebuild. `docker compose down -v`
above removes the Redis volume too, which is why the reset uses it.

---

## 12. Next Documents to Read

- [RUNBOOK.md](RUNBOOK.md) for operational commands, worker topology, and troubleshooting
- [API.md](API.md) for endpoint behavior
- [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) for architecture and design patterns
- [../codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md) before you assume any feature works end to end
