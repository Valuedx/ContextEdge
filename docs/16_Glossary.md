# ContextEdge — Glossary

Welcome to the ContextEdge Glossary. This document defines the core concepts, technologies, and terms used throughout the ContextEdge platform.

*Entries were re-checked against the code on 2026-08-19. Where a definition names a file, that file exists at that path today.*

## A

### Adjacency List
- **Term**: Adjacency List
- **Simple Definition**: A way to store graph data in a database where each row represents a connection between two nodes.
- **Detailed Explanation**: In ContextEdge, the adjacency list is used in PostgreSQL to represent relationships (edges) between operational entities (like tickets, users, and decisions). It allows for efficient graph traversals (like BFS) using recursive SQL queries, avoiding the need for a dedicated graph database. The traversal in `graph/queries.py` is an iterative BFS capped at depth 3.
- **Where Used**: `backend/src/contextedge/models/pattern.py:174` (the `GraphEdge` table) and `backend/src/contextedge/graph/`
- **Related Terms**: Graph Edge, Node, Graph

### Advisory Lock
- **Term**: Advisory Lock
- **Simple Definition**: A lock the application asks Postgres for, to stop two workers doing the same job at once.
- **Detailed Explanation**: Every sync run takes `pg_try_advisory_xact_lock(hashtext('sync:<object_id>'))` before touching a source object. A second worker that cannot get the lock returns `{"status": "skipped_locked"}` instead of racing the checkpoint. It is transaction-scoped, so it releases on commit or rollback and a crashed worker cannot leak it. Note what does **not** have one: pattern clustering and playbook generation, which is why they run on a single serialized worker instead.
- **Where Used**: `backend/src/contextedge/services/sync_worker_service.py:379`
- **Related Terms**: Sync, Worker, Queue

### AI Review (Episode)
- **Term**: AI Review (Episode)
- **Simple Definition**: An optional stage where a model reads an episode draft and either advises the human reviewer or approves it outright.
- **Detailed Explanation**: Controlled by `EPISODE_AI_REVIEW`, whose values are exactly `off` (default), `advisory`, and `auto_approve` (`backend/src/contextedge/config.py:185-187`). Advisory stamps a verdict on `episodes.ai_review` and approves nothing. Auto-approve additionally approves the draft, but only when it clears four deterministic floors on top of the model verdict: at least 2 evidence items, at least 20 characters of `final_outcome`, a verdict of exactly `approve`, and confidence at or above 0.8. An AI approval leaves `reviewer_user_id` NULL, so it stays permanently distinguishable from a human one. The hourly sweep commits per episode before dispatching anything downstream.
- **Where Used**: `backend/src/contextedge/services/episode_review_service.py:42-44,89-101`, `backend/src/contextedge/workers/evaluation_tasks.py:129`
- **Related Terms**: Episode, Review Queue, Human-in-the-Loop, Issue Signature

### Applicability
- **Term**: Applicability
- **Simple Definition**: The conditions under which a piece of knowledge actually applies — product, version, environment, component.
- **Detailed Explanation**: Stored on `evidence_items.applicability` and used when retrieving knowledge for playbook generation. Crucially it **re-ranks, never filters**: a mismatched article is demoted and carries a warning into the prompt rather than disappearing, because "no guidance exists" and "the guidance may not fit your version" are different answers. Extraction runs only on the manual re-classification path, and is skipped entirely when the source already stated environment and version.
- **Where Used**: `backend/src/contextedge/services/knowledge_retrieval_service.py`, `backend/src/contextedge/services/source_facets.py`
- **Related Terms**: Knowledge State, Source Facets, Grounding

### Agent
- **Term**: Agent
- **Simple Definition**: An automated consumer or service that executes tasks or retrieves playbooks.
- **Detailed Explanation**: Agents are downstream systems (like MAF or AutomationEdge) that call ContextEdge's runtime APIs to get troubleshooting guidance. They use service tokens and are bounded by the safety policies defined in the playbooks to ensure governed automation.
- **Where Used**: Runtime APIs, MAF Context Graph integration
- **Related Terms**: Service Token, Runtime, Tool

### Alembic
- **Term**: Alembic
- **Simple Definition**: A database migration tool for Python used with SQLAlchemy.
- **Detailed Explanation**: Alembic manages schema changes in the ContextEdge PostgreSQL database. It tracks changes as a sequence of migration scripts (revisions), ensuring the database structure stays in sync with the codebase across different environments.
- **Where Used**: `backend/alembic/versions/`, `make migrate`
- **Related Terms**: SQLAlchemy, Migration

### API
- **Term**: API
- **Simple Definition**: Application Programming Interface, a way for software to communicate.
- **Detailed Explanation**: ContextEdge exposes RESTful APIs powered by FastAPI under `/api/v1`. These endpoints allow clients (dashboard, agents) to query the graph, submit evidence, and retrieve playbooks, protected by JWT or service token authentication.
- **Where Used**: `backend/src/contextedge/api/v1/`
- **Related Terms**: FastAPI, OpenAPI, Endpoint

### Async
- **Term**: Async
- **Simple Definition**: Programming style that allows a program to do other things while waiting for an operation to finish.
- **Detailed Explanation**: ContextEdge uses asynchronous Python (async/await) heavily for database operations, HTTP calls to external APIs, and Celery task wrappers to improve scalability and performance, preventing I/O bounds from blocking the main thread.
- **Where Used**: Throughout `backend/src/contextedge/`
- **Related Terms**: FastAPI, AsyncPG

### AsyncPG
- **Term**: AsyncPG
- **Simple Definition**: A fast, asynchronous database driver for PostgreSQL in Python.
- **Detailed Explanation**: It is used underneath SQLAlchemy 2.0 to provide non-blocking database connections, enabling ContextEdge's FastAPI web server to handle many simultaneous requests efficiently.
- **Where Used**: `backend/src/contextedge/database.py`
- **Related Terms**: Async, PostgreSQL, SQLAlchemy

### Audit Log
- **Term**: Audit Log
- **Simple Definition**: A chronological record of security-relevant events and data access.
- **Detailed Explanation**: `RequestAuditMiddleware` fires after the response for every POST/PATCH/PUT/DELETE under `/api/v1` (except login): it always writes a structlog `http.mutating_request` line, and additionally inserts an `audit_logs` row when a tenant was resolved, with `action = "http.<method>.<path-slug>"` and an outcome derived from the status code. The insert runs on its own sync engine off-thread and swallows its own failures, because auditing must never turn a successful request into a failed one. Consequence worth knowing: unauthenticated 401 probes never resolve a tenant, so they exist only in the log line, not the table.
- **Where Used**: `backend/src/contextedge/middleware/request_audit.py:25-124`, `backend/src/contextedge/models/audit.py`
- **Related Terms**: Compliance, Trace Event, Operational Event

### Authentication
- **Term**: Authentication
- **Simple Definition**: The process of verifying who a user or system is.
- **Detailed Explanation**: Today there are exactly two working mechanisms. Human users POST to `/api/v1/auth/login`, which verifies a bcrypt password hash off the event loop and returns a JWT carrying `{sub, tenant_id, email, roles, exp}`. Machines send an `X-Service-Token` header, which is matched against the `SERVICE_TOKENS_JSON` map. **SAML and OIDC are stubs**: `middleware/auth.py` contains per-tenant config helpers and its own docstring says the full implementation is deferred — no login route is wired to them. Do not promise SSO on the strength of that file. Email is unique per tenant, not globally, so the login path handles the same address existing in two tenants by returning 401 "Ambiguous account" rather than guessing.
- **Where Used**: `backend/src/contextedge/api/v1/auth.py:35-101`, `backend/src/contextedge/deps.py`, `backend/src/contextedge/security_tokens.py`
- **Related Terms**: Authorization, JWT, Bearer Token, Service Token

### Authorization
- **Term**: Authorization
- **Simple Definition**: The process of checking what a verified user or system is allowed to do.
- **Detailed Explanation**: ContextEdge uses Role-Based Access Control (RBAC) with roles such as `tenant_admin`, `domain_admin`, `knowledge_manager`, and `playbook_reviewer`, enforced by `require_role(...)` on individual routes. **Important caveat:** `RoleBinding` rows carry `scope_type` and `scope_id`, but neither is enforced — login reads only role names and `has_role` is a pure name check, so a role granted for one domain applies tenant-wide on every `require_role` route. Finer scoping exists only where a route consults token claims such as `allowed_domain_ids`. `platform_super_admin`, `tenant_admin`, and `admin` short-circuit every check.
- **Where Used**: `backend/src/contextedge/deps.py:37-51`, `backend/src/contextedge/api/v1/`, `backend/src/contextedge/search/risk_policy.py`
- **Related Terms**: Authentication, RBAC, RoleBinding

## B

### Bearer Token
- **Term**: Bearer Token
- **Simple Definition**: A security token included in HTTP requests to prove identity.
- **Detailed Explanation**: Used in the `Authorization: Bearer <token>` header, typically carrying a JWT for users. It is a fundamental mechanism for passing identity and claims across the ContextEdge backend, allowing stateless but secure request processing.
- **Where Used**: `backend/src/contextedge/deps.py`
- **Related Terms**: JWT, Authentication

### Baseline
- **Term**: Baseline
- **Simple Definition**: The normal or previous known state of a metric or system.
- **Detailed Explanation**: Evidence baselines in ContextEdge carry a `baseline_ref` and a `delta_signal` to show how an alert or metric compares to history. This helps reviewers quickly spot anomalies via color-coded severity on reviewer cards.
- **Where Used**: `backend/src/contextedge/models/evidence.py`, Frontend reviewer cards
- **Related Terms**: Delta Signal, Evidence Baseline

### Beat Scheduler
- **Term**: Beat Scheduler
- **Simple Definition**: A Celery component that runs tasks on a schedule.
- **Detailed Explanation**: Celery Beat acts as the cron system for ContextEdge. There are 14 scheduled entries — scheduled syncs every 15 minutes, execution verification every 15 minutes, fleet-group detection every 30 minutes, the knowledge dedup sweep and the episode AI-review sweep hourly, drift and graph reconciliation every 6 hours, contradiction scans every 12 hours, five daily sweeps (identity reconciliation, decision calibration, decision pattern mining, orphan cleanup, retention archive) and the weekly retention purge. **Run exactly one beat process** — a second one double-dispatches every entry.
- **Where Used**: `backend/src/contextedge/workers/celery_app.py:281-384`, `make celery-beat-dev`
- **Related Terms**: Celery, Celery Beat, Worker, Retention

### Business Logic
- **Term**: Business Logic
- **Simple Definition**: The rules that define how the application processes data and enforces policies.
- **Detailed Explanation**: In ContextEdge's architecture, business logic is explicitly separated from routers and background workers. It lives in the `services/` directory, ensuring that rules like playbook lifecycles and retention run identically regardless of how they are invoked.
- **Where Used**: `backend/src/contextedge/services/`
- **Related Terms**: Service, Layer

## C

### Celery
- **Term**: Celery
- **Simple Definition**: A distributed task queue for background processing.
- **Detailed Explanation**: ContextEdge relies on Celery to offload slow processes like ingestion, LLM extractions, vector embedding, and correlation out of the HTTP request cycle. It uses Redis as its broker.
- **Where Used**: `backend/src/contextedge/workers/`
- **Related Terms**: Redis, Worker, Queue

### Celery Beat
- **Term**: Celery Beat
- **Simple Definition**: The scheduling engine for the Celery task queue.
- **Detailed Explanation**: See 'Beat Scheduler'. It dispatches tasks at regular intervals to be executed by Celery workers, used for maintenance sweeps and scheduled data ingestion.
- **Where Used**: Worker deployment configuration
- **Related Terms**: Celery, Beat Scheduler

### Chunk
- **Term**: Chunk
- **Simple Definition**: A small, meaningful segment of a larger piece of evidence.
- **Detailed Explanation**: To enable high-recall searches over long documents (like 40KB post-mortems), ContextEdge breaks them into smaller chunks. Each chunk gets its own vector embedding, allowing the system to locate specific details inside massive threads. Chunks are written by `write_chunks` with a per-chunk `content_hash`, a `chunker_version` for re-chunk safety, and a `source_authority` tag (`runbook` > `ticket` > `email` > `chat` > `gist`).
- **Where Used**: `backend/src/contextedge/services/evidence_chunk_service.py:43`, `backend/src/contextedge/models/evidence.py`
- **Related Terms**: Chunking, Evidence Chunk, HalfVec

### Chunking
- **Term**: Chunking
- **Simple Definition**: The process of dividing large texts into smaller pieces (chunks).
- **Detailed Explanation**: Chunking runs **inline** inside `normalize_evidence` when the body is under 16 KB and the source is on the allow-list (`jira_sm`, `servicenow`, `gmail`, `teams`, `sapphireims`, `zoho_desk`); everything larger or unfamiliar dispatches the async `extraction.chunk_evidence` task instead, so a slow parser cannot stall ingest. Both that task and `extraction.embed_chunks_batch` run on the dedicated **`embedding`** queue — not `extraction` — because when they shared the extraction FIFO, chunks were written and left unembedded, making evidence silently unsearchable. Which chunker runs is decided by record shape first (a `kb_article` gets the document chunker even from a ticket source) and source type second. Embeddings go out in batches of 32.
- **Where Used**: `backend/src/contextedge/services/chunkers/registry.py`, `backend/src/contextedge/workers/chunk_tasks.py:210,238`, `backend/src/contextedge/workers/extraction_tasks.py:54,60-62`
- **Related Terms**: Chunk, Embedding, Queue

### Claim
- **Term**: Claim
- **Simple Definition**: An evidence-backed assertion extracted from operational data.
- **Detailed Explanation**: Introduced in the AE Ops migration, a claim represents a structured fact (like "Server was restarted"). Claims have a validation lifecycle (`unverified` to `human_validated`) and help the system trust data based on its source.
- **Where Used**: `backend/src/contextedge/models/claim.py` (added in `0029`)
- **Related Terms**: Claim Evidence, Decision

### Claim Evidence
- **Term**: Claim Evidence
- **Simple Definition**: The specific source data that proves a given Claim is true.
- **Detailed Explanation**: This links a structured Claim to the exact normalized EvidenceItem that supports it. It provides the crucial "provenance" that ensures ContextEdge is an evidence-first system, not just a hallucinating AI.
- **Where Used**: `backend/src/contextedge/models/claim.py`
- **Related Terms**: Claim, Evidence Item

### Case State
- **Term**: Case State
- **Simple Definition**: Whether the source system considers this ticket resolved, cancelled, or neither.
- **Detailed Explanation**: Derived at ingest from the source's own status field — a Zoho ticket `status`, a numeric ServiceNow `state` — and normalized to `resolved`, `cancelled`, or NULL. It matters because `resolved` is what opens the episode-synthesis resolution gate; `cancelled` deliberately does not, since a cancelled ticket teaches nothing about how to fix anything.
- **Where Used**: `backend/src/contextedge/services/case_state.py:42-135`
- **Related Terms**: Resolution Gate, Knowledge State, Episode

### Classifier
- **Term**: Classifier
- **Simple Definition**: An AI component that categorizes data into groups.
- **Detailed Explanation**: Two run on the ingest path. The **relevance** classifier is the first LLM call on every record and labels it `operational`, `possibly_relevant`, or `not_relevant`; when the label is `not_relevant` *and* confidence is at least 0.75, the rest of the pipeline is skipped — no identity work, no decisions, no embedding, no chunking. The row is still kept as an audit trail, but it is invisible to vector search by construction. If the classifier itself errors, the pipeline **fails open** into the full path rather than dropping the record. The **message-function** classifier runs second, on conversational sources only.
- **Where Used**: `backend/src/contextedge/ai/classifiers/relevance.py`, `backend/src/contextedge/ai/classifiers/message_function.py`, gate at `backend/src/contextedge/workers/extraction_tasks.py:471-479`
- **Related Terms**: LLM, Relevance, Message Function, Noise Gate

### CORS
- **Term**: CORS
- **Simple Definition**: Cross-Origin Resource Sharing, a security feature for web browsers.
- **Detailed Explanation**: Configured in the FastAPI bootstrap, CORS dictates which web domains are allowed to call the ContextEdge API. It prevents malicious websites from making requests on behalf of an authenticated user.
- **Where Used**: `backend/src/contextedge/main.py`, `backend/src/contextedge/config.py`
- **Related Terms**: API, Security

### Connector
- **Term**: Connector
- **Simple Definition**: An adapter that links ContextEdge to an external data source.
- **Detailed Explanation**: Every connector implements the same five-method contract: `validate_credentials`, `discover_objects`, `backfill`, `fetch_changes`, and `hydrate_thread`. Seven are registered today — `teams`, `gmail`, `servicenow`, `jira_sm`, `manageengine`, `sapphireims`, `zoho_desk`; `confluence`, `sharepoint`, and `exchange` appear in the source-creation catalog marked `planned`. Connectors also cooperate with pause/cancel by polling a control callback inside their own loops; the default callback is a no-op and the check never raises, so a failing control channel cannot kill a sync.
- **Where Used**: `backend/src/contextedge/connectors/base.py:78-141`, `backend/src/contextedge/connectors/registry.py:91-122`
- **Related Terms**: Source, Ingestion, Sync

### Context Graph
- **Term**: Context Graph
- **Simple Definition**: The network of relationships between entities, episodes, patterns, and playbooks.
- **Detailed Explanation**: ContextEdge builds this graph to answer complex questions (e.g., "what steps commonly fail for this patch"). It's a temporal, tenant-scoped projection in PostgreSQL using adjacency lists, heavily utilized for ranking and visual exploration.
- **Where Used**: `backend/src/contextedge/graph/`, `frontend/src/app/(dashboard)/graph-explorer/`
- **Related Terms**: Graph Edge, Adjacency List

### Context Window
- **Term**: Context Window
- **Simple Definition**: The maximum amount of text an AI model can process at one time.
- **Detailed Explanation**: ContextEdge manages context windows strictly, using chunking for long evidence and prompt caching. Efficient use of the context window keeps LLM costs down and prevents errors during decision extraction.
- **Where Used**: `backend/src/contextedge/ai/`, Chunking design
- **Related Terms**: Chunking, LLM

### Contradiction
- **Term**: Contradiction
- **Simple Definition**: Conflicting information found within the evidence or playbooks.
- **Detailed Explanation**: ContextEdge actively detects when field evidence contradicts older KBs or playbooks (e.g., a known fix fails in a new environment). Contradictions are explicitly mapped in the Context Graph to warn users and downgrade stale playbooks at runtime.
- **Where Used**: `backend/src/contextedge/services/contradiction_service.py`
- **Related Terms**: Negative Knowledge, Drift

### Correlation
- **Term**: Correlation
- **Simple Definition**: Identifying that different pieces of evidence relate to the same problem.
- **Detailed Explanation**: The correlation engine links related artifacts across systems (e.g., a ServiceNow incident and the Teams thread discussing it) and runs in two tiers. **Tier 1** is deterministic case linking at confidence 1.0: the record's own external id, its thread id, ServiceNow task references, Jira linked-issue keys, Zoho ticket numbers. Shared infrastructure — CIs, assignment groups — is deliberately never a case-link key, because that would mass-merge every incident on the same server. **Tier 2** is identity co-occurrence, gated hard: only `resolved`/`verified` identities count, only inside a 7-day window, hub identities with 200+ links carry zero signal, and a single shared *person* is dropped entirely. Edges are created once and never upgraded, with the case-link tier winning when both matched. Runs as `extraction.correlate_evidence` on the `correlation` queue.
- **Where Used**: `backend/src/contextedge/services/correlation_service.py:197-791`, `backend/src/contextedge/workers/correlation_tasks.py:16`
- **Related Terms**: Episode, Graph Edge, Case State, Recurrence

### Cosine Similarity
- **Term**: Cosine Similarity
- **Simple Definition**: A mathematical way to measure how similar two pieces of text are.
- **Detailed Explanation**: Used by pgvector during vector searches, it compares the angles of embedding vectors. ContextEdge uses this to find evidence or playbooks that semantically match a user's query, even if the exact keywords differ.
- **Where Used**: `backend/src/contextedge/search/vector_search.py`
- **Related Terms**: Embedding, Vector Search, pgvector

### CQRS
- **Term**: CQRS
- **Simple Definition**: Command Query Responsibility Segregation; separating read and write models.
- **Detailed Explanation**: While not strictly enforced everywhere, ContextEdge loosely applies this pattern by separating heavy ingestion/write paths (Celery workers) from optimized read paths (Hybrid Ranker, Runtime API) to ensure high-performance retrieval.
- **Where Used**: Architecture design
- **Related Terms**: Architecture

### CRUD
- **Term**: CRUD
- **Simple Definition**: Create, Read, Update, Delete - the basic functions of data storage.
- **Detailed Explanation**: The Admin and CRUD routers in ContextEdge provide standard API endpoints for managing entities like Tenants, Users, and Sources, mapped directly to SQLAlchemy ORM operations.
- **Where Used**: `backend/src/contextedge/api/v1/`
- **Related Terms**: API, ORM

## D

### Decision
- **Term**: Decision
- **Simple Definition**: A recorded action or judgment made during an operational event.
- **Detailed Explanation**: Decisions are first-class entities extracted by AI or logged by execution engines. They track who did what to which system, helping ContextEdge build a rich "Decision Graph" that makes past troubleshooting steps auditable and traversable.
- **Where Used**: `backend/src/contextedge/services/decision_service.py`, `backend/src/contextedge/models/decision.py`
- **Related Terms**: Decision Trace, Graph Edge

### Decision Evidence
- **Term**: Decision Evidence
- **Simple Definition**: The specific log or message that proves a decision was made.
- **Detailed Explanation**: A relational mapping that connects a Decision record to the underlying EvidenceItem, ensuring every documented action (like a server reboot) has a verifiable source in the raw data.
- **Where Used**: `backend/src/contextedge/models/claim.py` (introduced in `0029`)
- **Related Terms**: Decision, Evidence Item

### Decision Trace
- **Term**: Decision Trace
- **Simple Definition**: The step-by-step history of decisions made during a specific session.
- **Detailed Explanation**: Used for auditing and reviewer bundle generation, it logs append-only trace events (approvals, denials, actions) during playbook execution. Reviewers use this to understand exactly what automation did.
- **Where Used**: `backend/src/contextedge/api/v1/sessions.py`
- **Related Terms**: Trace Event, Resolution Session

### Deep Link
- **Term**: Deep Link
- **Simple Definition**: A URL that takes a user directly to a specific piece of content.
- **Detailed Explanation**: ContextEdge uses deep links in its UI and API responses to allow users to click directly from a playbook step or decision graph edge straight to the underlying Jira ticket or Teams message in the Evidence Explorer.
- **Where Used**: Frontend Explorer UI
- **Related Terms**: Evidence

### Deduplication
- **Term**: Deduplication
- **Simple Definition**: Removing duplicate copies of the same data.
- **Detailed Explanation**: Three layers. At the raw layer, `persist_ingestion_events` skips a payload whose `(tenant_id, source_id, external_id, content_hash)` already exists. At the evidence layer, `_normalize` looks up `(tenant_id, content_hash)` and, on a hit, **refreshes the existing row instead of duplicating it** — merging facets, re-deriving states, repairing a missing embedding — and skips the correlation and hydration fan-out. Behind both sits migration `0026`'s unique index on `(tenant_id, content_hash)`: a concurrent insert raises `IntegrityError`, and the worker rolls back, adopts the winning row, and returns `{"deduped": true, "raced": true}` without re-spending a single LLM call. The content hash is computed on the **raw** body, before cleaning and redaction, so tuning a regex never breaks dedupe. Separately, the hourly `pattern.deduplicate_knowledge` sweep merges duplicate episodes, patterns, and playbooks after the fact.
- **Where Used**: `backend/src/contextedge/services/ingestion_persistence.py:60-72`, `backend/src/contextedge/workers/extraction_tasks.py:213-220,374-409`, Migration `0026`
- **Related Terms**: Normalization, Ingestion, Unique Constraint

### Delta Signal
- **Term**: Delta Signal
- **Simple Definition**: An indicator showing how a value has changed compared to its baseline.
- **Detailed Explanation**: Tagged as `neutral`, `amber`, or `red`, delta signals help reviewers quickly assess severity changes in evidence. For example, if a failure rate spikes, the delta signal makes it visually obvious in the reviewer console.
- **Where Used**: `backend/src/contextedge/models/evidence.py`
- **Related Terms**: Baseline, Evidence Baseline

### Dependency Injection
- **Term**: Dependency Injection
- **Simple Definition**: Providing a module with the exact tools it needs to run, rather than it creating them.
- **Detailed Explanation**: ContextEdge uses FastAPI's `Depends()` heavily in `deps.py` to inject database sessions, current user context, and configuration into router endpoints. This centralizes auth and DB logic, making testing easier.
- **Where Used**: `backend/src/contextedge/deps.py`
- **Related Terms**: FastAPI, Request Context

### Deployment
- **Term**: Deployment
- **Simple Definition**: The process of installing and running the software in an environment.
- **Detailed Explanation**: ContextEdge is deployed as a modular monolith using Docker and Docker Compose. The setup spans FastAPI, Celery, Postgres, Redis, and MinIO, managed via Make targets for local development.
- **Where Used**: `docs/SETUP_GUIDE.md`, `docs/RUNBOOK.md`
- **Related Terms**: Docker, Docker Compose

### Domain
- **Term**: Domain
- **Simple Definition**: A specific problem area or category, like "VPN" or "Identity".
- **Detailed Explanation**: Domains provide an isolation boundary within a tenant or workspace. Playbooks, sources, and graph edges are scoped to domains, allowing domain admins to control knowledge and access strictly within their operational area.
- **Where Used**: `backend/src/contextedge/api/v1/domains.py`, `backend/src/contextedge/models/tenant.py`
- **Related Terms**: Tenant, Workspace

### Drift
- **Term**: Drift
- **Simple Definition**: When an approved playbook becomes outdated or less effective over time.
- **Detailed Explanation**: The drift engine monitors success rates, environment changes, and new contradiction signals. When drift is detected, it flags playbooks for review, preventing the system from recommending stale or dangerous fixes.
- **Where Used**: `backend/src/contextedge/services/drift_service.py`
- **Related Terms**: Contradiction, Evaluation

### Docker
- **Term**: Docker
- **Simple Definition**: A tool that packages software into standardized containers.
- **Detailed Explanation**: Used to package the ContextEdge backend, frontend, and worker processes, ensuring consistent execution across developer laptops and production servers.
- **Where Used**: `docs/RUNBOOK.md`, `docs/SETUP_GUIDE.md`
- **Related Terms**: Deployment, Docker Compose

### Docker Compose
- **Term**: Docker Compose
- **Simple Definition**: A tool for defining and running multi-container Docker applications.
- **Detailed Explanation**: ContextEdge uses `docker-compose` to spin up its entire stack locally with a single command (`make up`), orchestrating Postgres, Redis, MinIO, Celery, and the web servers simultaneously.
- **Where Used**: Local development, `make up`
- **Related Terms**: Docker, Deployment

## E

### Edge
- **Term**: Edge
- **Simple Definition**: A connection between two nodes in a graph.
- **Detailed Explanation**: In ContextEdge, edges represent relationships (e.g., "based_on", "records_decision") between entities, evidence, and playbooks. They are the backbone of the Context Graph, stored in PostgreSQL. Every edge type must be declared in a registry — `require_registered` refuses an unknown type at write time, so a typo cannot quietly create an unreachable edge.
- **Where Used**: `backend/src/contextedge/models/pattern.py:174` (`GraphEdge`), `backend/src/contextedge/graph/edge_types.py`
- **Related Terms**: Graph Edge, Node, Graph

### Embedding
- **Term**: Embedding
- **Simple Definition**: A list of numbers representing the meaning of a piece of text.
- **Detailed Explanation**: Text is converted into vectors by whichever model `DEFAULT_EMBEDDING_MODEL` names, and that model **must return exactly 3072 dimensions** — `generate_embedding` raises a `ValueError` naming the offending model otherwise. The vectors are stored in PostgreSQL `Vector(3072)` columns on `evidence_items`, `evidence_chunks`, `decisions`, and `episodes`, but they are *indexed* as `halfvec` — see HalfVec and HNSW, because 3072 is above the limit for a plain `vector` HNSW index. Embedding calls made with tenant context are budget-gated; the parent-evidence embedding inside `_normalize` currently is not, while chunk embeddings are.
- **Where Used**: `backend/src/contextedge/ai/provider.py:739,787-793`, `backend/src/contextedge/search/vector_search.py`
- **Related Terms**: Vector Search, pgvector, Chunking, HalfVec

### Endpoint
- **Term**: Endpoint
- **Simple Definition**: A specific URL where an API can be accessed.
- **Detailed Explanation**: FastAPI routers define the endpoints (e.g., `POST /api/v1/runtime/match`) that the frontend UI and agent integrations use to interact with the ContextEdge system.
- **Where Used**: `backend/src/contextedge/api/v1/`
- **Related Terms**: API, Router, FastAPI

### Entity
- **Term**: Entity
- **Simple Definition**: A unique operational noun, like a specific server, user, or application.
- **Detailed Explanation**: Formalized in migration `0029` (AE Ops alignment), Entities unify how the system tracks infrastructure and people. They allow the graph to link disparate evidence to exact physical or logical assets.
- **Where Used**: `backend/src/contextedge/models/entity.py`
- **Related Terms**: Identity, Canonical Identity

### Episode
- **Term**: Episode
- **Simple Definition**: A complete, reconstructed history of a single troubleshooting case.
- **Detailed Explanation**: The system takes fragmented evidence (tickets, chats) and builds a structured timeline: trigger -> hypothesis -> steps -> outcome. Episodes are the intermediate step between raw evidence and durable patterns. Synthesis is deliberately gated: a cluster must contain at least 3 correlated evidence items, reconstruction is debounced 180 seconds so a still-arriving thread is narrated once rather than per message, and a re-synthesis needs the cluster to be at least 50% bigger than any episode already covering it. Drafts land in `reviewer_state = 'pending_review'` and wait for a human — or, if enabled, for the AI reviewer, whose verdict is stamped on `episodes.ai_review` (NULL there means "never reviewed").
- **Where Used**: `backend/src/contextedge/services/episode_service.py`, `backend/src/contextedge/models/episode.py`, `backend/src/contextedge/workers/extraction_tasks.py:1391`
- **Related Terms**: Correlation, Pattern, AI Review (Episode), Resolution Gate

### Evaluation
- **Term**: Evaluation
- **Simple Definition**: Testing the system to ensure it gives the right answers.
- **Detailed Explanation**: ContextEdge runs offline evaluations (Replay jobs) against historical "ground truth" datasets. It scores playbook match rates, extraction accuracy, and contradiction handling to catch regressions before they hit production.
- **Where Used**: `backend/src/contextedge/services/evaluation_service.py`, `backend/evals/`
- **Related Terms**: Drift, Grounding

### Event
- **Term**: Event
- **Simple Definition**: Something that happened in an external system or internally.
- **Detailed Explanation**: Three different things wear this name. An `IngestionEvent` is the in-memory unit a connector hands to persistence. A `DecisionTraceEvent` records a step inside a resolution session. An `OperationalEvent` is the append-only ledger row — `llm.usage`, `retention.applied`, `episode.ai_approved`, `correlation.case_linked` and dozens more — that inherits the originating request's correlation and causation ids automatically. Daily LLM spend is measured by summing that day's `llm.usage` events rather than a separate counter column, so there is no second number to drift.
- **Where Used**: `backend/src/contextedge/models/events.py:13-61`, `backend/src/contextedge/services/event_log_service.py:32-85`
- **Related Terms**: Trace Event, Audit Log, Operational Event

### Evidence
- **Term**: Evidence
- **Simple Definition**: Facts and data collected from operational tools.
- **Detailed Explanation**: Evidence is the core foundational data type in ContextEdge. It includes tickets, chats, and logs. ContextEdge never hallucinates knowledge; all playbooks must be firmly grounded in traced, verifiable evidence items.
- **Where Used**: `backend/src/contextedge/models/evidence.py`
- **Related Terms**: Evidence Item, Ingestion

### Evidence Baseline
- **Term**: Evidence Baseline
- **Simple Definition**: The expected normal value for a piece of metric evidence.
- **Detailed Explanation**: By storing baselines, ContextEdge can evaluate incoming numeric data (like a CPU spike) and present immediate, color-coded contextual deltas to reviewers.
- **Where Used**: Frontend reviewer cards, `models/evidence.py`
- **Related Terms**: Baseline, Delta Signal

### Evidence Chunk
- **Term**: Evidence Chunk
- **Simple Definition**: A broken-down segment of a large evidence file.
- **Detailed Explanation**: Added in schema `0030`, EvidenceChunks enable deep, high-recall retrieval. Instead of just searching a document's summary, the system searches the embeddings of these chunks to pinpoint exact failure logs deep within a 50-message thread.
- **Where Used**: `backend/src/contextedge/services/evidence_chunk_service.py`
- **Related Terms**: Chunking, Embedding

### Execution
- **Term**: Execution
- **Simple Definition**: The act of running a playbook to solve a problem.
- **Detailed Explanation**: When a playbook is matched at runtime, an Execution Session is opened. Governed by safety policies, the execution records every human approval and automated step back into the graph.
- **Where Used**: `backend/src/contextedge/services/execution_service.py`
- **Related Terms**: Playbook, Session, Trace Event

### Execution Step Run
- **Term**: Execution Step Run
- **Simple Definition**: The record of a single action taken during playbook execution.
- **Detailed Explanation**: It tracks what action was run, by whom, and the outcome. With migration `0029`, it includes `idempotency_key` to guarantee "banking-grade" duplicate prevention so an automated step isn't triggered twice.
- **Where Used**: `backend/src/contextedge/models/execution.py`
- **Related Terms**: Execution, Idempotency Key

### Extractor
- **Term**: Extractor
- **Simple Definition**: An AI module that pulls structured data out of raw text.
- **Detailed Explanation**: Extractors use LLMs (e.g., `decision_extractor.py`) to parse unstructured chats and tickets into structured claims, identities, and decision edges. They run during the normalization phase in Celery workers.
- **Where Used**: `backend/src/contextedge/ai/extractors/`
- **Related Terms**: LLM, Normalization

## F

### FastAPI
- **Term**: FastAPI
- **Simple Definition**: A fast Python web framework used to build APIs.
- **Detailed Explanation**: ContextEdge uses FastAPI for its backend, leveraging its async capabilities, dependency injection, and automatic OpenAPI schema generation to provide a highly performant API surface.
- **Where Used**: `backend/src/contextedge/main.py`, `backend/src/contextedge/api/v1/`
- **Related Terms**: API, Async

### Fernet Key
- **Term**: Fernet Key
- **Simple Definition**: A secret key used to securely encrypt and decrypt data.
- **Detailed Explanation**: Used for symmetric encryption in ContextEdge, typically to protect sensitive data at rest, like stored credentials for Connectors, ensuring that database leaks do not expose third-party access tokens.
- **Where Used**: Security and configuration modules.
- **Related Terms**: Authentication, Security

### FTS (Full-Text Search)
- **Term**: FTS (Full-Text Search)
- **Simple Definition**: Searching for exact words or phrases within text.
- **Detailed Explanation**: ContextEdge stores `tsvector` columns in Postgres with GIN indexes (migration `0007`) to support fast keyword searches. Evidence FTS is more than a `plainto_tsquery` match: it ORs in two fallbacks in the same statement — a ticket-number lookup against the raw payload (so a reviewer can find `INC0010427` by typing its number) and a `title ILIKE`. By default it also excludes `thread_message` rows, because hydrated replies belong under their parent's thread view rather than as standalone hits. The Hybrid Ranker combines the FTS score with vector and graph signals for playbook matching.
- **Where Used**: `backend/src/contextedge/search/pg_fts.py:12-105`, `backend/src/contextedge/search/hybrid_ranker.py`
- **Related Terms**: Vector Search, Hybrid Search, tsvector

### Foreign Key
- **Term**: Foreign Key
- **Simple Definition**: A database link connecting a row in one table to a row in another.
- **Detailed Explanation**: ContextEdge uses foreign keys extensively (e.g., linking `PlaybookVersion` to `Playbook`) to enforce relational integrity at the database layer, ensuring no orphaned data remains.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: ORM, PostgreSQL

## G

### Generator
- **Term**: Generator
- **Simple Definition**: An AI component that writes or summarizes text.
- **Detailed Explanation**: AI Generators are used to draft candidate playbooks or summarize episodes from normalized evidence. They are always kept strictly human-in-the-loop, generating "candidates" that require human approval.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: LLM, Extractor, Human-in-the-Loop

### GIN Index
- **Term**: GIN Index
- **Simple Definition**: A database index optimized for searching text and JSON.
- **Detailed Explanation**: Generalized Inverted Index (GIN) is used in PostgreSQL by ContextEdge to speed up Full-Text Search on `tsvector` columns, ensuring fast keyword retrieval even across millions of evidence rows.
- **Where Used**: PostgreSQL migrations, `backend/src/contextedge/models/`
- **Related Terms**: FTS, tsvector, PostgreSQL

### Graph
- **Term**: Graph
- **Simple Definition**: A network of connected points representing complex relationships.
- **Detailed Explanation**: ContextEdge utilizes a graph data model stored relationally to link evidence, identities, and playbooks. This allows users to visually explore "what connects to what" via the Graph Explorer dashboard.
- **Where Used**: `backend/src/contextedge/graph/`
- **Related Terms**: Context Graph, Edge, Node

### Graph Edge
- **Term**: Graph Edge
- **Simple Definition**: A single connection line in the Context Graph.
- **Detailed Explanation**: The `GraphEdge` table stores relationships with types like `approved_by` or `records_decision`, carrying temporal validity (`valid_from` / `valid_to`) so the graph can represent knowledge that changed over time. Writes go through `ensure_edge`, which is idempotent: it selects the active edge, then `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index `uq_graph_edges_active_logical` (`WHERE valid_to IS NULL`), then re-selects if it lost the race — so two concurrent workers can never abort each other's transaction.
- **Where Used**: `backend/src/contextedge/models/pattern.py:174-273`, `backend/src/contextedge/graph/builder.py:50-135`
- **Related Terms**: Edge, Context Graph, Temporal

### Graph Edge Meta
- **Term**: Graph Edge Meta
- **Simple Definition**: Extra information attached to a graph connection.
- **Detailed Explanation**: JSON metadata (`metadata_extra`) stored on a GraphEdge, carrying specifics like resolution state, risk level, or tool references. Note that an edge has **two** separate numeric fields that are easy to confuse: `weight` is traversal importance (how much this edge should pull the agent's attention) and `confidence` is belief (how sure we are the relationship is real). Callers pass both when they mean both.
- **Where Used**: `backend/src/contextedge/models/pattern.py:174-273`, `backend/src/contextedge/graph/builder.py:63-72`
- **Related Terms**: Graph Edge

### Grounding
- **Term**: Grounding
- **Simple Definition**: Ensuring AI answers are based strictly on real facts.
- **Detailed Explanation**: ContextEdge enforces grounding by linking every AI-generated claim, playbook step, or pattern back to source evidence. Generated playbook steps are split into a two-way taxonomy — grounded (traceable to an episode or a retrieved document) versus best practice — and the split is enforced structurally after citation cleaning, not just requested in the prompt. Steps that are not grounded are surfaced with a `[best practice]` marker rather than being hidden, so a reader can see which half of a playbook is evidence and which half is convention.
- **Where Used**: `backend/src/contextedge/ai/prompts/playbook.py`, `backend/src/contextedge/graph/agent/hydrators.py:211-220`
- **Related Terms**: Evidence, Claim Evidence, Applicability

## H

### HalfVec
- **Term**: HalfVec
- **Simple Definition**: A memory-efficient way to store vector embeddings.
- **Detailed Explanation**: A pgvector type that stores vector coordinates as 16-bit floats instead of 32-bit. In ContextEdge it is not a nice-to-have optimization — it is **the only way vector indexing works at all**, because pgvector's HNSW caps the plain `vector` type at 2,000 dimensions and this application stores 3,072. Every semantic query therefore orders by `halfvec_cosine_distance`, which casts both sides to `halfvec(3072)` so it matches the expression indexes built in migration `0032`. A query that calls `column.cosine_distance(...)` directly compiles fine, returns correct results, and silently does a sequential scan.
- **Where Used**: `backend/src/contextedge/search/vector_ops.py:40-45`, migration `0032_halfvec_hnsw_indexes`
- **Related Terms**: Vector, Embedding, pgvector, HNSW

### HNSW
- **Term**: HNSW
- **Simple Definition**: A fast search algorithm for finding similar vectors.
- **Detailed Explanation**: Hierarchical Navigable Small World indexes make similarity search fast. The history here matters: migration `0021` declared HNSW indexes that **could never have been built**, because the embeddings are 3,072-dimensional and pgvector's `vector` HNSW stops at 2,000 — so for a long time every similarity query was a sequential scan while the docs claimed otherwise. Real ANN indexing landed in migration `0032` as HNSW *expression* indexes over `(embedding::halfvec(3072))` on `evidence_items`, `evidence_chunks`, `decisions`, and `episodes`. It needs the pgvector server extension at 0.7 or newer, which is why `docker-compose.yml` pins `pgvector/pgvector:pg16`. One more operational detail: the indexes are global across tenants while every query post-filters by `tenant_id`, so callers run `SET LOCAL hnsw.ef_search = 200` first — at the default 40 a small tenant's rows can be missing from the candidate set entirely and the query quietly returns fewer rows than requested.
- **Where Used**: Migration `0032_halfvec_hnsw_indexes`, `backend/src/contextedge/search/vector_ops.py:31-45`
- **Related Terms**: pgvector, Vector Search, Similarity Search, HalfVec

### Hook
- **Term**: Hook
- **Simple Definition**: A way to trigger custom code when a specific event happens.
- **Detailed Explanation**: Used in architecture to allow integrations (like the MAF Context Graph integration) to react to internal events, like executing logic after a playbook is approved or when evidence is ingested.
- **Where Used**: Service integrations
- **Related Terms**: Event

### Human-in-the-Loop
- **Term**: Human-in-the-Loop
- **Simple Definition**: Requiring a person to review and approve AI decisions.
- **Detailed Explanation**: A core tenet of ContextEdge. The system never publishes a playbook or approves a high-risk automated execution without explicit human review via structured reason codes (e.g., `policy_violation`).
- **Where Used**: Playbook Lifecycle, Reviewer Queues
- **Related Terms**: Governance, Playbook

### Hybrid Search
- **Term**: Hybrid Search
- **Simple Definition**: Combining keyword search and meaning search to get the best results.
- **Detailed Explanation**: `hybrid_ranker.rank_playbooks` blends seven weighted signals — keyword 0.25, semantic 0.30, graph distance 0.15, evidence quality 0.10, identity 0.05, recency 0.10, freshness 0.05 — minus a negative-knowledge penalty. The semantic signal is gated by the keyword score, so pure vector drift cannot carry a result on its own. **Abstention is a feature**: anything scoring below `MIN_RECOMMENDATION_SCORE = 0.35` is dropped, and if candidates existed but all fell below, the ranker logs `ranking.abstained` and returns an empty list. An empty result means "no recommendation", not "search failed".
- **Where Used**: weights at `backend/src/contextedge/search/hybrid_ranker.py:22-31`, abstention floor at `:171`, the scorer at `:213-379`
- **Related Terms**: FTS, Vector Search, Runtime

### Hydration
- **Term**: Hydration
- **Simple Definition**: Filling in missing details to complete a dataset.
- **Detailed Explanation**: "Thread hydration" happens when ContextEdge has ingested a record that references a conversation — a Zoho ticket, a Teams thread — and reaches back into the source to pull the surrounding messages, building complete context without crawling everything. It runs as `hydration.hydrate_thread` on its own queue, and each fetched message becomes a raw row that loops back through normalization. **The loop terminates** because a hydrated message never requests hydration itself (one shared predicate prevents 10-50× re-hydration amplification) and re-delivered messages dedupe at the raw layer.
- **Where Used**: `backend/src/contextedge/workers/hydration_tasks.py:36-205`, `backend/src/contextedge/workers/extraction_tasks.py:611-615`
- **Related Terms**: Thread, Ingestion, Noise Gate

### Hydrator
- **Term**: Hydrator
- **Simple Definition**: The component responsible for performing data hydration.
- **Detailed Explanation**: Worker services that execute the targeted history reads to fetch full thread context for incident-linked events.
- **Where Used**: Celery hydration tasks
- **Related Terms**: Hydration

## I

### Identity
- **Term**: Identity
- **Simple Definition**: A unique, recognized person, system, or object.
- **Detailed Explanation**: Canonical identities map disparate text references (e.g., "jsmith", "John S.") to a single record. Only four entity types bear identity — `person`, `device`, `application`, `service`; things like environment or version are rejected because they belong in source facets and applicability instead. An identity carries a `resolution_state`: `provisional` (seen once), `needs_review` (the adjudicator was unsure), `resolved` (corroborated), `verified` (a human merged it).
- **Where Used**: `backend/src/contextedge/services/identity_service.py`, `backend/src/contextedge/models/episode.py:48-88`
- **Related Terms**: Identity Resolution, Canonical Identity

### Identity Resolution
- **Term**: Identity Resolution
- **Simple Definition**: The process of linking different names to the same identity.
- **Detailed Explanation**: Runs during evidence normalization, in four ordered layers, cheapest first. **1.** Strong-identifier lookup on an email, hostname, FQDN, IP, serial, or username — confidence 1.0, no model call. `vpn-gw-east-01` resolves here forever after its first sighting. **2.** Typed exact alias match — 0.95. **3.** A candidacy gate that rejects anything not worth spending on (identity work was 78% of all model spend before it existed), then LLM adjudication over up to 5 trigram/substring candidates; an auto-link only happens at or above 0.95 for people and 0.9 for everything else, and anything below that becomes a **new identity in `needs_review`** rather than a silent link or a silent fork. **4.** Otherwise a provisional identity is created. Successful matches learn the alias they matched on, so the next occurrence resolves deterministically at layer 1 or 2. A provisional identity linked by 2 or more distinct evidence items (and not yet common) is promoted to `resolved` at the moment it first *could* correlate something.
- **Where Used**: `backend/src/contextedge/services/identity_service.py:616-796`, `backend/src/contextedge/services/identity_candidacy.py`, `backend/src/contextedge/services/identity_promotion.py`
- **Related Terms**: Identity, Correlation

### Idempotency Key
- **Term**: Idempotency Key
- **Simple Definition**: A unique code ensuring an action only happens once, even if requested twice.
- **Detailed Explanation**: Added to `ExecutionStepRun` in migration `0029`, it provides banking-grade duplicate prevention. This guarantees that automated playbook steps (like rebooting a server) are never executed multiple times accidentally by network retries.
- **Where Used**: `backend/src/contextedge/models/execution.py`
- **Related Terms**: Execution Step Run

### Ingest Priority
- **Term**: Ingest Priority
- **Simple Definition**: An optional per-source-object rule for which raw records get normalized first.
- **Detailed Explanation**: Set on `source_objects.metadata_extra["ingest_priority"]`; valid values are `none` (arrival order, the default), `resolution_first`, `threads_desc`, and `threads_asc`. Ordering is SQL over the raw payload's thread count and resolution field, and it is fail-soft — an ordering error returns the original list, and ids are never added or dropped. **Known blind spot:** a raw payload over 32 KB lives in MinIO and the database row holds only a stub, so its thread count and resolution read as absent and it sorts to the back regardless of mode. The biggest tickets — the longest conversations — are exactly the ones prioritization cannot see.
- **Where Used**: `backend/src/contextedge/services/ingest_priority.py:36-95`
- **Related Terms**: Ingestion, Offload, Sync

### Index
- **Term**: Index
- **Simple Definition**: A database structure that makes searching for data much faster.
- **Detailed Explanation**: ContextEdge uses GIN indexes for text search, HNSW indexes for vector search, and standard B-Tree indexes for foreign keys and unique constraints (like `tenant_id` scopes) to ensure rapid API responses.
- **Where Used**: `backend/alembic/versions/`
- **Related Terms**: GIN Index, HNSW, PostgreSQL

### Ingestion
- **Term**: Ingestion
- **Simple Definition**: The process of bringing external data into the system.
- **Detailed Explanation**: Managed by Connectors and the Sync Worker Service. Data is pulled from the source, saved as `raw_evidence_objects` rows, committed, and only then queued for normalization — the ordering matters, because a worker must never be handed an id it cannot yet read. "Claim-before-queue" is the recovery pattern: raw ids that fail to enqueue are parked on `source_objects.metadata_extra["pending_normalize_raw_ids"]`, the run is marked failed with a handoff blob, and the next successful run re-drains the ledger. Registered connectors today: `teams`, `gmail`, `servicenow`, `jira_sm`, `manageengine`, `sapphireims`, `zoho_desk` (`confluence`, `sharepoint`, and `exchange` appear in the picker catalog as `planned`).
- **Where Used**: `backend/src/contextedge/connectors/registry.py:91-122`, `backend/src/contextedge/services/sync_worker_service.py:301-376`
- **Related Terms**: Connector, Sync, Normalization, Ingest Priority

### Issue Signature
- **Term**: Issue Signature
- **Simple Definition**: A generalized fingerprint of *what kind of problem* an episode was, so the same kind of problem can be recognized again later.
- **Detailed Explanation**: When an episode is approved, one LLM call distils it into `affected_capability`, `failing_component`, `failure_mode`, and a descriptive `trigger_change` / `environment` / `scope`. The identity key is only the first three, slugged and joined: `remote_access|tls_certificate|certificate_expired`. Trigger, environment, and scope are recorded but excluded from the key on purpose, so the same failure triggered a different way still counts as the same recurring issue. The prompt explicitly forbids hostnames, ticket numbers, and people — a signature that named `vpn-gw-east-01` would match nothing else, ever. Positioned deliberately between two neighbours: broader than an `ErrorSignature`'s exact error string, narrower than raw embedding similarity.
- **Where Used**: `backend/src/contextedge/services/issue_signature_service.py:76-86,89`, `backend/src/contextedge/models/issue_signature.py`, task `evaluation.extract_issue_signature`
- **Related Terms**: Recurrence, Episode, Error Signature, Pattern

### IVFFlat
- **Term**: IVFFlat
- **Simple Definition**: An older algorithm for vector search.
- **Detailed Explanation**: While pgvector supports it, ContextEdge prefers HNSW for its embeddings due to better performance at scale, though IVFFlat might be referenced in older Postgres vector search discussions.
- **Where Used**: pgvector configurations
- **Related Terms**: pgvector, HNSW

## J

### JSON
- **Term**: JSON
- **Simple Definition**: A standard text format for storing and sending data.
- **Detailed Explanation**: Used universally in ContextEdge for API payloads, Celery task arguments, and unstructured column storage in PostgreSQL (using `JSONB`). AI extractions are also forced into schema-validated JSON.
- **Where Used**: API, Database, LLM extraction schemas
- **Related Terms**: API

### JWT
- **Term**: JWT
- **Simple Definition**: JSON Web Token, a secure way to transmit user identity.
- **Detailed Explanation**: ContextEdge uses JWTs as Bearer tokens to authenticate human users. They contain claims about the user's tenant, roles, and session expiry, evaluated by dependency injection on every API request.
- **Where Used**: `backend/src/contextedge/deps.py`, `backend/src/contextedge/security_tokens.py`
- **Related Terms**: Authentication, Bearer Token

### Jira
- **Term**: Jira
- **Simple Definition**: A popular issue tracking and ticketing system.
- **Detailed Explanation**: One of the primary external Sources for ContextEdge, registered under the source type `jira_sm`. The connector pulls tickets and comments, maps them into EvidenceItems, and correlates them with chat threads to build complete Episodes. It uses kind-prefixed thread ids (`incident:PROJ-123`) and a JQL minute cursor with a 30-minute overlap rewind, because JQL's minute granularity would otherwise skip records that changed inside the same minute as the cursor.
- **Where Used**: `backend/src/contextedge/connectors/jira_sm/`
- **Related Terms**: Source, Connector

## K

### Knowledge State
- **Term**: Knowledge State
- **Simple Definition**: Whether the source system considers a KB article draft, in review, published, or retired.
- **Detailed Explanation**: Derived at ingest from the source's own lifecycle field — ServiceNow `kb_knowledge.workflow_state`, Zoho `articles.status` — and normalized to `draft` / `review` / `published` / `retired`. Retrieval withholds the first two and the last: only `published` articles reach a playbook prompt. The nuance that catches people out is **NULL**, which means "the source did not say" and therefore always serves. The SQL predicate is written as an explicit `IS NULL OR NOT IN` rather than a bare `NOT IN`, because three-valued logic would otherwise drop every NULL row. Rows ingested before migration `0067` stay NULL until their next sync, and are deliberately not backfilled with SQL, because payloads over 32 KB are offloaded and a SQL backfill would silently skip the longest articles.
- **Where Used**: `backend/src/contextedge/services/knowledge_lifecycle.py:48-152`
- **Related Terms**: Case State, Applicability, Offload

### Knowledge Transfer
- **Term**: Knowledge Transfer
- **Simple Definition**: Moving information from one place or person to another.
- **Detailed Explanation**: The ultimate goal of ContextEdge. It transfers operational tribal knowledge buried in ticket systems and chat threads into durable, versioned, machine-readable playbooks that can teach new analysts or guide automated agents.
- **Where Used**: Product Vision
- **Related Terms**: Playbook, Operational Memory

## L

### Layer
- **Term**: Layer
- **Simple Definition**: A distinct section of the software architecture.
- **Detailed Explanation**: ContextEdge uses a layered architecture (API routers -> Service Layer -> Data Models/Database). This separation of concerns ensures business logic is reusable by both HTTP endpoints and background Celery workers.
- **Where Used**: Architecture Blueprint
- **Related Terms**: Architecture, Service, API

### LiteLLM
- **Term**: LiteLLM
- **Simple Definition**: A tool that standardizes how to talk to different AI models.
- **Detailed Explanation**: ContextEdge uses LiteLLM as an abstraction layer to route prompts to various providers (OpenAI, Anthropic, Vertex AI) using a unified API, supporting prompt caching and fallback logic.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: LLM, Provider

### LLM
- **Term**: LLM
- **Simple Definition**: Large Language Model, a powerful AI that understands text.
- **Detailed Explanation**: LLMs power ContextEdge's Extractors, Generators, and Classifiers. They are used to parse messy chat logs into structured episodes and draft playbooks, guided by strict prompts and JSON schemas.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: LiteLLM, Prompt, Extractor

## M

### MAF
- **Term**: MAF
- **Simple Definition**: Microsoft Agent Framework.
- **Detailed Explanation**: A downstream consumer system. ContextEdge acts as a tool/provider for MAF, projecting a subset of the Context Graph securely so MAF agents can retrieve approved operational playbooks at runtime.
- **Where Used**: `docs/MAF_CONTEXT_GRAPH_INTEGRATION.md`
- **Related Terms**: Agent, Context Graph

### Materializer
- **Term**: Materializer
- **Simple Definition**: A process that calculates and stores complex data ahead of time.
- **Detailed Explanation**: Rather than calculating graph traversals or playbook statistics on every API call, background tasks "materialize" these views in the database to keep runtime retrieval latency extremely low.
- **Where Used**: Data pipeline, Workers
- **Related Terms**: Projection

### Message Function
- **Term**: Message Function
- **Simple Definition**: What a single chat or email message is *doing* — reporting a symptom, proposing a fix, confirming a resolution, coordinating.
- **Detailed Explanation**: The second LLM call on the ingest path, and only for conversational sources. The label lands on `evidence_items.message_function` with a confidence, and an out-of-vocabulary label degrades to `unclassified` rather than being stored raw. It is fail-soft: a failed call costs the label, not the record. This is what lets episode synthesis tell "any update on the VPN?" apart from "restarted IPSec on the gateway, tunnel stable".
- **Where Used**: `backend/src/contextedge/ai/classifiers/message_function.py:38-65`, `backend/src/contextedge/workers/extraction_tasks.py:487-505`
- **Related Terms**: Classifier, Thread, Noise Gate

### MMR (Maximal Marginal Relevance)
- **Term**: MMR (Maximal Marginal Relevance)
- **Simple Definition**: A way of picking search results that are relevant *and* different from each other.
- **Detailed Explanation**: Chunk-level semantic search oversamples candidates and then applies MMR with λ = 0.7 before rolling up to one hit per parent evidence item. Without it, twenty near-identical chunks from one long thread would crowd out every other thread. If a chunk's embedding is missing or corrupt, MMR degrades to plain distance ordering — one bad chunk never fails the request.
- **Where Used**: `backend/src/contextedge/search/chunk_rollup.py:31,79-121`, `backend/src/contextedge/search/vector_search.py:204-243`
- **Related Terms**: Vector Search, Chunk, Similarity Search

### Memory
- **Term**: Memory
- **Simple Definition**: The stored knowledge of past events and solutions.
- **Detailed Explanation**: "Operational Memory" is the core value proposition. The system retains the context of what broke, who fixed it, and how, turning transient chats into a permanent, queryable corporate memory bank.
- **Where Used**: Product Vision
- **Related Terms**: Operational Memory

### Middleware
- **Term**: Middleware
- **Simple Definition**: Software that runs in the middle of a request, processing it before the main code.
- **Detailed Explanation**: ContextEdge uses FastAPI middleware to intercept HTTP requests to enforce tenant context, inject audit tracing IDs (correlation_id), and record Prometheus metrics before the router handles the request.
- **Where Used**: `backend/src/contextedge/middleware/`
- **Related Terms**: Request Context, API

### Migration
- **Term**: Migration
- **Simple Definition**: A script that updates the database structure to a new version.
- **Detailed Explanation**: Managed by Alembic. As new features are added, migrations safely add tables, columns, and indexes without destroying existing data. **The standing rule in this repo: never quote a head revision number in prose — run `alembic heads` and trust that.** Two enforcement points depend on it: `/ready` returns 503 when `alembic_version` does not match the bundled head, and every Celery worker calls `SystemExit` at startup for the same reason. `0001_initial_schema` builds from model metadata rather than being a frozen DDL snapshot, which is why fresh installs never reproduce historical drift bugs (and why those bugs survived so long).
- **Where Used**: `backend/alembic/versions/`, `docs/MIGRATIONS.md`, `backend/src/contextedge/workers/celery_app.py:83-139`
- **Related Terms**: Alembic, Database

### MinIO
- **Term**: MinIO
- **Simple Definition**: A storage system for large files, compatible with Amazon S3.
- **Detailed Explanation**: The object store for raw evidence payloads over 32 KB (at `raw/{tenant}/{raw_id}.json`) and attachment binaries (at `artifacts/{tenant}/{evidence}/{artifact}/...`), so large blobs do not bloat Postgres. The client is deliberately impatient — 1-second connect and read timeouts with a single attempt — so a slow store fails fast rather than stalling a worker. The bucket is auto-created at API startup; a failure there only degrades (`object_store: degraded` on `/ready`), it does not stop the app.
- **Where Used**: `backend/src/contextedge/services/object_store.py:19-90`
- **Related Terms**: Object Store, Evidence, Offload

### Model
- **Term**: Model
- **Simple Definition**: The structural definition of data in code.
- **Detailed Explanation**: In ContextEdge, SQLAlchemy ORM models define the schema of the Postgres database (e.g., `Playbook`, `EvidenceItem`). They map Python objects to database rows.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: ORM, SQLAlchemy, Schema

### Multi-Tenancy
- **Term**: Multi-Tenancy
- **Simple Definition**: Serving multiple customers or organizations securely from one system.
- **Detailed Explanation**: ContextEdge enforces strict multi-tenancy. Every piece of data is tagged with a `tenant_id`, and all API queries and vector searches automatically append tenant filters to prevent data leakage between organizations.
- **Where Used**: `backend/src/contextedge/models/tenant.py`, Middleware
- **Related Terms**: Tenant, Workspace

## N

### Negative Knowledge
- **Term**: Negative Knowledge
- **Simple Definition**: Information about what NOT to do.
- **Detailed Explanation**: ContextEdge tracks failed troubleshooting steps, expired workarounds, and misleading SOPs as Negative Knowledge edges in the graph. This ensures the system explicitly warns users away from past mistakes.
- **Where Used**: `backend/src/contextedge/api/v1/negative_knowledge.py`
- **Related Terms**: Contradiction, Graph Edge

### Node
- **Term**: Node
- **Simple Definition**: A single point or entity in a graph.
- **Detailed Explanation**: In the Context Graph, nodes represent Entities (like users, servers), EvidenceItems, Episodes, and Playbooks. They are connected by Edges to map operational reality.
- **Where Used**: `backend/src/contextedge/graph/`
- **Related Terms**: Edge, Graph

### Normalization
- **Term**: Normalization
- **Simple Definition**: Cleaning and standardizing messy data into a uniform format.
- **Detailed Explanation**: The `extraction.normalize_evidence` task turns a raw payload into a standard `EvidenceItem`. The order inside `_normalize` is fixed and worth memorizing, because almost every ingest question is "which of these stages did it stop at?": load raw payload → hydrated-message noise gate → title/body extraction and content hash → **redaction** → dedupe on `(tenant_id, content_hash)` → insert the row with derived `evidence_type`, `knowledge_state`, `case_state`, `source_facets` → thread and attachments → relevance classification → extraction gate → message-function classification → deterministic error-signature fingerprints → identity resolution → decision extraction → parent embedding → chunk dispatch. Redaction sits before every model call by design, so PII never leaves the tenant boundary. Each LLM stage is individually wrapped: a failure degrades one field, it does not fail the record.
- **Where Used**: `backend/src/contextedge/workers/extraction_tasks.py:122-628`, `backend/src/contextedge/services/evidence_normalization.py`
- **Related Terms**: Evidence, Deduplication, Redaction, Noise Gate

### Noise Gate
- **Term**: Noise Gate
- **Simple Definition**: A cheap, deterministic filter that throws away chat messages carrying no diagnostic content, before any AI runs.
- **Detailed Explanation**: Applies only to hydrated thread messages. It rejects four kinds of message — `delivery_failure`, `quote_only`, `empty`, and `coordination_only` — where the last means "under 150 characters *and* containing no technical signal" (no error code, path, version, hostname, URL, IP, stack trace, SQL, or shell fragment; 15 regexes decide). Length is measured after markup and signatures are stripped, so a short message that mentions `vpn-gw-east-01` survives while "any update on the VPN?" does not. A rejection creates **no evidence row at all**, but the raw object is kept and the `filter_version` is logged, so tightening a rule later can re-judge every previously rejected message exactly. Measured impact: 47% of 18,907 live messages rejected before a single model call.
- **Where Used**: `backend/src/contextedge/services/message_filter.py:52-206`, `backend/src/contextedge/workers/extraction_tasks.py:147-160`
- **Related Terms**: Normalization, Hydration, Classifier

### Notification
- **Term**: Notification
- **Simple Definition**: An alert sent to a user about an important event.
- **Detailed Explanation**: Users receive alerts for sync failures, expiring credentials, playbook candidates, drift, contradictions, evaluation regressions, review assignments, and playbook approvals. Three channels exist: in-app (a `notifications` row, polled by the header bell), email over SMTP, and a Teams/Slack-compatible webhook. Email and webhook are **no-ops until configured** — they log `notification.email_skipped_unconfigured` / `notification.webhook_skipped_unconfigured` rather than failing. All delivery is best-effort: a failure is logged and never raised into the flow that triggered it. A `user_id` of NULL means the notification is a tenant-wide broadcast.
- **Where Used**: `backend/src/contextedge/services/notification_service.py:24-220`, `backend/src/contextedge/models/events.py:64-89`
- **Related Terms**: Drift, Review Queue

## O

### ORM
- **Term**: ORM
- **Simple Definition**: Object-Relational Mapping, a way to interact with databases using code objects.
- **Detailed Explanation**: ContextEdge uses SQLAlchemy 2.0 as its ORM, allowing developers to query and manipulate PostgreSQL data using Python classes rather than raw SQL strings, improving security and maintainability.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: SQLAlchemy, Model

### Object Store
- **Term**: Object Store
- **Simple Definition**: A system designed to store massive amounts of unstructured files.
- **Detailed Explanation**: ContextEdge uses S3-compatible storage (like MinIO locally) to store raw evidence payloads and attachments, isolating large blobs from the transactional Postgres database.
- **Where Used**: `backend/src/contextedge/services/object_store.py`
- **Related Terms**: MinIO, Evidence, Offload

### Offload (Raw Payload Offload)
- **Term**: Offload (Raw Payload Offload)
- **Simple Definition**: Moving a raw payload bigger than 32 KB out of the database and into object storage, leaving a placeholder behind.
- **Detailed Explanation**: When a raw payload's JSON exceeds `OFFLOAD_THRESHOLD_BYTES = 32_768`, it is uploaded to MinIO at `raw/{tenant_id}/{raw_id}.json` and the `raw_payload` column keeps only `{"_offloaded": true, "size_bytes": N}`, with the real key in `object_storage_key`. This keeps the table small, but it has one consequence every developer must internalize: **any SQL that filters or reads `raw_evidence_objects.raw_payload` silently skips the biggest rows** — which are exactly the longest tickets, threads, and articles. It already affects ingest-priority ordering, reply-inheritance reconciliation, and is the stated reason `knowledge_state` and `source_facets` on older rows are refilled on the next sync rather than by a SQL backfill. Reading a payload back goes through `load_raw_payload`; an offload stub with no storage key is treated as legacy corruption and reported rather than guessed at.
- **Where Used**: `backend/src/contextedge/services/ingestion_persistence.py:16,84-87`, `backend/src/contextedge/services/object_store.py:50-59`
- **Related Terms**: Object Store, MinIO, Ingest Priority, Knowledge State

### Operational Event
- **Term**: Operational Event
- **Simple Definition**: An append-only record of something the system did, with the ids needed to trace it back to whoever triggered it.
- **Detailed Explanation**: Rows in `operational_events` carrying `entity_type`, `entity_id`, `event_type`, `occurred_at`, `correlation_id`, `causation_id`, `actor_id`, and a JSONB payload. Correlation and causation ids default from the current request context, which is threaded from the HTTP middleware into Celery task headers and rebound on the worker — so one id joins an operator's click to the LLM spend it caused. Event families in use include `llm.usage`, `llm.budget_warning`, `retention.applied`, `session.created`, `runtime.match_completed`, `identity.resolved`, `episode.ai_approved`, `correlation.case_linked`, `decision.created`, and `agent_graph.projected`.
- **Where Used**: `backend/src/contextedge/models/events.py:13-61`, `backend/src/contextedge/services/event_log_service.py:32-85`
- **Related Terms**: Event, Audit Log, Request Context

### OpenAPI
- **Term**: OpenAPI
- **Simple Definition**: A standard way to document how an API works.
- **Detailed Explanation**: FastAPI automatically generates an OpenAPI schema for ContextEdge. This powers the interactive Swagger UI (`/docs`), allowing developers and MAF agents to understand the API contracts instantly.
- **Where Used**: `http://localhost:8000/docs`
- **Related Terms**: Swagger, API, FastAPI

### Operational Memory
- **Term**: Operational Memory
- **Simple Definition**: A company's recorded knowledge of how its systems break and get fixed.
- **Detailed Explanation**: The core product vision. ContextEdge transforms scattered, transient conversations and tickets into a structured, governed, and queryable memory graph that reduces mean-time-to-resolution.
- **Where Used**: Product Vision, PRD
- **Related Terms**: Context Graph, Playbook

## P

### Pattern
- **Term**: Pattern
- **Simple Definition**: A recurring issue or sequence of events identified by the system.
- **Detailed Explanation**: The pattern engine clusters repeated Episodes (e.g., "7 instances of VPN failure after Windows update"). An approved, embedded episode is first probed against existing patterns by cosine distance below 0.35 and adjudicated by an LLM; failing that, it clusters with unlinked neighbours inside cosine distance 0.20 and one LLM call synthesizes a new pattern. Creating a pattern auto-enqueues a playbook candidate. **There is no beat entry for clustering** — it fires when an episode is approved (by a human or by auto-approve), once per affected domain, or manually via `POST /api/v1/patterns/cluster`. Two behaviours to know: the match adjudication **fails open** during a provider outage, so the embedding probe alone decides membership; and a full pass runs inside a single long transaction, so a late failure rolls back every row while the LLM spend stays spent.
- **Where Used**: `backend/src/contextedge/workers/pattern_tasks.py:153,422`, `backend/src/contextedge/services/pattern_service.py:62-197`
- **Related Terms**: Episode, Playbook, Issue Signature

### PII
- **Term**: PII
- **Simple Definition**: Personally Identifiable Information (like names, SSNs).
- **Detailed Explanation**: ContextEdge runs regex PII redaction at ingest time, *before* anything is sent to an external LLM or embedded. Rule order is deliberate — secrets before numerics, so a token is never half-redacted — covering API tokens (GitHub, Slack, OpenAI, GitLab, Google), JWTs, bearer tokens, secret assignments, emails, phone numbers, SSNs, credit cards, AWS keys, and private-key blocks. Phone matching is word-boundary-guarded so hex ids and serial numbers survive intact: corrupting an external id would fork an identity.
- **Where Used**: `backend/src/contextedge/services/redaction_service.py:36-191`
- **Related Terms**: Redaction, Security

### Playbook
- **Term**: Playbook
- **Simple Definition**: An approved, step-by-step guide to solve a specific problem.
- **Detailed Explanation**: The ultimate output of the system. Playbooks contain trigger conditions, evidence links, and execution steps. They are strictly governed and versioned: only `lifecycle_state = 'approved'` playbooks with a published current version are visible to runtime matching or to the agent projection, and an expired one drops out automatically. Candidates are generated automatically from patterns (`pattern.generate_playbook_candidate`) — creating a pattern enqueues one, and growing a pattern's membership re-enqueues it — but a candidate is a draft, not a playbook. One deployment caveat: playbooks created before the embedding column landed have NULL embeddings until `evaluation.backfill_playbook_embeddings` is run once per environment.
- **Where Used**: `backend/src/contextedge/services/playbook_service.py`, `backend/src/contextedge/models/playbook.py`, `backend/src/contextedge/workers/pattern_tasks.py:446`
- **Related Terms**: Governance, Runtime, Pattern, Grounding

### Playbook Step
- **Term**: Playbook Step
- **Simple Definition**: A single action or check within a playbook.
- **Detailed Explanation**: Steps can be manual ("Ask user to reboot") or automated ("Run API script"). During execution, these are tracked as `ExecutionStepRun` records to ensure idempotency and accurate audit trails.
- **Where Used**: Playbook Editor UI, Playbook Schema
- **Related Terms**: Playbook, Execution Step Run

### Policy
- **Term**: Policy
- **Simple Definition**: A rule governing system behavior, access, or playbook execution.
- **Detailed Explanation**: `TenantPolicy` defines retention and risk caps. `ActionPolicy` (added in `0029`) dictates automation limits (e.g., `approval_required` vs `manual_only`), acting as the security gate before downstream agents can execute playbooks.
- **Where Used**: `backend/src/contextedge/search/risk_policy.py`, `models/policy.py`
- **Related Terms**: Governance, RoleBinding

### Primary Key
- **Term**: Primary Key
- **Simple Definition**: The unique identifier for a row in a database table.
- **Detailed Explanation**: Usually UUIDs in ContextEdge, primary keys ensure every Tenant, EvidenceItem, and Playbook can be uniquely referenced, updated, and linked via Foreign Keys throughout the system.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: Database, Foreign Key, UUID

### Profile
- **Term**: Profile
- **Simple Definition**: Settings and preferences for a specific user.
- **Detailed Explanation**: Contains user configurations, saved views, and role assignments, determining what domains and features the user experiences in the dashboard.
- **Where Used**: Frontend Application Shell
- **Related Terms**: User, RoleBinding

### Projection
- **Term**: Projection
- **Simple Definition**: A filtered view of complex data prepared for a specific consumer.
- **Detailed Explanation**: ContextEdge creates safe, temporal "projections" of the full Context Graph for downstream systems like MAF. This ensures agents only see approved, relevant relationships, hiding raw or unapproved data.
- **Where Used**: `docs/MAF_CONTEXT_GRAPH_INTEGRATION.md`
- **Related Terms**: MAF, Context Graph

### Pipeline Health
- **Term**: Pipeline Health
- **Simple Definition**: The one screen that tells an operator where the pipeline stopped.
- **Detailed Explanation**: `GET /api/v1/admin/pipeline-health` (rendered at `/admin/pipeline`) reports two things together. First, Redis queue depth per lane in pipeline order, plus the count of in-flight unacknowledged tasks — that second number matters because during a reconstruction tail thousands of debounced tasks can churn for hours while every queue length reads zero. Second, one SQL read counting the graph chain end to end (evidence → embedded → identities → …), where **the first zero in the sequence is the diagnosis**. Backlog alert threshold is 500. It never raises on broker failure; it returns empty depths. It exists because during one incident every per-task metric said "healthy" while correlation starved behind 8,000 normalizations and episodes stayed at zero.
- **Where Used**: `backend/src/contextedge/services/pipeline_health_service.py:43-110`, `backend/src/contextedge/api/v1/admin_cost.py:166`
- **Related Terms**: Queue, Worker, Prometheus

### Prometheus
- **Term**: Prometheus
- **Simple Definition**: A monitoring system for tracking application metrics.
- **Detailed Explanation**: The API exposes `/metrics` via `prometheus_fastapi_instrumentator`, which covers HTTP-level metrics — request counts, latencies, status codes. It does **not** carry LLM token usage or Celery queue depth: token spend lives in `llm.usage` operational events (surfaced at `GET /api/v1/admin/llm-usage`) and queue depth comes from `GET /api/v1/admin/pipeline-health`. Knowing which of the three to look at saves a lot of time.
- **Where Used**: `backend/src/contextedge/main.py:168`
- **Related Terms**: Monitoring, Metrics, Pipeline Health

### Prompt
- **Term**: Prompt
- **Simple Definition**: Instructions given to an AI model to tell it what to do.
- **Detailed Explanation**: ContextEdge stores a versioned registry of prompts (`ai/prompts/`) used by its extractors and generators. Tenant admins can configure A/B prompt variants via `settings.tenant_prompt_variants_json`.
- **Where Used**: `backend/src/contextedge/ai/prompts/`
- **Related Terms**: LLM, Generator, Extractor

### Provider
- **Term**: Provider
- **Simple Definition**: The company hosting the AI models (e.g., OpenAI, Google).
- **Detailed Explanation**: ContextEdge is model-agnostic via LiteLLM, allowing it to route prompts to different providers based on tenant configuration, API key availability, or cost limits.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: LiteLLM, LLM

### Pydantic
- **Term**: Pydantic
- **Simple Definition**: A Python library for data validation.
- **Detailed Explanation**: Used everywhere in FastAPI for request/response schemas (`schemas/`). It ensures incoming JSON data strictly matches expected formats before it reaches the service logic.
- **Where Used**: `backend/src/contextedge/schemas/`
- **Related Terms**: FastAPI, Schema

### pgvector
- **Term**: pgvector
- **Simple Definition**: A PostgreSQL extension that allows the database to store and search vectors.
- **Detailed Explanation**: Critical to Semantic Search. It allows ContextEdge to store 3072-dimensional LLM embeddings directly in Postgres alongside relational data, powering fast HNSW searches without needing a separate vector database.
- **Where Used**: Database initialization, Vector search queries
- **Related Terms**: Embedding, Vector Search, PostgreSQL

## Q

### Query
- **Term**: Query
- **Simple Definition**: A request for data from a database.
- **Detailed Explanation**: SQLAlchemy translates Python commands into SQL queries. The runtime API executes complex queries involving vector similarity, text matching, and graph traversal simultaneously.
- **Where Used**: `backend/src/contextedge/search/`, `backend/src/contextedge/graph/`
- **Related Terms**: SQL, Similarity Search

### Queue
- **Term**: Queue
- **Simple Definition**: A waiting line for background tasks to be processed.
- **Detailed Explanation**: There are **eight** queues: `default`, `sync`, `hydration`, `extraction`, `correlation`, `embedding`, `pattern`, `evaluation`. Separate lanes exist so a slow stage cannot starve a fast one. `correlation` and `embedding` are the two most commonly missed: they were split out of `extraction` after a measured incident where the graph chain and chunk embedding sat behind bulk normalization in FIFO — 193 evidence items ingested, zero episodes, and 1,879 chunks with only 15% embedded, all with no error anywhere. A worker fleet that does not list all eight fails this way silently. The routing table is matched in order, so an earlier specific key wins over a later wildcard.
- **Where Used**: `backend/src/contextedge/workers/celery_app.py:226-280`, `backend/dev.py:16`
- **Related Terms**: Celery, Worker, Pipeline Health

## R

### RAG
- **Term**: RAG
- **Simple Definition**: Retrieval-Augmented Generation; giving an AI facts to read before it answers.
- **Detailed Explanation**: ContextEdge operates as an advanced, governed RAG layer. It retrieves approved playbooks and context graphs to ground downstream agents, ensuring they act on verified evidence rather than hallucinating.
- **Where Used**: Runtime APIs, MAF integration
- **Related Terms**: Grounding, Runtime

### RBAC
- **Term**: RBAC
- **Simple Definition**: Role-Based Access Control; granting permissions based on a user's job.
- **Detailed Explanation**: ContextEdge enforces RBAC (roles like `domain_admin`, `knowledge_manager`) strictly. It dictates who can approve playbooks, access specific domains, or view sensitive evidence.
- **Where Used**: `backend/src/contextedge/api/v1/`, Token claims
- **Related Terms**: Authorization, Security

### Reactor
- **Term**: Reactor
- **Simple Definition**: A system component that responds automatically to events.
- **Detailed Explanation**: Celery tasks and internal signal handlers act as reactors in ContextEdge. For example, ingesting new evidence triggers the correlation engine automatically.
- **Where Used**: Worker pipeline
- **Related Terms**: Event, Worker

### Redaction
- **Term**: Redaction
- **Simple Definition**: Removing or hiding sensitive information.
- **Detailed Explanation**: Handled by `redaction_service.py`, ContextEdge masks PII, credit cards, and API keys via regex immediately upon ingestion, ensuring secrets never touch LLM APIs or vector storage.
- **Where Used**: `backend/src/contextedge/services/redaction_service.py`
- **Related Terms**: PII, Security

### Redis
- **Term**: Redis
- **Simple Definition**: A fast, in-memory data store.
- **Detailed Explanation**: Three separate Redis databases, on purpose: db 0 for the application cache, db 1 for the Celery broker, db 2 for the Celery result backend. It also caches runtime explain payloads under `runtime:match:{match_id}` with a one-hour TTL, which is why `GET /runtime/explain/{id}` 404s after an hour. Two practical consequences: a cache write failure is swallowed rather than failing the match, and **Celery task messages survive a Postgres rebuild** because they live in db 1 — clearing a poison message means dropping the Redis volume too.
- **Where Used**: `backend/src/contextedge/config.py:26-28`, `backend/src/contextedge/api/v1/runtime.py:29`
- **Related Terms**: Celery, Cache, Queue

### Repository
- **Term**: Repository
- **Simple Definition**: A design pattern that handles data storage logic.
- **Detailed Explanation**: While ContextEdge mostly uses service functions directly with SQLAlchemy sessions, the repository pattern concept is present in how database queries are isolated from business logic.
- **Where Used**: `backend/src/contextedge/database.py`
- **Related Terms**: ORM, Database

### Request Context
- **Term**: Request Context
- **Simple Definition**: Data about the current HTTP request, like the user ID or correlation ID.
- **Detailed Explanation**: Middleware captures trace IDs and user claims, attaching them to the request context. This ensures audit logs and Celery tasks downstream inherit the exact identity and tracking info of the initial API call.
- **Where Used**: `backend/src/contextedge/middleware/`
- **Related Terms**: Middleware, Audit Log

### Resolution Gate
- **Term**: Resolution Gate
- **Simple Definition**: An optional check that skips episode-building for ticket clusters where no solution has been mentioned yet.
- **Detailed Explanation**: With `EPISODE_RESOLUTION_GATE=cluster`, scheduled episode reconstruction defers any correlation cluster whose evidence carries no resolution signal (status vocabulary, resolution phrasing, or classifier summaries) — saving the pipeline's most expensive LLM lane for content that can actually yield reusable knowledge. Deferred clusters are re-checked automatically as new evidence joins, so a fix that arrives later from a different source still produces an episode. Off by default.
- **Where Used**: `backend/src/contextedge/services/resolution_signal_service.py`, `workers/extraction_tasks.py`
- **Related Terms**: Episode, Correlation, Cluster

### Resolution Session
- **Term**: Resolution Session
- **Simple Definition**: A tracked troubleshooting event where a playbook is being actively used.
- **Detailed Explanation**: When an agent or human uses a playbook, a Session is opened. It records state transitions and decision traces, linking outcomes (success/failure) directly back to the Context Graph.
- **Where Used**: `backend/src/contextedge/services/session_service.py`
- **Related Terms**: Session, Decision Trace

### Retention
- **Term**: Retention
- **Simple Definition**: Rules dictating how long data is kept before being deleted.
- **Detailed Explanation**: Two scheduled phases, not one. A **daily archive** sweep marks evidence past its window as `relevance_state = "archived"`, where the window depends on the item's memory class: `short_term` gets the base window (365 days by default), `long_term` gets `max(base × 6, 180)`, `reasoning` gets `max(base × 3, 90)`. A **weekly purge** then acts on archived rows older than a 30-day grace period, in `RETENTION_PURGE_MODE`, whose default is **`soft_purge`** — NULLing text, summary, embedding, entity refs and the blob pointer and retitling to `[purged]`, plus explicitly deleting the row's chunks (which carry the same content, and whose cascade does not fire while the parent survives). `hard_delete` is the opt-in mode. Legal hold is excluded in the SQL WHERE clause of both phases, never post-filtered. A third daily sweep reaps orphaned MinIO blobs and graph edges left behind by hard deletes.
- **Where Used**: `backend/src/contextedge/services/retention_service.py:66-258`, `backend/src/contextedge/services/memory_service.py:64-79`, `backend/src/contextedge/workers/retention_tasks.py:72,104`
- **Related Terms**: Compliance, Beat Scheduler, Memory

### Recurrence
- **Term**: Recurrence
- **Simple Definition**: A pointer saying "this looks like the same *kind* of problem we saw before" — never "this is the same incident".
- **Detailed Explanation**: When an approved episode's issue signature already exists, the system finds the most recent previous episode under that signature, finds that episode's primary case, and gives the new episode's first evidence item an `evidence_case_memberships` row of type `recurrence` at confidence 0.6. **The load-bearing invariant:** the episode cluster resolver explicitly refuses to expand through `recurrence` memberships. If it did, six months of separate VPN certificate expiries would collapse into one incomprehensible episode. Recurrence exists for precedent retrieval, not for merging. The same principle constrains the dedup sweep: two semantically near-identical episodes with *disjoint* evidence are refused as a merge for exactly this reason.
- **Where Used**: `backend/src/contextedge/services/issue_signature_service.py:36,249-312`, `backend/src/contextedge/services/episode_cluster_service.py:158-193`
- **Related Terms**: Issue Signature, Episode, Correlation

### Review Queue
- **Term**: Review Queue
- **Simple Definition**: A list of items waiting for human approval.
- **Detailed Explanation**: Candidate Playbooks, newly identified Patterns, and system Drift alerts flow into the review queue. Knowledge Managers work through this queue to govern what becomes active Operational Memory.
- **Where Used**: Frontend Dashboard, `playbook_service.py`
- **Related Terms**: Human-in-the-Loop, Governance

### RRF
- **Term**: RRF
- **Simple Definition**: Reciprocal Rank Fusion, a method for combining different search results.
- **Detailed Explanation**: Used inside the `hybrid_ranker.py`. RRF mathematically combines the scoring from Full-Text Search and Vector Search into a single unified ranking list to surface the most relevant playbooks.
- **Where Used**: `backend/src/contextedge/search/hybrid_ranker.py`
- **Related Terms**: Hybrid Search, FTS, Vector Search

### Router
- **Term**: Router
- **Simple Definition**: Code that maps a URL request to the correct function.
- **Detailed Explanation**: FastAPI routers defined in `api/v1/` handle routing HTTP requests (like `POST /match`) to the appropriate Service functions, applying authentication dependencies along the way.
- **Where Used**: `backend/src/contextedge/api/v1/`
- **Related Terms**: FastAPI, API, Endpoint

### Runtime
- **Term**: Runtime
- **Simple Definition**: The live execution environment where agents and responders query data.
- **Detailed Explanation**: The Runtime APIs (`/api/v1/runtime/...`) only serve approved, published playbooks, and enforce a risk-tier cap derived from the caller's roles: admins are uncapped, `knowledge_manager` and service accounts cap at `high`, everyone else at `medium`. `POST /runtime/match` assembles a memory context, ranks playbooks, records a retrieval trace event when given a session id, writes a `runtime.match_completed` operational event, and caches the full explain payload in Redis for an hour so `GET /runtime/explain/{match_id}` can serve it back.
- **Where Used**: `backend/src/contextedge/api/v1/runtime.py:42-52,89-267`
- **Related Terms**: Playbook, Agent, Hybrid Search

## S

### Schema
- **Term**: Schema
- **Simple Definition**: The defined structure of data.
- **Detailed Explanation**: ContextEdge uses Pydantic schemas to validate incoming JSON requests, and SQLAlchemy schemas to define database tables. It also forces LLMs to output data matching strict JSON schemas for reliability.
- **Where Used**: `backend/src/contextedge/schemas/`
- **Related Terms**: Pydantic, Model

### Seed
- **Term**: Seed
- **Simple Definition**: Initial data loaded into the database to get started.
- **Detailed Explanation**: Developers use `make seed` to populate the local development environment with dummy tenants, users, and graph data to test the UI and API functionality immediately.
- **Where Used**: `make seed`
- **Related Terms**: Database, Deployment

### Selector
- **Term**: Selector
- **Simple Definition**: Logic used to pick specific items out of a larger group.
- **Detailed Explanation**: Selectors define trigger conditions in Playbooks. When runtime data matches a Playbook's selector logic (e.g., specific error codes or environments), that playbook is recommended.
- **Where Used**: Playbook schema
- **Related Terms**: Playbook

### Service
- **Term**: Service
- **Simple Definition**: A block of code that handles specific business rules.
- **Detailed Explanation**: The core logic layer in ContextEdge. Services (like `playbook_service.py`) orchestrate database updates, validation, and graph changes. They are called by both API Routers and Celery Workers.
- **Where Used**: `backend/src/contextedge/services/`
- **Related Terms**: Business Logic, Layer

### Service Token
- **Term**: Service Token
- **Simple Definition**: An API key used by machines to talk to the system.
- **Detailed Explanation**: Instead of user JWTs, downstream integrations (like MAF) use `X-Service-Token` headers. These tokens can be scoped to specific domains to restrict automated access at runtime.
- **Where Used**: `backend/src/contextedge/security_tokens.py`, Runtime APIs
- **Related Terms**: Authentication, Agent

### ServiceNow
- **Term**: ServiceNow
- **Simple Definition**: An enterprise IT service management platform.
- **Detailed Explanation**: Similar to Jira, it is an external Source. The Connector ingests tickets and change records, turning them into EvidenceItems to build operational context.
- **Where Used**: Connectors
- **Related Terms**: Jira, Source, Connector

### Session
- **Term**: Session
- **Simple Definition**: A tracked period of interaction or playbook execution.
- **Detailed Explanation**: See 'Resolution Session'. It groups all trace events, approvals, and outcomes together into a single graph node, proving an audit trail of an incident response.
- **Where Used**: `backend/src/contextedge/services/session_service.py`
- **Related Terms**: Resolution Session

### Similarity Search
- **Term**: Similarity Search
- **Simple Definition**: Finding documents that mean the same thing, not just share the same words.
- **Detailed Explanation**: Driven by `pgvector`, it allows ContextEdge to match a user saying "login broke" to a playbook titled "SSO Auth Failure" by comparing their semantic vector embeddings.
- **Where Used**: `backend/src/contextedge/search/vector_search.py`
- **Related Terms**: Vector Search, Embedding, pgvector

### Source Facets
- **Term**: Source Facets
- **Simple Definition**: Structured fields pulled out of a source's own custom fields — root cause, component, environment, version, customer, region, ticket type.
- **Detailed Explanation**: Derived at ingest by mapping the source's configured `facet_fields` onto a fixed set of keys, reading from `cf` / `custom_fields` / the top level. Placeholder values (`"NA"`, `"None"`, `"-"`) are discarded and values are capped at 120 characters. A source with no facet mapping produces `{}`. Facets matter downstream in two places: they let knowledge applicability be answered for free when the source already stated environment and version, and they are re-merged on a re-ingest, because a root cause is typed at resolve time rather than at creation.
- **Where Used**: `backend/src/contextedge/services/source_facets.py:38-109`
- **Related Terms**: Applicability, Knowledge State, Evidence

### Source
- **Term**: Source
- **Simple Definition**: An external system providing data to ContextEdge.
- **Detailed Explanation**: Sources (ServiceNow, Jira Service Management, Zoho Desk, Teams, Gmail, ManageEngine, SapphireIMS) are configured in the Source Registry, with credentials Fernet-encrypted at rest. Discovery enumerates the readable objects inside a source — a ServiceNow table, a Zoho module, a mailbox — as `SourceObject` rows, and tenant admins govern which of those are `approved_for_backfill` and `approved_for_sync`. Only approved objects are picked up by the 15-minute scheduled sync.
- **Where Used**: `backend/src/contextedge/api/v1/sources.py`, `backend/src/contextedge/models/source.py:11-125`
- **Related Terms**: Connector, Ingestion, Sync

### SQL
- **Term**: SQL
- **Simple Definition**: Structured Query Language, used to talk to databases.
- **Detailed Explanation**: While SQLAlchemy abstracts most of it, raw SQL (and Postgres-specific extensions) are used under the hood for recursive graph traversals and advanced vector search optimization.
- **Where Used**: `backend/src/contextedge/database.py`
- **Related Terms**: PostgreSQL, Query

### SQLAlchemy
- **Term**: SQLAlchemy
- **Simple Definition**: A Python tool for interacting with SQL databases.
- **Detailed Explanation**: The ORM framework used by ContextEdge. Version 2.0 is used with async support to manage all database queries, models, and relationships safely.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: ORM, AsyncPG

### Structlog
- **Term**: Structlog
- **Simple Definition**: A library for writing clear, structured log messages.
- **Detailed Explanation**: Replaces standard Python logging. It outputs JSON-formatted logs containing correlation IDs and tenant contexts, making it easy to search and debug errors in production log aggregators.
- **Where Used**: Application-wide logging
- **Related Terms**: Audit Log, Observability

### Sync
- **Term**: Sync
- **Simple Definition**: The process of copying data from a Source to ContextEdge.
- **Detailed Explanation**: Sync runs are either historical **backfills** (default window 90 days, requires `approved_for_backfill`) or **incremental** steady-state updates (requires `approved_for_sync`, dispatched by beat every 15 minutes). An append-only `SyncCheckpoint` tracks position; the newest row by `captured_at` wins. Three safety behaviours worth knowing: an incremental run against an object with **no checkpoint** completes as `skipped_no_checkpoint` rather than silently becoming a first full pull; a second worker on the same object returns `skipped_locked` because of the advisory lock; and pause/cancel are **cooperative** — the connector polls a control column on a fresh connection every page and every 25 detail records, and both stops persist everything already fetched along with a checkpoint, so a cancel is not a rollback.
- **Where Used**: `backend/src/contextedge/services/sync_worker_service.py:419-637`, `backend/src/contextedge/services/sync_control_service.py`, `backend/src/contextedge/api/v1/sync.py`
- **Related Terms**: Connector, Ingestion, Advisory Lock

### Swagger
- **Term**: Swagger
- **Simple Definition**: An interactive webpage for testing APIs.
- **Detailed Explanation**: Hosted at `/docs` locally, it is generated automatically by FastAPI. Developers use it to see all available endpoints, required JSON structures, and to manually test API calls.
- **Where Used**: `http://localhost:8000/docs`
- **Related Terms**: OpenAPI, FastAPI

## T

### TanStack Query
- **Term**: TanStack Query
- **Simple Definition**: A tool for managing data fetching in the frontend.
- **Detailed Explanation**: Used by the Next.js React frontend to call the FastAPI backend. It handles caching, retries, and loading states automatically, ensuring the dashboard UI is snappy and robust.
- **Where Used**: `frontend/src/lib/api.ts`, React components
- **Related Terms**: UI, API

### Tenant
- **Term**: Tenant
- **Simple Definition**: A distinct customer or organization using the platform.
- **Detailed Explanation**: The highest level of multi-tenant isolation. Every user, evidence item, and playbook belongs to a specific tenant. Cross-tenant data mixing is strictly prevented at the database and API levels.
- **Where Used**: `backend/src/contextedge/models/tenant.py`, Middleware
- **Related Terms**: Multi-Tenancy, Workspace

### Temporal
- **Term**: Temporal
- **Simple Definition**: Relating to time.
- **Detailed Explanation**: Context Graph edges carry `valid_from` and `valid_to` timestamps, so the system can express that a fix was valid last year and is now expired. Queries pass an `as_of`: with none, the predicate is simply `valid_to IS NULL` (current state); with one, it is `valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)`. Naive datetimes are rejected (422), as is anything more than five minutes in the future. **The caveat the projection states out loud in a warning:** historical *edges* are combined with *current* node facts, so a point-in-time projection is not a full time machine and callers must not draw historical operational conclusions from it.
- **Where Used**: `backend/src/contextedge/graph/temporal.py:12-36`, `backend/src/contextedge/models/pattern.py:174-273`
- **Related Terms**: Graph Edge, History

### Thread
- **Term**: Thread
- **Simple Definition**: A connected series of messages or comments.
- **Detailed Explanation**: A `threads` row groups related messages and tracks `hydration_status`, `message_count`, `participant_count`, and first/last message times. Threads are created lazily by normalization, which is why hydrating a thread before its parent has been normalized returns a 404. During hydration, cross-message quote stripping removes text already seen earlier in the same thread — that pass happens here rather than per message because only hydration holds the whole thread in arrival order; measured, 89% of substantive text was repetition.
- **Where Used**: `backend/src/contextedge/models/evidence.py:223-246`, `backend/src/contextedge/services/thread_text_service.py`
- **Related Terms**: Hydration, Evidence, Noise Gate

### Token
- **Term**: Token
- **Simple Definition**: A piece of data representing permission or identity.
- **Detailed Explanation**: See JWT and Service Token. Tokens are passed in HTTP headers to authenticate human and machine users for API access.
- **Where Used**: `backend/src/contextedge/security_tokens.py`
- **Related Terms**: JWT, Service Token

### Tool
- **Term**: Tool
- **Simple Definition**: A capability given to an AI agent to let it take action.
- **Detailed Explanation**: In the MAF integration, ContextEdge exposes an "on-demand MAF tool" that allows upstream agents to query the operational memory graph dynamically.
- **Where Used**: MAF Integration
- **Related Terms**: Agent, MAF

### Trace Event
- **Term**: Trace Event
- **Simple Definition**: A log of a specific action taken during a session.
- **Detailed Explanation**: Appended to Resolution Sessions, Trace Events record exactly what a human or automation did (e.g., "approved action", "executed script"), forming the unbreakable audit trail for governed execution.
- **Where Used**: `backend/src/contextedge/models/session.py`
- **Related Terms**: Resolution Session, Decision Trace

### tsvector
- **Term**: tsvector
- **Simple Definition**: A PostgreSQL data type optimized for text search.
- **Detailed Explanation**: ContextEdge stores normalized evidence text in `tsvector` columns. This strips out stop words and stems words, allowing the Hybrid Ranker's FTS engine to match searches quickly using GIN indexes.
- **Where Used**: PostgreSQL migrations, Models
- **Related Terms**: FTS, GIN Index

## U

### Unique Constraint
- **Term**: Unique Constraint
- **Simple Definition**: A database rule ensuring no two rows have the same value.
- **Detailed Explanation**: ContextEdge uses constraints (e.g., `(tenant_id, content_hash)`) to prevent duplicate evidence ingestion, and on Playbook Semantic Versions to prevent race conditions during version allocation.
- **Where Used**: `backend/alembic/versions/`
- **Related Terms**: Deduplication, Database

### UUID
- **Term**: UUID
- **Simple Definition**: A universally unique identifier.
- **Detailed Explanation**: ContextEdge uses UUIDv4 for all primary keys in the database. This obscures database size, prevents ID guessing, and allows distributed systems to generate IDs without collisions.
- **Where Used**: `backend/src/contextedge/models/`
- **Related Terms**: Primary Key

## V

### Vector
- **Term**: Vector
- **Simple Definition**: A list of numbers representing data.
- **Detailed Explanation**: See Embedding. The models turn text into 3072-dimensional vectors, stored in `Vector(3072)` columns and indexed as `halfvec(3072)`. Vectors that point in similar directions represent text with similar meanings.
- **Where Used**: `backend/src/contextedge/search/`
- **Related Terms**: Embedding, Vector Search, HalfVec

### Vector Search
- **Term**: Vector Search
- **Simple Definition**: Searching a database by meaning rather than exact keywords.
- **Detailed Explanation**: Powered by `pgvector`. Evidence search is **chunk-aware in two passes**: an oversampled ANN query over `evidence_chunks` (oversample between 80 and 240), MMR diversification, rollup to the single closest chunk per parent — then a second ANN pass over the parents' own embeddings, merged and re-sorted, so evidence that was never chunked still surfaces. Both passes apply the same visibility gates: no legal hold, no pending redaction, no excluded access policy. Results are `(EvidenceItem, distance, best_chunk_or_None)`, where `best_chunk` carries the section breadcrumb and a 240-character snippet for rendering.
- **Where Used**: `backend/src/contextedge/search/vector_search.py:40-243`, `backend/src/contextedge/search/chunk_rollup.py`
- **Related Terms**: Similarity Search, pgvector, Hybrid Search, MMR

### Versioning
- **Term**: Versioning
- **Simple Definition**: Keeping track of different drafts and updates of a document.
- **Detailed Explanation**: Playbooks have strict semantic versioning. The runtime API *only* serves published versions. Draft updates are tracked safely in `PlaybookVersion` without breaking live integrations.
- **Where Used**: `backend/src/contextedge/services/playbook_service.py`
- **Related Terms**: Playbook, Governance

### Vertex AI
- **Term**: Vertex AI
- **Simple Definition**: Google's enterprise AI platform.
- **Detailed Explanation**: One of the LLM Providers ContextEdge supports via LiteLLM for text generation and vector embeddings, configurable by the Tenant Admin based on corporate data agreements.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: Provider, LLM

### Vitest
- **Term**: Vitest
- **Simple Definition**: A fast testing framework for frontend JavaScript code.
- **Detailed Explanation**: `npm test` runs `vitest run` against the frontend unit tests — currently covering role predicates, the graph API client, graph query controls, and the applicability, playbook-step, and thread-conversation components. Older documentation described this script as a placeholder that only printed a skip message; that is no longer true.
- **Where Used**: `frontend/package.json`, `npm test`
- **Related Terms**: UI

## W

### Worker
- **Term**: Worker
- **Simple Definition**: A background process that does heavy lifting.
- **Detailed Explanation**: Celery workers run outside the API web server to handle long-running tasks like fetching tickets, generating LLM summaries, and running drift evaluations, keeping the web UI responsive. Two things shape how they are deployed here. First, every task body runs through `run_async`, which builds a **fresh NullPool engine and session per task** and owns the commit/rollback contract — services called from workers flush, they never commit. Second, on Windows the prefork pool does not work and the threads pool breaks LLM calls (litellm holds asyncio locks bound to their creating loop), so parallelism comes from running several `-P solo` **processes**. Workers also refuse to start when the database is behind the code's Alembic head.
- **Where Used**: `backend/src/contextedge/workers/asyncio_runner.py:10-34`, `backend/src/contextedge/workers/celery_app.py:83-139`, `docs/RUNBOOK.md` "Worker topology"
- **Related Terms**: Celery, Queue, Migration

### Workspace
- **Term**: Workspace
- **Simple Definition**: A sub-division within a Tenant.
- **Detailed Explanation**: Used to separate data between different business units or geographic regions within the same organization. It acts as an optional secondary security boundary beneath the Tenant level.
- **Where Used**: `backend/src/contextedge/models/tenant.py`
- **Related Terms**: Tenant, Domain
