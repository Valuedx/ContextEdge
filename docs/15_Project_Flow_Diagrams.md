# ContextEdge — Project Flow Diagrams

This document contains flow diagrams for critical processes and data pipelines within the ContextEdge platform.

## 1. Project Architecture Overview
**Description:** High-level overview of the ContextEdge system architecture, showing the relationship between Frontend, API, Workers, and Data Stores.
**Key Files:** `main.py`, `celery_app.py`, `api.ts`

```mermaid
graph TD
    User((User)) --> NextJS[Next.js Frontend]
    NextJS --> FastAPI[FastAPI Backend]
    FastAPI --> Postgres[(PostgreSQL)]
    FastAPI --> Redis[(Redis Cache)]
    FastAPI --> Celery[Celery Workers]
    Celery --> Postgres
    Celery --> Redis
    Celery --> MinIO[(MinIO Object Store)]
    Celery --> VectorDB[(Vector DB)]
    Celery --> LLM[AI Providers]
```

## 2. Login/Authentication Flow
**Description:** Sequence of events when a user logs in.
**Key Files:** `frontend/src/lib/auth.ts`, `backend/src/contextedge/api/v1/auth.py`

```mermaid
sequenceDiagram
    actor User
    participant Frontend
    participant API
    participant DB
    User->>Frontend: Enter Credentials
    Frontend->>API: POST /api/v1/auth/login
    API->>DB: Verify Credentials
    DB-->>API: User Data
    API-->>Frontend: JWT Access Token
    Frontend->>Frontend: Store in localStorage
    Frontend-->>User: Redirect to Dashboard
```

## 3. API Request Lifecycle
**Description:** How an HTTP request flows through the FastAPI middleware before hitting the router.
**Key Files:** `main.py`, `middleware/request_context.py`

```mermaid
flowchart TD
    Req[Incoming Request] --> Audit[Audit Middleware]
    Audit --> Tenant[Tenant Context Middleware]
    Tenant --> Router[API Router]
    Router --> Service[Service Layer]
    Service --> DB[Database]
    DB --> Service
    Service --> Router
    Router --> Resp[Response]
```

## 4. Evidence Ingestion Flow
**Description:** How raw data is ingested and normalized.
**Key Files:** `sync_tasks.py`, `extraction_tasks.py`

```mermaid
flowchart LR
    Source[External Source] --> Raw[Raw Evidence]
    Raw --> Normalizer[Normalizer Task]
    Normalizer --> Redaction[PII Redaction]
    Redaction --> Classifier[Relevance Classifier]
    Classifier --> Storage[Evidence Item DB]
```

## 5. AI Extraction Pipeline
**Description:** Fan-out of tasks to extract knowledge from evidence.
**Key Files:** `extraction_tasks.py`, `chunk_tasks.py`

```mermaid
flowchart TD
    Ev[Evidence Created] --> IdExt[Extract Identities]
    Ev --> DecExt[Extract Decisions]
    Ev --> Embed[Generate Embeddings]
    Ev --> Chunk[Chunk Large Files]
    Chunk --> EmbedChunk[Embed Chunks]
```

## 6. Episode Creation Flow
**Description:** Reconstructing episodes from grouped evidence.
**Key Files:** `episode_service.py`

```mermaid
sequenceDiagram
    participant Worker
    participant DB
    participant AI
    Worker->>DB: Fetch Clustered Evidence
    Worker->>AI: Generate Episode Summary
    AI-->>Worker: Summary & Timeline
    Worker->>DB: Create Episode & Steps
```

## 7. Pattern Detection Flow
**Description:** Background mining of recurring issues.
**Key Files:** `pattern_tasks.py`

```mermaid
flowchart LR
    Eps[Recent Episodes] --> Miner[Pattern Miner]
    Miner --> LLM[AI Analysis]
    LLM --> Match[Match Existing Patterns]
    Match -- Yes --> Update[Update Pattern]
    Match -- No --> Create[Create New Pattern]
```

## 8. Playbook Generation Flow
**Description:** Converting detected patterns into actionable playbooks.
**Key Files:** `playbook_service.py`

```mermaid
flowchart TD
    Pattern[Detected Pattern] --> Gen[Playbook Generator]
    Gen --> Steps[Draft Steps]
    Steps --> Review[Human Review Queue]
    Review -- Approved --> Active[Active Playbook]
```

## 9. Agent Execution Flow (MAF)
**Description:** Orchestration of a playbook run.
**Key Files:** `execution_service.py`

```mermaid
sequenceDiagram
    participant API
    participant ExecSvc
    participant Graph
    participant Tools
    API->>ExecSvc: start_execution()
    ExecSvc->>Graph: Check Permissions & Safety
    loop Over Steps
        ExecSvc->>Tools: Invoke Tool
        Tools-->>ExecSvc: Result
        ExecSvc->>ExecSvc: Evaluate Approval Gates
    end
    ExecSvc-->>API: Execution Complete
```

## 10. Vector Retrieval Flow
**Description:** Semantic search for relevant knowledge.
**Key Files:** `hybrid_ranker.py`

```mermaid
flowchart LR
    Query[User/Agent Query] --> Embed[Embed Query]
    Embed --> VDB[Vector Search]
    VDB --> Ranker[Hybrid Ranker]
    Ranker --> Result[Top K Results]
```

## 11. Context Graph Update Flow
**Description:** Mutating the knowledge graph.
**Key Files:** `graph/builder.py`

```mermaid
flowchart TD
    Event[System Event] --> Builder[Graph Builder]
    Builder --> Edge[Create/Update Edge]
    Builder --> Node[Create/Update Node]
    Edge --> DB[(Postgres JSONB)]
    Node --> DB
```

## 12. Review Queue Flow
**Description:** Human-in-the-loop approvals for uncertain AI actions.
**Key Files:** `review_queue_service.py`

```mermaid
flowchart LR
    Task[AI Task Low Confidence] --> Queue[Review Queue]
    Queue --> Admin[Human Admin]
    Admin -- Approve --> Exec[Execute Action]
    Admin -- Modify --> ExecMod[Execute Modified]
    Admin -- Reject --> Drop[Drop Action]
```

## 13. Evidence Baseline Flow
**Description:** Computing the operational baseline of an entity.
**Key Files:** `evidence_baseline_tasks.py`

```mermaid
flowchart TD
    Ev[New Evidence] --> Task[Baseline Task]
    Task --> DB[Fetch Historical Data]
    DB --> LLM[Compute Deviation]
    LLM --> Update[Update Baseline Profile]
```

## 14. Sync Operation Flow
**Description:** Syncing from external integrations (e.g. Jira).
**Key Files:** `sync_worker_service.py`

```mermaid
sequenceDiagram
    participant Cron
    participant Celery
    participant API
    participant DB
    Cron->>Celery: trigger_scheduled_syncs
    Celery->>API: Fetch External Data
    API-->>Celery: JSON Payload
    Celery->>DB: Upsert RawEvidence
```

## 15. Runtime Query Execution Flow
**Description:** Processing dynamic queries against the operational memory.
**Key Files:** `graph/queries.py`

```mermaid
flowchart LR
    Q[Query] --> Parser[Query Parser]
    Parser --> SQL[Generate SQL]
    SQL --> DB[Execute]
    DB --> Format[Format Results]
```

## 16. Decision Pipeline Flow
**Description:** How historical decisions inform current actions.
**Key Files:** `decision_tasks.py`

```mermaid
flowchart TD
    Hist[Historical Actions] --> Mine[Mine Decisions]
    Mine --> Calibrate[Calibrate Confidence]
    Calibrate --> Profile[Update Decision Profile]
    Profile --> Agent[Inform Future Agents]
```

## 17. Evaluation Flow
**Description:** Evaluating playbook performance and AI output quality.
**Key Files:** `evaluations.py`

```mermaid
flowchart LR
    Run[Completed Run] --> Eval[Evaluate Outcome]
    Eval --> Metric[Calculate Metrics]
    Metric --> Dash[Dashboard]
```

## 18. Policy Enforcement Flow
**Description:** Checking RBAC and safety policies before actions.
**Key Files:** `execution_service.py`

```mermaid
sequenceDiagram
    participant User
    participant Exec
    participant Policy
    User->>Exec: Run Playbook
    Exec->>Policy: check_safety_class()
    Policy-->>Exec: requires_approval
    Exec->>User: Request Approval
```

## 19. LLM Interaction Flow
**Description:** Wrapper around AI providers (OpenAI, Anthropic).
**Key Files:** `ai/provider.py`

```mermaid
flowchart TD
    Prompt[System Prompt] --> Client[AI Client Wrapper]
    Client --> API[Provider API]
    API --> Client
    Client --> Parse[Parse JSON Output]
    Parse --> App[App Logic]
```

## 20. Contradiction Detection Flow
**Description:** Identifying conflicting information in the memory graph.
**Key Files:** `contradiction_service.py`

```mermaid
flowchart LR
    New[New Fact] --> Graph[Context Graph]
    Graph --> Scan[Scan for Conflicts]
    Scan -- Found --> Alert[Flag Contradiction]
```

## 21. Identity Resolution Flow
**Description:** Merging aliases into a single canonical identity.
**Key Files:** `identity_service.py`

```mermaid
flowchart TD
    Raw[Raw Identity String] --> Match[Fuzzy Match DB]
    Match -- High Confidence --> Merge[Link to Canonical]
    Match -- Low Confidence --> New[Create New Identity]
```

## 22. Correlation Discovery Flow
**Description:** Finding links between disparate evidence items.
**Key Files:** `correlation_service.py`

```mermaid
flowchart LR
    Ev1[Evidence A] --> Analyzer[Correlation Engine]
    Ev2[Evidence B] --> Analyzer
    Analyzer --> Embed[Compare Embeddings]
    Analyzer --> Meta[Compare Metadata]
    Embed & Meta --> Link[Create Correlation Edge]
```

## 23. Drift Detection Flow
**Description:** Detecting when playbooks become stale compared to recent evidence.
**Key Files:** `drift.py`

```mermaid
sequenceDiagram
    participant Cron
    participant DB
    participant AI
    Cron->>DB: Get Active Playbooks
    DB->>AI: Compare with Recent Incidents
    AI-->>DB: Drift Score
    DB->>DB: If > Threshold, Flag Stale
```

## 24. Worker Task Chain Flow
**Description:** The propagation of Celery request context and correlations.
**Key Files:** `celery_app.py`

```mermaid
flowchart TD
    Req[HTTP Request] --> ID[Inject Request ID]
    ID --> Queue[Celery Queue]
    Queue --> Prerun[Task Prerun Context Bind]
    Prerun --> Work[Execute Work]
    Work --> Postrun[Release Context]
```

## 25. Retention/Cleanup Flow
**Description:** Hard deletion of expired data and orphans.
**Key Files:** `cleanup_tasks.py`

```mermaid
flowchart TD
    Cron[Daily Cron] --> Sweep[Find Expired Evidence]
    Sweep --> DB[Delete DB Rows]
    Sweep --> S3[Delete MinIO Objects]
    Sweep --> Graph[Delete Orphan Edges]
```

---
*Generated by the Senior Technical Writer Agent.*
