# ContextEdge

Operational Memory and Living Playbook Platform.

ContextEdge turns fragmented operational evidence from tickets, chat, email, and knowledge systems into governed, evidence-backed, versioned playbooks for human review and runtime retrieval.

## Documentation

| Document | Description |
| --- | --- |
| [**Setup guide**](docs/SETUP_GUIDE.md) | End-to-end local setup for Docker-first and host-run workflows |
| [**Technical blueprint**](docs/TECHNICAL_BLUEPRINT.md) | Architecture, component map, design patterns, data model |
| [**API reference**](docs/API.md) | Auth, `/api/v1` router surface, runtime semantics, observability URLs |
| [**Runbook**](docs/RUNBOOK.md) | Operational commands, migrations, workers, troubleshooting |
| [**Implementation plan**](CONTEXTEDGE_IMPLEMENTATION_PLAN.md) | Phased checklist and repository-status planning document |
| [**Product requirements**](STANDALONE_OPERATIONAL_MEMORY_PRD.md) | Product-level scope and behavior |

OpenAPI for a running local API:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Architecture Snapshot

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2 async, Alembic, structlog, Prometheus metrics
- **Frontend:** Next.js 16 App Router, React, Tailwind, shadcn/ui, TanStack Query
- **Database:** PostgreSQL 16 with pgvector, stored tsvector FTS columns with GIN indexes
- **Queue:** Celery with Redis (queues: default, sync, hydration, extraction, pattern, evaluation)
- **Object storage:** MinIO (S3-compatible) for raw evidence offload and attachments
- **AI integration:** LiteLLM with provider-specific keys (OpenAI, Anthropic, Google/Vertex AI)
- **Auth:** Bearer JWT for users and optional `X-Service-Token` for service integrations
- **Graph:** PostgreSQL adjacency table (`graph_edges`) for pattern/entity/contradiction relationships
- **Decision traces:** Resolution sessions with append-only trace events for runtime audit

## Quick Start

The recommended setup flow is documented in [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md).

Shortest host-run path:

```bash
make up
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd ../frontend && npm install
cd ..
make migrate
make seed
make backend-dev
```

Windows PowerShell equivalent:

```powershell
make up
cd backend
# If `py` is unavailable on your machine, use the full path to a Python 3.12+
# executable instead.
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd ..\frontend
npm install
cd ..
make migrate
make seed
make backend-dev
```

Then, in separate terminals:

```bash
make celery-dev
make frontend-dev
```

## Development

Common commands:

```bash
make up
make backend-dev
make celery-dev
make celery-beat-dev
make frontend-dev
make migrate
make seed
make lint
make test
```

Notes:

- `make dev` starts the full Docker development stack.
- Current Alembic head is `0009_case_links`. Run `make migrate` after pulling to apply any new revisions.
- Frontend `npm test` is currently a placeholder script; there is no real frontend unit-test suite yet.
- The backend enforces a non-default `JWT_SECRET_KEY` when `APP_ENV` is not `development`. Set a real secret before deploying to staging or production.

## Known Constraints

- Sync scheduling is not single-flight per source object yet. Avoid overlapping manual backfills or retries for the same object.
- Evidence dedupe is currently application-layer and hash-based; there is not yet a database uniqueness constraint that hard-prevents duplicate `EvidenceItem` rows under concurrency.
- Residual operational caveats are tracked in [docs/TECHNICAL_BLUEPRINT.md](docs/TECHNICAL_BLUEPRINT.md) and [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Project Structure

```text
docs/
  SETUP_GUIDE.md
  TECHNICAL_BLUEPRINT.md
  API.md
  RUNBOOK.md
  MIGRATIONS.md

backend/
  alembic/versions/        # 0001..0009 migration chain
  src/contextedge/
    api/v1/                 # FastAPI routers (auth, evidence, episodes, patterns,
                            #   playbooks, runtime, sessions, evaluations, drift, ...)
    models/                 # SQLAlchemy ORM (evidence, episode, pattern, playbook,
                            #   session, policy, evaluation, audit, tenant)
    schemas/                # Pydantic request/response models
    services/               # Business logic (playbook lifecycle, retention, correlation,
                            #   contradiction detection, session/trace, object store, ...)
    connectors/             # Source-specific ingestion adapters
    workers/                # Celery tasks (extraction, correlation, pattern, evaluation)
    ai/                     # LLM provider, extractors, classifiers, generators
    graph/                  # Context graph builder and query helpers
    search/                 # Hybrid ranker, FTS, vector search, access control
    middleware/             # Request audit, tenant context

frontend/
  src/
    app/
    components/
    lib/
```

## Default Development Credentials

- `admin@contextedge.local` / `admin123`
- `analyst@contextedge.local` / `analyst123`
