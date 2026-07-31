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

---

## 📚 Documentation Index

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
| 12 | [File Dependency Map](12_File_Dependency_Map.md) | Import relationships, data flow, control flow with Mermaid dependency diagrams | Developers |
| 13 | [Developer Guide](13_Developer_Guide.md) | Setup, running, testing, adding features, migrations, deployment | New Developers |
| 14 | [Debugging Guide](14_Debugging_Guide.md) | Common errors, debugging steps, logging, observability, troubleshooting | All Developers |
| 15 | [Project Flow Diagrams](15_Project_Flow_Diagrams.md) | 25+ Mermaid diagrams covering every major system flow | Everyone |
| 16 | [Glossary](16_Glossary.md) | Every technical term used in the project, explained in simple English | Everyone |
| — | [ContextEdge, Explained (HTML)](CONTEXTEDGE_EXPLAINED.html) | Fresher-friendly visual tour: with/without ContextEdge, value added, MAF pluggability, high-level + granular architecture, and a 7-stage walkthrough of how the context graph grows — with Mermaid diagrams and cited code references. Open in a browser. | Freshers & Everyone |

---

## 🗺️ Reading Order

### If you're completely new to the project:
1. Start with **[01 - Project Overview](01_Project_Overview.md)** — understand what the project does and why
2. Read **[16 - Glossary](16_Glossary.md)** — learn the vocabulary used throughout
3. Read **[02 - Project Architecture](02_Project_Architecture.md)** — understand the system design
4. Read **[03 - End-to-End Flow](03_End_to_End_Project_Flow.md)** — see how data flows through the system
5. Read **[13 - Developer Guide](13_Developer_Guide.md)** — set up your development environment

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
1. **[14 - Debugging Guide](14_Debugging_Guide.md)** — troubleshooting steps
2. **[12 - File Dependency Map](12_File_Dependency_Map.md)** — trace dependencies
3. **[15 - Project Flow Diagrams](15_Project_Flow_Diagrams.md)** — visualize the flow

### If you're an architect:
1. **[02 - Project Architecture](02_Project_Architecture.md)** — system design
2. **[08 - Context Graph](08_Context_Graph.md)** — graph architecture
3. **[09 - MAF](09_Microsoft_Agent_Framework_MAF.md)** — agent framework integration
4. **[15 - Project Flow Diagrams](15_Project_Flow_Diagrams.md)** — all system diagrams

---

## 🏗️ Project Quick Reference

| Aspect | Technology |
|--------|-----------|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic |
| **Frontend** | Next.js 16, React, Tailwind CSS, shadcn/ui, TanStack Query |
| **Database** | PostgreSQL 16 + pgvector |
| **Queue** | Celery + Redis |
| **Object Storage** | MinIO (S3-compatible) |
| **AI/LLM** | LiteLLM, Google Vertex AI (Gemini), OpenAI, Anthropic |
| **Embeddings** | text-embedding-004 (1536 dimensions) |
| **Auth** | JWT Bearer + X-Service-Token |
| **Logging** | structlog |
| **Monitoring** | Prometheus metrics |
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
- **Frontend**: http://localhost:3000

---

*This documentation was generated by reading the complete source code of the ContextEdge project. Every file, function, and module has been documented to enable zero-knowledge onboarding.*
