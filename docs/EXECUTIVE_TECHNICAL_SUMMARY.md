# ContextEdge — Technical Architecture & System Guide

---

## 1. What is ContextEdge? (Simple Overview)

**ContextEdge** is an AI-powered system that remembers every IT incident your company has ever faced and learns from them.

Think of it like this: Every time a server crashes or a login system breaks, engineers scramble to fix it. They search old tickets, ask senior colleagues, and dig through Slack messages. Most of this knowledge is lost after the incident is closed.

ContextEdge solves this by:
1. **Collecting** raw tickets, logs, and chat messages from ServiceNow, Splunk, Slack, and Jira
2. **Stitching** related evidence into complete incident stories (Episodes)
3. **Detecting** which problems keep repeating over months (Patterns)
4. **Creating** step-by-step fix guides (Playbooks)
5. **Feeding** this knowledge to AI agents so they can fix problems automatically next time

---

## 2. How ContextEdge Builds Knowledge (The 4-Step Lifecycle)

This is the most important part of the system. Everything in ContextEdge flows through these 4 steps:

```text
STEP 1              STEP 2              STEP 3              STEP 4
Raw Data ──────►  Episode  ──────►  Pattern  ──────►  Playbook
(Evidence)        (Incident Story)    (Repeating Problem)  (Fix Manual)
```

---

### Step 1: Evidence Collection, Normalization & Storage

**What is Evidence?**
Evidence is any raw piece of data that proves something happened — a ServiceNow ticket, a Splunk log alert, a Slack chat message, or an email.

**What happens in this step?**
ContextEdge connects to your company's tools and automatically pulls in raw data. Each piece of raw data becomes an **Evidence Item** stored in the database.

**Real-world example:**
On July 27, 2026, at 10:15 AM, the ordering system goes down. Within minutes, three things happen in three different tools:

| Tool | What gets created | Example |
|------|------------------|---------|
| ServiceNow | A support ticket is filed | `INC0010427 - "OrderApp is returning 503 errors"` |
| Splunk | A monitoring alert fires | `SPL-99812 - "2,841 SQL connection timeout errors detected"` |
| Slack | Engineers start chatting | `#inc-orders-db - "Hey team, ORDERS_DB seems stuck again"` |

ContextEdge pulls all three into the database as 3 separate **Evidence Items**. For each one, it stores:
- The original text (ticket description, log message, chat message)
- When it was created (timestamp)
- Where it came from (ServiceNow, Splunk, or Slack)
- A numeric fingerprint (vector embedding) so the system can compare it with other tickets later

**What about sensitive data?**
Before storing, ContextEdge runs a redaction service that automatically removes passwords, API keys, email addresses, and SSNs from the text. So the stored version is safe.

**What about duplicate tickets?**
Each evidence item gets a unique content hash (like a digital fingerprint). If the same ticket is synced twice, the system recognizes the duplicate and skips it.

**In simple words:** Evidence = raw proof that something happened. Like collecting witness statements at a crime scene.

**Where in the code:**
- Ingestion connectors: `connectors/servicenow/`, `connectors/gmail/`, `connectors/jira_sm/`
- PII Redaction: `services/redaction_service.py`
- Database tables: `evidence_items` (metadata + redacted text), `evidence_chunks` (searchable text segments + vector embeddings)

---

### Step 2: AI Episode Reconstruction

**What is an Episode?**
An Episode is one complete incident story. It takes those 3 scattered evidence items from Step 1 and combines them into a single, clean summary that says: "Here's what happened, why it happened, and how it was fixed."

**How does it know which evidence items belong together?**
The system looks at three clues:
1. **Same ticket number** — All three mention `INC0010427`
2. **Same time window** — All three happened within 30 minutes of each other
3. **Same servers/systems** — All three mention `ORDERS_DB` or `SQLPROD01`

When these clues match, the system groups them and creates one Episode.

**Real-world example:**

```
Episode: "Incident Analysis INC0010427 - Runaway sales report connection leak"
Date: July 27, 2026

What happened (root cause):
  Monthly sales report query held 61 SQL connections for 45 minutes,
  starving the connection pool. OrderApp health checks started failing.

Evidence collected from:
  ├── ServiceNow Ticket INC0010427 (the official IT ticket)
  ├── Splunk Log Alert SPL-99812 (the monitoring alert)
  └── Slack Thread #inc-orders-db (the engineer discussion)
```

**How does the AI extraction work?**
A background worker sends the raw ticket text to an LLM (like GPT-4). The LLM extracts:
- What broke (symptom)
- Why it broke (root cause)
- How it was fixed (resolution steps)

Then a second validation check ensures the extracted facts actually match the original text (no hallucinations).

**In simple words:** Episode = one complete incident story, built by combining clues from multiple tools. Like a detective writing one case report from witness statements, CCTV, and phone records.

**Where in the code:**
- Episode extraction worker: `workers/extraction_tasks.py`
- Correlation logic: `services/correlation_service.py`
- Database table: `episodes` (stores title, root_cause_summary, final_outcome, embedding vector)

---

### Step 3: Pattern Detection

**What is a Pattern?**
A Pattern is a repeating problem. When the system notices that the same type of incident keeps happening over weeks or months, it groups those incidents together and says: "This is a recurring issue."

**How does it detect patterns?**
The system compares all past episode stories using AI similarity. If 3 or more episodes have similar root causes (more than 85% match), they get grouped into one Pattern.

**Real-world example:**
Look at these 6 incidents that happened over the past year — all related to ORDERS_DB:

| Date | Incident | What Went Wrong |
|------|----------|----------------|
| Sep 5, 2025 | INC0005230 | Connection pool max size was set too low after deployment |
| Nov 28, 2025 | INC0006110 | Database backup job locked the pool during peak hours |
| Jan 19, 2026 | INC0007204 | Flash sale traffic surge exceeded the pool limit |
| Apr 2, 2026 | INC0008431 | OrderApp thread pool deadlock leaked SQL connections |
| Jun 14, 2026 | INC0009812 | Unindexed batch query consumed all available connections |
| Jul 27, 2026 | INC0010427 | Runaway sales report held 61 connections for 45 minutes |

The triggers are different each time, but the core problem is always the same: **ORDERS_DB runs out of SQL connections.**

ContextEdge groups these 6 episodes into one Pattern:

```
Pattern: "Database server unresponsive / connection pool exhaustion on ORDERS_DB"
Confidence: 88%
Episode Count: 6
Date Range: September 2025 — July 2026

Common triggers found:
  • Runaway queries holding too many connections
  • Backup jobs colliding with peak traffic
  • Connection pool max size set too low

Common fix steps found:
  1. Check active SQL sessions and pool usage
  2. Kill the runaway query
  3. Restart the SQL Server service
  4. Recycle the application pool
```

**In simple words:** Pattern = a repeating problem that keeps coming back. Like a hospital noticing that every winter, 20 patients come in with the same flu symptoms — so they create a standard treatment plan.

**Where in the code:**
- Pattern detection worker: `workers/pattern_tasks.py`
- Database table: `patterns` (stores title, confidence, episode_count, trigger_conditions, root_causes)
- Links: `pattern_evidence_links` (maps pattern → episode → evidence)

---

### Step 4: Playbook Creation

**What is a Playbook?**
A Playbook is a verified, step-by-step fix manual for a specific repeating problem. Once a Pattern is detected, the system drafts a Playbook. A senior engineer reviews and approves it. After approval, AI agents can use this playbook to fix the problem automatically next time.

**Real-world example:**

```
Playbook: "Recover ORDERS_DB from connection-pool exhaustion"
Risk Level: High (requires approval before execution)
Status: Approved by Senior DBA
Linked Pattern: "Database server unresponsive on ORDERS_DB"

Steps:
  Step 1: Capture active SQL sessions and connection pool stats
  Step 2: Identify and kill the confirmed runaway query
  Step 3: After emergency approval, restart MSSQLSERVER on SQLPROD01
  Step 4: Recycle the orders-prod IIS application pool on APPPROD02
  Step 5: Verify OrderApp health checks return HTTP 200
```

**Who uses the Playbook?**
- **Human engineers** can look up the playbook on the dashboard during an incident
- **AI agents** (via Microsoft Agent Framework) can read and execute the playbook steps automatically

**In simple words:** Playbook = a verified fix manual. Like a fire department's standard operating procedure — when there's a kitchen fire, follow these exact steps in this exact order.

**Where in the code:**
- Database tables: `playbooks` (metadata), `playbook_versions` (versioned steps)
- Graph edge: `playbook ──addresses──► pattern`

---

### How It All Connects (The Complete Picture)

```text
STEP 1: Raw Data Comes In
  ServiceNow Ticket ─┐
  Splunk Log Alert   ─┼──► These are EVIDENCE items (raw proof)
  Slack Chat Thread  ─┘

STEP 2: AI Groups Related Evidence
  Evidence + Evidence + Evidence ──► This becomes an EPISODE (one incident story)

STEP 3: System Finds Repeating Problems
  Episode + Episode + Episode + ... ──► This becomes a PATTERN (repeating issue)

STEP 4: Fix Guide is Created
  Pattern ──► This gets a PLAYBOOK (step-by-step fix manual)
```

On the dashboard graph view, it looks like this (read left to right):

```text
[ PLAYBOOK ] ──► [ PATTERN ] ──► [ EPISODE ] ──► [ EVIDENCE ]
   (fix guide)    (repeating     (one incident    (raw ticket,
                   problem)       story)           log, chat)
```

---

## 3. System Architecture

### Architectural Model
ContextEdge is a **Modular Monolith** — one FastAPI backend, one PostgreSQL database, one deployment. All core logic runs together in one application.

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

**Why this design?**
- All data lives in one database — no network calls between microservices
- Graph queries run in under 5 milliseconds
- Simple to deploy and manage

---

## 4. User Roles & Access Controls

ContextEdge has 5 user roles:

| Role | Who is this person? | What can they do? |
|------|-------------------|------------------|
| **Support Engineers** | L1/L2 Support | Search for playbooks and evidence during active incidents |
| **Knowledge Managers** | L3 SMEs / Senior Engineers | Review, edit, and approve AI-generated episodes and playbooks |
| **Domain Admins** | Team Leads | Configure which channels and data sources to ingest |
| **Tenant Admins** | Platform Admins | Manage security settings, user roles, API keys, and LLM budgets |
| **AI Service Accounts** | Autonomous Systems | Query ContextEdge memory and execute approved playbooks automatically |

---

## 5. Microsoft Agent Framework (MAF) Integration

**What is MAF?**
MAF is Microsoft's framework for building AI agents that can think, plan, and take actions.

**What does ContextEdge do with MAF?**
ContextEdge acts as the **operational memory** for MAF agents. Without ContextEdge, an AI agent has no idea what happened in your company before. With ContextEdge, the agent wakes up already knowing all past incidents and approved fix steps.

---

### How Many MAF Agents Are Supported?

ContextEdge supports **4 distinct MAF Agent Roles** (all governed by the `maf.v1` projection profile):

| Agent Role | What it does | Real-World Example |
|------------|-------------|-------------------|
| **1. Operational Resolution Agent** | Diagnoses active incidents by fetching matching graph context | During an outage, reads past episodes and recommends the right playbook |
| **2. Playbook Execution Agent** | Executes approved remediation steps safely step by step | Runs database query kill commands or restarts services per approved playbook |
| **3. Audit & Compliance Agent** | Logs every decision, policy check, and tool call into PostgreSQL | Ensures all AI actions have a complete audit trace for compliance teams |
| **4. Diagnostic & Analysis Agent** | Evaluates incoming evidence, claims, and flags contradictions | Detects if a newly proposed fix contradicts an existing security policy |

---

### What Tools & Mechanisms Are Exposed to MAF Agents?

ContextEdge exposes **2 core MAF mechanisms** via `ContextGraphMAFPlugin` (`integrations/maf/plugin.py`):

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ContextGraphMAFPlugin                                 │
│                                                                             │
│  ┌─────────────────────────────────────┐   ┌─────────────────────────────┐  │
│  │ 1. Proactive Memory Provider        │   │ 2. On-Demand Graph Tool     │  │
│  │    (ContextProvider / before_run)   │   │    (@tool query_context)    │  │
│  │  Injects graph memory into prompt   │   │  Agent queries graph at run │  │
│  └─────────────────────────────────────┘   └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Mechanism 1: Proactive Memory Provider (`ContextGraphProvider`)
- **How it works:** Before the agent runs a step (`before_run` hook), this provider automatically inspects the user's latest query, retrieves a bounded memory subgraph from ContextEdge, and injects it into the LLM system prompt.
- **Result:** The agent receives relevant memory **automatically before it even asks**.

#### Mechanism 2: On-Demand Graph Tool (`query_context_graph`)
- **How it works:** A Python function decorated with `@tool("query_context_graph")` exposed to MAF agents.
- **Parameters:**
  - `query`: The question or task (e.g. `"What is the fix for ORDERS_DB connection leak?"`)
  - `seeds`: Optional node IDs (e.g. specific Episode or Pattern UUIDs)
  - `entities`: System/server names (e.g. `["ORDERS_DB", "SQLPROD01"]`)
  - `max_depth`: Depth limit (1 to 3 hops)
- **Result:** If the agent needs deeper details mid-reasoning, it can invoke this tool dynamically.

---

### Memory Safety & Guardrails (`maf.v1` Profile)

To prevent AI agents from getting confused or exceeding token limits, ContextEdge strictly bounds MAF memory under the `maf.v1` profile:

- **Max Nodes**: 60 nodes max per query
- **Max Relationships**: 120 edges max
- **Max Depth**: 3 relationship hops max
- **Max Characters**: 30,000 characters max

---

**Summary:**
- **Without MAF integration:** The web dashboard still works for humans, but AI agents are "blind" to company history.
- **With MAF integration:** 4 MAF agent roles use 2 memory mechanisms (Proactive Provider + On-Demand Tool) under strict safety caps to automate IT incident resolution.

---

## 6. Multi-Topic Filtering & Quality Control

Sometimes a single ticket contains unrelated topics (e.g., a printer issue mixed into a VPN ticket). ContextEdge handles this with 4 safeguards:

1. **Vector Distance Check**: If two items are less than 70% similar, they are separated
2. **LLM Smart Extraction**: The AI is told to ignore off-topic comments
3. **Contradiction Detection**: If extracted facts conflict with each other, the item goes to the Review Queue for a human to check
4. **Negative Knowledge**: When a reviewer says "Evidence B is NOT related to Incident A", the system remembers this and won't make the same mistake again

---

## 7. Context Graph & Database Storage

### How the Knowledge Graph Works
ContextEdge stores relationships between all its data in a graph structure:

```text
[ PLAYBOOK ] ──(addresses)──► [ PATTERN ] ──(clusters)──► [ EPISODE ] ──(derived_from)──► [ EVIDENCE ]
```

These relationships are stored in a PostgreSQL table called `graph_edges`:

```sql
CREATE TABLE graph_edges (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  source_type VARCHAR(50),   -- 'evidence', 'episode', 'pattern', 'playbook'
  source_id UUID,
  edge_type VARCHAR(50),     -- 'part_of', 'derived_from', 'clusters', 'addresses'
  target_type VARCHAR(50),
  target_id UUID
);
```

When someone asks "How to fix Error 503?" or opens the Pattern graph view, PostgreSQL runs a multi-hop traversal query that walks through these edges to find all connected nodes in under 5 milliseconds.

---

## 8. Prometheus Monitoring

Prometheus tracks system health:
- **API Performance**: Request counts, response times, error rates
- **AI Model Metrics**: LLM token usage, request duration, cost per tenant
- **System Health**: Database connection pool usage, worker queue depth

---

## 9. System Component Map

| Step | What Happens | Code Location |
|------|-------------|--------------|
| 1. Ingestion | Pull tickets from ServiceNow, Slack, etc. | `connectors/servicenow/`, `connectors/gmail/` |
| 2. Redaction | Remove passwords, emails, SSNs | `services/redaction_service.py` |
| 3. Storage | Save text + vector embeddings | `evidence_items` & `evidence_chunks` tables |
| 4. Episode Extraction | AI converts raw tickets into structured stories | `workers/extraction_tasks.py` |
| 5. Pattern Detection | Group similar episodes into patterns | `workers/pattern_tasks.py` |
| 6. Graph Wiring | Create edges between nodes | `graph/builder.py` |
| 7. Search | Vector search + full-text search combined | `search/hybrid_ranker.py` |
| 8. Graph Traversal | Multi-hop SQL queries for graph views | `graph/queries.py` |
| 9. MAF Agent Memory | Feed knowledge to AI agents | `integrations/maf/provider.py` |
| 10. Decision Logging | Record AI/human decisions for audit | `services/decision_trace_service.py` |

---
