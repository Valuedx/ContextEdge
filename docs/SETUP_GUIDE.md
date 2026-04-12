# ContextEdge - Setup Guide

This is the canonical local onboarding guide for ContextEdge.

Use this document for first-time setup. Use [RUNBOOK.md](RUNBOOK.md) after the system is already installed and you need operational commands or troubleshooting. Use [API.md](API.md) for endpoint behavior and [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) for architecture.

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
- Python 3.12+
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

Important variables in [`.env.example`](/d:/Projects/github/ContextEdge/ContextEdge/.env.example):

- `DATABASE_URL`
- `DATABASE_URL_SYNC`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `MINIO_ENDPOINT`
- `MINIO_BUCKET`
- `JWT_SECRET_KEY`
- `FERNET_KEY`
- `OPENAI_API_KEY`
- `SERVICE_TOKENS_JSON`

Minimum changes to make before running locally:

- Replace `JWT_SECRET_KEY` with a real random value (the backend refuses to start with the default when `APP_ENV` is not `development`)
- Generate and set `FERNET_KEY`
- Keep host-run URLs on `localhost`
- Ensure `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` match your Docker Compose configuration

Generate a Fernet key:

```powershell
@'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
'@ | python -
```

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

- PostgreSQL with pgvector on `localhost:5432`
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

This applies the full migration chain through `0009_case_links`, creating stored tsvector columns with GIN indexes, resolution session and decision trace tables, and the case links table.

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

### Step 8: Start Celery beat

```bash
make celery-beat-dev
```

Equivalent:

```bash
cd backend
python dev.py beat
```

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
- Health: `http://localhost:8000/health`
- Metrics: `http://localhost:8000/metrics`
- MinIO console: `http://localhost:9001`

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

---

## 6. Backend-Only Setup

If you do not need the frontend:

1. Start infrastructure with `make up`
2. Install backend dependencies
3. Run `make migrate`
4. Run `make seed`
5. Start `make backend-dev`
6. Start `make celery-dev`

---

## 7. Service Token Setup

To test integration-style runtime access, define `SERVICE_TOKENS_JSON` in `.env`.

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

If `allowed_domain_ids` is omitted, the token gets full-tenant runtime access. If it is an empty list, the token is limited to tenant-wide playbooks only.

---

## 8. Local Development Caveats

- Set `allowed_domain_ids` explicitly on service tokens unless tenant-wide runtime access is intended.
- Avoid overlapping manual backfills or retries for the same source object while testing sync behavior. The system does not yet serialize those requests per object.
- Evidence dedupe is currently application-layer. Concurrency or recovery experiments can still produce duplicate evidence rows until hard database constraints are added.
- MinIO bucket is auto-created on startup. If MinIO is unreachable the backend still starts but logs a warning; raw payload offload will fail until the store is available.
- The contradiction scanner and correlation worker require Celery beat and at least one worker listening on the `evaluation` and `extraction` queues respectively. Start both `make celery-dev` and `make celery-beat-dev` during development to exercise these features.
- Access policy filtering is active on evidence search, runtime match, and the evidence detail endpoint. Non-admin roles will not see items attached to restricted access policies.

---

## 9. Common Commands

| Goal | Command |
| --- | --- |
| Start infrastructure | `make up` |
| Stop infrastructure | `make down` |
| Full Docker stack | `make dev` |
| Start backend | `make backend-dev` |
| Start worker | `make celery-dev` |
| Start beat | `make celery-beat-dev` |
| Start frontend | `make frontend-dev` |
| Apply migrations | `make migrate` |
| Create migration | `make migrate-new msg="..."` |
| Seed data | `make seed` |
| Lint | `make lint` |
| Backend tests | `make test-backend` |

Notes:

- `make test` runs backend tests plus a placeholder frontend test script.
- Backend test suite currently covers 48 tests across security, FTS, retention, episodes, object store, runtime sessions, correlation, contradiction detection, and access-aware retrieval.
- Frontend `npm test` currently prints a skip message and exits successfully.

---

## 10. Troubleshooting Setup

| Problem | Check |
| --- | --- |
| API cannot connect to Postgres | `DATABASE_URL`, Postgres container health, port `5432` |
| Backend crashes with JWT_SECRET_KEY RuntimeError | Set a non-default `JWT_SECRET_KEY` or set `APP_ENV=development` |
| Celery is idle | `CELERY_BROKER_URL`, Redis reachability, worker process |
| Frontend cannot call API | `NEXT_PUBLIC_API_URL`, backend port, CORS |
| MinIO errors | `MINIO_ENDPOINT`, credentials, bucket name |
| Login fails after env edits | Restart backend to reload settings |
| Migration failure on fresh DB | Read [MIGRATIONS.md](MIGRATIONS.md) for the `0001` bootstrap caveat |
| FTS returns empty results | Verify `0007_fts_gin_indexes` is applied (`alembic current`) |
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

---

## 12. Next Documents to Read

- [RUNBOOK.md](RUNBOOK.md) for operational commands and troubleshooting
- [API.md](API.md) for endpoint behavior
- [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) for architecture and design patterns
