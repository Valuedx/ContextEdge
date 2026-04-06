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
- `extraction`
- `pattern`
- `evaluation`

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

- Backend tests exist but coverage is still limited.
- Frontend `npm test` is a placeholder script and does not run a real unit-test suite.

---

## 10. Operational Caveats

- Do not trigger overlapping backfills or retries for the same `SourceObject`. Sync recovery is bounded, but there is no single-flight guard that serializes manual sync requests per object.
- Evidence dedupe is application-layer and based on normalized content hash. If you are stress-testing sync or recovery behavior, verify duplicates in `evidence_items` rather than assuming the database will reject them.
- Service tokens without `allowed_domain_ids` are tenant-wide for runtime access. Set an explicit allowlist when you want least-privilege behavior.

---

## 11. Common Issues

| Symptom | What to check |
| --- | --- |
| Login fails or API returns 401 | JWT secret mismatch, expired token, backend restart required after env change |
| Runtime returns 403 | Caller risk tier cap, playbook/domain mismatch, or service-token domain allowlist |
| Runtime explain returns 404 | Redis cache expired or there was no previous `POST /runtime/match` |
| Missing tables or columns | Run migrations and verify Alembic head |
| `ModuleNotFoundError: No module named 'contextedge'` | Start host-run services with `cd backend && python dev.py ...` so `src/` is added automatically. If it still fails, check `python -c "import sys; print(sys.executable); print(sys.version)"` and verify you are using a Python 3.12+ backend virtualenv with dependencies installed |
| Celery tasks do not execute | Worker not running, Redis misconfigured, broker URL mismatch |
| MinIO failures | Endpoint, credentials, bucket name, host vs container hostname |
| Frontend cannot reach API | `NEXT_PUBLIC_API_URL`, backend port, and `APP_CORS_ORIGINS` |

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

- Replace all default secrets before any shared deployment.
- Run the API behind TLS and a real reverse proxy.
- Treat `SERVICE_TOKENS_JSON` as a secrets-bearing config surface.
- Scale Celery workers by queue characteristics rather than as one undifferentiated pool.
- Back up Postgres and object storage independently.

---

## 14. Maintenance Rules

Update this runbook when:

- Make targets change
- Docker compose services change
- worker queues change
- migration filenames change
- operational commands or troubleshooting steps change

Update [SETUP_GUIDE.md](SETUP_GUIDE.md) when onboarding steps change. Update [API.md](API.md) when HTTP behavior changes.
