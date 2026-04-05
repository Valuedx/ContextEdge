# ContextEdge

Operational Memory and Living Playbook Platform.

ContextEdge converts fragmented operational evidence (tickets, chat threads, emails, KBs, alerts) into governed, evidence-backed, machine-usable living playbooks for IT operations troubleshooting.

## Documentation

| Document | Description |
| --- | --- |
| [**Technical blueprint**](docs/TECHNICAL_BLUEPRINT.md) | Architecture, component map, data model, doc index, known gaps |
| [**API reference**](docs/API.md) | Auth (`Bearer`, `X-Service-Token`), `/api/v1` routers, runtime/policies/drift, observability URLs |
| [**Runbook**](docs/RUNBOOK.md) | Environment, Docker/Make, Alembic, Celery, health, logs, troubleshooting |
| [**Implementation plan**](CONTEXTEDGE_IMPLEMENTATION_PLAN.md) | Phased delivery checklist, stack choices, repository notes |
| [**Product requirements**](STANDALONE_OPERATIONAL_MEMORY_PRD.md) | Product-level scope and behavior |

OpenAPI for the running API: `http://localhost:8000/docs` (Swagger) or `http://localhost:8000/redoc`.

## Architecture

- **Backend**: Python 3.12+ / FastAPI (modular monolith), SQLAlchemy 2 async + Alembic, structlog, Prometheus metrics (`/metrics`)
- **Frontend**: Next.js 15 (App Router) / React / Tailwind / shadcn/ui / TanStack Query
- **Database**: PostgreSQL 16 + pgvector
- **Queue**: Celery + Redis (sync, hydration, extraction, pattern, evaluation queues)
- **Object Storage**: MinIO (S3-compatible)
- **AI/LLM**: LiteLLM (multi-provider: OpenAI, Anthropic, Azure)
- **Auth**: JWT (`Authorization: Bearer`) for users; optional `X-Service-Token` via `SERVICE_TOKENS_JSON` for integrations (see `.env.example`)

## Quick Start

```bash
# 1. Copy environment config
cp .env.example .env

# 2. Start infrastructure (PostgreSQL, Redis, MinIO)
make up

# 3. Install backend dependencies
cd backend && pip install -e ".[dev]"

# 4. Run database migrations
make migrate

# 5. Seed development data
make seed

# 6. Start backend
make backend-dev

# 7. In another terminal, install and start frontend
cd frontend && npm install && npm run dev
```

## Development

```bash
# Full stack with Docker
make dev

# Run tests
make test

# Lint
make lint
```

## Project Structure

```
docs/
  TECHNICAL_BLUEPRINT.md   # Architecture + data model (see also API.md, RUNBOOK.md)
  API.md                   # HTTP API and auth reference
  RUNBOOK.md               # Operations, migrations, troubleshooting

backend/
  alembic/versions/        # Database migrations (run through 0004_* for policies FKs)
  src/contextedge/
    api/v1/                # FastAPI route handlers (/api/v1/...)
    models/                # SQLAlchemy models
    schemas/               # Pydantic request/response schemas
    services/              # Business logic
    connectors/            # Source connector implementations
    workers/               # Celery task definitions
    ai/                    # LLM abstraction, classifiers, extractors
    graph/                 # Pattern/context graph
    search/                # FTS, vector search, hybrid ranking, risk caps
    middleware/            # Tenant context, audit, SSO stubs

frontend/
  src/
    app/                   # Next.js App Router pages (dashboard, auth, runtime sandbox)
    components/            # React components
    lib/                   # API client, auth, hooks, stores, types
```

## Default Credentials (Dev)

- Admin: `admin@contextedge.local` / `admin123`
- Analyst: `analyst@contextedge.local` / `analyst123`
