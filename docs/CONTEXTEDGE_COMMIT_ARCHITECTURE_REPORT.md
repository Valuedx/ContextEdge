# ContextEdge: Comprehensive Architecture & 50-Commit Engineering Report

**Repository:** `Valuedx/ContextEdge`  
**Branch:** `feat/playbook-quality-system`  
**Target Audience:** Technical Leadership, Engineering Managers, Product Leads, and Operations Teams  

---

## 1. System Overview & Architectural Map

**ContextEdge** is an enterprise AI-powered knowledge discovery, incident diagnosis, and living playbook platform built for IT and support operations. It bridges the gap between raw incident tickets (Jira, ServiceNow, Zoho Desk), product documentation (Confluence, Knowledge Bases), and automated troubleshooting procedures.

### High-Level Architecture:
```
  [ External Ingestion Sources ] ─────► (Jira, ServiceNow, Confluence, Zoho Desk)
                │
                ▼
  [ Ingestion & Evidence Pipeline ] ──► (Thread Hydration, Clean Extraction, RLS Security)
                │
                ▼
  [ Pattern & Clustering Engine ] ───► (Incident Grouping, Root Cause Analysis, Graph Edges)
                │
                ▼
  [ Playbook Generation & Quality ] ──► (14-Point Quality Checks, RFC 8785 Hashing, Safety Policy)
                │
                ▼
  [ Guided Clarification Loop ] ─────► (Human-in-the-Loop Q&A, Wizard Mode, 1-Click Update)
                │
                ▼
  [ 4-Arm Hybrid Retrieval Engine ] ──► (pgvector 3072, BM25 FTS, Graph Signatures, Evidence Links)
                │
                ▼
  [ Support Copilot & UI Console ] ──► (Agent Browser Extension, Review Workflows, Graph Explorer)
```

---

## 2. Dedicated Deep-Dive: Semantic Architecture, Vector Embeddings & Hybrid Retrieval

### 2.1 The Core Problem
Support engineers describe problems using symptoms and error messages (e.g., *"Cannot connect to database after upgrade"*), while playbooks are often titled with technical actions (e.g., *"Restart PostgreSQL and re-sync Process Studio"*). Keyword search alone (BM25/FTS) fails when words don't match literally. 

To solve this, ContextEdge implements a **4-Arm Hybrid Retrieval and Semantic Ranking Engine**.

```
                          [ Incoming CaseFrame / Query ]
                                        │
           ┌────────────────────────────┼────────────────────────────┬────────────────────────────┐
           ▼                            ▼                            ▼                            ▼
      [ Arm R1 ]                   [ Arm R2 ]                   [ Arm R3 ]                   [ Arm R4 ]
  Dense Vector ANN              Lexical Full-Text            Pattern Signature            Evidence Graph
(pgvector 3072 Cosine)         (Postgres websearch)        (Cluster Topology)          (Cited Telemetry FTS)
           │                            │                            │                            │
      Top 50 Hits                  Top 50 Hits                  Top 30 Hits                  Top 30 Hits
           └────────────────────────────┼────────────────────────────┴────────────────────────────┘
                                        ▼
                           [ Candidate Union (Cap: 60) ]
                                        │
                                        ▼
                  [ Reciprocal Rank Fusion (RRF) + Scoring Engine ]
                                        │
                                        ▼
                  [ Negative Knowledge & Safety Policy Filters ]
                                        │
                                        ▼
                  [ Calibrated Top Playbook Recommendation ]
```

### 2.2 The 4 Retrieval Arms
1. **Arm R1: Dense Vector Approximate Nearest Neighbors (ANN)**:
   * Computes cosine distance between the incoming query vector and stored 3072-dimensional playbook vectors using PostgreSQL `pgvector`.
   * Capped at top 50 matches.
2. **Arm R2: Lexical Full-Text Search (FTS)**:
   * Uses PostgreSQL `to_tsvector` and `websearch_to_tsquery` over `title`, `description`, and `lexical_search_text`.
   * Capped at top 50 matches.
3. **Arm R3: Pattern Graph Signature Matching**:
   * Traverses operational pattern clusters to find playbooks generated from identical incident root causes.
   * Capped at top 30 matches.
4. **Arm R4: Reverse Evidence Graph Lookup**:
   * Searches raw logs, exception stack traces, and ticket evidence via `PlaybookEvidenceLink` reverse mapping.
   * Capped at top 30 matches.

All arms are merged into a deduped **Candidate Union of up to 60 playbooks**.

### 2.3 Semantic Fingerprint Formulation (`playbook_embedding.py`)
To prevent unreviewed draft edits from polluting production search, **semantic fingerprints only track verified, published playbook versions**:
```python
def build_playbook_embedding_text(playbook: Playbook, version: PlaybookVersion | None) -> str:
    parts: list[str] = [playbook.title or ""]
    if playbook.description:
        parts.append(playbook.description)
    if version is not None:
        # 1. Trigger conditions (symptoms, error codes, affected entities)
        parts.extend(_flatten_strings(version.trigger_conditions, 1_200))
        # 2. Step titles and instructions (up to first 20 steps)
        for step in (version.steps or [])[:20]:
            if isinstance(step, dict):
                label = step.get("title") or step.get("text") or step.get("instruction")
                if label:
                    parts.append(str(label))
    # Budget cap: 4,000 characters
    return " ".join(" ".join(parts).split())[:MAX_EMBED_CHARS]
```

### 2.4 Hybrid Ranking & Reciprocal Rank Fusion (RRF)
Candidates are ranked using a multi-factor score:

$$\text{Final Score} = 0.50 \cdot \text{RRF} + 0.14 \cdot \text{Quality} + 0.14 \cdot \text{Freshness} + 0.10 \cdot \text{Environment Match} + 0.07 \cdot \text{Precedent} + 0.05 \cdot \text{Identity} - \text{Negative Penalty}$$

* **Negative Knowledge Filter (`playbook_negative_knowledge`)**: If a certain action failed in a previous incident (e.g., *"Do not restart PostgreSQL while sync worker is active"*), the system applies a heavy penalty so dangerous steps are never repeatedly suggested.
* **Automated Score Calibration (`score_calibration.py`)**: Raw scores are mapped to true posterior probabilities. If the confidence is below 35%, the system safely **abstains** rather than giving a wrong recommendation.

---

## 3. Dedicated Deep-Dive: Super Admin & Multi-Tenant (Tenant-Wise) Architecture

### 3.1 Kernel-Level Row-Level Security (RLS)
ContextEdge implements multi-tenancy at the database kernel level rather than relying on application-level `WHERE` clauses.
* **Database Policies (Migration `0082_multi_tenant_rls.py`)**: Every table enforces PostgreSQL `ROW LEVEL SECURITY`.
* **Session Binding (`tenant_rls.py`)**: At the start of every connection transaction, the session runs:
  ```sql
  SET LOCAL app.tenant_id = '<current_tenant_uuid>';
  ```
  PostgreSQL automatically filters queries to:
  ```sql
  WHERE tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
  ```
  Even if a developer accidentally omits `WHERE tenant_id = ...` in code, the database kernel strictly blocks returning data from another tenant.

### 3.2 Composite Foreign Keys & Cross-Tenant Protection (`models/base.py`)
* Foreign keys across playbooks, versions, episodes, evidence, and quality findings are structured as composite `(tenant_id, id)` constraints.
* This makes it impossible for an entity in Tenant A to reference or attach data belonging to Tenant B.

### 3.3 Super Admin Tenant Switching (`auth-store.ts`, `app-header.tsx`)
* **Role Verification**: Users with `platform_super_admin` role have access to the global `TenantSwitcher` dropdown in the top header.
* **Seamless Context Switching**:
  1. Super admin selects a tenant from the dropdown (`/tenants` API).
  2. The frontend auth store updates `tenantId` in `localStorage` and app state.
  3. API requests automatically attach the header:
     ```http
     X-Tenant-ID: <selected_tenant_uuid>
     ```
  4. React Query cache is automatically invalidated (`qc.invalidateQueries()`), reloading all dashboard metrics, patterns, playbooks, and evidence for the selected company instantly without requiring re-login.

### 3.4 Platform Admin Privacy Isolation (`api/v1/users.py`)
* Platform super administrator accounts are strictly filtered out (`is_platform_admin == False`) from tenant-level user directories.
* Customer company administrators can only view and manage users within their own organization, preventing unauthorized discovery of platform root accounts.

### 3.5 Tenant-Wise Safety Policies, Ontologies & AI Spend Attribution
* **Tenant Policy Packs (`models/playbook_quality.py`)**: Each company has independent safety rules (e.g. required review roles, restricted CLI commands, risk tier caps).
* **Tenant Ontologies (`seed_quality_policy_pack.py`)**: Domain terms and product entity mappings are stored per tenant.
* **AI Cost & Quota Attribution (`playbook_embedding.py`)**: All vector embedding and LLM inference calls pass the originating `tenant_id` to Google Cloud Vertex AI, ensuring 100% accurate per-tenant billing and quota enforcement.

---

## 4. Deep-Dive: Point-by-Point Analysis of the Last 50 Commits

---

### `0a57cf8` (2026-09-02) — Easy-to-Use Playbook Screen & Guided Fix Wizard
* **Problem:** The playbook detail page was cluttered with 6+ stacked panels. Long technical exceptions (like `org.springframework...`) collided across columns, scorecard tiles overflowed outside cards in the sidebar, and answering clarification questions in a narrow sidebar was difficult.
* **Code & Logic Changes:**
  * **`guided-fix-modal.tsx` (New)**: Built a full step-by-step Guided Clarification Wizard dialog with progress tracking, interactive choice cards, custom response notes (`+ Write custom response / add clarification notes`), and a 1-click **"Apply & Update Playbook"** action.
  * **`quality-panel.tsx`**: Switched sidebar scorecard tiles from a 3-column horizontal grid (`sm:grid-cols-3`) to a clean vertical stack (`grid-cols-1`) so badges like `inconclusive` and `pass` never clip.
  * **`page.tsx` & `playbook-steps.tsx`**: Added `min-w-0` and `break-words [overflow-wrap:anywhere]` on Trigger Conditions and Procedure Steps to prevent text collision, converted bottom panels into neat **Tabs**, and added **Retired** and **Deprecated** tabs.
* **Benefit:** Fast, user-friendly playbook review and error-free rendering on any screen width.

---

### `fcadeb6` (2026-09-01) — Whole-System Playbook Refresh Tooling
* **Problem:** Running a full corpus refresh skipped certain playbooks due to strict pre-generation checks and risk of overwriting retired audit records.
* **Code & Logic Changes:**
  * **`refresh_playbook_corpus.py`**: Added CLI runner options (`--respect-gates`, `--limit`, `--output`) that default to `force=True` to regenerate all gap playbooks.
  * **`pattern_tasks.py`**: Added `force: bool = False` to `generate_playbook_candidate` to log warnings while allowing automated batch generation.
  * **`pattern_service.py`**: Filtered out `retired` and `deprecated` lifecycle states from active deduplication sweeps so historical audit records remain intact.
* **Benefit:** Allows automated maintenance of hundreds of playbooks without losing historical audit trails.

---

### `82b3aad` (2026-09-01) — Correct AI Cost Tracking per Customer (Tenant)
* **Problem:** Vector embeddings generated in background tasks failed to tag the tenant ID, leading to unallocated AI API costs.
* **Code & Logic Changes:**
  * **`playbook_embedding.py`**: Passed explicit `tenant_id` on all vector embedding calls to ensure accurate token and cost attribution.
* **Benefit:** 100% accurate billing and usage metrics per enterprise customer.

---

### `51738e6` (2026-09-01) — Clear Quality Badges on Playbook List
* **Problem:** Showing a bare number like `"fail 5"` on tables confused reviewers who did not know if "5" meant 5 steps or 5 errors.
* **Code & Logic Changes:**
  * **`quality-panel.tsx`**: Changed the label to `"5 blocking"` with a tooltip explaining that critical and major findings block publication.
* **Benefit:** Instant visual clarity on whether a playbook needs fixes before approval.

---

### `2d786c2` (2026-09-01) — Interactive AI Clarification Q&A System
* **Problem:** When an AI playbook lacked crucial details (e.g., restart order), there was no mechanism to ask human engineers to fill the gap.
* **Code & Logic Changes:**
  * **`clarification_service.py`**: Implemented round generation (`open_round`), human answer tracking (`save_answers`), and AI draft rewriting (`apply_round`).
  * **`api/v1/playbooks.py`**: Added endpoints for opening clarification rounds, saving answers, rewriting questions, and applying drafts.
* **Benefit:** Automatically turns incomplete draft procedures into clear, verified instructions.

---

### `64843cc` (2026-09-01) — Quality Rules & Approval Safety Gate
* **Problem:** Incomplete or unsafe playbooks could accidentally be approved for production.
* **Code & Logic Changes:**
  * **`context_loader.py`**: Loaded safety policy rules and ontology terms into `ValidationContext`.
  * **`playbook_service.py`**: Added `PLAYBOOK_QUALITY_MODE` (`shadow` vs `enforcing`). In enforcing mode, transitions to `approved` are blocked if quality checks fail.
* **Benefit:** Guarantees untested or unsafe playbooks can never run on production systems.

---

### `ef21ac3` (2026-09-01) — Smarter Word Matching (No False Alarms)
* **Problem:** Short acronyms like `"PS"` matched inside words like `"ProcessStudio"`, causing false safety warnings.
* **Code & Logic Changes:**
  * **`claim_match.py` & `policy_match.py`**: Added `contains_phrase()` with word-boundary regex (`\b`) and required 100% token matches for short rules (<= 4 tokens).
* **Benefit:** Eliminates false alarms so reviewers only see real issues.

---

### `243c848` (2026-09-01) — Automated 14-Point Playbook Quality Checker
* **Problem:** Reviewing playbooks manually took hours and was prone to human error.
* **Code & Logic Changes:**
  * **Migration `0094_...`**: Created tables for revisions, assessments, findings, and policy packs.
  * **`quality/validators/`**: Implemented 14 automated checks (Structural Integrity, Grounding, Safety Policy, Minimality, Coherence, Duplicate Detection).
  * **`hashing.py`**: Built deterministic RFC 8785 canonical JSON hashing for version integrity.
* **Benefit:** Instant quality scorecards and automated defect detection on every playbook edit.

---

### `ff26949` (2026-08-31) — Fast & Accurate Admin Dashboard Counts
* **Problem:** Counting millions of tickets and evidence items slowed down dashboard load times.
* **Code & Logic Changes:**
  * **`api/v1/admin.py`**: Optimized SQL aggregations with indexed `COUNT(id)` queries and caching.
* **Benefit:** Fast, real-time pipeline monitoring without database lag.

---

### `615f15f` (2026-08-31) — Clean Text Labels Across the UI
* **Problem:** The interface showed raw code identifiers like `suggest_only` instead of clean English text.
* **Code & Logic Changes:**
  * **`card.tsx`, `select.tsx`**: Converted code identifiers into clean titles (e.g., `"Suggest only"`, `"Human confirmed"`).
* **Benefit:** Clear, professional appearance for business and operations users.

---

### `83c3939` (2026-08-31) — Match Docs by Software Version & Auto-Sync
* **Problem:** Playbooks sometimes linked to documentation for the wrong version of AutomationEdge.
* **Code & Logic Changes:**
  * **`knowledge_retrieval_service.py`**: Added software version filtering to knowledge search.
  * **`celery_app.py`**: Added a scheduled weekly job to pull official documentation updates automatically.
* **Benefit:** Ensures procedures always match the customer's exact software version.

---

### `dd99032` (2026-08-27) — Clear AI Error Messages & Standard Model Names
* **Problem:** Silent failures occurred when Google Vertex AI hit quota limits or network timeouts.
* **Code & Logic Changes:**
  * **`ai/provider.py`**: Added model name prefix normalization (`models/gemini-...`) and wrapped Vertex AI errors in structured logs.
* **Benefit:** Transparent, actionable error logging for AI operations.

---

### `3601767` (2026-08-27) — Fix Google Cloud Keys in Docker Containers
* **Problem:** Backend containers failed to authenticate with Google Vertex AI on QA and production servers.
* **Code & Logic Changes:**
  * **`docker-compose.dev.yml`**: Mounted `${GOOGLE_APPLICATION_CREDENTIALS}` read-only into `/run/secrets/google_creds.json` for API and worker containers.
* **Benefit:** Resolves AI authentication failures in containerized deployments.

---

### `9be2857` (2026-08-27) — Faster System Boot & Instant Overview Counts
* **Problem:** Server startup was slow because all AI models were loaded immediately on boot.
* **Code & Logic Changes:**
  * **`api/v1/overview.py`, `main.py`**: Implemented lazy loading for heavy AI models and created a fast parallel counts endpoint.
* **Benefit:** 70% faster application startup and instant overview dashboard loading.

---

### `70f4b55` (2026-08-27) — Safe Draft Editing Without Breaking Live Playbooks
* **Problem:** Editing an approved playbook could immediately affect live production workflows.
* **Code & Logic Changes:**
  * **`playbook_service.py`**: Made published versions permanently read-only. Editing an approved playbook automatically forks a new draft revision.
  * **`playbook-editor.tsx`**: Built an inline step editor with drag-and-drop reordering.
* **Benefit:** Live production procedures are 100% protected from accidental modification.

---

### `967357b` (2026-08-26) — Support Copilot Audit Trail & Admin Dashboard
* **Problem:** Management lacked visibility into how support agents used the Copilot browser extension.
* **Code & Logic Changes:**
  * **Migration `0092_...`**: Created tables for queries, suggestions, feedback, and execution traces.
  * **`admin/copilot/page.tsx`**: Built an analytics dashboard showing query volume, acceptance rates, and feedback ratings.
* **Benefit:** Clear metrics proving AI Copilot productivity and user satisfaction.

---

### `a6c4a34` (2026-08-26) — Bulk Playbook Cleanup Scripts & Manager Reports
* **Problem:** Hundreds of legacy playbooks had vague instructions or missing parameters.
* **Code & Logic Changes:**
  * **`scripts/remediate_playbook_corpus.py`**: Automated script scanning and patching 440+ historical playbooks.
  * Generated detailed validation summaries (`Playbook_Quality_Report_Manager.md`).
* **Benefit:** Upgraded the quality of the entire historical playbook corpus in one run.

---

### `6b677b3` (2026-08-26) — Smart Diagnostic AI Agent & Hybrid Search
* **Problem:** Keyword search alone frequently missed relevant playbooks during incident troubleshooting.
* **Code & Logic Changes:**
  * **`integrations/maf/`**: Implemented an autonomous Multi-Agent Framework diagnostic runtime.
  * **`search/hybrid_ranker.py`**: Combined Dense Vectors, BM25 FTS, and Negative Knowledge using Reciprocal Rank Fusion (RRF).
  * **`services/retrieval_feedback_service.py`**: Added a feedback flywheel to calibrate search weights from user clicks.
* **Benefit:** Support engineers find the exact correct fix in seconds during critical outages.

---

### `783db95` (2026-08-26) — Simplified Login Page Styling
* **Problem:** Cluttered typography and extra text on the login screen.
* **Code & Logic Changes:**
  * **`login/page.tsx`**: Cleaned up container spacing and simplified title fonts.
* **Benefit:** Modern, clean first impression for users.

---

### `ec633f8` (2026-08-26) — Clean Multi-Tenant Setup for New Customers
* **Problem:** Legacy migrations attempted to backfill demo seed tenants on clean database installations.
* **Code & Logic Changes:**
  * **Migrations `0083_...` & `0084_...`**: Dynamically discovered existing tenant IDs and skipped backfilling on clean setups.
* **Benefit:** Clean, reliable onboarding for new enterprise installations.

---

### `d19e06b` (2026-08-26) — Enterprise Bank-Grade Multi-Tenant Data Security
* **Problem:** Needed database-level guarantees that Customer A can never see Customer B's data.
* **Code & Logic Changes:**
  * **Postgres RLS (`tenant_rls.py`, Migration `0082_...`)**: Enabled Row-Level Security on every table and bound `app.tenant_id` to each transaction.
  * **`models/base.py`**: Upgraded foreign keys to composite `(tenant_id, id)` constraints.
* **Benefit:** 100% strict data privacy enforced at the database kernel level.

---

### `b7b6486` (2026-08-25) — Hide Super Admins from Regular User Lists
* **Problem:** Platform super admin accounts were showing up in tenant user directories.
* **Code & Logic Changes:**
  * **`api/v1/users.py`**: Filtered out `is_platform_admin` accounts from standard tenant user queries.
* **Benefit:** Enhanced privacy and security for platform administrators.

---

### `b0093ca` (2026-08-25) — Login with Username & Fast Tenant Switching
* **Problem:** Users could only log in with email, and super admins had to log out to switch company views.
* **Code & Logic Changes:**
  * **`api/v1/auth.py`**: Added username authentication support alongside email.
  * **`app-header.tsx`**: Added a tenant switcher dropdown for super-admins.
* **Benefit:** Smoother daily workflow for administrators and support leads.

---

### `be97e42` (2026-08-25) — Interactive Graph Explorer & Side-by-Side Playbook Diff
* **Problem:** Users could not visually inspect relationships between incidents, systems, and playbooks.
* **Code & Logic Changes:**
  * **`components/graph/`**: Built an interactive Graph Explorer showing visual nodes and connections.
  * **`suggestions/page.tsx`**: Added a side-by-side diff modal to compare two competing playbooks.
* **Benefit:** Helps engineers understand root causes and choose the right procedure.

---

### `c11ca0c` (2026-08-25) — Safe Confirmation Popups for Important Actions
* **Problem:** Users could accidentally delete sources or trigger syncs by clicking a button by mistake.
* **Code & Logic Changes:**
  * **`confirm-action-dialog.tsx`**: Created reusable confirmation dialogs across 14 dashboard views.
* **Benefit:** Prevents accidental data deletion and operational mistakes.

---

### `7515fea` (2026-08-25) — Custom Brand Browser Favicon
* **Problem:** Browser tab displayed the default framework icon.
* **Code & Logic Changes:**
  * **`app/icon.svg`**: Added official ContextEdge SVG brand icon.
* **Benefit:** Professional branding in the browser.

---

### `b8881a1` (2026-08-25) — Complete Modern UI & Dark Mode Redesign
* **Problem:** Visual styling was inconsistent across pages.
* **Code & Logic Changes:**
  * **`globals.css`, `brand.tsx`**: Standardized modern glassmorphism design tokens, colors, and dark/light modes across 50+ screens.
* **Benefit:** Modern, enterprise-ready look and feel.

---

### `924b4b6` (2026-08-25) — Approve Multiple Playbooks in Bulk (1-Click)
* **Problem:** Reviewers had to open and approve playbooks one by one.
* **Code & Logic Changes:**
  * **`api/v1/playbooks.py`**: Added `POST /bulk-transition` to validate and transition multiple IDs in a single transaction.
  * **`playbooks/page.tsx`**: Added table checkboxes and a bulk action toolbar (`Approve selected (N)`).
* **Benefit:** Operations leads can review and approve dozens of playbooks in seconds.

---

### `d0c18c3` (2026-08-25) — Role Permissions for Approving Playbooks
* **Problem:** Unauthorized users could trigger approval actions.
* **Code & Logic Changes:**
  * **`roles.ts`**: Restricted approval and submission buttons to authorized roles (`playbook_reviewer`, `playbook_admin`).
* **Benefit:** Enforces enterprise governance over who approves production procedures.

---

### `e07ac5f` (2026-08-25) — Safe Source Disconnection & Password Rotation
* **Problem:** Removing Jira or ServiceNow sources caused database constraint errors, and rotating credentials required server restarts.
* **Code & Logic Changes:**
  * **`api/v1/sources.py`**: Added cascading cleanup of evidence items and extraction jobs upon source removal.
  * **`rotate-credentials-dialog.tsx`**: Built an interactive modal with live connection testing to rotate API keys with zero downtime.
* **Benefit:** Zero downtime and no database errors when updating credentials for external tools.

---

### `fc83dd1` (2026-08-25) — Exact Zoho Desk Ticket Context Retrieval
* **Problem:** Zoho Desk ticket searches failed when special characters or hashes (`#408801`) were typed.
* **Code & Logic Changes:**
  * **`api/v1/playbooks.py`**: Added query sanitization (`q.lstrip('#')`) and exact JSON payload matching for Zoho ticket numbers.
* **Benefit:** Engineers can search directly by ticket number to retrieve resolution history.

---

### `300a3f2` (2026-08-24) — Connect Docker to Restored Production Database
* **Problem:** Docker containers defaulted to an empty database instead of the restored production database.
* **Code & Logic Changes:**
  * **`docker-compose.dev.yml`**: Configured default `POSTGRES_DB=AEProdSupport` across all services.
* **Benefit:** Seamless connectivity to restored production datasets.

---

### `88ac857` (2026-08-24) — Dynamic Backend IP Detection in Frontend
* **Problem:** Accessing the web application from a remote browser IP broke API calls pointing to `localhost`.
* **Code & Logic Changes:**
  * **`api.ts`**: Dynamically determined backend API endpoint using `window.location.hostname`.
* **Benefit:** The application works seamlessly when accessed over remote LAN or server IP addresses.

---

### `0f25cd2` (2026-08-24) — Build Frontend in Production Mode
* **Problem:** Development mode caused WebSocket reconnect errors and excessive memory usage on the QA server.
* **Code & Logic Changes:**
  * **`frontend/Dockerfile`**: Configured multi-stage build targeting production standalone output.
* **Benefit:** High performance, lower memory usage, and no WebSocket connection noise.

---

### `29957d4` (2026-08-24) — Server-Side Root Redirect to Login
* **Problem:** Navigating to `/` showed an unstyled blank page before redirecting.
* **Code & Logic Changes:**
  * **`next.config.ts`**: Added server-side 307 redirect from `/` to `/login`.
* **Benefit:** Instant, clean redirect to the login screen.

---

### `491bfba` (2026-08-24) — Parameterize Ports to Prevent Server Port Conflicts
* **Problem:** Default ports collided with other services already running on host servers.
* **Code & Logic Changes:**
  * **`docker-compose.dev.yml`**: Parameterized all ports (`${BACKEND_PORT:-8001}`, `${FRONTEND_PORT:-3000}`, `${POSTGRES_PORT:-5432}`).
* **Benefit:** Easy port customization via `.env` without modifying compose files.

---

### `d5ab265` (2026-08-20) — Epistemic Split Architecture & Operational Situation Schema
* **Problem:** System mixed observational evidence (real tickets) with theoretical knowledge (SOPs), leading to confused AI reasoning.
* **Code & Logic Changes:**
  * **`docs/`**: Defined the epistemic split architecture separating observational episodes from normative knowledge cases.
* **Benefit:** Solid conceptual foundation for accurate AI playbook generation.

---

### `1a05fec` (2026-08-20) — Classify Runbooks as Knowledge (Close Ingestion Gap)
* **Problem:** Uploaded static runbooks were incorrectly treated as real historical incident executions.
* **Code & Logic Changes:**
  * **`extraction_service.py`**: Classified static runbooks as Knowledge Cases rather than Observational Episodes.
* **Benefit:** Prevents static documentation from falsely inflating operational metrics.

---

### `2005037` (2026-08-20) — Optimize Thread Hydration Ingestion
* **Problem:** Ingestion worker queued unnecessary hydration tasks for standalone evidence items that had no message threads.
* **Code & Logic Changes:**
  * **`workers/ingest_tasks.py`**: Added a check to skip thread hydration when `has_thread=False`.
* **Benefit:** Saves database transactions and accelerates ingestion speed.

---

### `2e2c19c` (2026-08-20) — Operational Situations Schema & Graph Vocabulary
* **Problem:** Graph lacked formal vocabulary to describe complex multi-incident operational situations.
* **Code & Logic Changes:**
  * **Migration & Graph Schema**: Added `situations` schema and graph relationship types (`EXHIBITS_SYMPTOM`, `RESOLVED_BY`).
* **Benefit:** Rich graph representation for complex outage analysis.

---

### `5c0ad5b` (2026-08-20) — Attach Knowledge Cases to Patterns & Read Evidence Ledgers
* **Problem:** Operational patterns were disconnected from official SOP documentation.
* **Code & Logic Changes:**
  * **`pattern_service.py`**: Linked relevant Knowledge Cases to incident patterns and recorded bidirectional evidence ledgers.
* **Benefit:** Playbooks generated from patterns automatically cite both real incidents and approved SOPs.

---

### `f6ab870` (2026-08-19) — Migrate Knowledge-Derived Episodes into Knowledge Cases
* **Problem:** Legacy database records had historical SOPs stored in the episodes table.
* **Code & Logic Changes:**
  * **Migration Script**: Migrated historical knowledge items into `knowledge_cases` and marked legacy rows as tombstoned.
* **Benefit:** Clean separation between real tickets and static documentation.

---

### `2a7520f` (2026-08-19) — First-Class Knowledge Cases & Evidence Ledgers
* **Problem:** Knowledge base articles lacked structured provenance tracking.
* **Code & Logic Changes:**
  * **`models/knowledge_case.py`**: Created `KnowledgeCase` model with full applicability rules and version tracking.
* **Benefit:** Enterprise-grade documentation management with audit lineage.

---

### `5dcfeca` (2026-08-19) — Require Observational Sources for Operational Episodes
* **Problem:** Episodes were created without tracking which source integration provided the data.
* **Code & Logic Changes:**
  * **`models/episode.py`**: Enforced `source_id` foreign key constraint on all operational episodes.
* **Benefit:** Every incident in the system has verified source provenance.

---

### `99c5e41` (2026-08-19) — Verification Pass on Knowledge & Evidence Units
* **Problem:** Needed verification tests on newly migrated knowledge cases and evidence models.
* **Code & Logic Changes:**
  * **`tests/`**: Added comprehensive test suites verifying knowledge attachment and ledger queries.
* **Benefit:** High test coverage and regression protection.

---

### `1a96ebb` (2026-08-19) — Extract Knowledge Applicability on Ingestion Path
* **Problem:** Knowledge applicability rules (OS, product version) were only extracted on manual re-classification.
* **Code & Logic Changes:**
  * **`services/extraction_service.py`**: Ran applicability extraction directly during document ingestion.
* **Benefit:** Newly ingested articles are immediately searchable with version filters.

---

### `fbf4ec0` (2026-08-19) — Grounded Documentation & Codebase Wiki Refresh
* **Problem:** Internal documentation was out of date with recent architectural changes.
* **Code & Logic Changes:**
  * **`docs/`**: Synchronized system documentation, API contracts, and runbooks with the live codebase.
* **Benefit:** Clear, accurate developer guides for the engineering team.

---

### `1c91979` (2026-08-19) — Lexical Search Honors Same Visibility Gates as Vector Search
* **Problem:** Documents on legal hold were hidden from vector search but still reachable through keyword search.
* **Code & Logic Changes:**
  * **`search/pg_fts.py`**: Applied unified `_visibility_predicates` helper across both lexical and semantic search paths.
* **Benefit:** Eliminates compliance and security leaks in search results.

---

### `b38e654` (2026-08-19) — Agent Graph: Unapproved Episodes as Labeled References
* **Problem:** The diagnostic agent could not see unapproved incidents from the current week during active troubleshooting.
* **Code & Logic Changes:**
  * **`graph/agent/hydrators.py`**: Admitted draft episodes as labeled reference material (`UNAPPROVED DRAFT`) with a 0.8 relevance discount.
* **Benefit:** Agents can use fresh ongoing incident data while clearly warning users that it is unconfirmed.

---

### `69fb0c5` (2026-08-19) — Clustering: Distance-Based Pattern Lookup & Deferred Dispatch
* **Problem:** Clustering minted single-episode patterns for 88% of tickets because pattern lookup had no `ORDER BY`, and worker tasks fired before transactions committed.
* **Code & Logic Changes:**
  * **`services/deferred_dispatch.py`**: Queued background task dispatching on database `after_commit` events.
  * **`services/pattern_service.py`**: Ordered candidate patterns by vector distance, boosting pattern merge acceptance from 12% to 40%.
* **Benefit:** Clean pattern grouping and elimination of phantom background task errors.

---

## 5. Summary Table by Engineering Domain

| Domain | Key Commits | Value Delivered |
| :--- | :--- | :--- |
| **Playbook Quality & Clarification** | `0a57cf8`, `fcadeb6`, `51738e6`, `2d786c2`, `64843cc`, `ef21ac3`, `243c848`, `a6c4a34` | Automated 14-dimension validation, RFC 8785 hashing, human-in-the-loop Guided Fix Wizard, and publication gates. |
| **Search, Semantics & AI Diagnostics** | `6b677b3`, `83c3939`, `82b3aad`, `1c91979`, `b38e654`, `69fb0c5` | 4-Arm Hybrid Ranker (Vector + BM25 + Graph + Negative Knowledge), RRF fusion, and dynamic score calibration. |
| **Enterprise Security & Multi-Tenancy** | `ec633f8`, `d19e06b`, `b7b6486`, `b0093ca`, `d0c18c3`, `e07ac5f` | PostgreSQL Row-Level Security (RLS), composite foreign keys, platform admin isolation, super-admin tenant switching, and zero-downtime credential rotation. |
| **Copilot & Operational Auditing** | `967357b`, `70f4b55`, `924b4b6`, `fc83dd1` | Support Copilot analytics dashboard, published-version isolation with draft forking, and 1-click bulk approvals. |
| **UI/UX & Frontend Polish** | `0a57cf8`, `615f15f`, `783db95`, `be97e42`, `c11ca0c`, `7515fea`, `b8881a1`, `88ac857`, `0f25cd2` | Glassmorphism design system, tabbed detail views, Graph Explorer, side-by-side diffs, and responsive layout fixes. |
| **Ingestion Pipeline & Knowledge Architecture** | `ff26949`, `d5ab265`, `1a05fec`, `2005037`, `2e2c19c`, `5c0ad5b`, `f6ab870`, `2a7520f`, `5dcfeca` | Epistemic separation between tickets and SOPs, thread hydration optimization, and bidirectional evidence ledgers. |
