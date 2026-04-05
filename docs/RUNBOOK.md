# ContextEdge — Runbook

Operational guide for **local development**, **Docker**, **migrations**, **workers**, and **common failures**. For API behavior and auth headers, see [API reference](API.md). For architecture and data model, see [Technical blueprint](TECHNICAL_BLUEPRINT.md).

---

## Prerequisites

- **Docker** and Docker Compose (for Postgres, Redis, MinIO, optional full stack)
- **Python 3.12+** and **Node.js** (for host-run backend/frontend)
- Copy **`.env.example`** → **`.env`** at the repo root and adjust secrets (JWT, `FERNET_KEY`, LLM keys as needed)

---

## Configuration checklist

Settings load from the environment via **`contextedge.config`** (pydantic-settings). Key groups (see [`.env.example`](../.env.example)):

| Area | Variables (representative) |
| --- | --- |
| Database | `DATABASE_URL` (async), `DATABASE_URL_SYNC` |
| Redis / Celery | `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| Object storage | `MINIO_*`, bucket name |
| Auth | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `SERVICE_TOKENS_JSON` |
| Crypto | `FERNET_KEY` (credential encryption) |
| LLM | `OPENAI_API_KEY`, model defaults, etc. |
| App | `APP_CORS_ORIGINS`, `APP_DEBUG`, `APP_LOG_LEVEL` |

**Compose note:** `docker-compose.dev.yml` overrides several URLs to use service hostnames (`postgres`, `redis`, `minio`) inside the network; keep `.env` consistent when mixing host and container workflows.

---

## Starting infrastructure

**Postgres (pgvector), Redis, MinIO only:**

```bash
make up
```

**Full development stack** (backend, Celery worker, Celery beat, frontend):

```bash
make dev
```

- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- MinIO console: port **9001** (see `docker-compose.yml`)

Stop full stack: `make dev-down` or `docker compose -f docker-compose.dev.yml down`.

---

## Database migrations

Revisions live in **`backend/alembic/versions/`**.

| Revision | Purpose |
| --- | --- |
| `0001_initial_schema` | Base schema |
| `0002_tenant_policies` | `tenant_policies` |
| `0003_source_policy_fks` | Source → policy FKs |
| `0004_evidence_access_policy_fk` | Evidence → access policy FK |

**Apply all pending migrations:**

```bash
make migrate
```

(`cd backend && alembic upgrade head`)

**Create a new revision** (after model changes):

```bash
make migrate-new msg="short description"
```

**Rollback one step:**

```bash
make migrate-down
```

### Practices

- Prefer **Alembic** for shared and production-like environments; do not rely only on **`seed.py`** creating tables.
- Ensure **no orphan UUIDs** on new FK columns before applying policy-related migrations (`0003`, `0004`).
- If the API fails with missing-table errors, confirm migrations have been applied: `alembic current` from `backend/`.

---

## Seed data (development)

```bash
make seed
```

Creates dev tenants/users and sample data as implemented in `backend/src/contextedge/seed.py`. Default logins are documented in the root [README](../README.md) (for example `admin@contextedge.local` / `admin123`).

---

## Running services on the host

With infrastructure up via `make up`:

| Goal | Command |
| --- | --- |
| API (reload) | `make backend-dev` → Uvicorn on **8000** |
| Celery worker | `make celery-dev` |
| Celery beat | `make celery-beat-dev` |
| Frontend | `cd frontend && npm install && npm run dev` |

**Queues** used by worker commands: `default`, `sync`, `hydration`, `extraction`, `pattern`, `evaluation` (see `Makefile` and `docker-compose.dev.yml`).

Workers need reachability to **Postgres**, **Redis**, and **MinIO** using the same logical config as the API.

---

## Health checks and metrics

| Endpoint | Use |
| --- | --- |
| `GET http://localhost:8000/health` | Basic liveness |
| `GET http://localhost:8000/ready` | Readiness-style stub |
| `GET http://localhost:8000/metrics` | Prometheus scrape target |

---

## Logs

- **API**: structlog to console; JSON when `APP_DEBUG` is false (see `main.py` processors).
- **Docker**: `make logs` tails `docker-compose.dev.yml` services.

---

## Testing and lint

| Scope | Command |
| --- | --- |
| Backend tests | `make test-backend` or `cd backend && pytest` |
| Frontend tests | `cd frontend && npm test` |
| Both (if configured) | `make test` |
| Lint | `make lint` (Ruff + ESLint) |
| Format | `make format` |

---

## Common issues

| Symptom | Things to check |
| --- | --- |
| **401** / redirect to login (UI) | Token expired or missing; re-login. `JWT_SECRET_KEY` must match between token issuance and validation. |
| **403** on `/runtime/playbooks/...` | Risk tier above caller cap, or domain mismatch for a domain-scoped playbook. See [API.md — Runtime](API.md#runtime). |
| **404** on `/runtime/explain/...` | Redis key expired or no prior `POST /match` for that `match_id`; tenant mismatch also returns 403. |
| **DB errors** / missing columns | Run `make migrate`; confirm `alembic_version` matches head. |
| **Celery tasks not running** | Worker process up; `CELERY_BROKER_URL` / result backend; Redis reachable. |
| **MinIO upload failures** | `MINIO_ENDPOINT` (host vs Docker service name), credentials, bucket creation. |
| **CORS** | `APP_CORS_ORIGINS` includes frontend origin (e.g. `http://localhost:3000`). |

---

## Production-oriented notes (high level)

- Rotate **`JWT_SECRET_KEY`**, **`FERNET_KEY`**, and MinIO credentials; never ship defaults.
- Run API behind TLS; restrict **`SERVICE_TOKENS_JSON`** to short-lived integration tokens where possible.
- Scale Celery workers per queue; use beat only once per logical scheduler.
- Back up Postgres and object storage according to your RPO/RTO; this repo does not ship a full DR playbook.

---

## Document maintenance

When **Makefile targets**, **compose services**, or **migration filenames** change, update this runbook. When **HTTP behavior** changes, update [API.md](API.md).
