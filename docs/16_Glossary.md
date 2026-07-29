# ContextEdge — Glossary

Welcome to the ContextEdge Glossary. This document defines the core concepts, technologies, and terms used throughout the ContextEdge platform.

## A

### Adjacency List
- **Term**: Adjacency List
- **Simple Definition**: A way to store graph data in a database where each row represents a connection between two nodes.
- **Detailed Explanation**: In ContextEdge, the adjacency list is used in PostgreSQL to represent relationships (edges) between operational entities (like tickets, users, and decisions). It allows for efficient graph traversals (like BFS) using recursive SQL queries, avoiding the need for a dedicated graph database.
- **Where Used**: `backend/src/contextedge/models/graph.py` and `backend/src/contextedge/graph/`
- **Related Terms**: Graph Edge, Node, Graph

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
- **Detailed Explanation**: ContextEdge logs all admin, reviewer, retrieval, and policy actions to maintain compliance. It tracks who accessed what evidence and why, which is crucial for proving control and retention in enterprise deployments.
- **Where Used**: `backend/src/contextedge/middleware/`, `backend/src/contextedge/models/audit.py`
- **Related Terms**: Compliance, Trace Event

### Authentication
- **Term**: Authentication
- **Simple Definition**: The process of verifying who a user or system is.
- **Detailed Explanation**: ContextEdge supports enterprise SSO, SAML, and OIDC for human users, translating them into JWTs. For machine access, it uses Service Tokens. Authentication is enforced at the API layer to block unverified requests.
- **Where Used**: `backend/src/contextedge/deps.py`, `backend/src/contextedge/security_tokens.py`
- **Related Terms**: Authorization, JWT, Bearer Token

### Authorization
- **Term**: Authorization
- **Simple Definition**: The process of checking what a verified user or system is allowed to do.
- **Detailed Explanation**: ContextEdge uses Role-Based Access Control (RBAC) scoped to tenants, workspaces, and domains. Authorization ensures users can only view evidence, approve playbooks, or configure sources they explicitly have permission for.
- **Where Used**: `backend/src/contextedge/api/v1/`, `backend/src/contextedge/search/risk_policy.py`
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
- **Detailed Explanation**: Celery Beat acts as the cron system for ContextEdge. It triggers periodic background tasks, such as cleanup routines (`cleanup_tasks.py`), drift checks, and periodic syncs for connectors that pull data.
- **Where Used**: `backend/src/contextedge/workers/cleanup_tasks.py`, `make celery-beat-dev`
- **Related Terms**: Celery, Celery Beat, Worker

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
- **Detailed Explanation**: To enable high-recall searches over long documents (like 40KB post-mortems), ContextEdge breaks them into smaller chunks. Each chunk gets its own vector embedding, allowing the system to locate specific details inside massive threads.
- **Where Used**: `backend/src/contextedge/services/evidence_chunk_service.py`, `backend/src/contextedge/models/evidence.py`
- **Related Terms**: Chunking, Evidence Chunk

### Chunking
- **Term**: Chunking
- **Simple Definition**: The process of dividing large texts into smaller pieces (chunks).
- **Detailed Explanation**: This pipeline runs inline or asynchronously via `chunk_evidence_task`. Source-specific chunkers split text intelligently (e.g., by Jira description, Teams message), and the resulting chunks are embedded in batches of 32 for vector search.
- **Where Used**: `backend/src/contextedge/services/chunkers/`
- **Related Terms**: Chunk, Embedding

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

### Classifier
- **Term**: Classifier
- **Simple Definition**: An AI component that categorizes data into groups.
- **Detailed Explanation**: ContextEdge uses LLM-powered classifiers during ingestion to perform lightweight first-pass labeling, deciding if an incoming ticket or message is operationally relevant before doing expensive deep extraction.
- **Where Used**: `backend/src/contextedge/ai/`
- **Related Terms**: LLM, Relevance

### CORS
- **Term**: CORS
- **Simple Definition**: Cross-Origin Resource Sharing, a security feature for web browsers.
- **Detailed Explanation**: Configured in the FastAPI bootstrap, CORS dictates which web domains are allowed to call the ContextEdge API. It prevents malicious websites from making requests on behalf of an authenticated user.
- **Where Used**: `backend/src/contextedge/main.py`, `backend/src/contextedge/config.py`
- **Related Terms**: API, Security

### Connector
- **Term**: Connector
- **Simple Definition**: An adapter that links ContextEdge to an external data source.
- **Detailed Explanation**: Connectors follow a standard contract to fetch data from Jira, Slack, Teams, etc. They handle discovery, backfill, and incremental sync, normalizing the external API responses into a standard internal format.
- **Where Used**: `backend/src/contextedge/connectors/`
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
- **Detailed Explanation**: The correlation engine links related artifacts across systems (e.g., a Jira ticket and a Slack thread). These links, marked with confidence scores, form the basis for reconstructing a complete incident "Episode".
- **Where Used**: `backend/src/contextedge/services/correlation_service.py`
- **Related Terms**: Episode, Graph Edge

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
- **Detailed Explanation**: Implemented via application-layer hash checks and database constraints (partial unique index on `content_hash`). It prevents identical ticket updates or emails from cluttering the context graph, catching `IntegrityError` to handle concurrent inserts safely.
- **Where Used**: `backend/src/contextedge/services/evidence_normalization.py`, Migration `0026`
- **Related Terms**: Normalization, Ingestion

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
- **Detailed Explanation**: In ContextEdge, edges represent relationships (e.g., "based_on", "records_decision") between entities, evidence, and playbooks. They are the backbone of the Context Graph, stored in PostgreSQL.
- **Where Used**: `backend/src/contextedge/models/graph.py`
- **Related Terms**: Graph Edge, Node, Graph

### Embedding
- **Term**: Embedding
- **Simple Definition**: A list of numbers representing the meaning of a piece of text.
- **Detailed Explanation**: Text from evidence is converted into 3072-dimensional vector embeddings via LLM APIs. These embeddings are stored in PostgreSQL using `pgvector`, allowing semantic search to find conceptually similar problems even without exact keyword matches.
- **Where Used**: `backend/src/contextedge/ai/`, `backend/src/contextedge/search/vector_search.py`
- **Related Terms**: Vector Search, pgvector, Chunking

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
- **Detailed Explanation**: The system takes fragmented evidence (tickets, chats) and builds a structured timeline: trigger -> hypothesis -> steps -> outcome. Episodes are the intermediate step between raw evidence and durable patterns.
- **Where Used**: `backend/src/contextedge/services/episode_service.py`, `backend/src/contextedge/models/episode.py`
- **Related Terms**: Correlation, Pattern

### Evaluation
- **Term**: Evaluation
- **Simple Definition**: Testing the system to ensure it gives the right answers.
- **Detailed Explanation**: ContextEdge runs offline evaluations (Replay jobs) against historical "ground truth" datasets. It scores playbook match rates, extraction accuracy, and contradiction handling to catch regressions before they hit production.
- **Where Used**: `backend/src/contextedge/services/evaluation_service.py`, `backend/evals/`
- **Related Terms**: Drift, Grounding

### Event
- **Term**: Event
- **Simple Definition**: Something that happened in an external system or internally.
- **Detailed Explanation**: Ingestion events represent raw incoming data, while Trace Events record internal execution steps. Events are immutable, append-only records that drive asynchronous workflows.
- **Where Used**: `backend/src/contextedge/models/event.py`
- **Related Terms**: Trace Event, Audit Log

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
- **Detailed Explanation**: ContextEdge stores `tsvector` columns in Postgres with GIN indexes to support fast keyword searches. The Hybrid Ranker combines this FTS score with Vector Search to find the best playbook matches.
- **Where Used**: `backend/src/contextedge/search/hybrid_ranker.py`
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
- **Detailed Explanation**: The `GraphEdge` table stores relationships with types like `approved_by` or `records_decision`. Migration `0029` adds temporal validity (`valid_from`/`valid_to`), allowing the graph to represent knowledge that changes over time.
- **Where Used**: `backend/src/contextedge/models/graph.py`
- **Related Terms**: Edge, Context Graph

### Graph Edge Meta
- **Term**: Graph Edge Meta
- **Simple Definition**: Extra information attached to a graph connection.
- **Detailed Explanation**: JSON metadata stored on a GraphEdge, carrying specifics like confidence scores, risk levels, or tool references, allowing the graph traversals to be weighted and context-aware.
- **Where Used**: `backend/src/contextedge/models/graph.py`
- **Related Terms**: Graph Edge

### Grounding
- **Term**: Grounding
- **Simple Definition**: Ensuring AI answers are based strictly on real facts.
- **Detailed Explanation**: ContextEdge enforces grounding by linking every AI-generated claim, playbook step, or pattern directly back to the original source evidence. This prevents LLM hallucinations.
- **Where Used**: UI tracing, Playbook schema
- **Related Terms**: Evidence, Claim Evidence

## H

### HalfVec
- **Term**: HalfVec
- **Simple Definition**: A memory-efficient way to store vector embeddings.
- **Detailed Explanation**: Used by pgvector, it stores vector coordinates using 16-bit floats instead of 32-bit, saving storage space and improving vector search performance without significant loss of semantic accuracy.
- **Where Used**: PostgreSQL pgvector configuration
- **Related Terms**: Vector, Embedding, pgvector

### HNSW
- **Term**: HNSW
- **Simple Definition**: A fast search algorithm for finding similar vectors.
- **Detailed Explanation**: Hierarchical Navigable Small World (HNSW) indexes are created in Postgres (via `pgvector`) on embedding columns. This makes finding semantically similar evidence or playbooks incredibly fast at runtime.
- **Where Used**: Database migrations (e.g., `0021`), Vector search
- **Related Terms**: pgvector, Vector Search, Similarity Search

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
- **Detailed Explanation**: ContextEdge's `hybrid_ranker.py` blends Full-Text Search (keyword match), Vector Search (semantic match), and Graph signals (recency, success rate) to rank and return the most accurate playbooks at runtime.
- **Where Used**: `backend/src/contextedge/search/hybrid_ranker.py`
- **Related Terms**: FTS, Vector Search

### Hydration
- **Term**: Hydration
- **Simple Definition**: Filling in missing details to complete a dataset.
- **Detailed Explanation**: "Thread hydration" happens when ContextEdge detects an interesting single message and reaches back into the source (e.g., Slack) to pull the surrounding conversation, building complete context without crawling everything.
- **Where Used**: Connectors, Ingestion pipeline
- **Related Terms**: Thread, Ingestion

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
- **Detailed Explanation**: Canonical identities map disparate text references (e.g., "jsmith", "John S.") to a single record. This allows the graph to track recurring issues accurately across different tools.
- **Where Used**: `backend/src/contextedge/services/identity_service.py`
- **Related Terms**: Identity Resolution, Canonical Identity

### Identity Resolution
- **Term**: Identity Resolution
- **Simple Definition**: The process of linking different names to the same identity.
- **Detailed Explanation**: Runs during evidence normalization. It uses aliases, product synonyms, and mapping dictionaries to map incoming text to canonical entities in the Context Graph.
- **Where Used**: `backend/src/contextedge/services/identity_service.py`
- **Related Terms**: Identity

### Idempotency Key
- **Term**: Idempotency Key
- **Simple Definition**: A unique code ensuring an action only happens once, even if requested twice.
- **Detailed Explanation**: Added to `ExecutionStepRun` in migration `0029`, it provides banking-grade duplicate prevention. This guarantees that automated playbook steps (like rebooting a server) are never executed multiple times accidentally by network retries.
- **Where Used**: `backend/src/contextedge/models/execution.py`
- **Related Terms**: Execution Step Run

### Index
- **Term**: Index
- **Simple Definition**: A database structure that makes searching for data much faster.
- **Detailed Explanation**: ContextEdge uses GIN indexes for text search, HNSW indexes for vector search, and standard B-Tree indexes for foreign keys and unique constraints (like `tenant_id` scopes) to ensure rapid API responses.
- **Where Used**: `backend/alembic/versions/`
- **Related Terms**: GIN Index, HNSW, PostgreSQL

### Ingestion
- **Term**: Ingestion
- **Simple Definition**: The process of bringing external data into the system.
- **Detailed Explanation**: Managed by Connectors and the Sync Worker Service. Data is pulled from Jira, Slack, etc., saved as raw events, and placed in a queue for normalization. It utilizes a "claim-before-queue" recovery pattern for reliability.
- **Where Used**: `backend/src/contextedge/connectors/`, `backend/src/contextedge/services/sync_worker_service.py`
- **Related Terms**: Connector, Sync, Normalization

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
- **Detailed Explanation**: One of the primary external Sources for ContextEdge. The Jira connector pulls tickets and comments, mapping them into EvidenceItems and correlating them with chat threads to build complete Episodes.
- **Where Used**: `backend/src/contextedge/connectors/jira/`
- **Related Terms**: Source, Connector

## K

### Knowledge Transfer
- **Term**: Knowledge Transfer
- **Simple Definition**: Moving information from one place or person to another.
- **Detailed Explanation**: The ultimate goal of ContextEdge. It transfers operational tribal knowledge buried in Slack and Jira into durable, versioned, machine-readable playbooks that can teach new analysts or guide automated agents.
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

### LOEUF
- **Term**: LOEUF
- **Simple Definition**: A genetic constraint metric.
- **Detailed Explanation**: (Domain-specific term, normally associated with genetic science, potentially caught in general taxonomy). In the context of software operations, ignore; if referenced, it highlights statistical boundaries.
- **Where Used**: Science plugins (N/A for core platform)
- **Related Terms**: None

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
- **Detailed Explanation**: Managed by Alembic. As new features (like AE Ops alignment in `0029`) are added, migrations safely add tables, columns, and indexes to the PostgreSQL database without destroying existing data.
- **Where Used**: `backend/alembic/versions/`, `docs/MIGRATIONS.md`
- **Related Terms**: Alembic, Database

### MinIO
- **Term**: MinIO
- **Simple Definition**: A storage system for large files, compatible with Amazon S3.
- **Detailed Explanation**: Used as the Object Store to offload large raw evidence payloads, logs, and attachments so they don't bloat the relational PostgreSQL database, keeping DB queries fast.
- **Where Used**: `backend/src/contextedge/services/object_store.py`
- **Related Terms**: Object Store, Evidence

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
- **Detailed Explanation**: The Celery Normalization worker takes raw data from Jira, Slack, or email and converts it into standard `EvidenceItem` records, deduplicating them and applying redaction rules before any LLM processing.
- **Where Used**: `backend/src/contextedge/services/evidence_normalization.py`
- **Related Terms**: Evidence, Deduplication, Redaction

### Notification
- **Term**: Notification
- **Simple Definition**: An alert sent to a user about an important event.
- **Detailed Explanation**: Managed by the notification service, users receive alerts in the UI for sync failures, playbook drift, and pending approval queues, ensuring governance bottlenecks are cleared quickly.
- **Where Used**: `backend/src/contextedge/services/notification_service.py`
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
- **Related Terms**: MinIO, Evidence

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
- **Detailed Explanation**: The pattern engine clusters repeated Episodes (e.g., "7 instances of VPN failure after Windows update"). These patterns justify the creation of a formalized Playbook candidate for human review.
- **Where Used**: `backend/src/contextedge/services/pattern_service.py`
- **Related Terms**: Episode, Playbook

### PII
- **Term**: PII
- **Simple Definition**: Personally Identifiable Information (like names, SSNs).
- **Detailed Explanation**: ContextEdge runs Regex PII redaction (masking SSNs, credit cards, API keys) at ingest time. This critical security step runs *before* data is sent to external LLMs or embedded, preventing data leaks.
- **Where Used**: `backend/src/contextedge/services/redaction_service.py`
- **Related Terms**: Redaction, Security

### Playbook
- **Term**: Playbook
- **Simple Definition**: An approved, step-by-step guide to solve a specific problem.
- **Detailed Explanation**: The ultimate output of the system. Playbooks contain trigger conditions, evidence links, and execution steps. They are strictly governed, versioned, and require human approval before becoming available in runtime search.
- **Where Used**: `backend/src/contextedge/services/playbook_service.py`, `backend/src/contextedge/models/playbook.py`
- **Related Terms**: Governance, Runtime

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

### Prometheus
- **Term**: Prometheus
- **Simple Definition**: A monitoring system for tracking application metrics.
- **Detailed Explanation**: ContextEdge exposes Prometheus metrics endpoints to monitor sync success rates, API latencies, LLM token usage, and Celery queue depths, giving platform admins visibility into system health.
- **Where Used**: `backend/src/contextedge/main.py`
- **Related Terms**: Monitoring, Metrics

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
- **Detailed Explanation**: Celery manages multiple queues (default, sync, hydration, extraction, pattern, evaluation) to ensure heavy tasks like LLM generation don't block lightweight tasks like database cleanup.
- **Where Used**: `backend/src/contextedge/workers/`
- **Related Terms**: Celery, Worker

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
- **Detailed Explanation**: Serves two main purposes in ContextEdge: it is the message broker/result backend for Celery queues, and it acts as a short-lived cache for runtime explanation payloads to keep API responses blazing fast.
- **Where Used**: Cache infrastructure, Celery broker
- **Related Terms**: Celery, Cache

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

### Resolution Session
- **Term**: Resolution Session
- **Simple Definition**: A tracked troubleshooting event where a playbook is being actively used.
- **Detailed Explanation**: When an agent or human uses a playbook, a Session is opened. It records state transitions and decision traces, linking outcomes (success/failure) directly back to the Context Graph.
- **Where Used**: `backend/src/contextedge/services/session_service.py`
- **Related Terms**: Session, Decision Trace

### Retention
- **Term**: Retention
- **Simple Definition**: Rules dictating how long data is kept before being deleted.
- **Detailed Explanation**: Configurable by Tenant Admins, retention policies ensure stale or legally burdensome evidence (like old Slack chats) is hard-deleted automatically by beat schedulers, except when guarded by "legal-hold" flags.
- **Where Used**: `backend/src/contextedge/services/evidence_filters.py`, Cleanup workers
- **Related Terms**: Compliance, Beat Scheduler

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
- **Detailed Explanation**: The Runtime APIs (e.g., `/api/v1/runtime`) only serve approved, published playbooks. They are highly optimized for speed, enforcing role/domain security policies dynamically to prevent unsafe automation.
- **Where Used**: `backend/src/contextedge/api/v1/runtime/`
- **Related Terms**: Playbook, Agent

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

### Source
- **Term**: Source
- **Simple Definition**: An external system providing data to ContextEdge.
- **Detailed Explanation**: Sources (Jira, Slack, Teams) are configured in the Source Registry. Tenant admins govern which Source Objects (e.g., specific Slack channels) are approved for backfill and incremental sync.
- **Where Used**: `backend/src/contextedge/api/v1/sources.py`, Connectors
- **Related Terms**: Connector, Ingestion

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
- **Detailed Explanation**: Managed by Connectors, sync runs can be historical "backfills" or incremental steady-state updates. A "SyncCheckpoint" tracks the last processed item to ensure recovery without duplication.
- **Where Used**: `backend/src/contextedge/api/v1/sync.py`, `services/sync_worker_service.py`
- **Related Terms**: Connector, Ingestion

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
- **Detailed Explanation**: With migration `0029`, Context Graph edges became "temporal," gaining `valid_from` and `valid_to` timestamps. This allows the system to understand that a fix was valid last year, but is now expired.
- **Where Used**: `backend/src/contextedge/models/graph.py`
- **Related Terms**: Graph Edge, History

### Thread
- **Term**: Thread
- **Simple Definition**: A connected series of messages or comments.
- **Detailed Explanation**: A type of evidence. Rather than treating every Slack message independently, ContextEdge "hydrates" and stores them as complete threads to provide the AI extractors with proper troubleshooting context.
- **Where Used**: `backend/src/contextedge/models/evidence.py`, Connectors
- **Related Terms**: Hydration, Evidence

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
- **Detailed Explanation**: See Embedding. LLMs turn text into 3072-dimensional vectors. Vectors that point in similar directions represent text with similar meanings.
- **Where Used**: `backend/src/contextedge/search/`
- **Related Terms**: Embedding, Vector Search

### Vector Search
- **Term**: Vector Search
- **Simple Definition**: Searching a database by meaning rather than exact keywords.
- **Detailed Explanation**: Powered by `pgvector`, it compares the vector of the user's query against the vectors of chunks, evidence, and playbooks to find the closest conceptual matches.
- **Where Used**: `backend/src/contextedge/search/vector_search.py`
- **Related Terms**: Similarity Search, pgvector, Hybrid Search

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
- **Detailed Explanation**: Used by ContextEdge to run unit tests on the Next.js React frontend components and data utilities, ensuring the UI remains stable during development.
- **Where Used**: `frontend/`, `npm test`
- **Related Terms**: UI

## W

### Worker
- **Term**: Worker
- **Simple Definition**: A background process that does heavy lifting.
- **Detailed Explanation**: Celery workers run outside the API web server to handle long-running tasks like fetching Jira tickets, generating LLM summaries, and running drift evaluations, keeping the web UI responsive.
- **Where Used**: `backend/src/contextedge/workers/`
- **Related Terms**: Celery, Queue

### Workspace
- **Term**: Workspace
- **Simple Definition**: A sub-division within a Tenant.
- **Detailed Explanation**: Used to separate data between different business units or geographic regions within the same organization. It acts as an optional secondary security boundary beneath the Tenant level.
- **Where Used**: `backend/src/contextedge/models/tenant.py`
- **Related Terms**: Tenant, Domain
