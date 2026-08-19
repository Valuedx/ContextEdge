# ContextEdge — Knowledge Transfer Documentation

> **Complete Knowledge Transfer (KT) documentation for the ContextEdge platform.**
>
> This documentation is designed so that a new developer with **zero prior knowledge** can understand the entire system without asking anyone for help.

> **Relationship to `codewiki/`:** the numbered articles in
> [`codewiki/`](../codewiki/) are the **authoritative, editorially
> maintained** architecture documentation —
> [`codewiki/KNOWN_GAPS.md`](../codewiki/KNOWN_GAPS.md) is the single
> source of truth for what is and isn't implemented. This `docs/` KT suite
> is an onboarding companion; where the two disagree, the codewiki wins,
> and `10_API_Documentation.md` is generated from the OpenAPI schema
> (`backend/generate_docs.py`) rather than hand-maintained.

> **Freshness:** the pipeline facts, queue names, and citations on this page were
> re-verified against the working tree on **2026-08-19**. Line numbers move when
> code moves — if a citation looks wrong, trust the code and fix the doc.

---

## 📚 Documentation Index

### Learn the system (numbered KT chapters)

| # | Document | Description | Audience |
|---|----------|-------------|----------|
| 01 | [Project Overview](01_Project_Overview.md) | Business problem, goals, tech stack, high-level architecture, environment config | Everyone |
| 02 | [Project Architecture](02_Project_Architecture.md) | Layer architecture, design patterns, security, worker topology, AI architecture | Developers & Architects |
| 03 | [End-to-End Project Flow](03_End_to_End_Project_Flow.md) | Complete data lifecycle from ingestion through extraction to runtime retrieval | Developers |
| 04 | [Backend KT](04_Backend_KT.md) | Every backend folder, file, class, and function documented comprehensively | Backend Developers |
| 05 | [Frontend KT](05_Frontend_KT.md) | Every frontend page, component, hook, API call, and store documented | Frontend Developers |
| 06 | [Database Design](06_Database_Design.md) | Every table, column, index, relationship, and migration documented | Backend Developers & DBAs |
| 07 | [Vector Search & Embeddings](07_Vector_Search_and_Embeddings.md) | Embeddings, pgvector, similarity search, hybrid ranking, RAG, chunking | AI/ML Engineers & Developers |
| 08 | [Context Graph](08_Context_Graph.md) | Node types, edge types, graph builder, queries, temporal tracking, MAF projection | Developers & Architects |
| 09 | [Microsoft Agent Framework (MAF)](09_Microsoft_Agent_Framework_MAF.md) | MAF integration, agent execution, tools, context projection, decision traces | Developers & AI Engineers |
| 10 | [API Documentation](10_API_Documentation.md) | Every API endpoint with request/response examples, auth, and backend flow | All Developers |
| 11 | [UI Tabs Explanation](11_UI_Tabs_Explanation.md) | Every dashboard tab with business purpose, workflow, APIs, and data flow | Frontend Developers & PMs |
| 11A | [UI Tabs Plain English Guide](UI_TABS_FUNCTIONALITY_PLAIN_ENGLISH.md) | Plain-English explanation of each UI tab, why it is needed, main functionality, and examples | Business Users, PMs & Freshers |
| 11B | [UI Tabs Backend File Map](UI_TABS_BACKEND_FILE_MAP.md) | Tab-wise frontend/backend file mapping for explaining which backend files power each screen | Developers, PMs & Demo Presenters |
| 11C | [UI Tabs Workflow Flows](UI_TABS_WORKFLOW_FLOWS_PLAIN_ENGLISH.md) | Simple flow for every UI tab, including input, process, output, and MG22 demo path | Business Users, PMs & Demo Presenters |
| 12 | [File Dependency Map](12_File_Dependency_Map.md) | Import relationships, data flow, control flow with Mermaid dependency diagrams | Developers |
| 13 | [Developer Guide](13_Developer_Guide.md) | Setup, running, testing, adding features, migrations, deployment | New Developers |
| 14 | [Debugging Guide](14_Debugging_Guide.md) | Common errors, debugging steps, logging, observability, troubleshooting | All Developers |
| 15 | [Project Flow Diagrams](15_Project_Flow_Diagrams.md) | 25+ Mermaid diagrams covering every major system flow | Everyone |
| 16 | [Glossary](16_Glossary.md) | Every technical term used in the project, explained in simple English | Everyone |
| — | [ContextEdge, Explained (HTML)](CONTEXTEDGE_EXPLAINED.html) | Fresher-friendly visual tour in two tabs: **The tour** (with/without ContextEdge, value added, MAF pluggability, high-level + granular architecture, 7-stage graph-growth walkthrough) and **The schema** (all tables in plain English, grouped by purpose, with ER diagrams). Mermaid diagrams and cited code references throughout. Open in a browser. | Freshers & Everyone |

### Run and operate the system

| Document | Description | Audience |
|----------|-------------|----------|
| [SETUP_GUIDE.md](SETUP_GUIDE.md) | Canonical first-time local install: env file, Docker infra, migrations, seed, worker and beat | New Developers |
| [RUNBOOK.md](RUNBOOK.md) | Day-two operations: queues, Windows worker topology, beat schedule, health, retention, troubleshooting | Developers & Operators |
| [MIGRATIONS.md](MIGRATIONS.md) | How the Alembic chain works, the `0001` bootstrap caveat, and pre-migration data steps | Backend Developers |
| [API.md](API.md) | Hand-written HTTP behaviour and auth semantics (complements the generated `10_API_Documentation.md`) | All Developers |
| [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) | Architecture and design-pattern reference | Architects |
| [MAF_CONTEXT_GRAPH_INTEGRATION.md](MAF_CONTEXT_GRAPH_INTEGRATION.md) | How the agent-facing graph projection is produced and consumed | AI Engineers |
| [CONTEXT_GRAPH_WALKTHROUGH.md](CONTEXT_GRAPH_WALKTHROUGH.md) | Narrated walk through one incident becoming graph structure | Everyone |
| [EXECUTIVE_TECHNICAL_SUMMARY.md](EXECUTIVE_TECHNICAL_SUMMARY.md) | Short technical summary for leadership | Leadership & PMs |

---

## 🗺️ Reading Order

### If you're completely new to the project:
1. Start with **[01 - Project Overview](01_Project_Overview.md)** — understand what the project does and why
2. Read **[16 - Glossary](16_Glossary.md)** — learn the vocabulary used throughout
3. Read **[02 - Project Architecture](02_Project_Architecture.md)** — understand the system design
4. Read **[03 - End-to-End Flow](03_End_to_End_Project_Flow.md)** — see how data flows through the system
5. Read **[SETUP_GUIDE.md](SETUP_GUIDE.md)**, then **[13 - Developer Guide](13_Developer_Guide.md)** — set up your development environment

### If you're a backend developer:
1. **[04 - Backend KT](04_Backend_KT.md)** — deep dive into every backend file
2. **[06 - Database Design](06_Database_Design.md)** — understand the data model
3. **[10 - API Documentation](10_API_Documentation.md)** — learn every API endpoint
4. **[07 - Vector Search](07_Vector_Search_and_Embeddings.md)** — understand AI/vector operations
5. **[08 - Context Graph](08_Context_Graph.md)** — understand the graph system

### If you're a frontend developer:
1. **[05 - Frontend KT](05_Frontend_KT.md)** — deep dive into every frontend file
2. **[11 - UI Tabs Explanation](11_UI_Tabs_Explanation.md)** — understand every dashboard tab
3. **[10 - API Documentation](10_API_Documentation.md)** — learn the APIs you'll call

### If you're debugging an issue:
1. **[RUNBOOK.md](RUNBOOK.md)** — queues, beat schedule, health endpoints, common symptoms
2. **[14 - Debugging Guide](14_Debugging_Guide.md)** — troubleshooting steps
3. **[12 - File Dependency Map](12_File_Dependency_Map.md)** — trace dependencies
4. **[15 - Project Flow Diagrams](15_Project_Flow_Diagrams.md)** — visualize the flow

### If you're an architect:
1. **[02 - Project Architecture](02_Project_Architecture.md)** — system design
2. **[08 - Context Graph](08_Context_Graph.md)** — graph architecture
3. **[09 - MAF](09_Microsoft_Agent_Framework_MAF.md)** — agent framework integration
4. **[15 - Project Flow Diagrams](15_Project_Flow_Diagrams.md)** — all system diagrams

---

## 🔭 The pipeline in one screen

Everything in ContextEdge is one long conveyor belt: a ticket lands, it gets cleaned
and classified, it gets chunked and embedded so search can find it, it gets correlated
with related records, and eventually it becomes an episode, a pattern, and a playbook.
Each stage is a named Celery task on a named queue. Learn these names first — nearly
every operational question ("why is nothing appearing on the Episodes page?") is
answered by finding the first stage in this list that has stopped.

| # | Stage | Celery task name | Queue | Entry point in code |
|---|-------|------------------|-------|---------------------|
| 1 | Pull records from a source system | `sync.run_backfill` / `sync.run_incremental_sync` | `sync` | `backend/src/contextedge/workers/sync_tasks.py:39,68` |
| 2 | Store the raw payload (>32 KB goes to MinIO) | (inline, inside the sync job) | `sync` | `backend/src/contextedge/services/ingestion_persistence.py:16,85` |
| 3 | Clean, redact, classify, extract identities and decisions | `extraction.normalize_evidence` | `extraction` | `backend/src/contextedge/workers/extraction_tasks.py:122,1304` |
| 4 | Re-run just the relevance gate (fast lane) | `extraction.classify_relevance` | `default` | `backend/src/contextedge/workers/extraction_tasks.py:1361` |
| 5 | Pull the rest of a conversation from the source | `hydration.hydrate_thread` | `hydration` | `backend/src/contextedge/workers/hydration_tasks.py:189` |
| 6 | Split long bodies into retrievable chunks | `extraction.chunk_evidence` | `embedding` | `backend/src/contextedge/workers/chunk_tasks.py:210` |
| 7 | Embed those chunks in batches of 32 | `extraction.embed_chunks_batch` | `embedding` | `backend/src/contextedge/workers/chunk_tasks.py:238` |
| 8 | Link this record to related records | `extraction.correlate_evidence` | `correlation` | `backend/src/contextedge/workers/correlation_tasks.py:16` |
| 9 | Turn a correlated cluster into an episode | `extraction.reconstruct_episode` | `correlation` | `backend/src/contextedge/workers/extraction_tasks.py:1391` |
| 10 | Let the model pre-review episode drafts | `evaluation.ai_review_episodes` | `evaluation` | `backend/src/contextedge/workers/evaluation_tasks.py:129` |
| 11 | Fingerprint an approved episode, link recurrences | `evaluation.extract_issue_signature` | `evaluation` | `backend/src/contextedge/workers/signature_tasks.py:24` |
| 12 | Cluster approved episodes into patterns | `pattern.cluster_episodes` | `pattern` | `backend/src/contextedge/workers/pattern_tasks.py:422` |
| 13 | Draft a playbook candidate from a pattern | `pattern.generate_playbook_candidate` | `pattern` | `backend/src/contextedge/workers/pattern_tasks.py:446` |
| 14 | Merge duplicate evidence/episodes/patterns/playbooks | `pattern.deduplicate_knowledge` | `pattern` | `backend/src/contextedge/workers/pattern_tasks.py:834` |

There are **eight** queues in total: `default`, `sync`, `hydration`, `extraction`,
`correlation`, `embedding`, `pattern`, `evaluation`. The routing table that assigns
tasks to them is `backend/src/contextedge/workers/celery_app.py:226-280`, and the
launcher consumes all eight by default (`backend/dev.py:16`). `correlation` and
`embedding` are separate lanes on purpose: when they shared the `extraction` queue,
graph building and chunk embedding starved behind bulk normalization and evidence was
silently unsearchable — the incident is written up in the routing table's own comments.

Full operational detail, including the Windows worker topology and the beat schedule,
lives in [RUNBOOK.md](RUNBOOK.md).

---

## 🏗️ Project Quick Reference

| Aspect | Technology |
|--------|-----------|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic |
| **Frontend** | Next.js 16 (App Router), React 19, Tailwind CSS 4, shadcn/ui, TanStack Query 5 (`frontend/package.json`) |
| **Database** | PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`, `docker-compose.yml`) |
| **Queue** | Celery + Redis — eight queues, one beat process |
| **Object Storage** | MinIO (S3-compatible); raw payloads over 32 KB are offloaded here |
| **AI/LLM** | LiteLLM in front of Google Vertex AI (Gemini), OpenAI, Anthropic |
| **Embeddings** | The configured embedding model, which **must return exactly 3072 dimensions** — `generate_embedding` rejects anything else (`backend/src/contextedge/ai/provider.py:739,787-793`). Code default is `text-embedding-3-small` (`backend/src/contextedge/config.py:58`); deployments override it via `DEFAULT_EMBEDDING_MODEL` |
| **Vector index** | HNSW **expression** indexes over `(embedding::halfvec(3072))`, migration `0032` — plain `vector` HNSW cannot hold 3072 dims |
| **Auth** | JWT Bearer + `X-Service-Token` |
| **Logging** | structlog, with request/correlation/causation ids threaded into Celery |
| **Monitoring** | Prometheus at `/metrics`; queue depth and pipeline stage counts at `GET /api/v1/admin/pipeline-health` |
| **Deployment** | Docker + Docker Compose |

---

## 🔑 Default Development Credentials

| Email | Password | Role |
|-------|----------|------|
| `admin@contextedge.local` | `admin123` | Admin |
| `analyst@contextedge.local` | `analyst123` | Analyst |

> ⚠️ Local development seed accounts only — never reuse in shared or
> production environments.

---

## 🚀 Quick Start

```bash
# Start infrastructure (PostgreSQL, Redis, MinIO)
make up

# Setup backend
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

# Setup frontend
cd ..\frontend
npm install
cd ..

# Run migrations and seed data
make migrate
make seed

# Start services (in separate terminals)
make backend-dev
make celery-dev
make frontend-dev
```

- **Backend API**: http://localhost:8000
- **Swagger UI**: http://localhost:8000/docs
- **Readiness probe**: http://localhost:8000/ready — checks database, Alembic head, and Redis
- **Frontend**: http://localhost:3000

Two things worth knowing before your first run:

- `make celery-dev` runs `python dev.py worker`, which consumes **all eight** queues in
  one process (`backend/dev.py:16`). That is fine for reading code; it is too slow for a
  real backfill. For anything bulky, use the multi-process topology in
  [RUNBOOK.md](RUNBOOK.md) — on Windows the prefork pool does not work and the threads
  pool breaks LLM calls, so parallelism comes from running several `-P solo` processes.
- Workers refuse to start if the database is behind the code's Alembic head
  (`backend/src/contextedge/workers/celery_app.py:83-139`), and `/ready` returns 503 for
  the same reason. If a worker exits immediately, run `make migrate` before debugging
  anything else.

---

*This documentation was written by reading the source code. Where a statement is
load-bearing it carries a `file:line` citation you can click through; if the code has
moved since, the code is right and the doc is wrong.*
