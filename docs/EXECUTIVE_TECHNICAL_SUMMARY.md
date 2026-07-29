# ContextEdge — Technical Architecture & System Guide

---

## 1. System Overview

**ContextEdge** is an Operational Memory and Playbook platform. It collects operational evidence from ticketing systems (ServiceNow, Jira), emails (Gmail), and chat applications (MS Teams, Slack), normalizes it, and builds evidence-backed, versioned playbooks and context graphs for human review and automated AI agent retrieval.

---

## 2. Modular Monolith Architecture vs. External Integrations

### Architectural Model
ContextEdge is designed as a **Modular Monolith**—a single FastAPI backend application, single PostgreSQL database, and single deployment container, organized into clean internal modules (`connectors/`, `services/`, `graph/`, `ai/`, `search/`, `workers/`).

```text
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         ContextEdge Modular Monolith                        │
 │                                                                             │
 │   ┌─────────────────────────────────────────────────────────────────────┐   │
 │   │                       FastAPI Backend Application                   │   │
 │   │                                                                     │   │
 │   │  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────┐  │   │
 │   │  │  API Routers │   │ Service Layer│   │ Ingestion Connectors    │  │   │
 │   │  │  (/api/v1)   │───│ (Playbooks,  │───│ (gmail, servicenow,     │  │   │
 │   │  │              │   │  Decisions)  │   │  jira_sm, teams)        │  │   │
 │   │  └──────────────┘   └──────────────┘   └────────────┬────────────┘  │   │
 │   └─────────────────────────────────────────────────────│───────────────┘   │
 └─────────────────────────────────────────────────────────│───────────────────┘
                                                           │
                        HTTP REST / OAuth API Calls        │
             ┌─────────────────────────────────────────────┴───────────┐
             │                                                         │
             ▼                                                         ▼
  ┌──────────────────────┐                                  ┌────────────────────┐
  │  ServiceNow (Cloud)  │                                  │   Gmail / Google   │
  │  (Incidents/Tickets) │                                  │   (Emails/Threads) │
  └──────────────────────┘                                  └────────────────────┘
```

### Key Design Rationale
External systems (ServiceNow, Gmail, Jira) are **external data sources**, not internal microservices.

* **Data Consistency**: Storing all evidence, graph edges, and decisions in one PostgreSQL database provides ACID transactional safety.
* **Low Latency**: Context Graph traversals and hybrid vector queries run in under 5 milliseconds in the same database without cross-service network calls.
* **Simplified Operations**: Single container build and straightforward deployment workflow.

> ⚡ **1-Line Summary**: ContextEdge is a Modular Monolith because all core logic and graph storage run in one unified application and database, while external integrations (ServiceNow/Gmail) act as data-intake adapters.

---

## 3. User Roles & System Access Controls

ContextEdge enforces Role-Based Access Control (RBAC) across 5 primary roles:

| Role | Target User | System Function |
|---|---|---|
| **Analysts / Support Engineers** | L1 / L2 Support | Uses runtime search APIs & chat widgets to fetch verified playbooks and evidence during active incidents. |
| **Knowledge Managers** | L3 SMEs / Reviewers | Reviews, modifies, and approves AI-generated episodes and playbooks on the web dashboard before release. |
| **Domain Admins** | Team Leads | Configures which team channels and knowledge bases are ingested for specific areas (Cloud, Network, Identity). |
| **Tenant Admins** | Platform Admins | Configures organization-wide security settings, user roles, API keys, and daily LLM token cost budgets. |
| **Service Accounts / Agents** | Autonomous Systems (AEAIHub) | Programmatically queries ContextEdge as operational memory to execute approved playbooks automatically. |

> ⚡ **1-Line Summary**: ContextEdge's 5 roles form a complete governance workflow—from ingesting data (Domain Admins) and curating memory (L3 Reviewers) to using memory (L1/L2 Engineers & AI Agents) under administrative control (Tenant Admins).

---

## 4. Microsoft Agent Framework (MAF) Integration

### Functionality
The Microsoft Agent Framework (MAF) is an open-source framework for building autonomous AI agents that can reason, plan, and execute operational tasks.

### ContextEdge Integration
ContextEdge acts as the **Memory Engine** for MAF agents:

```text
       Incident Event (e.g., Application Outage)
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  1. READS MEMORY (integrations/maf/provider.py)          │
│     MAF queries ContextEdge's Graph to retrieve past     │
│     episodes, verified playbooks, and security policies. │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  2. EXECUTES TOOLS (integrations/maf/tools.py)           │
│     MAF invokes ContextEdge functions: search_playbooks(),│
│     get_incident_context(), execute_step().              │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  3. LOGS AUDIT TRACE (services/decision_trace_service.py)│
│     MAF records decision events in PostgreSQL for        │
│     auditability and reviewer feedback.                  │
└──────────────────────────────────────────────────┘
```

> ⚡ **1-Line Summary**: MAF allows autonomous AI agents to query ContextEdge memory, call approved playbook tools, execute resolution steps, and record decision audit traces.

---

## 5. Distinction Between Evidence, Episode, and Pattern

### The Operational Memory Hierarchy
ContextEdge processes data through three distinct stages of abstraction:

```text
 RAW DATA                      SINGLE INCIDENT SUMMARY        RECURRING PROBLEM              SOLVING PLAYBOOK
 (Noisy Messages)              (One Outage Event)            (Systemic Pattern)             (Step-by-step Fix)

 ┌──────────────┐
 │ Slack Thread │──┐
 └──────────────┘  │
 ┌──────────────┐  ├──► [ Episode #1 (Mon Outage) ] ──┐
 │ Jira Ticket  │──┘                                 │
 └──────────────┘                                    │
                                                     ├──► [ PATTERN ] ──────────► [ PLAYBOOK ]
 ┌──────────────┐                                    │  ("VPN Auth Failures      ("Renew Cert &
 │ PagerDuty    │──────► [ Episode #2 (Fri Outage) ] ──┤   caused by expired       Restart Radius")
 └──────────────┘                                    │   OAuth Certs")
                                                     │
 ┌──────────────┐                                    │
 │ Email Log    │──────► [ Episode #3 (Last Month) ] ┘
 └──────────────┘
```

### Why Both Episode and Pattern Extractions Exist

| Level | What it Contains | Scope | Primary Purpose |
|---|---|---|---|
| **Evidence** | Raw tickets, Slack chats, emails, log files. | Unstructured raw text | Provides audit proof and source evidence. |
| **Episode** | Clean summary of **ONE specific outage incident**. | 1 Single Event (e.g., Monday 9 AM Outage) | Extracts problem, root cause, and fix steps for a specific event. |
| **Pattern** | Combination of **MULTIPLE similar Episodes** across time. | Recurring Trend (e.g., 10 Outages over 3 months) | Identifies recurring systemic problems and generates standard playbooks. |

* **Evidence ➔ Episode**: A single incident (e.g., a VPN outage on Monday) may involve a Slack chat, a Jira ticket, and a PagerDuty alert. These multiple Evidence items are combined into **1 Episode summary**.
* **Episode ➔ Pattern**: When 5 or 10 separate outages (Episodes) occur across weeks or months with the same root cause, ContextEdge clusters them into **1 Pattern** to generate an enterprise **Playbook**.

> ⚡ **1-Line Summary**: An Episode summarizes ONE specific incident (from multiple raw Evidence items), while a Pattern connects MULTIPLE Episodes across time to discover recurring systemic problems and generate verified Playbooks.

---

## 6. Pattern vs. Agent Context

### Structural Difference

| Feature | PATTERN (Knowledge Node) | AGENT CONTEXT (Runtime Memory) |
|---|---|---|
| **What is it?** | A **stored knowledge object** in the database representing a recurring operational problem across tickets. | A **temporary, tailored bundle of memory** (nodes & edges) given to an AI Agent or shown in a UI preview at runtime. |
| **Creation Frequency** | Created asynchronously by background workers when incidents repeat. | Assembled live on-the-fly whenever a query or API call is made. |
| **Storage Location** | `patterns` table & `graph_edges` table in PostgreSQL | Temporary data structure returned by `AgentGraphSelector` (`graph/agent/selector.py`) |

> ⚡ **1-Line Summary**: A Pattern is a static recurring problem node stored in PostgreSQL, whereas Agent Context is a dynamic, token-budgeted memory bundle built live for an AI agent.

---

## 7. System Observability & Prometheus Metrics

### Functionality
Prometheus tracks system performance metrics, endpoint latency, traffic volume, error rates, and AI token consumption.

* **API Performance**: Measured in `main.py` using `prometheus-fastapi-instrumentator` for request counts and latency.
* **AI Model Metrics**: Custom instrumentation in `ai/observability.py` tracks LLM token counts, request duration, and costs per tenant.

> ⚡ **1-Line Summary**: Prometheus monitors API latency, request traffic, error rates, and daily LLM token costs to ensure system reliability and budget control.

---

## 8. Evidence Normalization, Redaction & Database Storage

### Evidence Definition
Evidence is the raw operational proof ingested from ServiceNow incidents, Jira tickets, Gmail threads, and system logs.

### Database Storage Architecture
Evidence is stored in **PostgreSQL 16** across two primary tables:

```text
 ┌────────────────────────────────────────────────────────────┐
 │  1. "evidence_items" Table                                 │
 │  Stores ticket metadata, PII-redacted text & content_hash  │
 └─────────────────────────────┬──────────────────────────────┘
                               │ (1 to Many)
                               ▼
 ┌────────────────────────────────────────────────────────────┐
 │  2. "evidence_chunks" Table                                │
 │  Stores text segments, tsvector (FTS) & HalfVec (Vectors)  │
 └────────────────────────────────────────────────────────────┘
```

1. **`evidence_items`**:
   - `id`: UUID primary key.
   - `redacted_content`: Cleaned text with passwords, API keys, emails, and SSNs removed by `services/redaction_service.py`.
   - `content_hash`: SHA-256 hash preventing duplicate ticket storage.
   - `baseline_ref` & `delta_signal`: Status tracking (`neutral`, `amber`, `red`).
2. **`evidence_chunks`**:
   - `search_vector`: Full-Text Search (`tsvector`) GIN index for exact keyword queries.
   - `embedding`: 1,536-dimensional AI vector embedding (`HalfVec` column) generated by Google Vertex AI (`text-embedding-004`).

> ⚡ **1-Line Summary**: Evidence is the PII-redacted, vector-indexed proof layer in PostgreSQL that grounds AI recommendations in verified historical IT tickets.

---

## 9. AI Episode Reconstruction

### Extraction & Validation Workflow
A Celery background task (`workers/extraction_tasks.py`) processes raw tickets to create structured incident records called **Episodes**.

```text
Raw Ticket / Chat Thread
           │
           ▼
1. LLM Extraction (Extracts core facts, removes conversational noise)
           │
           ▼
2. Schema Validation (Secondary check ensures extracted facts match original text)
           │
           ▼
3. Structured Episode Record (Symptom + Root Cause + Resolution Fix)
           │
           ▼
4. Embedded & Stored in PostgreSQL ("episodes" table with HNSW index)
```

### Episode Vector Indexing
Extracted Episodes are converted into 1,536-dimensional vectors and saved in the `episodes.embedding` column with an **HNSW index** (`idx_episodes_embedding_hnsw`) for fast similarity matching.

> ⚡ **1-Line Summary**: AI Episode Reconstruction converts unstructured ticket comments into structured "Problem-Cause-Fix" records and indexes them with HNSW vectors for rapid solution retrieval.

---

## 10. Multi-Topic Filtering & Quality Control

When incoming evidence contains unrelated topics (such as a printer issue mixed into a VPN ticket), ContextEdge uses four safeguards:

1. **Vector & Entity Distance (`services/correlation_service.py`)**: Cosine similarity < 0.70 or mismatched system hostnames separates unrelated items.
2. **LLM Extractor Prompting**: Prompts instruct the model to ignore off-topic comments.
3. **Contradiction Detection (`services/contradiction_service.py`)**: Identifies conflicting facts and routes items to the Review Queue (`/review`).
4. **Negative Knowledge Tracking (`negative_knowledge` table)**: When a reviewer unlinks incorrect evidence, a record is stored (`"Evidence B is not related to Incident A"`) to prevent identical misclassifications in future sync operations.

> ⚡ **1-Line Summary**: ContextEdge separates unrelated topics via vector distance, identifies contradictions, routes items to human reviewers, and stores negative knowledge records to prevent recurring grouping errors.

---

## 11. Context Graph & Recursive SQL Traversal

### Context Graph Structure
ContextEdge connects operational entities (**Nodes**: Error Codes, Episodes, Patterns, Playbooks, Policies) using relationship edges (**Edges**: `part_of`, `derived_from`, `generates`, `applied_policy`).

### PostgreSQL Adjacency Storage
Graph edges are stored in PostgreSQL inside the **`graph_edges`** table:

```sql
CREATE TABLE graph_edges (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  source_type VARCHAR(50) NOT NULL, -- 'evidence', 'episode', 'pattern', 'playbook'
  source_id UUID NOT NULL,
  edge_type VARCHAR(50) NOT NULL,   -- 'part_of', 'derived_from', 'resolves', 'contradicts'
  target_type VARCHAR(50) NOT NULL, -- 'episode', 'pattern', 'playbook', 'policy'
  target_id UUID NOT NULL
);
```

### 5-Level Operational Hierarchy & Collection Lifecycle

```text
 Level 1 (Highest)   ──►   [ PLAYBOOK ]         (Verified resolution steps)
                              │
                              ▼ (addresses)
 Level 2             ──►   [ PATTERN ]          (Recurring problem pattern)
                              │
                              ▼ (clusters 1:N historical episodes)
 Level 3             ──►   [ EPISODE ]          (Single incident analysis story)
                              │
                              ▼ (derived_from 1:N multi-source proof)
 Level 4             ──►   [ EVIDENCE ITEM ]    (ServiceNow Ticket + Splunk Log + Slack Thread)
                              │
                              ▼ (references)
 Level 5 (Lowest)    ──►   [ ENTITY / POLICY ]  (Hostnames, DB name, Action Policy)
```

#### How a Pattern Collects Episodes:
1. **Vector Clustering Algorithm (`pattern_tasks.py`)**: The pattern detection worker periodically generates 3072-dimensional vector embeddings for all closed incident `Episode` summaries using pgvector (`text-embedding-004`).
2. **Similarity Grouping (>85% Match)**: Episodes sharing over 85% root-cause summary similarity (e.g. connection leaks on `ORDERS_DB` occurring in Sept 2025, Nov 2025, Jan 2026, April 2026, June 2026, and July 2026) are grouped under a single **Pattern** record.
3. **Graph Edge Creation**: The system creates `GraphEdge` records (`pattern ──clusters──► episode`) and `PatternEvidenceLink` relational mappings.

#### How an Episode Collects Evidence:
1. **Multi-Source Correlation (`episode_extractor`)**: When raw evidence arrives from ServiceNow, Splunk, or Slack, the episode worker correlates them by shared incident case reference (e.g. `INC0010427`), timestamp proximity (within 30 mins), and matching entity targets (`ORDERS_DB`).
2. **Multi-Evidence Stitching**: The worker links the `Episode` to 3–4 distinct Evidence Items via `GraphEdge` links (`episode ──derived_from──► evidence`).

### Visual Graph UI Breakdown (Node-by-Node Guide for New Users)

When viewing the Knowledge Graph visualization on the dashboard screen, read left to right to understand how data flows:

```text
[ PLAYBOOK ] ──(addresses)──► [ PATTERN ] ──(clusters)──► [ EPISODE ] ──(derived_from)──► [ EVIDENCE ITEM ]
```

1. **`PLAYBOOK` (Node 1 - Verified Fix Guide)**: Step-by-step operational instructions to safely fix the issue.
2. **`PATTERN` (Node 2 - Recurring Problem)**: Title of the systemic issue identified across repeating incidents (e.g., *"Database connection pool exhaustion on ORDERS_DB"*).
3. **`EPISODE` (Node 3 - Incident Analysis)**: Clean AI summary of **one specific outage event** (`INC0010427`), detailing symptom, root cause, date, and fix steps.
4. **`EVIDENCE ITEM` (Node 4 - Raw Proof)**: The original, raw ticket messages ingested directly from ServiceNow, Splunk, or Slack (`INC0010427`).
When an endpoint queries `"How to fix Error 503"`, or when `get_pattern_subgraph()` in `graph/queries.py` builds a pattern view, PostgreSQL executes a multi-hop recursive traversal:

```sql
WITH RECURSIVE graph_traversal AS (
  SELECT source_type, source_id, edge_type, target_type, target_id, 1 AS depth
  FROM graph_edges WHERE source_type = 'pattern' AND source_id = 'PATTERN_UUID'
  UNION ALL
  SELECT e.source_type, e.source_id, e.edge_type, e.target_type, e.target_id, gt.depth + 1
  FROM graph_edges e JOIN graph_traversal gt ON e.source_type = gt.target_type AND e.source_id = gt.target_id
  WHERE gt.depth < 3
)
SELECT * FROM graph_traversal;
```

> ⚡ **1-Line Summary**: The Context Graph stores node relationships in PostgreSQL's `graph_edges` table, allowing multi-hop recursive SQL queries to trace the 5-level operational hierarchy in under 5 milliseconds.

---

## 12. System Component Mapping

| Lifecycle Stage | System Function | Primary Module / File |
|---|---|---|
| **1. Ingestion** | Ticket receipt, PII redaction, content hashing | [`services/evidence_normalization.py`](file:///d:/ContextEdge/backend/src/contextedge/services/evidence_normalization.py) |
| **2. Storage** | Save text, vector embeddings, and FTS indexes | `evidence_items` & `evidence_chunks` tables |
| **3. Extraction** | LLM Episode extraction and schema validation | [`workers/extraction_tasks.py`](file:///d:/ContextEdge/backend/src/contextedge/workers/extraction_tasks.py) |
| **4. Graph Wiring** | Adjacency edge creation & multi-hop pattern graph | [`graph/builder.py`](file:///d:/ContextEdge/backend/src/contextedge/graph/builder.py) |
| **5. Search** | Vector search (HNSW) + FTS combined via RRF ranking | [`search/hybrid_ranker.py`](file:///d:/ContextEdge/backend/src/contextedge/search/hybrid_ranker.py) |
| **6. Traversal** | Recursive SQL CTE hierarchy & pattern graph traversal | [`graph/queries.py`](file:///d:/ContextEdge/backend/src/contextedge/graph/queries.py) |
| **7. Execution** | MAF Agent or user execution & decision logging | [`integrations/maf/provider.py`](file:///d:/ContextEdge/backend/src/contextedge/integrations/maf/provider.py) & `decisions` table |

---
