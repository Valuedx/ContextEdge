# End-to-end pipeline

## Summary

After reading this page, you should see how operational content enters ContextEdge, becomes **tenant-scoped evidence**, is enriched and searched, surfaces as **episodes**, **patterns**, and **governed playbooks**, and is finally retrieved at **runtime** with audit-friendly traces—without needing to open every subsystem first. Deeper articles in this wiki unpack each box in the diagram below.

## Business picture

Organizations store truth in many places: tickets, chat, email, and knowledge bases. ContextEdge’s job is to **pull or receive** that activity, **normalize** it into comparable records, **remember** it per customer (**tenant**), and **derive** structured memory (what happened, what repeats, what we officially say to do next). Humans and integrations then **search** and **match** approved guidance, while the system keeps enough trace to explain **why** something was shown. Later, **retention** and policy shape how long raw and derived data remain available.

None of that requires the reader to know PostgreSQL, Celery, or vector indexes—but those are how the implementation delivers the story.

## Technical walkthrough

The path below is the backbone of the product; names in parentheses are the main implementation homes.

1. **API surface** — Clients call FastAPI routes under `/api/v1` for admin CRUD, sync, evidence, episodes, patterns, playbooks, sessions, runtime, and more. The router index wires modules to URL prefixes. **In code:** `backend/src/contextedge/api/v1/__init__.py`, `backend/src/contextedge/main.py`.

2. **Request context and audit** — Each request carries tenant and identity context; auditing records meaningful access. **In code:** `middleware/request_context.py`, `middleware/request_audit.py`, `middleware/auth.py`.

3. **Sources and sync** — External systems are modeled as **sources**. Sync runs and connectors fetch or accept changes, then hand off batches of ingestion events. **In code:** `connectors/` (adapters), `services/sync_worker_service.py`, `api/v1/sources.py`, `api/v1/sync.py`.

4. **Raw then normalized evidence** — Ingestion persists **raw** payloads (and object storage for larger blobs), then workers **normalize** into **evidence items** (title, body, hashes, provenance). A recovery-aware handoff claims backlog safely before queueing normalize work so broker hiccups do not double-process forever. **In code:** `services/ingestion_persistence.py` (`persist_ingestion_events`), `services/sync_worker_service.py` (`_claim_pending_raw_ids_for_handoff`), `workers/extraction_tasks.py` (`normalize_evidence`), `services/evidence_normalization.py`, `models/evidence.py`.

5. **Search** — Analysts and services query evidence using **full-text search (FTS)** and **semantic** (embedding) signals, combined in a **hybrid** ranker; results respect **access** rules. **In code:** `search/pg_fts.py`, `search/vector_search.py`, `search/hybrid_ranker.py`, `search/access_control.py`, `api/v1/evidence.py`.

6. **AI-assisted extraction** — Language models and embeddings help classify relevance, extract structure, and power semantic retrieval. **In code:** `ai/provider.py`, `ai/embeddings.py`, `ai/extractors/`, `ai/classifiers/`.

7. **Episodes, patterns, graph** — Higher-level objects summarize incidents (**episodes**), surface recurrence (**patterns**), and link entities via a **graph** and correlation services. **In code:** `services/episode_service.py`, `services/pattern_service.py`, `graph/builder.py`, `services/correlation_service.py`, `services/contradiction_service.py`.

8. **Playbooks and governance** — **Playbooks** move through a lifecycle; only **published** versions are what runtime retrieval prefers to serve. **In code:** `services/playbook_service.py`, `models/playbook.py`, `api/v1/playbooks.py`.

9. **Runtime match and explain** — Integrations call **runtime** endpoints to match a situation to ranked playbooks; scoring reuses hybrid ideas; explanations can be cached briefly in Redis. **In code:** `api/v1/runtime.py`, `services/runtime_service.py`, `search/hybrid_ranker.py` (`rank_playbooks`).

10. **Sessions, execution, audit trail** — **Resolution sessions** and **execution** APIs support governed operational workflows and append-style traces for review. **In code:** `api/v1/sessions.py`, `api/v1/execution.py`, `services/session_service.py`, `services/execution_service.py`, `services/audit_service.py`, `services/event_log_service.py`.

11. **Background work** — Celery workers drain dedicated **queues** (sync, hydration, extraction, pattern, evaluation, default) so HTTP stays responsive and failures can retry. **In code:** `workers/celery_app.py`, task modules under `workers/`.

12. **Retention** — Policies and services eventually remove or age data according to tenant rules. **In code:** `services/retention_service.py` (see also tenant policy schemas).

The **Next.js** dashboard is a thin client over this API; most rules stay on the server (`frontend/`).

## Flow diagram

This is the same story as the numbered list, compressed for orientation.

```mermaid
flowchart LR
  subgraph ingest[Ingestion]
    SRC[Sources and connectors]
    RAW[Raw evidence objects]
    NORM[Normalize to EvidenceItem]
    SRC --> RAW --> NORM
  end

  subgraph enrich[Enrichment and memory]
    SRCH[Search FTS plus vectors]
    AI[Extractors and embeddings]
    EP[Episodes and patterns]
    GR[Graph and correlation]
    NORM --> SRCH
    NORM --> AI
    AI --> EP
    EP --> GR
  end

  subgraph gov[Governance and delivery]
    PB[Playbook lifecycle]
    RT[Runtime match and explain]
    SE[Sessions and audit]
    EP --> PB
    PB --> RT
    RT --> SE
  end

  subgraph async[Async workers]
    W[Celery queues]
    SRC -.-> W
    W -.-> NORM
    W -.-> AI
    W -.-> EP
  end
```

Solid arrows are the main data dependency; dashed arrows show where **workers** extend or continue the HTTP-started path.

## Design decisions

- **Modular monolith (FastAPI + one Postgres)** — *Why:* simpler operations and consistent transactions across tenants’ data. *Tradeoff:* horizontal scaling is mostly “scale the app + DB,” not independent microservices per feature.

- **Post-commit worker pipeline for normalization** — *Why:* HTTP and sync paths stay fast; heavy parsing and embedding do not block the caller. *Tradeoff:* evidence is briefly “raw-only” until workers catch up; monitoring queue depth matters.

- **Claim-before-queue handoff for raw backlog** — *Why:* survives Redis/broker outages without duplicate normalize tasks or lost tails. *Tradeoff:* more moving parts in `sync_worker_service` than a naive “enqueue immediately.”

- **Application-layer hash dedupe for evidence** — *Why:* flexible evolution of what counts as “the same” content. *Tradeoff:* under extreme concurrency, duplicates are still possible until a DB uniqueness story hardens (see root `README.md` known constraints).

- **Hybrid retrieval (lexical + semantic + signals)** — *Why:* operational questions are both keyword-precise (“VPN gateway host name”) and conceptually fuzzy (“similar outage last month”). *Tradeoff:* more indexes to maintain (FTS, pgvector) and tuning surface area.

- **Playbook publication gate for runtime** — *Why:* customers should not get draft or unreviewed procedures in automated retrieval. *Tradeoff:* operators must complete lifecycle steps before runtime “sees” the latest version.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| App bootstrap | `backend/src/contextedge/main.py` | `create_app`, `lifespan` | Process start |
| API mount | `backend/src/contextedge/api/v1/__init__.py` | `router`, `include_router` | Per route registration |
| Tenant and audit middleware | `backend/src/contextedge/middleware/request_context.py` | `TenantContextMiddleware` | Each HTTP request |
| Ingestion persist | `backend/src/contextedge/services/ingestion_persistence.py` | `persist_ingestion_events` | After connector/sync produces events |
| Sync handoff / recovery | `backend/src/contextedge/services/sync_worker_service.py` | `_claim_pending_raw_ids_for_handoff` | Before enqueueing normalize tasks |
| Normalize worker | `backend/src/contextedge/workers/extraction_tasks.py` | `normalize_evidence` | Celery **extraction** queue |
| Hybrid ranking | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks` | Runtime match and evaluations |
| Runtime orchestration | `backend/src/contextedge/services/runtime_service.py` | `match_playbooks` | Service layer for `/runtime` |
| Celery topology | `backend/src/contextedge/workers/celery_app.py` | `celery_app`, `task_routes`, `beat_schedule` | Worker and beat processes |
| Playbook governance | `backend/src/contextedge/services/playbook_service.py` | lifecycle transitions (module-level API) | Admin API and internal callers |

## Acme VPN incident (this layer)

When **Acme Corp**’s **Corporate VPN** outage spawns duplicate Jira tickets, Teams threads, and a follow-up email, **connectors and sync** land multiple **raw** payloads that **normalize** into evidence rows analysts can find with “VPN gateway.” **Extraction** proposes an **episode** spanning the noise; **patterns** may later reflect “auth certificate expiry” style repeats. A reviewed **playbook** version captures the approved fix; **runtime** ranks that playbook when an integration asks about VPN failures, and **sessions** can record what was decided—so the same story threads every stage above without changing the example.

## Further reading

- Repository architecture and package map: [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md)
- HTTP details and auth headers: [`docs/API.md`](../docs/API.md)
- Commands, workers, and operations: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md)
- Next articles in this wiki: [PLAN.md](./PLAN.md) (02 API lifecycle through 11 retention)
