# ContextEdge — Developer Guide

Welcome. This guide is for a developer joining the team. It explains things from scratch: how to get the stack running, how the pieces fit, and how to add each kind of new thing without breaking the pipeline.

For every feature we try to answer:
- **What** is it?
- **Why** do we need it?
- **Where** does it live? (with `file:line` you can click through)
- **Who** calls it?
- **What happens next?**
- **Input** and **output**?
- **Failure behavior**?
- **Design rationale**?

Files are rated 1–10 for importance so you know what to read first.

> **Verified against the working tree on 2026-08-19.** Line numbers drift as files change — search for the named symbol if a citation looks a few lines off.

---

## 1. Prerequisites

### Software

- **Python 3.12+**
  - **What:** the backend language.
  - **Why:** the launcher hard-refuses anything older (`backend/dev.py:11, 45-56`), and CI pins 3.12 (`.github/workflows/ci.yml:35`).
- **Node.js 22**
  - **What:** the frontend runtime.
  - **Why:** CI runs Node 22 deliberately. The jsdom/undici stack in our lockfile needs `util.markAsUncloneable`, which Node 20 lacks — the first CI run failed on exactly that (`.github/workflows/ci.yml:53-56`). Node 20 may work for `npm run dev` but will fail `npm test`.
- **npm** — package manager, drives `frontend/package.json`.
- **Docker + Docker Compose** — runs PostgreSQL, Redis and MinIO locally (`docker-compose.yml`). Using containers for infrastructure keeps every machine and CI identical.
- **Git**.
- **make** (optional but recommended) — short commands instead of long ones (`Makefile`, rated 8/10). On Windows without `make`, open the `Makefile` and run the underlying command; each target is one or two lines.

### System

- **RAM:** 8 GB minimum, 16 GB comfortable (Postgres + Redis + MinIO + Next.js + several Celery processes).
- **Disk:** ~20 GB for images, volumes and `node_modules`.
- **OS:** Windows 10/11, macOS, or Linux. Windows is a first-class dev target here — see [§3.4 Windows worker topology](#34-windows-worker-topology), which is not optional reading.

---

## 2. Initial setup

### 2.1 Clone and configure

```bash
git clone <repository_url> ContextEdge
cd ContextEdge
```

Copy the environment template. `.env` lives at the **repo root** and is gitignored; `config.py` reads the root `.env` first, then `backend/.env` (`backend/src/contextedge/config.py:10-15`).

```powershell
Copy-Item .env.example .env    # Windows PowerShell
```
```bash
cp .env.example .env           # macOS / Linux
```

### 2.2 Generate the two keys

Both commands are printed in `.env.example` next to the key they fill.

**`FERNET_KEY`** — encrypts stored source credentials at rest:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**`JWT_SECRET_KEY`** — signs auth tokens:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Failure behavior if you skip these:** in `development` the app boots with the defaults. Outside development, `config.py` raises `RuntimeError` at *import time* — a default JWT secret at `config.py:248-252`, a missing or placeholder Fernet key at `config.py:254-264`. The Fernet guard is deliberately harsh: if that key changes, every previously encrypted source credential becomes unrecoverable garbage, so failing to boot is the kind outcome.

### 2.3 Start the infrastructure

```bash
make up
# equivalent: docker compose up -d
```

This starts three containers (`docker-compose.yml`):

| Service | Image | Ports | Why |
| --- | --- | --- | --- |
| postgres | `pgvector/pgvector:pg16` | 5432 | relational rows, full-text search **and** vector embeddings — one database, no separate vector store |
| redis | `redis:7-alpine` | 6379 | Celery broker (DB 1), result backend (DB 2), app cache (DB 0) |
| minio | `minio/minio` | 9000 API, 9001 console | raw payloads over 32 KB and attachment bytes |

> ### Known trap: the Postgres port in `.env.example`
> `.env.example` ships `DATABASE_URL=...@localhost:5433/contextedge` (`.env.example:11-14`) but `docker-compose.yml` publishes **5432:5432** (`docker-compose.yml:9-10`). If you copy the template verbatim you will get "connection refused" on first boot. Either change your `.env` to `5432`, or map 5433 in a compose override. The credentials come from the same `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`), so whatever you set there is what the container is created with — and changing them after the volume exists has no effect until you drop the volume.

**Failure behavior per service:**
- **Postgres down or wrong URL** → the backend crashes on startup with an `asyncpg`/SQLAlchemy connection error, and `/ready` returns 503.
- **Redis down** → Celery workers cannot start; the API's `/ready` reports the Redis check failed.
- **MinIO down** → the API still starts (the lifespan marks `object_store_ok=False`, `main.py:44-59`), but raw-payload offload raises and the sync run fails. Timeouts are 1 s each with a single attempt (`services/object_store.py:28-33`) so a slow MinIO fails fast instead of stalling a worker.

### 2.4 Install dependencies

```bash
cd backend
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

```bash
cd frontend
npm install
```

---

## 3. Running the project

### 3.1 Option A — host-run app (recommended for iteration)

Five processes. Open five terminals, or use a process manager.

| # | Command | What it starts | Where to look |
| --- | --- | --- | --- |
| 1 | `make backend-dev` (`cd backend && python dev.py api`) | FastAPI with hot reload on `http://localhost:8000` | `backend/dev.py:89-100` |
| 2 | `make celery-dev` (`cd backend && python dev.py worker`) | a Celery worker consuming **all eight queues** | `backend/dev.py:102-126` |
| 3 | `make celery-beat-dev` (`cd backend && python dev.py beat`) | the scheduler for the 14 recurring tasks | `backend/dev.py:127-137` |
| 4 | `make frontend-dev` (`cd frontend && npm run dev`) | Next.js on `http://localhost:3000` | `frontend/package.json` |
| 5 | `make migrate` then `make seed` (once) | schema + dev data | see §4, §5 |

`python dev.py` prepends `backend/src` to `PYTHONPATH` for you (`dev.py:19-26`), which is why running `uvicorn` directly usually fails with `ModuleNotFoundError: No module named 'contextedge'`.

### 3.2 The eight queues — read this before you start a worker

```
default, sync, hydration, extraction, correlation, embedding, pattern, evaluation
```

That list is `DEFAULT_QUEUES` in `backend/dev.py:16`, and it is the authority. The routing table lives at `backend/src/contextedge/workers/celery_app.py:226-279`.

**Why this matters more than it looks.** The `correlation` and `embedding` lanes were split out on 2026-08-17 after measured starvation: the extraction queue was growing ~70 tasks/min at 8,255 deep, `correlate_evidence` was dispatched but never consumed, and 1,879 chunks existed with only 289 (15 %) embedded. Evidence was being ingested and silently never becoming retrievable, and episodes stayed at zero. `dev.py:12-16` records that a stock deployment ran for a month with those two lanes unconsumed.

If you start a worker with a hand-written `-Q` list, include all eight or you will reproduce that failure locally. [RUNBOOK.md §7.1](RUNBOOK.md) now lists the same eight and its Windows worker block includes `correlation` and `embedding`; if you are reading an older copy of that file, trust `dev.py:16`.

### 3.3 Option B — full Docker development

```bash
make dev
# equivalent: docker compose -f docker-compose.dev.yml up --build
```

- **Rationale:** your host stays clean and the environment is identical to CI.
- **Drawback:** slower rebuilds, especially when Python dependencies change.
- The dev compose worker already uses the correct eight-queue list (`docker-compose.dev.yml:47`).

### 3.4 Windows worker topology

This is the part that surprises people. Two facts, both measured:

1. **Celery's prefork pool does not work on Windows.**
2. **`-P threads` does not work either for the LLM-bearing lanes.** LiteLLM holds asyncio locks bound to the loop that created them, so a threads pool raises "Lock is bound to a different event loop" on every enrichment call, which trips the provider circuit breaker and fails the run near-silently. Measured on a live backfill, 2026-08-16 (`docs/RUNBOOK.md:281-282`). Threads remain fine for lanes that make no LLM call, which is what the launcher's own comment says (`dev.py:113-115`).

So parallelism comes from **separate processes**, each `-P solo` with its own event loop:

```powershell
# Worker A — the parallel one. Ticket processing is ~95% waiting on the LLM,
# so process parallelism is near-linear. Note ALL EIGHT lanes minus the ones
# Worker B owns.
1..4 | ForEach-Object {
  Start-Process python -ArgumentList "-m","celery","-A","contextedge.workers.celery_app",`
    "worker","-l","INFO","-n","workerA$_@%h",`
    "-Q","extraction,hydration,correlation,embedding,default","-P","solo"
}

# Worker B — the serialized one. Clustering and playbook generation operate on
# the whole graph and have NO advisory lock (unlike sync), so two concurrent
# runs could mint duplicate patterns.
python -m celery -A contextedge.workers.celery_app worker -l INFO -n workerB@%h -Q sync,pattern,evaluation -P solo

# Beat — exactly ONE instance. A second beat double-dispatches every entry.
python -m celery -A contextedge.workers.celery_app beat -l INFO
```

`dev.py` defaults to `-P solo` on Windows unless you supply your own pool (`dev.py:113-124`).

**Why the split is safe:** every task runs `asyncio.run` with a fresh `NullPool` engine (`workers/asyncio_runner.py:10-34`) so no loop or connection is shared; syncs take a per-source-object Postgres advisory lock so concurrent workers skip rather than race a checkpoint (`services/sync_worker_service.py:379-395`); and `task_acks_late=True` re-delivers a crashed worker's task (`workers/celery_app.py:199`).

**Ceilings to respect:** roughly 8 concurrent Gemini calls ≈ 60–120 requests/min against the Vertex quota, and concurrent hydration can get you rate-limited by the source (move `hydration` to Worker B if Zoho starts returning 429s). NullPool means each running task holds its own DB connections — budget ~2–3 × concurrency.

### 3.5 Before a bulk backfill

Measured on a live 84-ticket Zoho backfill: a cold-start ingest burned the 2,000,000-token deployment-default daily budget in about two hours, and the `block` action froze the pipeline mid-run until an operator intervened.

1. **Provision a `tenant_llm_budgets` row** for the onboarding tenant (size it around 100k tokens per thread-heavy ticket), or set its action to `warn` for the window and restore afterwards. `PUT /api/v1/admin/tenant-budget`.
2. **Use the connector's own filter key** — `module_filters` for Zoho Desk, `table_filters` for ServiceNow. The wrong key is silently ignored and the whole modified window syncs.
3. **Consider `EPISODE_RESOLUTION_GATE=cluster`** for corpora where many tickets carry no resolution. Episode synthesis was ~73 % of cold-start spend on the measured run; the gate defers those clusters at zero LLM cost and re-checks as new evidence arrives.

---

## 4. Database migrations

We use **Alembic**. There are 72 revision files in `backend/alembic/versions/`.

### How it works
- **What:** Alembic compares your SQLAlchemy models to the database and generates ordered SQL scripts.
- **Where:** `backend/alembic/versions/`, driven by `backend/alembic/env.py` (rated 7/10).
- **env.py mechanics worth knowing:**
  - It imports the model modules so `Base.metadata` is complete (`env.py:12-29`). **If a new model is not reachable from those imports, autogenerate will not see it** and will happily generate a migration that drops nothing and creates nothing.
  - The URL comes from `settings.database_url_sync` (`env.py:42-44`).
  - Before running anything online it widens `alembic_version.version_num` on a **separate bootstrap connection with its own commit** (`env.py:70-72`). Doing that on the migration connection made Alembic see a transaction it did not start, and `alembic upgrade` then reported success while changing nothing. The widening itself is idempotent (`backend/src/contextedge/migration_support.py:58-80`). It exists because six revision ids in this chain exceed 32 characters and databases created by pre-1.10 Alembic sized that column at `VARCHAR(32)` — those upgrades died on the *stamp*, which reads like a broken migration.

### Commands

| Goal | Command | Notes |
| --- | --- | --- |
| Apply everything | `make migrate` (`cd backend && alembic upgrade head`) | run this every time you pull |
| New migration | `make migrate-new msg="add_widgets_table"` | **always read the generated script** before committing |
| Roll back one | `make migrate-down` | destructive migrations lose data permanently |
| What am I on? | `cd backend && alembic current` | |
| What is the head? | `cd backend && alembic heads` | |

> **Standing rule: never quote a head revision number in a doc.** Trust `alembic heads`. Two previous doc-drift incidents came from hardcoded head numbers (`codewiki/KNOWN_GAPS.md:49`).

### Two runtime guards on the head
- The API's `/ready` compares `alembic_version` to the bundled scripts' head and returns **503** on mismatch (`backend/src/contextedge/main.py:89-106, 179-210`).
- Celery workers **exit at startup** on a definite mismatch (`workers/celery_app.py:83-139`). Without that, workers would consume the normalize queue against a stale schema and corrupt ingestion mid-transaction. If your worker keeps dying at boot, run `make migrate`.

### Migrations that need operator care
- `0026` / `0027` need the pre-migration dedupe and NULLing SQL documented in [RUNBOOK.md](RUNBOOK.md).
- `0032_halfvec_hnsw_indexes` **requires pgvector server extension ≥ 0.7** and fails loud below it. `docker-compose.yml` pins `pgvector/pgvector:pg16`. An environment stamped at an earlier revision of that file never re-executes it and stays on sequential scans (`codewiki/KNOWN_GAPS.md:40`).

---

## 5. Seeding data

### `seed.py` — rated 8/10
- **Where:** `backend/src/contextedge/seed.py`
- **What:** inserts the default tenant, workspace, and domain. Users are not hardcoded; they come from the database (Settings) or optional `SEED_*` environment variables.
- **Run it:** `make seed` (`cd backend && python dev.py seed`).
- **Rationale:** tenant structure should be turnkey. Sign-in uses hashed passwords already stored on `users`.

### The destructive scripts are guarded
`reset_db_and_seed.py` and `demo_maf_seed.py` TRUNCATE tenant-global tables. `seed_guard.require_destructive_reset_allowed` refuses to run either unless `APP_ENV=development` or `CONTEXTEDGE_ALLOW_DB_RESET=1` (`backend/src/contextedge/seed_guard.py:35-60`). `demo_maf_seed.py` additionally seeds context-graph and playbook data for Microsoft Agent Framework demos.

### Default credentials
Users and passwords are stored in the database. Seed does not hardcode
accounts. Create users in Settings, or pass `SEED_*` environment variables
when running `python -m contextedge.seed`.

> ⚠️ Never commit usernames or passwords in application or UI code.

Note that `users.email` is **unique per tenant, not globally** (`models/tenant.py:68-85`). Login handles that deliberately: it fetches up to five matching active users, and if the same email and password work in two tenants it returns 401 "Ambiguous account" rather than guessing (`api/v1/auth.py:35-101`).

---

## 6. How to add a new feature

### 6.1 Add a new API endpoint

**Design rationale:** routers stay thin — validate, authorize, call a service, serialize. All logic lives in `services/`. That is what keeps things testable, and the whole backend test suite runs without live services because of it.

#### Step 1 — schema in `schemas/`
`backend/src/contextedge/schemas/widget.py`:
```python
from pydantic import BaseModel, Field

class WidgetCreate(BaseModel):
    name: str = Field(..., description="The name of the widget")
    size: int = Field(default=10)

class WidgetResponse(BaseModel):
    id: str
    name: str
    size: int
```

#### Step 2 — model in `models/`
`backend/src/contextedge/models/widget.py`:
```python
import uuid
from sqlalchemy import String, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from contextedge.models.base import Base, TenantScopedMixin

class Widget(Base, TenantScopedMixin):
    __tablename__ = "widgets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=10)
```

**`TenantScopedMixin` is not optional for tenant data** (`models/base.py:22-27`). It adds the indexed `tenant_id` and, through `TimestampMixin`, the `created_at`/`updated_at` columns. Forgetting it is how a table ends up queryable across tenants.

**Then import the module in `backend/alembic/env.py`** (or make it reachable from something already imported there), or Alembic will not see your table.

#### Step 3 — service in `services/`
```python
from sqlalchemy.ext.asyncio import AsyncSession
from contextedge.models.widget import Widget
from contextedge.schemas.widget import WidgetCreate

async def create_widget(db: AsyncSession, tenant_id, data: WidgetCreate) -> Widget:
    widget = Widget(tenant_id=tenant_id, name=data.name, size=data.size)
    db.add(widget)
    await db.flush()
    return widget
```

Prefer `await db.flush()` in the service and let the caller commit. `get_db` commits at the end of a successful request (`database.py:29-42`), and in a Celery task `run_async` commits after the task body returns (`workers/asyncio_runner.py:10-28`). Committing inside a service makes it unusable from a worker that needs the whole task to be one transaction.

#### Step 4 — router in `api/v1/`
```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from contextedge.database import get_db
from contextedge.deps import CurrentUser, get_current_user
from contextedge.schemas.widget import WidgetCreate, WidgetResponse
from contextedge.services import widget_service

router = APIRouter()

@router.post("/", response_model=WidgetResponse)
async def create(
    widget_in: WidgetCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    user.require_role("knowledge_manager")
    return await widget_service.create_widget(db, user.tenant_id, widget_in)
```

**Always scope by `user.tenant_id`, never by a tenant id in the request body.** A previous review found bulk-delete resolving caller-supplied UUIDs *before* any tenant check — cross-tenant deletion was live (`codewiki/KNOWN_GAPS.md:46`). The rule that came out of it: resolve and authorize first, and a single foreign id fails the whole request with 404 before any statement runs.

**Role names in use:** `platform_super_admin`, `tenant_admin`, `domain_admin`, `knowledge_manager`, `playbook_reviewer`. Note that `has_role` short-circuits True for `platform_super_admin`, `tenant_admin` and `admin` (`deps.py:37-44`), and that `RoleBinding.scope_type`/`scope_id` are stored but **not enforced** — a domain admin bound to one domain holds that role tenant-wide (`codewiki/KNOWN_GAPS.md:187-191`).

#### Step 5 — register the router
`backend/src/contextedge/api/v1/__init__.py`: add the module to the import block (`:5-39`) and one `router.include_router(...)` line (`:41-83`).

#### Step 6 — migration
`make migrate-new msg="add_widgets_table"` then `make migrate`.

#### Step 7 — test
`backend/tests/test_widgets.py`, then `make test-backend`.

```mermaid
sequenceDiagram
    participant Frontend
    participant Router as api/v1/widgets.py
    participant Deps as deps.get_current_user
    participant Service as services/widget_service.py
    participant DB as PostgreSQL
    Frontend->>Router: POST /api/v1/widgets {name: "Alpha"}
    Router->>Deps: resolve principal, require_role
    Deps-->>Router: CurrentUser(tenant_id, roles)
    Router->>Service: create_widget(db, tenant_id, data)
    Service->>DB: INSERT INTO widgets (tenant_id, name, size)
    DB-->>Service: row
    Service-->>Router: Widget
    Router-->>Frontend: 200 {id, name, size}
```

---

### 6.2 Add a new database table (no endpoint yet)

1. Create the model under `models/`, inheriting `Base` and `TenantScopedMixin` if it holds tenant data.
2. Make it reachable from `backend/alembic/env.py:12-29`.
3. `make migrate-new msg="new_table_name"` and **read the script**.
4. Add service functions rather than letting callers write raw queries.

**One more rule that CI enforces:** `backend/tests/test_governance_column_writers.py` scans every `mapped_column` for a writer somewhere under `src/contextedge` and asserts set equality against a register of deliberately-unwritten columns, each carrying an owner and a reason. Set equality runs both ways, so a column that later *gains* a writer also fails CI until its register entry is removed. If you add a column you do not write yet, register it with a reason — a NULL-by-construction column that looks like shipped capability is exactly the problem that test exists to stop.

---

### 6.3 Add a new UI tab

**Where:** `frontend/src/app/(dashboard)/`

1. **Create the page.** Next.js App Router uses file-based routing, so `widgets/page.tsx` becomes `/widgets`.
   ```tsx
   export default function WidgetsPage() {
     return (
       <div className="p-6">
         <h1 className="text-2xl font-bold mb-4">Widgets</h1>
       </div>
     );
   }
   ```
2. **Add it to the sidebar.** `frontend/src/components/shell/sidebar-nav.tsx:44-70` holds the ordered `navItems` array. Add `{ label, href, icon, requiredRoles? }`. Omit `requiredRoles` to show it to everyone.
3. **Fetch data** with TanStack Query. API helpers live in `frontend/src/lib/api.ts` and `graph-api.ts`; shared hooks in `frontend/src/lib/hooks/`.
4. **Build the UI** from `frontend/src/components/ui/` (shadcn/ui) plus the domain folders `components/common`, `components/graph`, `components/sources`, `components/patterns`, `components/decisions`.

> **Nav visibility is UX filtering, not security.** The frontend's `hasRole` treats only `platform_super_admin` as a super-role (`frontend/src/lib/roles.ts:7-9`), while the backend also short-circuits `tenant_admin` and `admin` (`deps.py:37-44`). So a `tenant_admin` sees only nav items that list `tenant_admin` explicitly, yet the API would authorize them for `knowledge_manager`-gated calls anyway. Real enforcement is the API's 401/403 — the dashboard layout's redirect is client-side only (`frontend/src/app/(dashboard)/layout.tsx:17-21`).

---

### 6.4 Add a new Celery task

**What:** a background job. **Why:** LLM calls take seconds to a minute and a backfill takes hours; neither can happen inside an HTTP request.

1. **Write the task** in `backend/src/contextedge/workers/my_tasks.py`:
   ```python
   from contextedge.workers.celery_app import celery_app
   from contextedge.workers.asyncio_runner import run_async

   @celery_app.task(name="evaluation.process_widget", bind=True, max_retries=3, default_retry_delay=60)
   def process_widget_task(self, widget_id: str, tenant_id: str):
       async def work(db):
           return await do_the_thing(db, widget_id, tenant_id)
       try:
           return run_async(work)
       except Exception as exc:
           raise self.retry(exc=exc)
   ```

2. **Always go through `run_async`.** It creates a fresh `NullPool` engine and session per task, commits on success and rolls back on exception (`workers/asyncio_runner.py:10-34`). Never share a session, engine or event loop across tasks.

3. **Register the module** in the `include=[...]` list at `workers/celery_app.py:142-190`, or the worker will not discover the task.

4. **Choose the queue by naming the task correctly.** Routing is by task-name prefix and is **order-matched** (`celery_app.py:226-279`). `evaluation.*` → `evaluation`, `pattern.*` → `pattern`, `extraction.*` → `extraction` (with three explicit exceptions that go to `correlation` and two that go to `embedding`), `sync.*` → `sync`, `hydration.*` → `hydration`. A short name that matches nothing lands on `default` — which is how `identity.*` and `maintenance.*` end up there. If your task needs its own lane, add an explicit route **above** the wildcards and add the queue to `dev.py:16` and `docker-compose.dev.yml`.

5. **Dispatch after commit, never before.** This is the single most common bug in this codebase's history. A task consumed before its transaction commits reads stale state and no-ops **without retry**; and a task dispatched from a transaction that then rolls back names a row that never existed. The pattern where you own the commit: commit, then `.delay(...)`, and treat a broker failure as a warning, not a rollback — see `api/v1/episodes.py:255-278` and `workers/evaluation_tasks.py:273-331`, both of which carry the comment explaining it.

   **Where you do not own the commit** — a service called inside `run_async` or behind a `get_db` dependency — use `dispatch_after_commit(db, task_name, args)` from `services/deferred_dispatch.py:72-95`. It parks the send on the session and fires it from SQLAlchemy's `after_commit` event, discarding it on `after_rollback`. `pattern_service.create_pattern_from_episodes` uses it for `pattern.generate_playbook_candidate` (`services/pattern_service.py:192-194`) after a rolled-back clustering pass left 65 queued tasks naming patterns that never existed.

6. **Add a beat entry** only if it is genuinely recurring: `workers/celery_app.py:281-384`. Fan-out tasks take the literal `"all"` sentinel and iterate tenants with per-tenant try/except so one bad tenant never stops the sweep. Schedule it unconditionally even if a setting gates it — `ai-review-episodes-hourly` does exactly that so enabling the feature needs no beat restart.

7. **Test the routing.** `backend/tests/test_celery_queue_routing.py` exists for this.

---

### 6.5 Add a new connector

**What:** connectors pull records from external systems. **Why:** the operational memory graph is built from the tools humans actually use.

1. **Create the package:** `backend/src/contextedge/connectors/new_system/`.
2. **Subclass `BaseConnector`** (`connectors/base.py:78-141`) and implement all five abstract methods:

   | Method | Returns | Notes |
   | --- | --- | --- |
   | `validate_credentials()` | `CredentialStatus` | should name what was and was not granted — partial scopes are normal |
   | `discover_objects()` | `list[DiscoveredObject]` | one per syncable module/table/mailbox; skip an unreadable one rather than aborting |
   | `backfill(object_id, object_type, window, checkpoint)` | `BackfillResult` | set `has_more=True` when a page budget is spent |
   | `fetch_changes(object_id, object_type, checkpoint)` | `ChangeResult` | the checkpoint is **non-optional** |
   | `hydrate_thread(thread_ref)` | `HydratedThread` | a no-op is acceptable if the source has no conversation API |

3. **Emit `IngestionEvent`s** — `external_id`, `source_type`, `object_type`, `content` dict, optional `thread_id`, `timestamp`, `metadata` (`connectors/base.py:37-45`). You do **not** write evidence rows; `persist_ingestion_events` does, and `_normalize` derives everything else from your `content` dict.
4. **Honour the cooperative stop.** The sync job installs a callback through `set_control_check` (`base.py:94-97`); call `await self._check_control()` (`base.py:99-107`) inside your page and record loops. A single `backfill()` call can run for a quarter of an hour, so a signal checked only between invocations does nothing for that whole time.
5. **Register it** in `_register_connectors` (`connectors/registry.py:91-110`) and add its display entry to `_SOURCE_TYPE_LABELS` (`:36-66`). The UI catalog computes `connector_available` from the registry rather than from the label table (`source_type_catalog`, `:69-88`), so a registered connector can never be missing from the picker and a label can never claim a connector that does not exist. `backend/tests/test_source_type_catalog.py` asserts the two agree — that coupling exists because the lists once drifted in both directions, offering Confluence/SharePoint/Exchange (no connector, so creation succeeded and sync died) while hiding SapphireIMS and Zoho Desk (working connectors nobody could select).
6. **Add a reference service** if the source exposes relationship fields (`services/servicenow_reference_service.py` is the model to copy). It turns vendor reference fields into case-link keys, typed graph edges and entity rows, and it runs inside a SAVEPOINT so a failure loses enrichment, never the correlation.
7. **Check the chunker resolution** in `services/chunkers/registry.py:116-143`. A new ticket-shaped source belongs in the ticket set; a new conversational one in the thread set.
8. **Map facets** if the source carries structured fields worth keeping — `source.config["facet_fields"]` feeds `derive_facets` (`services/source_facets.py:63-85`), and a stated environment or version becomes `applicability` directly through `applicability_from_facets` (`:88`), skipping a ~7,200-token applicability LLM call entirely.

**Design lessons from the connectors we already have, worth reading before you write yours:**
- Verify against a live instance if you possibly can. The Zoho connector's three most important behaviours (page size caps at 50, no modified-since filter exists, ties arrive id-ascending inside a time-descending walk) were all found live and would have shipped as bugs otherwise.
- Prefer fail-closed guards on paging. Zoho's walk refuses a page that is out of descending order or missing a timestamp and does **not** advance the checkpoint (`connectors/zoho_desk/connector.py:858-874`); the next tick refetches and dedupe absorbs it.
- If the vendor contract is not public, make it config-mapped rather than guessed — `connectors/sapphireims/` does this, and `validate_credentials` probes the configured path so a wrong mapping fails loudly at setup instead of silently fetching nothing.

---

### 6.6 Add a new AI extractor or classifier

**What:** code that turns unstructured text into structured data using an LLM.

1. **Add the prompt** under `backend/src/contextedge/ai/prompts/` as a versioned, immutable `Prompt` dataclass, registered in the family module and listed in `ai/prompts/__init__.py:189-201`.

   > **Prompts are immutable once shipped. Never edit a released version — add a new one and update the default.** Old versions stay registered so evaluation baselines keep working. This is a repo convention in `CLAUDE.md`, not a suggestion.

2. **Write the extractor** under `ai/extractors/` or the classifier under `ai/classifiers/`, and:
   - Wrap untrusted evidence text in `fence_untrusted(...)` (`ai/fencing.py`) before it reaches the prompt.
   - Bound the input with `salient_slice(...)` (`ai/text_salience.py`). Existing budgets: relevance and message-function 2,000 chars, identity and decision 4,000 chars.
   - Call `llm_complete_json` (or `llm_complete_json_validated` when you have a Pydantic schema) from `ai/provider.py:504`, passing `task=` so the right model and output ceiling apply, and passing `prompt_name` + `prompt_version` so `llm.usage` records them.
   - Pass `tenant_id` and `db` so the budget gate runs and the spend is attributed.
   - **Gate the output with a schema.** Be "strict about structure, lenient about vocabulary" — `IssueSignatureDraft` (`services/issue_signature_service.py:47-73`) is the model: required fields with length bounds, enum-ish fields that silently null on an unknown value, and a confidence that clamps to `[0, 1]`.

3. **Wire it into the pipeline.** `_normalize` (`workers/extraction_tasks.py:122-641`) is the ingest path; each enrichment there is individually try/except'd so a failure degrades rather than losing the evidence row. Follow that pattern.

4. **Measure before you ship.** Any change to prompts, thinking budgets, truncation or slicing ships only with a before/after measurement on real data, and negative results get recorded so decisions do not get re-litigated (`CLAUDE.md`, "Measure-first discipline"). A cap that changes the model's *output structure* on identical input is a quality change, not a cost change.

5. **Add a per-task output ceiling** in `config.py:132-138` if your task's correct answer is genuinely long. The flat 4096 ceiling once truncated playbook JSON mid-array, and the repair path then persisted a playbook with zero steps while reporting success.

---

### 6.7 Add a new context graph edge or node type

**What:** the context graph is the `graph_edges` table in Postgres. **Why:** a new type lets the graph express a relationship it currently cannot.

1. **Register the edge type** in `backend/src/contextedge/graph/edge_types.py`. There are 69 types across five semantic group frozensets (`:36-137`), and `require_registered` (`:186`) is called by `add_edge`, `ensure_edge`, `close_edge` and `replace_edge` — an unregistered type raises `UnknownEdgeType` at runtime.
2. **Make the projection decision in the same change.** Either allowlist the type in `MAF_RELATIONSHIP_TYPES` (`graph/agent/profiles.py:89`) or record why it is excluded in `PROJECTION_EXCLUSIONS` (`edge_types.py`). `backend/tests/test_edge_type_registry.py` fails if you do neither. 16 of the 69 registered types are deliberately not traversable by `maf.v1`; `mentions_identity` is excluded because it fans out 40–70 edges per handful of tickets.
3. **Write edges through `ensure_edge`** (`graph/builder.py:50-135`), not raw INSERTs. It is race-safe via `ON CONFLICT DO NOTHING` against the partial unique index `uq_graph_edges_active_logical`.
4. **Pass `weight` and `confidence` separately.** `weight` is traversal importance; `confidence` is belief. Conflating them was a real defect found in code written days earlier in this repo.
5. **Follow the one domain-derivation rule.** Migration `0031` established a single owning row per edge type, and the mapping is written out in a comment at `graph/agent/materializer.py:23-37`. Every writer must agree, or the unique index treats the same logical edge with different domains as two distinct edges.
6. **If a node type is new,** add it to the `maf.v1` profile's node list (`graph/agent/profiles.py:59-87`) and give it a hydrator in `graph/agent/hydrators.py` so an agent sees facts, not a bare id.
7. **For relational rows that should become edges,** extend `GraphRelationshipMaterializer.reconcile_tenant` (`graph/agent/materializer.py:107-359`). It is additive-only, idempotent, and runs every 6 hours.

---

### 6.8 Add a new MAF (Microsoft Agent Framework) tool

**Where:** `backend/src/contextedge/integrations/maf/`

1. **Define a client protocol and an implementation** in `client.py`. Every existing capability has an in-process form and, where it is HTTP-reachable, an HTTP form (`InProcessContextGraphClient` at `:105`, `HttpContextGraphClient` at `:128`).
2. **Expose it as a tool class** in `tools.py` — see `ContextGraphTools` (`:25`), `CmdbTopologyTools` (`:184`), `ChangeRiskTools` (`:225`), `FixApplicabilityTools` (`:273`).
3. **Add it to the plugin** so an agent gets it: `ContextGraphMAFPlugin` (`plugin.py:26`).
4. **Consider proactive injection** instead of a tool. `ContextGraphProvider.before_run` (`provider.py:50`) pushes a scoped subgraph into the conversation without the agent having to ask, and `after_run` (`:114`) writes decisions back.
5. **Respect the projection budget.** Defaults are 24 nodes / 48 relationships / depth 2 / 12,000 characters, hard-capped at 100 / 250 / 3 / 50,000 (`graph/agent/contracts.py:26-30`).

> **Hard gate:** every MAF tool on this branch is read-or-propose. There is no write-capable agent tool and no executor (`codewiki/KNOWN_GAPS.md:34`), and **no side-effecting tool merges until the skills registry, approval binding and attempt ledger work are complete**. If your tool would mutate state, it belongs behind that gate.

---

### 6.9 Add a new chunker

Chunking is what makes long evidence retrievable, so it is worth its own recipe.

1. **Write the chunker** under `services/chunkers/`, implementing the protocol in `chunkers/base.py:65-102`. It must be **pure and deterministic — no I/O**; it receives `title`, `body` and the raw `payload` and returns `ChunkSpec` objects.
2. **Set a `version`.** All five current chunkers are version 1. `write_chunks` deletes prior rows for that evidence **at the same `chunker_version` only** (`services/evidence_chunk_service.py:77-86`), so bumping a version writes a new generation alongside the old one rather than replacing it — which is what lets you compare two chunkings side by side.
3. **Register it** in `services/chunkers/registry.py`. Registration is lazy and per-chunker fail-soft: a chunker module that fails to import logs `chunker.register_failed` and is skipped rather than taking down ingest.
4. **Add a resolution rule** in `get_chunker` (`registry.py:116-143`). Order matters — **record shape beats source type**, which is why a Zoho `kb_article` resolves to the document chunker rather than the ticket chunker.
5. **Decide the authority** in `_default_authority` (`evidence_chunk_service.py:135-169`). Evidence type is checked before source type, so a KB page carries `knowledge_article` authority and does not compete with an incident record on incident-specific fields.
6. **Consider the inline budget.** Bodies under `INLINE_CHUNK_BUDGET_BYTES = 16 * 1024` from a source in `INLINE_CHUNK_SOURCE_ALLOWLIST` are chunked inside the normalize transaction (`workers/extraction_tasks.py:54, 60-62`). Add your source to that allowlist only after load-testing at typical body sizes.

---

## 7. Testing

### Backend (pytest)
- **Where:** `backend/tests/` — 175 test modules.
- **Command:** `make test-backend` (`cd backend && python -m pytest -v`), or `python -m pytest -q` from `backend/` for the count.
- **How it works:** `asyncio_mode = "auto"` and `testpaths = ["tests"]` (`backend/pyproject.toml:108-110`). `tests/conftest.py` puts `backend/src` on `sys.path` and provides a `make_user` helper for principals.
- **Important correction to older docs:** the suite **does not** spin up a database. Every test uses fakes and mocks for PostgreSQL, Redis, MinIO and the LLM provider, which is why CI needs no service containers (`.github/workflows/ci.yml:3-5`). `testcontainers` appears in the dev extras (`pyproject.toml:69`) but nothing in `tests/` imports it.
- **Consequence worth knowing:** because there is no live Postgres, SQLAlchemy will happily describe a column the database does not have. That gap produced three separate "ORM column no migration created" outages, and `backend/tests/test_orm_migration_column_parity.py` now reads the migration chain as text to catch the next one.

### Frontend (vitest)
- **Where:** alongside the components, e.g. `frontend/src/lib/roles.test.ts`.
- **Command:** `npm test` or `make test-frontend`.
- **Config:** `frontend/vitest.config.ts`. Vitest rather than Jest because it is faster and natively handles the ESM builds Next.js produces.

### Everything
`make test` runs both. Run it before opening a pull request.

### The review discipline this repo expects
`CLAUDE.md` at the repo root is binding for changes here. In short: every implementation gets **three review–fix–review passes before commit** — correctness (trace each changed path with a concrete input), blast radius (find every caller, including tests, workers and the `maf.v1` projection, and verify degrade-not-crash on malformed input), and tests-and-evidence (new behavior gets a test that fails without the change; run the full backend suite and record the count in the commit message). A pass that finds nothing must say what it looked for.

---

## 8. Linting and formatting

### Ruff (backend)
- **What:** a fast Python linter and formatter that replaces Flake8, Black and isort.
- **Where:** configured in `backend/pyproject.toml:84-106`. Target `py312`, line length 100, rule sets `E, F, I, N, W, UP`.
- **Commands:** `make lint` (check) and `make format` (rewrite).
- **Deliberate exceptions carry their reasons in the config:** `N818` is globally ignored because four released exception classes would break catch sites if renamed; `E501` is ignored per-file for `ai/prompts/*` (prompt text is data, and eval baselines pin the exact strings), the two seed scripts, and `services/chunkers/attachment.py` (a markdown table in the module docstring).
- **Ruff is a required CI gate** since the 2026-08 cleanup that took 367 findings to zero (`.github/workflows/ci.yml:64-80`).

### ESLint (frontend)
- **Where:** `frontend/eslint.config.mjs` (flat config — there is no `.eslintrc.json`).
- **Command:** `npm run lint`.

---

## 9. Docker build

### Production images
`backend/Dockerfile` and `frontend/Dockerfile` use multi-stage builds so the final image carries only runtime dependencies — smaller image, smaller attack surface.

```bash
docker build -t contextedge-backend ./backend
```

### Compose profiles
- **`docker-compose.yml`** — infrastructure only (Postgres, Redis, MinIO). Use it when running the app on your host.
- **`docker-compose.dev.yml`** — extends the base file and additionally builds the backend, frontend and Celery worker. The worker command already carries the eight-queue list (`docker-compose.dev.yml:47`), and the in-container DATABASE_URLs use the service name `postgres`, not `localhost`.

---

## 10. Deployment

### Environment configuration
Secrets come from a secret manager, never from the repo. `.env` is gitignored and must stay that way — scan every staged diff before committing.

### Production settings
- Strong random `JWT_SECRET_KEY` and `FERNET_KEY`. Both are enforced at import time outside development (`config.py:248-264`). Set the Fernet key **once** and keep it: rotating it makes existing encrypted source credentials unrecoverable.
- Run FastAPI behind a reverse proxy for TLS and load balancing.
- **Scale workers by lane, not by count.** The eight queues exist because they have different profiles: `extraction` and `embedding` are the volume lanes, `correlation` is the graph lane, and `pattern`/`evaluation` must stay serialized because clustering and playbook generation have no advisory lock and two concurrent runs could mint duplicate patterns.
- **Exactly one beat process**, always.
- Provision `tenant_llm_budgets` rows. A tenant with no row falls back to the deployment defaults — 2,000,000 tokens/day, $25/day, action `block` (`config.py:191-198`) — which is a real ceiling that will freeze a cold-start ingest mid-run.
- Point Prometheus at `/metrics` and load-balancer health checks at `/health` (liveness) and `/ready` (readiness — it 503s on a migration mismatch, which is what you want during a rolling deploy).

### Security
- **PII redaction runs before any text reaches an LLM or an embedding model** (`services/redaction_service.py`, gated by `redaction_enabled`, default True). Turning it off is a local-debugging move only.
- JWTs expire in 60 minutes by default. Service-to-service calls use `X-Service-Token` validated against `SERVICE_TOKENS_JSON`; a service token without `allowed_domain_ids` is tenant-wide.
- `APP_CORS_ORIGINS` must be restricted to known frontend domains.
- Audit: `RequestAuditMiddleware` records every mutating `/api/v1` request, including denials, to `audit_logs` (`middleware/request_audit.py:25-124`). Unauthenticated 401 probes never resolve a tenant, so those exist only in structlog — alert on `http.mutating_request` with status 401.

---

## 11. Configuration reference

Everything below is a field on `Settings` in `backend/src/contextedge/config.py`, sourced from the repo-root `.env` then `backend/.env`. `.env.example` is the annotated template and stays in sync with the code.

### Database
- **`DATABASE_URL`** — async connection string, e.g. `postgresql+asyncpg://postgres:postgres@localhost:5432/contextedge`. Used by FastAPI and by every Celery task's per-task engine.
- **`DATABASE_URL_SYNC`** — synchronous string. Used by Alembic, by the worker's migration-head check, and by the audit middleware's off-thread insert.
- **`POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`** — consumed by docker-compose when it *creates* the container. Changing them after the volume exists does nothing until the volume is dropped.

### Redis
- **`REDIS_URL`** — DB 0, app cache (the `/runtime/match` explain payload lives here for one hour).
- **`CELERY_BROKER_URL`** — DB 1, the task queues.
- **`CELERY_RESULT_BACKEND`** — DB 2, task states and return values.

### Object storage
- **`MINIO_ENDPOINT`**, **`MINIO_ROOT_USER`**, **`MINIO_ROOT_PASSWORD`**, **`MINIO_BUCKET`** (default `contextedge-evidence`), **`MINIO_USE_SSL`**.
- Raw payloads over 32 KB go to `raw/{tenant_id}/{raw_id}.json`; attachment bytes go to `artifacts/{tenant_id}/{evidence_id}/{artifact_id}/{filename}`.

### Auth and encryption
- **`JWT_SECRET_KEY`** — REQUIRED outside development; the app refuses to boot on the default.
- **`JWT_ALGORITHM`** (HS256), **`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`** (60), **`JWT_REFRESH_TOKEN_EXPIRE_DAYS`** (7).
- **`FERNET_KEY`** — REQUIRED outside development. Encrypts stored source credentials.
- **`SERVICE_TOKENS_JSON`** — map of token string → `{tenant_id, user_id, email, roles[, allowed_domain_ids]}`.

### LLM routing
The provider is chosen by the LiteLLM prefix on each model id, not by a separate switch. `DEFAULT_LLM_PROVIDER` only matters when it is `vertex_ai` and bare `gemini-*` ids need routing.
- **`DEFAULT_CLASSIFICATION_MODEL`** — the relevance gate, message function, identity work.
- **`DEFAULT_EXTRACTION_MODEL`** — normalization and extraction.
- **`PATTERN_MODEL`** — pattern synthesis. Deliberately unmeasured; it stays on its current model until it gets its own A/B.
- **`PLAYBOOK_MODEL`** — playbook generation. The 2026-08-17 A/B moved this lane: grounded share 0.70 → 0.81, latency halved.
- **`DEFAULT_EMBEDDING_MODEL`** — **must return exactly 3,072 dimensions**, or the call raises (`ai/provider.py:786-793`). Watch the trap: the *code* default is `text-embedding-3-small` (`config.py:58`), which returns 1,536 and will raise. `.env.example:87-89` pins `text-embedding-3-large` and names `vertex_ai/gemini-embedding-001` as the alternative, so set the variable rather than relying on the field default.
- **`*_LOCATION`** — per-task Vertex region, all `global` by default.
- Credentials: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_*`, `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS`.
- **`LLM_FALLBACK_MODEL`** — retry one failed call on this model. Usage is recorded against whichever model actually served, so `generation_provenance.model_requested` can differ from what answered; `correlation_id` joins to the truth in `llm.usage`.

### Cost containment
Each of these is a **ceiling, not a target** — ordinary work stays well under all of them.
- **`LLM_NUM_RETRIES`** (2) — each retry is a fully billed call.
- **`LLM_MAX_OUTPUT_TOKENS`** (4096) — the global output ceiling.
- **`LLM_TASK_OUTPUT_TOKENS`** (`{"playbook": 16384, "extraction": 16384, "pattern": 16384}`) — per-task overrides. Add any new long-output lane here; the flat ceiling once truncated playbook JSON mid-array and the repair path persisted a zero-step playbook while reporting success.
- **`LLM_THINKING_BUDGETS`** (`{"relevance": 0}`) — exactly one entry. Disabling thinking on relevance cut output tokens ~70 % with an unchanged verdict; everything else keeps dynamic thinking because a controlled test showed identity-adjudication confidence dropping 0.95 → 0.80 under caps, which would have silently diverted auto-links into the review queue. **Do not add entries here without the A/B discipline.**
- **`EMBEDDING_MAX_BATCH_SIZE`** (64) — texts per embedding request.
- **`DEFAULT_DAILY_TOKEN_LIMIT`** (2,000,000), **`DEFAULT_DAILY_COST_CAP_USD`** (25.0), **`DEFAULT_BUDGET_ACTION_ON_EXCEED`** (`block`) — applied to any tenant with no `tenant_llm_budgets` row. Blank both to restore unlimited. Roll out as `warn`, then flip to `block`.

### Pipeline gates
- **`EPISODE_RESOLUTION_GATE`** — `off` (default) or `cluster`. `cluster` defers reconstruction for clusters carrying no resolution signal anywhere. It is a **cluster-level** check, never an evidence filter: in scattered-source deployments the problem and the fix arrive from different systems.
- **`EPISODE_AI_REVIEW`** — `off` (default) / `advisory` / `auto_approve`. `advisory` stamps a verdict on `episodes.ai_review` for the human queue. `auto_approve` additionally approves the subset clearing both the model verdict **and** the deterministic floors, keeping `reviewer_user_id` NULL so machine approvals stay permanently distinguishable from human ones.
- **`DOCUMENT_VISION_ENABLED`** — vision-model description of document figures during artifact extraction.

### Ingest safety and retention
- **`REDACTION_ENABLED`** (True) — regex redaction before embedding and LLM extraction.
- **`RETENTION_PURGE_MODE`** (`soft_purge`) — `soft_purge` scrubs content in place; `hard_delete` removes rows and cascades.
- **`RETENTION_DEFAULT_DAYS`** (365) — base window when a tenant has no retention policy.

### Application
- **`APP_ENV`** — anything other than `development` enforces the JWT and Fernet guards at startup.
- **`APP_DEBUG`**, **`APP_LOG_LEVEL`** (`INFO`; set `DEBUG` when troubleshooting), **`APP_CORS_ORIGINS`**, **`BACKEND_PORT`** (8000), **`FRONTEND_URL`**.

### Notifications (no-ops until configured)
- **`SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_STARTTLS`**, **`NOTIFICATION_WEBHOOK_URL`** (Teams/Slack-compatible). Unconfigured channels log an explicit `*_skipped_unconfigured` line rather than failing.

### Connectors
`SERVICENOW_*`, `JIRA_*`, `TEAMS_*`, and `ZOHO_DESK_*`. Note on Zoho: scopes are fixed when the refresh token is issued, so adding one later means re-issuing the token; and `ZOHO_DESK_DATA_CENTER` must match the portal's region because the accounts host and API host are a pair.

### Prompt A/B
- **`TENANT_PROMPT_VARIANTS_JSON`** — `{"<tenant-uuid>": {"relevance": "v2", "episode": "v3"}}`. Resolution is tenant override → registered default. An unknown prompt *name* raises `KeyError` on purpose; an unregistered *override* falls back with a `prompt_variant_not_registered_falling_back` log, and malformed JSON logs `prompt_variants_config_invalid` and yields an empty map so ingest never crashes on config.

---

## 12. Where to go next

| I want to… | Read |
| --- | --- |
| Understand the backend folder by folder | [04_Backend_KT.md](04_Backend_KT.md) |
| Fix something that is broken | [14_Debugging_Guide.md](14_Debugging_Guide.md) |
| Operate a running deployment | [RUNBOOK.md](RUNBOOK.md) |
| Understand the API surface | [API.md](API.md), [10_API_Documentation.md](10_API_Documentation.md) |
| Understand a design decision | `codewiki/` |
| Know what is **not** finished | `codewiki/KNOWN_GAPS.md` — check it before claiming any feature works end to end |

**The short version of everything above:** routers are thin, services do the work, workers own anything slow, commit before you dispatch, prompts are immutable, every LLM call is budgeted and attributed, and evidence must never be lost because an enrichment step failed.
