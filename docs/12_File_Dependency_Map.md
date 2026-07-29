# ContextEdge — File Dependency Map

## Overview
This document maps the core file dependencies in the ContextEdge repository. It outlines who imports what, data flow, control flow, and design rationale for each key file. 

---

## 1. Backend Core

### `backend/src/contextedge/main.py`
**Rating: 10/10**
- **What it is:** The entry point for the FastAPI application.
- **Why it exists:** To configure and launch the web server, attach middleware, handle startup/shutdown lifecycles (like connecting to Redis and MinIO), and include API routers.
- **Where it is:** `backend/src/contextedge/main.py`
- **Who calls it:** Uvicorn or another ASGI server invokes it to start the app.
- **Who it imports from:** `contextedge.config.settings`, `contextedge.services.object_store.ensure_bucket`, `contextedge.api.v1.router`, `contextedge.middleware.request_audit`, `contextedge.middleware.request_context`.
- **What happens next:** The application starts listening for HTTP requests on the configured port.
- **Data flow:** Receives raw HTTP requests, processes global exception handling, and forwards them to API routers.
- **Control flow:** Initialization -> Lifespan context (startup) -> App creation -> Request handling -> Lifespan context (shutdown).
- **Design rationale:** Centralized app configuration makes it easier to inject middleware and manage global state.

### `backend/src/contextedge/api/v1/__init__.py`
**Rating: 9/10**
- **What it is:** The central router registry for API v1.
- **Why it exists:** To aggregate all sub-routers (e.g., auth, tenants, evidence, etc.) under the `/api/v1` prefix.
- **Where it is:** `backend/src/contextedge/api/v1/__init__.py`
- **Who calls it:** `backend/src/contextedge/main.py` imports and mounts it.
- **Who it imports from:** All module-level routers inside the `v1` package (e.g., `auth.py`, `tenants.py`, `sources.py`, etc.).
- **Data flow:** Routes incoming HTTP requests to the appropriate handler function based on the URL path.
- **Control flow:** Path matching -> Delegation to specific router.

---

## 2. Workers & Background Tasks

### `backend/src/contextedge/workers/celery_app.py`
**Rating: 10/10**
- **What it is:** The Celery application configuration and task registry.
- **Why it exists:** To manage asynchronous background tasks, configure queues, and schedule periodic beats.
- **Where it is:** `backend/src/contextedge/workers/celery_app.py`
- **Who calls it:** The Celery worker process on startup.
- **Who it imports from:** `contextedge.config.settings`, `contextedge.middleware.request_context` (for correlation ID injection). Includes task modules like `sync_tasks`, `extraction_tasks`, etc.
- **What happens next:** Celery workers start polling the broker (Redis/RabbitMQ) for tasks.
- **Data flow:** Injects HTTP correlation headers into Celery task messages and extracts them when a task starts running.
- **Control flow:** Task Enqueue -> Broker -> Worker -> Task Prerun (context bind) -> Execution -> Task Postrun (context release).
- **Design rationale:** Separates heavy lifting (syncs, AI extractions) from the fast HTTP request-response cycle.

### `backend/src/contextedge/workers/extraction_tasks.py`
**Rating: 9/10**
- **What it is:** Defines Celery tasks for processing raw evidence, extracting entities, classifying relevance, and embedding.
- **Why it exists:** Ingestion is computationally heavy. Processing must be offloaded to avoid blocking the API.
- **Where it is:** `backend/src/contextedge/workers/extraction_tasks.py`
- **Who calls it:** Triggered by evidence ingestion API endpoints or sync workers.
- **Who it imports from:** `contextedge.ai.classifiers`, `contextedge.ai.embeddings`, `contextedge.models.evidence`, `contextedge.services.*`.
- **What happens next:** Creates `EvidenceItem` rows, triggers chunking tasks, and evaluates relevance.
- **Data flow:** `RawEvidenceObject` -> Redaction -> AI Classification -> Identity/Decision extraction -> Embedding.
- **Design rationale:** Uses an allow-list for inline vs async chunking to balance ingest latency against reliability.

---

## 3. Services Layer

### `backend/src/contextedge/services/execution_service.py`
**Rating: 10/10**
- **What it is:** Orchestrates the execution of AI playbooks with safety class enforcement and approval gates.
- **Why it exists:** Provides a governed way for the system to execute automated remediation or analysis steps while keeping human-in-the-loop oversight.
- **Where it is:** `backend/src/contextedge/services/execution_service.py`
- **Who calls it:** API endpoints (e.g., user triggering a playbook) or background automated response systems.
- **Who it imports from:** `contextedge.models.execution`, `contextedge.models.playbook`, `contextedge.graph.builder`.
- **Data flow:** Playbook inputs -> Safety evaluation -> Step execution -> Tool invocations -> Results/Approvals.
- **Control flow:** `start_execution` checks caller roles. If a step exceeds safety limits, an `ApprovalRequest` is spawned. Execution halts until `decide_approval` is invoked.
- **Design rationale:** Shadow-mode allows dry-runs for testing side-effects safely. Approval gates enforce organizational policies.

---

## 4. Frontend Core

### `frontend/src/lib/api.ts`
**Rating: 9/10**
- **What it is:** A centralized fetch wrapper for communicating with the backend API.
- **Why it exists:** Ensures consistent error handling, auth token injection, and request ID generation across the UI.
- **Where it is:** `frontend/src/lib/api.ts`
- **Who calls it:** React components, hooks, and services in the frontend.
- **What happens next:** Makes an HTTP request to the backend. If it receives a 401, it logs the user out.
- **Data flow:** JSON objects in -> Serialized HTTP request -> Parsed JSON response out.
- **Design rationale:** Avoids repetitive fetch boilerplate and standardizes auth token handling from localStorage.

---

## Dependency Diagrams

### 1. Backend Module Dependency Graph

```mermaid
graph TD
    A[main.py] --> B[api/v1/__init__.py]
    A --> C[middleware/request_context.py]
    B --> D[api/v1/auth.py]
    B --> E[api/v1/evidence.py]
    B --> F[api/v1/playbooks.py]
    D --> G[services/auth_service.py]
    E --> H[services/evidence_service.py]
    F --> I[services/execution_service.py]
    H --> J[models/evidence.py]
    I --> K[models/execution.py]
    I --> L[models/playbook.py]
```

### 2. API → Service → Model Dependency Graph

```mermaid
graph TD
    API[API Layer (FastAPI)] --> SvcAuth[Auth Service]
    API --> SvcExec[Execution Service]
    API --> SvcSync[Sync Service]
    API --> SvcGraph[Graph Service]
    
    SvcAuth --> ModUser[User Model]
    SvcAuth --> ModTenant[Tenant Model]
    
    SvcExec --> ModPlaybook[Playbook Model]
    SvcExec --> ModExecution[Execution Model]
    SvcExec --> GraphBuilder[Graph Builder]
    
    SvcSync --> ModSource[Source Model]
    SvcSync --> WorkerTasks[Celery Tasks]
```

### 3. Worker Task Dependency Chain

```mermaid
graph TD
    T1[sync.trigger_scheduled_syncs] --> T2[sync.run_source_sync]
    T2 --> T3[extraction.normalize_evidence]
    T3 --> T4[extraction.classify_relevance]
    T3 --> T5[chunking.chunk_evidence_task]
    T3 --> T6[extraction.extract_attachment]
    T5 --> T7[chunking.embed_chunks_batch_task]
    T3 --> T8[correlation.correlate_evidence]
    T3 --> T9[baseline.compute_evidence_baseline]
```

### 4. AI Pipeline Dependency Graph

```mermaid
graph TD
    A[Raw Evidence] --> B[Redaction Service]
    B --> C[Relevance Classifier LLM]
    C -->|If Relevant| D[Identity Extraction LLM]
    C -->|If Relevant| E[Decision Extraction LLM]
    D --> F[Embedding Model]
    E --> F
    F --> G[Vector Store]
    D --> H[Context Graph Builder]
    E --> H
```

### 5. Graph Module Dependency Graph

```mermaid
graph TD
    Builder[graph/builder.py] --> Models[models/graph.py]
    Queries[graph/queries.py] --> Models
    Execution[services/execution_service.py] --> Builder
    Evidence[services/evidence_service.py] --> Builder
    API[api/v1/graph.py] --> Queries
```

### 6. Frontend Component Hierarchy

```mermaid
graph TD
    App[Next.js App Router] --> Layout[Dashboard Layout]
    Layout --> Nav[Sidebar Navigation]
    Layout --> PageContent[Page Content]
    PageContent --> Dashboard[Dashboard View]
    PageContent --> Evidence[Evidence View]
    PageContent --> Playbook[Playbook Builder]
    Evidence --> EvidenceList[Evidence Table]
    Evidence --> EvidenceDetail[Evidence Detail Panel]
```

### 7. Frontend API Layer Dependency Graph

```mermaid
graph TD
    Hooks[React Query Hooks] --> APIClient[lib/api.ts]
    Components[UI Components] --> Hooks
    APIClient --> Fetch[Browser fetch API]
    APIClient --> LocalStorage[localStorage (Auth Token)]
    APIClient --> AuthGuard[401 Redirect Logic]
```

---
*Generated by the Senior Technical Writer Agent.*
