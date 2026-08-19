# ContextEdge — Project Overview

This document provides a comprehensive, extremely detailed overview of the ContextEdge platform. It is designed for new team members, junior developers, and stakeholders who want to understand the platform from the ground up. Every technical term is explained simply, ensuring that even a complete beginner can grasp the architecture and flows. This document is meant to be a deep-dive, leaving no stone unturned.

> **Accurate as of 2026-08-19.** Where this document describes what the system *does*, the claim was checked against the code and carries a `file:line` citation you can click. Paths are relative to the repository root. If prose and code ever disagree, the code wins — open a PR and fix the doc.
>
> **Before you tell anyone "ContextEdge does X", read [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md).** It is the deliberately honest list of what is built, what is scaffolding waiting on something else, and what was measured and abandoned. Several things in this repository look finished from the schema and are not yet reachable.

## 0. The running example: the Acme VPN incident

Every ContextEdge document traces the same incident, so you can follow one record end to end across all of them. Do not invent a new example when extending these docs.

> Tenant **Acme Corp** runs ServiceNow, Microsoft Teams, and Gmail. One Tuesday morning the corporate VPN starts dropping connections. ServiceNow incident **`INC0010427`** is filed — *"VPN tunnel flapping on `vpn-gw-east-01`"*. Several colleagues file near-duplicate tickets, a Teams thread fills with diagnosis, and an engineer emails a root-cause note that quotes the ticket number. The cause turns out to be an expired TLS certificate on the gateway. The fix: renew the certificate and restart RADIUS.

You will see this thread reappear at every stage below — as raw evidence, as a hydrated conversation, as an episode, as a fingerprinted recurring problem, as a pattern, and finally as an approved playbook.

---

## 1. Business Problem

### What operational problem does ContextEdge solve?
In most modern organizations, IT operations and support teams rely on a multitude of tools to do their jobs. They use ticketing systems like Jira Service Management and ServiceNow to track issues. They use chat platforms like Slack and Microsoft Teams to discuss problems and collaborate on fixes. They use shared mailboxes to receive alerts and communicate with vendors. They use knowledge bases (KBs) to store standard operating procedures (SOPs).

The problem is **fragmentation**. When a new issue arises, the evidence of how a similar issue was solved in the past is scattered across all these different systems. A support analyst trying to fix a broken VPN connection might have to search through old tickets, read through chat threads, and hope they find the correct, most up-to-date knowledge base article. 

Worse, knowledge base articles often go stale. They are written once and rarely updated when the actual procedures change in the real world. This leads to teams repeating the same troubleshooting steps, making the same mistakes, and relying on "tribal knowledge" (information known only by a few experienced people) rather than documented, governed processes. 

ContextEdge acts as an "Operational Memory" layer. It watches all these systems, learns from how experienced engineers solve problems, and automatically documents these solutions into playbooks.

### Why do organizations need this?
Organizations need a way to turn this fragmented, unstructured operational evidence into **durable, governed, machine-usable playbooks**. They need a system that doesn't just store documents, but actively learns from the actual decisions and actions taken by engineers in the field. 

Instead of an analyst wasting hours rediscovering past troubleshooting paths, they need a system that can instantly surface the correct, approved playbook for a given issue, backed by the evidence of how it was solved before. This is what we call **Operational Memory**.

Furthermore, with the rise of AI Agents, organizations want to automate IT support. But an AI Agent is only as good as the instructions it is given. Generic LLMs (Large Language Models) do not know how your specific company fixes its specific VPN. ContextEdge provides the highly-contextual, company-specific instructions that these AI Agents need to do their jobs safely and correctly.

### What pain points exist without it?
Without a system like ContextEdge, organizations experience a wide variety of systemic failures and inefficiencies:
1. **High Mean Time to Resolution (MTTR):** Issues take longer to fix because analysts have to reinvent the wheel. Every time a complex issue occurs, the team starts from scratch, searching Jira or asking in Teams.
2. **Inconsistent Quality:** Different teams or analysts solve the same problem in different ways, some of which may violate security policies or be inefficient. Analyst A might restart a server, while Analyst B might just clear a cache.
3. **Stale Knowledge:** Documentation quickly becomes outdated, leading to frustration and errors when analysts follow obsolete instructions. A Confluence page written in 2021 about resetting passwords might tell you to use a tool that was decommissioned in 2023.
4. **Wasted Effort (Toil):** Senior engineers spend too much time answering the same questions or assisting with recurring issues because the knowledge isn't easily accessible to junior staff. This leads to burnout and prevents senior staff from working on strategic projects.
5. **AI Hallucinations:** When organizations try to use generic AI or Large Language Models (LLMs) to answer support questions, the AI often makes up answers (hallucinates) because it isn't grounded in the organization's specific, approved operational reality.
6. **Onboarding Delays:** New hires take months to become productive because they have to absorb years of undocumented tribal knowledge.
7. **Compliance Risks:** When processes are not standardized and documented, it is impossible to prove to auditors that standard operating procedures are being followed for critical actions.

---

## 2. Business Goal

### What does the platform achieve?
ContextEdge acts as a **Standalone Operational Memory and Living Playbook Platform**. 

It achieves the following six core objectives:
1. **Ingestion and Discovery:** It connects to external sources and ingests operational evidence (tickets, chats, emails, knowledge-base articles, alert rollups), safely and within tenant boundaries. Seven connectors are registered today — ServiceNow, Jira Service Management, Gmail, Microsoft Teams, Zoho Desk, ManageEngine ServiceDesk Plus, and SapphireIMS (backend/src/contextedge/connectors/registry.py:100-110). Confluence, SharePoint, and Exchange appear in the setup catalog with status `planned` only.
2. **Episode Reconstruction:** It uses AI to read this fragmented evidence and reconstruct a structured "episode" — a step-by-step timeline of what happened, what was diagnosed, what failed, and how it was ultimately fixed. An episode takes chaotic chat logs and turns them into a clean story.
3. **Problem Fingerprinting:** Each approved episode is distilled into a generalized issue signature, stripped of hostnames and ticket numbers, so the same failure is recognizable months later as a *recurrence* rather than a fresh novelty. A recurrence is recorded as a precedent link, never as a merge.
4. **Pattern Recognition:** It looks across many episodes to find patterns. If the same VPN certificate expiry happens six times, ContextEdge recognizes it as one pattern and clusters those episodes together.
5. **Playbook Generation and Governance:** It generates a proposed playbook from a pattern, combining what engineers actually did (empirical), what the documentation says (normative), and what has already been shown not to work (negative). Crucially, a human reviewer must approve it before it becomes active. This ensures Human-in-the-Loop safety.
6. **Runtime Retrieval:** When a new issue occurs, downstream systems or human analysts can query ContextEdge. It returns the best-matching, human-approved playbook with a confidence score and the exact evidence behind it — **or nothing at all**, deliberately, when no candidate is good enough. "No recommendation" is a supported answer, not an error.

By doing this, ContextEdge reduces the time to resolve issues, increases the quality of fixes, and provides a safe, governed way to use AI in IT operations.

### Who are the target users?
The platform serves multiple personas, each with different needs and permissions:
1. **Analysts / Support Engineers (L1/L2):** The primary consumers who use the runtime APIs (often via a chat interface, a ticketing tool widget, or an orchestration tool) to get recommendations on how to fix active issues.
2. **Knowledge Managers / Reviewers (L3/SME):** Experienced engineers who review the AI-generated episodes and playbooks, correcting them if necessary, and approving them for organizational use. They use the ContextEdge web dashboard.
3. **Domain Admins:** Leaders responsible for a specific IT area (like Identity, Networking, or Cloud Ops) who manage which KBs and channels are ingested, and assign reviewers to specific topics.
4. **Tenant Admins / Platform Admins:** Administrators who manage the overall platform configuration, security policies, API keys, billing (tokens), and access controls for their entire organization.
5. **Service Accounts / Agent Consumers (e.g., AEAIHub):** Automated systems and autonomous AI agents that query the platform programmatically. These agents use ContextEdge as their "memory" to decide what actions to take without human intervention.

---

## 3. High-Level Architecture

The architecture of ContextEdge is designed as a **Modular Monolith**. 

**What is a Modular Monolith?**
In software development, there are generally two extremes: Monoliths (one giant codebase where everything is tangled together) and Microservices (dozens of tiny, separate applications that talk to each other over the network). 
A Modular Monolith is the best of both worlds for a project of this scale. All the backend code lives in a single application (one FastAPI server), making it easy to deploy, test, and debug. However, inside that application, the code is strictly organized into distinct, logical modules (like authentication, sync, evidence processing, and AI). Modules are not allowed to blindly modify each other's data; they must use internal service interfaces.

### ASCII Architecture Diagram

```text
                      +-------------------------------------------------+
                      |                 Human Users                     |
                      |   (Knowledge Managers, Reviewers, Admins)       |
                      +-----------------------+-------------------------+
                                              |
                                              v (HTTPS)
                      +-------------------------------------------------+
                      |                Next.js React                    |
                      |                 Frontend UI                     |
                      |   (Pages, Components, TanStack Query Hooks)     |
                      +-----------------------+-------------------------+
                                              |
                                              v (HTTP/REST + JWT)
+-----------------------------------------------------------------------------------+
|                            FastAPI Backend API (Modular Monolith)                 |
|                                                                                   |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  | Auth & Middleware  |   | Admin/CRUD Routes |   | Runtime Retrieval Routes   |  |
|  | (Tenant Isolation) |   | (Review, Config)  |   | (Search, Match Playbooks)  |  |
|  +---------+----------+   +---------+---------+   +-------------+--------------+  |
|            |                        |                           |                 |
|            v                        v                           v                 |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  |   Ingestion &      |   |  Extraction &     |   |       Hybrid Ranker        |  |
|  |   Sync Service     |   |  Playbook Service |   | (Vector + FTS + Graph)     |  |
|  +---------+----------+   +---------+---------+   +-------------+--------------+  |
|            |                        |                           |                 |
+------------|------------------------|---------------------------|-----------------+
             |                        |                           |
             | (Sends Tasks)          | (Sends Tasks)             | (Queries DB)
             v                        v                           v
+-----------------------------------------------------------------------------------+
|                                 Data Plane & Queue                                |
|                                                                                   |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  |     Redis          |   |   PostgreSQL 16   |   |           MinIO            |  |
|  | (Celery Broker &   |   |  (Relational DB   |   |   (S3-Compatible Object    |  |
|  |  Response Cache)   |   |   with pgvector)  |   |    Storage for raw files)  |  |
|  +---------+----------+   +---------+---------+   +-------------+--------------+  |
|            ^                        ^                           ^                 |
+------------|------------------------|---------------------------|-----------------+
             |                        |                           |
             | (Pulls Tasks)          | (Reads/Writes Data)       | (Reads/Writes)
             v                        v                           v
+-----------------------------------------------------------------------------------+
|                              Celery Workers (Async)                               |
|                                                                                   |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  | Sync/Ingest Worker |   | Extract/AI Worker |   | Pattern/Evaluation Worker  |  |
|  | (Connects to APIs) |   | (Calls LLMs)      |   | (Analyzes graphs/trends)   |  |
|  +---------+----------+   +---------+---------+   +-------------+--------------+  |
|            |                        |                           |                 |
+------------|------------------------|---------------------------|-----------------+
             |                        |                           |
             v                        v                           v
+-----------------------------------------------------------------------------------+
|                              External Services                                    |
|                                                                                   |
|  +--------------------+   +-------------------+   +----------------------------+  |
|  | Enterprise Sources |   | LiteLLM Provider  |   |    Downstream Consumers    |  |
|  | (Jira, Teams, ITSM)|   | (OpenAI, Claude)  |   |   (AEAIHub, Chat Bots)     |  |
|  +--------------------+   +-------------------+   +----------------------------+  |
+-----------------------------------------------------------------------------------+
```

### Mermaid Architecture Diagram

```mermaid
flowchart TD
    subgraph Clients["Clients & Consumers"]
        UI["Next.js Frontend Dashboard\n(Human UI)"]
        Agent["Downstream Agents\n(e.g., AEAIHub)"]
        Chat["IT Support Chat UI\n(End Users)"]
    end

    subgraph API["FastAPI Backend (Modular Monolith)"]
        AuthMiddleware["Auth & Tenant Middleware\n(Ensures strict isolation)"]
        AdminRouter["Admin & Settings Routers"]
        IngestRouter["Ingestion & Sync Routers"]
        ReviewRouter["Review Queue & Playbook Routers"]
        RuntimeRouter["Runtime Retrieval API"]
        SearchRanker["Hybrid Ranker\n(FTS + Vector + Graph)"]
    end

    subgraph AsyncQueue["Background Processing"]
        RedisBroker[("Redis\n(Celery Broker &\nResult Backend)")]
        CeleryWorkerSync["Sync & Backfill Worker\n(IO Bound)"]
        CeleryWorkerExtract["Extraction & AI Worker\n(CPU/Network Bound)"]
        CeleryWorkerPattern["Pattern & Evaluation Worker\n(Compute Bound)"]
        CeleryBeat["Celery Beat\n(Task Scheduler)"]
    end

    subgraph DataPlane["Data Storage"]
        PostgreSQL[("PostgreSQL 16\n(Relational DB +\npgvector extension)")]
        MinIO[("MinIO\n(S3-compatible\nRaw Evidence & Attachments)")]
        RedisCache[("Redis Cache\n(Fast runtime responses)")]
    end

    subgraph ExtProviders["External Services"]
        LLM["LiteLLM Wrapper\n(OpenAI, Anthropic, Gemini)"]
        Sources["Enterprise Sources\n(Jira, ServiceNow, Teams)"]
    end

    %% Client Connections
    UI -->|JWT Bearer Token| AuthMiddleware
    Agent -->|Service Token| AuthMiddleware
    Chat -->|Service Token| AuthMiddleware

    %% API Routing
    AuthMiddleware --> AdminRouter
    AuthMiddleware --> IngestRouter
    AuthMiddleware --> ReviewRouter
    AuthMiddleware --> RuntimeRouter

    RuntimeRouter --> SearchRanker
    SearchRanker --> PostgreSQL
    RuntimeRouter --> RedisCache

    %% Admin & DB connections
    AdminRouter --> PostgreSQL
    ReviewRouter --> PostgreSQL

    %% Worker Triggering
    IngestRouter --> RedisBroker
    ReviewRouter --> RedisBroker

    %% Worker Consumption
    RedisBroker --> CeleryWorkerSync
    RedisBroker --> CeleryWorkerExtract
    RedisBroker --> CeleryWorkerPattern
    CeleryBeat --> RedisBroker

    %% Worker DB/Storage Connections
    CeleryWorkerSync --> PostgreSQL
    CeleryWorkerExtract --> PostgreSQL
    CeleryWorkerPattern --> PostgreSQL
    
    CeleryWorkerSync --> MinIO
    CeleryWorkerExtract --> MinIO

    %% External Connections
    CeleryWorkerSync --> Sources
    CeleryWorkerExtract --> LLM
    SearchRanker --> LLM
```

### Component Explanations in Depth

1. **Clients & Consumers:**
   - **Next.js Frontend Dashboard:** This is the administrative interface. Knowledge managers use this to view the AI's work, approve playbooks, and configure what systems ContextEdge should ingest data from.
   - **Downstream Agents (e.g., AEAIHub) & Chat UIs:** These are the runtime consumers. When a user asks a chatbot "My printer isn't working," the chatbot calls the ContextEdge API to find the approved printer troubleshooting playbook.

2. **FastAPI Backend (The Core Application):**
   - **Auth & Tenant Middleware:** Security is paramount. This middleware inspects every single incoming request. If a human is logged in, it verifies their JWT. If a machine is calling, it verifies the Service Token. It also extracts the `tenant_id` (the ID of the specific organization) and securely attaches it to the request context. This ensures that Tenant A can never accidentally query Tenant B's playbooks.
   - **Routers:** The API is broken down into logical sections called routers. 
     - `AdminRouter` handles creating users and configuring settings. 
     - `IngestRouter` provides webhooks for Jira or Teams to push data to ContextEdge. 
     - `ReviewRouter` provides the data needed for the frontend approval screens. 
     - `RuntimeRouter` is a highly optimized, high-speed endpoint used solely for searching and matching playbooks in real-time.
   - **Hybrid Ranker:** This is the intelligent search engine. It doesn't rely on just one search method. It uses:
     - *Full-Text Search (FTS):* Looking for exact keyword matches (e.g., "Error Code 503").
     - *Vector Search:* Using AI to understand the meaning of the query (e.g., matching "cannot connect to web" with "HTTP outage").
     - *Graph Traversal:* Looking at how different pieces of knowledge are connected to boost the score of highly relevant playbooks.

3. **Background Processing (Celery & Redis):**
   - In web development, you should never make a user wait for a slow operation (like downloading 1,000 emails or asking an AI to summarize a long thread). 
   - **Redis (Broker):** When the FastAPI backend needs a slow task done, it writes a message to Redis saying "Please do Task X." Redis acts as a waiting room (Broker).
   - **Celery Workers:** These are background processes running separately from the web server. They constantly watch Redis. When they see a task, they grab it and execute it. 
     - *Sync Worker:* specialized in talking to external APIs and downloading data.
     - *Extract/AI Worker:* specialized in talking to LLMs and parsing JSON responses.
     - *Pattern Worker:* specialized in doing heavy math to find clusters of similar issues.
   - **Celery Beat:** A task scheduler (like a cron job) that wakes up the workers at specific times (e.g., "Check Jira every 5 minutes").

4. **Data Storage (The Data Plane):**
   - **PostgreSQL 16:** The heart of the system. It is a highly reliable relational database. It stores users, settings, and the final approved playbooks.
   - **pgvector:** A magical extension for PostgreSQL. Normally, a database just stores text and numbers. pgvector allows Postgres to store "Vectors" (lists of thousands of numbers generated by AI) and mathematically compare them to find similar text at lightning speed.
   - **MinIO:** An object storage system, exactly like Amazon S3, but it can run locally. Relational databases are bad at storing massive, unstructured files (like a 5MB JSON dump from Jira, or PDF attachments). ContextEdge stores these heavy files in MinIO and just saves a link to them in PostgreSQL.
   - **Redis Cache:** Redis is so fast because it lives entirely in RAM (memory). The system uses it to cache (temporarily save) the results of complex searches so that if someone asks the same question 5 seconds later, it can reply instantly without doing the hard work again.

5. **External Services:**
   - **LiteLLM:** A brilliant open-source library. Instead of writing code that only knows how to talk to OpenAI, ContextEdge uses LiteLLM. LiteLLM translates ContextEdge's requests into the format needed by OpenAI, Anthropic, Google Gemini, or even local models. This means ContextEdge can switch AI providers instantly without changing the core code.
   - **Enterprise Sources:** The actual IT systems (Jira, Teams, ServiceNow, etc.) where the raw, chaotic human evidence is generated.

---

## 4. Technologies Used

In this section, we will break down every piece of technology used in ContextEdge. We will assume you are a fresher (a beginner), so we will explain exactly what the technology is, why it was chosen over alternatives, where it lives in the code, and what version is used.

### Frontend Technologies (The User Interface)

#### 1. Next.js
- **What it is:** Next.js is a framework built on top of React. React helps you build user interfaces (buttons, forms, pages), and Next.js provides the structure to turn those components into a full website, handling things like routing (moving from page to page) and server-side rendering (preparing the page on the server before sending it to the browser).
- **Why it was chosen:** It provides excellent performance, great developer experience, and modern features like the App Router, which makes building complex dashboards easier. It handles all the complicated webpack bundling automatically.
- **Where it is used:** The entire `frontend/` directory is a Next.js application.
- **Version used:** 16.2.2

#### 2. React
- **What it is:** A JavaScript library originally developed by Facebook for building user interfaces. It lets you create reusable UI components (like creating a `<PrimaryButton />` once and using it everywhere).
- **Why it was chosen:** It is the undisputed industry standard for building interactive, dynamic single-page applications. It has the largest ecosystem and hiring pool.
- **Where it is used:** Throughout the `frontend/src/` folder.
- **Version used:** 19.2.4 (Using modern React Server Components and Hooks).

#### 3. Tailwind CSS
- **What it is:** A "utility-first" CSS framework. Instead of writing custom CSS rules in separate `.css` files and linking them, you use tiny, predefined classes directly in your HTML/React code (e.g., `<div className="text-center text-red-500 font-bold p-4">`).
- **Why it was chosen:** It speeds up development drastically. You don't have to invent class names (no more struggling to name a container `user-profile-card-inner-wrapper`), and it keeps styling perfectly consistent across the whole app.
- **Where it is used:** In almost every React component file to style the UI.
- **Version used:** 4.x

#### 4. shadcn/ui
- **What it is:** This is not a traditional component library that you install via npm (like Material UI or Bootstrap). Instead, it is a collection of beautifully designed, accessible UI components (like buttons, dialogs, dropdowns) that you actually copy and paste into your project codebase.
- **Why it was chosen:** Traditional libraries are hard to customize. Because shadcn/ui copies the actual source code into your project, developers have 100% full control over how the components look and behave, while still getting a professional, accessible foundation instantly.
- **Where it is used:** Located in `frontend/src/components/ui/`.
- **Version used:** 4.1.2

#### 5. TanStack Query (formerly React Query)
- **What it is:** A tool for fetching, caching, synchronizing and updating server state in your React applications.
- **Why it was chosen:** Fetching data from an API can be messy. You have to write code to handle "loading" states, "error" states, and figure out when to refresh data if it goes stale. TanStack Query handles all of this automatically, making the frontend code much cleaner and providing a lightning-fast experience for the user via its internal caching.
- **Where it is used:** Used in `frontend/src/lib/hooks/` to talk to the FastAPI backend.
- **Version used:** 5.96.2

#### 6. Zustand
- **What it is:** A small, fast, and scalable state-management solution. It replaces older, heavier tools like Redux.
- **Why it was chosen:** Sometimes data needs to be shared across the whole app (like "Who is the currently logged-in user?" or "Which Tenant is currently selected?"). Zustand makes it incredibly easy to manage this global state without writing huge amounts of boilerplate code.
- **Where it is used:** Used in `frontend/src/lib/stores/`.
- **Version used:** 5.0.12

### Backend Technologies (The Brains)

#### 7. Python
- **What it is:** A popular, easy-to-read programming language widely used in AI, data science, and web backend development.
- **Why it was chosen:** Because ContextEdge relies heavily on AI, data processing, and text extraction, Python is the only logical choice. The Python ecosystem has the best, most mature libraries for dealing with AI models (like LiteLLM, LangChain) and data parsing.
- **Where it is used:** The entire `backend/` directory.
- **Version used:** 3.12 or higher (taking advantage of modern typing and async features).

#### 8. FastAPI
- **What it is:** A modern, incredibly fast web framework for building APIs with Python based on standard Python type hints.
- **Why it was chosen:** It is one of the fastest Python frameworks available. It fully supports asynchronous programming (meaning it can handle thousands of requests at once without waiting). Best of all, because it uses type hints, it automatically generates interactive OpenAPI (Swagger) documentation for the API, saving developers countless hours of writing docs.
- **Where it is used:** `backend/src/contextedge/api/` and `main.py`.
- **Version used:** >=0.115

#### 9. SQLAlchemy 2
- **What it is:** An Object-Relational Mapper (ORM). It allows developers to interact with the database using Python objects instead of writing raw SQL queries as strings. For example, instead of writing `SELECT * FROM users WHERE id = 1`, you write `db.query(User).filter(User.id == 1).first()`.
- **Why it was chosen:** It prevents dangerous SQL injection attacks, makes database code easier to test, and allows developers to switch database engines if ever needed. The new 2.0 version fully supports the `asyncio` framework, matching FastAPI's speed.
- **Where it is used:** `backend/src/contextedge/models/` and database configuration files.
- **Version used:** >=2.0

#### 10. Alembic
- **What it is:** A database migration tool specifically designed to work hand-in-hand with SQLAlchemy.
- **Why it was chosen:** When you change your database schema (like adding a new "phone_number" column to a user table), you need a way to apply that change to every developer's local database, and to the production database, without losing data. Alembic tracks these changes as "migrations"—essentially version control (Git) for your database structure.
- **Where it is used:** `backend/alembic/` directory.
- **Version used:** >=1.14

#### 11. Pydantic
- **What it is:** A data validation library for Python. You define how data should look (e.g., "age must be an integer over 18"), and Pydantic ensures any incoming data matches those rules.
- **Why it was chosen:** It is deeply integrated into FastAPI. It automatically validates incoming JSON requests from the frontend and blocks bad data from ever reaching the database.
- **Where it is used:** `backend/src/contextedge/schemas/`.
- **Version used:** >=2.10

#### 12. structlog
- **What it is:** A library for structured logging. Instead of logging simple, unstructured text strings (e.g., `INFO: User 123 logged in at 5:00`), it logs data as structured JSON (e.g., `{"level": "info", "event": "user_login", "user_id": 123, "timestamp": "2023-10-27T17:00:00Z"}`).
- **Why it was chosen:** Structured logs are much easier for machines to read, search, and analyze in modern enterprise observability tools like Datadog, Splunk, or ELK.
- **Where it is used:** Configured in `main.py` and used across every module in the backend.
- **Version used:** >=24.4

### Database and Background Queues

#### 13. PostgreSQL 16
- **What it is:** An advanced, open-source relational database management system. It stores data in highly structured tables with strict rules (schemas).
- **Why it was chosen:** It is the absolute gold standard for reliable database storage. It scales incredibly well, and crucially, it supports advanced features like native JSON storage, powerful Full-Text Search (FTS), and custom extensions (like pgvector).
- **Where it is used:** Runs in a Docker container locally, accessed via SQLAlchemy.
- **Version used:** 16

#### 14. pgvector
- **What it is:** An extension for PostgreSQL that allows it to natively store and search "vectors." Vectors are lists of hundreds or thousands of numbers generated by AI to represent the semantic meaning of text.
- **Why it was chosen:** It allows ContextEdge to perform "semantic search" directly inside the main database. If you search for "network is down," the vector search can match it with a ticket that says "connectivity outage," because the AI understands they mean the same thing mathematically. Using pgvector means we don't have to manage a completely separate vector database (like Pinecone or Weaviate), keeping the architecture simple.
- **Where it is used:** Configured in `backend/src/contextedge/search/vector_search.py` and applied via Alembic migrations.
- **Version used:** >=0.5

#### 15. Celery
- **What it is:** A robust distributed task queue for Python. It allows the main web application to hand off slow, heavy tasks to background worker processes.
- **Why it was chosen:** If the API tried to call an AI model directly during a user's HTTP request, the user would be stuck staring at a spinning loading screen for 30 to 60 seconds while the AI thinks. Celery moves this work to the background, returning an immediate "Task Accepted" response to the user so the UI stays fast and responsive.
- **Where it is used:** `backend/src/contextedge/workers/`.
- **Version used:** >=5.4

#### 16. Redis
- **What it is:** An in-memory data structure store. It is extremely fast because it keeps data in RAM (memory) rather than on a spinning hard drive or SSD.
- **Why it was chosen:** In ContextEdge, Redis serves two critical, distinct purposes: 
   1) It acts as the "message broker" for Celery (holding the queue of tasks waiting to be done). 
   2) It acts as a fast cache for API responses (like the Runtime Explain cache) to speed up repeated identical queries.
- **Where it is used:** Configured in `docker-compose.yml` and accessed via the `redis` python package and Celery config.
- **Version used:** 7-alpine (Docker) / >=5.2 (Python client)

### Object Storage

#### 17. MinIO
- **What it is:** A high-performance object storage server that is 100% compatible with the Amazon S3 API. "Object storage" is used to store unstructured files (blobs), rather than structured rows of data.
- **Why it was chosen:** Relational databases (like Postgres) become very slow and bloated if you try to save large files inside them. MinIO allows the system to store massive raw JSON files from Jira, PDFs, and log file attachments safely. Because it is S3-compatible, developers can run MinIO locally on their laptops using Docker, and then seamlessly switch the application to use real Amazon S3 in the production cloud without changing a single line of code.
- **Where it is used:** `backend/src/contextedge/services/object_store.py`.
- **Version used:** latest (Docker image)

### AI and LLM Integration

#### 18. LiteLLM
- **What it is:** A Python library that creates a standardized interface to interact with over 100 different LLM APIs (OpenAI, Anthropic, Google, Cohere, etc.).
- **Why it was chosen:** Instead of writing complex, custom code to talk to OpenAI, and then writing entirely different code to talk to Anthropic, LiteLLM lets developers write code once. It prevents vendor lock-in. It also allows the system to intelligently route different tasks to different models (e.g., using a very cheap, fast model for simple classification, and an expensive, smart model for deep playbook extraction).
- **Where it is used:** `backend/src/contextedge/ai/provider.py`.
- **Version used:** >=1.55

#### 19. Large Language Models (Google Vertex AI Gemini today)
- **What they are:** These are the actual AI models hosted by providers like Google, OpenAI, and Anthropic. They are neural networks trained on vast amounts of text.
- **Why they are used:** They perform the heavy lifting of reading messy human text (chat logs, ticket descriptions), classifying it, extracting structured steps, determining root causes, and generating the final clean playbooks.
- **What is actually configured** (backend/src/contextedge/config.py:56-67). Models are chosen per *task lane*, not per call site:

  | Task lane | Model | Used for |
  |---|---|---|
  | `classification` | `vertex_ai/gemini-2.5-flash` | relevance gate, message function, identity extraction and adjudication, decision extraction |
  | `extraction` | `vertex_ai/gemini-2.5-flash` | episode synthesis, issue signatures, knowledge applicability |
  | `pattern` | `vertex_ai/gemini-2.5-flash` | pattern synthesis |
  | `playbook` | `vertex_ai/gemini-3.7-flash` | playbook generation |

- **Why the playbook lane is different, and why the pattern lane is not.** The playbook lane moved to 3.7-flash on a measured A/B run: grounded step share rose 0.70 → 0.81 and latency halved, with no pattern getting worse. The pattern lane was *not* in that test, so it deliberately stays on 2.5-flash until it has its own measurement (config.py:59-66). This is a repository-wide convention: model, prompt, and truncation changes ship with a before/after measurement, and negative results get written down so nobody re-litigates them.
- **Thinking budgets.** On these models most output tokens are reasoning, so capping them is the biggest available cost lever — but it is not uniformly safe. Only the `relevance` prompt is capped, at zero (`llm_thinking_budgets`, config.py:188-190). Everything else keeps the provider's dynamic default, because a controlled test showed identity-adjudication confidence dropping from 0.95 to 0.80 under a cap, and the person auto-link threshold is *exactly* 0.95 — the cap would have silently diverted automatic links into the human review queue.

#### 20. Embedding Models
- **What it is:** These are specialized AI models that don't generate text, but instead convert text into a mathematical vector (a long list of numbers).
- **Why it is used:** This enables semantic search. It takes the text of a record, runs it through the model, and saves the resulting numbers in Postgres via pgvector.
- **Which model:** whatever `DEFAULT_EMBEDDING_MODEL` names in your environment, and it **must return exactly 3,072 dimensions** — the provider raises a `ValueError` naming valid models otherwise (backend/src/contextedge/ai/provider.py:786-793). The literal in `config.py:58` is `text-embedding-3-small`, which returns 1,536 and would be rejected, so real deployments override it. Read that constant as a placeholder, not as a description of the running system.

### Advanced Concepts & Enterprise Features

#### 21. Authentication & Multi-Tenancy (JWT, Bearer, Service Tokens)
- **What it is:** How the system knows who is making a request and what they are allowed to see.
  - **JWT (JSON Web Token):** A secure, cryptographically signed string given to human users when they log in. They send it with every HTTP request (as a "Bearer" token). It proves their identity.
  - **Service Tokens:** Long-lived, static API keys used by automated systems (like an external bot) to authenticate without needing to log in via a browser.
  - **Tenant Isolation:** Every single database table has a `tenant_id` column. The backend middleware intercepts every request, determines the tenant of the user, and forcibly injects that `tenant_id` into every database query, ensuring data from Tenant A can never leak to Tenant B.
- **Where it is used:** `backend/src/contextedge/api/dependencies.py` and middleware.

#### 22. Context Graph (PostgreSQL adjacency projection)
- **What it is:** A graph is a way to link data points together (Nodes and Edges). For example, Node A (a specific Error Code) is linked to Node B (a specific Playbook). Instead of using a complex, heavy Graph Database like Neo4j, ContextEdge stores these links directly in PostgreSQL using what is called an "adjacency table."
- **Why it is used:** It tracks complex relationships (e.g., "This Symptom is caused by This Error", "This Ticket contradicts This Playbook"). Keeping it in PostgreSQL keeps the overall architecture simple and highly performant without adding another expensive database to maintain.
- **Where it is used:** `backend/src/contextedge/graph/` and models like `pattern.py::GraphEdge`.

#### 23. MAF (Microsoft Agent Framework) Integration
- **What it is:** A framework for building AI agents that can reason, plan, and take actions autonomously.
- **Why it is used:** ContextEdge is designed to be the "memory engine" for these agents. It integrates cleanly with MAF to inject historical context into the agent's prompt and provides the agent with safe, approved playbooks to execute.

#### 24. Prometheus Metrics & Observability
- **What it is:** An open-source system monitoring and alerting toolkit.
- **Why it is used:** In a production enterprise environment, you must know how your system is performing. Prometheus collects real-time metrics (e.g., "How many requests per second are we handling?", "What is the average latency of the Runtime API?", "How many LLM tokens did we consume today?"). This data is usually visualized in a tool like Grafana.
- **Where it is used:** The `prometheus-fastapi-instrumentator` package in `main.py` and custom metrics in `ai/observability.py`.

#### 25. Docker & Docker Compose
- **What it is:** Docker packages software and all its dependencies (libraries, OS tools) into standardized units called containers. Docker Compose is a tool for defining and running multi-container Docker applications locally.
- **Why it is used:** It guarantees that the software will run exactly the same way on a developer's Windows laptop, a colleague's Mac, and the production Linux server. It completely eliminates the dreaded "it works on my machine" problem.
- **Where it is used:** `docker-compose.yml`, `docker-compose.dev.yml`, and `Dockerfile`s in both frontend and backend.

---

## 5. Complete Project Flow

This section details the complete lifecycle of a request, from the moment a user (or agent) takes an action, all the way down to the database and back up to the frontend UI. We will trace a "Runtime Retrieval" request.

### The Flow: User Action to Final Response

1. **User / Agent Initiation:** A downstream agent (e.g., AEAIHub) or a human user via a chat interface encounters a problem (e.g., "User laptop is severely slow and overheating"). They trigger a search request to the system.
2. **Frontend Interception (If human):** If using the dashboard, the Next.js frontend captures this intent in a search bar component.
3. **React Component & Hook:** The React component uses a custom TanStack Query hook (e.g., `useMatchPlaybook(query)`). This hook prepares to make an asynchronous HTTP request.
4. **API Service Call:** The `frontend/src/lib/api.ts` HTTP client attaches the correct JWT (for humans) or Service Token (for agents) to the Authorization headers and sends an HTTP POST request to the backend.
5. **Backend Route Hit:** FastAPI receives the request at the defined endpoint, typically `POST /api/v1/runtime/match`.
6. **Middleware Execution Pipeline.** Only two middlewares are registered, and Starlette runs the *last-added* one outermost, so the real order is CORS → `TenantContextMiddleware` → `RequestAuditMiddleware` → router (backend/src/contextedge/main.py:122-130):
   - *`TenantContextMiddleware`* mints or propagates `X-Request-ID` / `X-Correlation-ID` / `X-Causation-ID`, decodes the Bearer JWT or `X-Service-Token` to stamp `request.state`, and binds all of it into Python ContextVars so any function deep in the call stack inherits it (backend/src/contextedge/middleware/request_context.py:87). It **does not enforce** the token — see step 7.
   - *`RequestAuditMiddleware`* runs **after** the response, not before, and only for mutating methods under `/api/v1` (backend/src/contextedge/middleware/request_audit.py:29). It writes one `audit_logs` row on a worker thread and swallows its own failures, so auditing can never turn a good request into a 500.
7. **Authentication and authorization.** This happens in the route's `Depends(get_current_user)`, not in middleware (backend/src/contextedge/deps.py:72). A bad JWT is 401; an invalid service token is 403; a missing role is 403 via `require_role`. **An endpoint that forgets the dependency is unauthenticated even though the middleware ran** — that is the thing to check in review.
8. **Controller Validation:** The route function receives the payload. Pydantic validates that the payload has the correct structure.
9. **Service Layer Handoff:** The router hands validated data to the service layer, keeping HTTP concerns separate from business logic.
10. **Memory context assembly.** `build_runtime_memory_context` gathers three classes of memory in one pass — *short term* (the session and its recent trace events, plus the tenant's most recent evidence), *long term* (resolved canonical identities for the named entities, plus approved-playbook and active-pattern counts), and *reasoning* (recent execution runs and decisions). It also builds the effective query text by deduplicating symptoms, entities, context, and resolved identity names (backend/src/contextedge/services/memory_service.py:82).
11. **Vector Embedding Generation.** Exactly **one** query embedding is generated per match, and it is budget-gated and cost-attributed. If it fails, the semantic signal contributes zero and ranking continues on the other signals rather than erroring out.
12. **Hybrid ranking.** `rank_playbooks` loads approved playbooks that have a published version, filters by domain and by the caller's risk cap, then scores each candidate on keyword, semantic, graph, identity, evidence-quality, recency, and freshness signals, minus a negative-knowledge penalty. Every query includes `WHERE tenant_id = :tenant_id`; that isolation is written by each query, not injected by the middleware.
13. **Abstention.** Results below `MIN_RECOMMENDATION_SCORE = 0.35` are dropped. If candidates existed but none cleared the bar, the service logs `ranking.abstained` and returns an **empty list on purpose** (backend/src/contextedge/search/hybrid_ranker.py:171, 369-378). Callers must treat empty as "no recommendation", not as an error.
14. **Trace and cache.** If a `session_id` was supplied, a `retrieve` trace event is appended to the session. The full explain payload — every candidate's score breakdown — is then written to Redis under `runtime:match:{match_id}` with a one-hour TTL (backend/src/contextedge/api/v1/runtime.py:29, 230-238). **This cache is written after the match, keyed by match id.** It is not a request-level short-circuit: an identical query submitted five seconds later is fully re-ranked. Its only reader is `GET /runtime/explain/{match_id}`, which 404s once the entry expires.
15. **Serialization & Response:** FastAPI serializes the response object to JSON and returns 200.
16. **Frontend State Update:** TanStack Query receives the JSON, updates its cache, and re-renders.
17. **User Visibility:** The user or agent now sees the recommended playbook, with the evidence behind it.

### Mermaid Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Autonomous Agent
    participant FE as Next.js Frontend App
    participant API as FastAPI Main Router
    participant MW as Middleware (Auth/Tenant/Audit)
    participant SVC as Core Service Layer
    participant LLM as LiteLLM (Embedding API)
    participant DB as PostgreSQL (pgvector & Relational)
    participant Redis as Redis (Explain Cache)
    
    User->>FE: Inputs issue (e.g. "Laptop slow and hot")
    FE->>FE: React Hook (TanStack) triggers API Client
    FE->>API: POST /api/v1/runtime/match (with Token)
    
    API->>MW: Process Request Pipeline
    MW->>MW: Validate Token signature & Expiry
    MW->>MW: Extract Tenant ID & Inject ContextVar
    MW-->>API: Request Authorized & Tenant Context Set
    
    API->>API: Depends(get_current_user) -> 401/403 or CurrentUser
    API->>SVC: Call rank_playbooks(query, tenant_id, risk cap, domain scope)

    SVC->>DB: Build runtime memory context (session, identities, decisions)
    SVC->>LLM: One budget-gated embedding for the query text
    LLM-->>SVC: Return Vector [0.01, 0.45, -0.12...]

    SVC->>DB: Approved playbooks + newest published version (one batched query)
    SVC->>DB: Per candidate - FTS rank, chunk-aware semantic search, graph + identity edges
    Note over SVC,DB: Every statement carries WHERE tenant_id = :tenant_id.<br/>Legal hold, pending redaction and excluded<br/>access policies are filtered in SQL, not after.
    DB-->>SVC: Rows + score inputs

    SVC->>SVC: Weighted sum per candidate, then drop everything below 0.35
    alt All candidates below threshold
        SVC->>SVC: log ranking.abstained
        SVC-->>API: Empty result list (this is a valid answer)
    else At least one clears the bar
        SVC->>Redis: Cache the explain payload under match_id (TTL 1h)
        SVC-->>API: match_id + ranked results + filters_applied
    end

    API-->>FE: HTTP 200 JSON Response (Serialized)

    FE->>FE: Update TanStack Query Cache & UI State
    FE-->>User: Render Playbook UI and Confidence Score

    opt Operator wants the reasoning
        User->>API: GET /runtime/explain/{match_id}
        API->>Redis: Fetch cached breakdown (404 after TTL)
        API-->>User: Per-signal score breakdown
    end
```

> **Correction to earlier revisions of this document.** The sequence above used to show a "cache hit / cache miss" branch at the start of the request. There is no such branch. Redis stores the *explain payload* after a match completes; it never short-circuits a match. Repeating the same query re-runs the whole ranking.

---

## 6. Default Credentials

For local development, testing, and debugging purposes, the database initialization script (`make seed`) seeds the database with default test users. 

**WARNING: Do NOT use these in production or staging environments. They must be removed or passwords changed before deployment.**

- **Platform Admin User:**
  - Role: Has full access to all system settings across the tenant.
  - Email: `admin@contextedge.local`
  - Password: `admin123`
- **Standard Analyst User:**
  - Role: Has read-only access to playbooks and runtime search.
  - Email: `analyst@contextedge.local`
  - Password: `analyst123`

---

## 7. Environment Configuration

The entire platform follows the Twelve-Factor App methodology, meaning all configuration is stored in the environment. This is typically managed via a `.env` file at the root of the project during local development, or via Kubernetes Secrets/ConfigMaps in production.

Here is an exhaustive, detailed explanation of what every variable controls:

| Variable Name | Purpose / Deep Explanation |
|---|---|
| `DATABASE_URL` | The asynchronous connection string for PostgreSQL. Used by FastAPI and Celery for high-performance, non-blocking database queries. Format: `postgresql+asyncpg://user:pass@host:port/dbname`. |
| `DATABASE_URL_SYNC` | The synchronous connection string for PostgreSQL. Used exclusively by Alembic for running database migrations, as Alembic prefers synchronous drivers. Format: `postgresql://user:pass@host:port/dbname`. |
| `REDIS_URL` | Connection string for the Redis instance used for general caching (Database 0). Format: `redis://host:port/0`. |
| `CELERY_BROKER_URL` | Connection string for the Celery message broker (Redis Database 1). This is where tasks wait in a queue before workers pick them up. |
| `CELERY_RESULT_BACKEND` | Connection string where Celery stores the final return values and status (success/failure) of completed tasks (Redis Database 2). |
| `MINIO_ENDPOINT` | The host and port where the MinIO object storage server is running (e.g., `localhost:9000` or `minio:9000` in docker). |
| `MINIO_ROOT_USER` | The administrative username required to access MinIO buckets. |
| `MINIO_ROOT_PASSWORD` | The administrative password required to access MinIO buckets. |
| `MINIO_BUCKET` | The name of the specific bucket (logical folder) inside MinIO where all evidence files, chunked texts, and attachments are stored for the application. |
| `JWT_SECRET_KEY` | A highly secure, random cryptographic string used by the `python-jose` library to sign and verify user login tokens. If this leaks, attackers can forge logins. **Must be a strong random value in production.** |
| `FERNET_KEY` | A secure key used by the `cryptography` library for two-way encryption of sensitive data stored in the database (like third-party API credentials for Jira or ServiceNow). |
| `OPENAI_API_KEY` | API key for OpenAI, used only if you route a task lane there. The shipped default provider is Vertex AI, so most deployments authenticate with `GOOGLE_APPLICATION_CREDENTIALS` (a service-account file) and/or `GOOGLE_API_KEY` instead. |
| `GOOGLE_APPLICATION_CREDENTIALS` / `GOOGLE_CLOUD_PROJECT` / `VERTEX_LOCATION` | Vertex AI service-account credentials, project, and region. Vertex calls pass project and location explicitly per request rather than relying on process environment. |
| `SERVICE_TOKENS_JSON` | A JSON map of static machine-to-machine tokens: token → `{tenant_id, user_id, email, roles[, allowed_domain_ids]}`. **Omitting `allowed_domain_ids` makes the token tenant-wide** — that is by design, but it is a decision, not a default to ignore. |
| `DEFAULT_LLM_PROVIDER` | Which provider LiteLLM treats as the default. Ships as `vertex_ai`. |
| `APP_ENV` | Execution environment: `development`, `staging`, `production`. Anything other than `development` turns on two **fail-fast import-time guards**: a default `JWT_SECRET_KEY` and a missing or placeholder `FERNET_KEY` each raise `RuntimeError` at startup rather than booting insecurely. |
| `APP_DEBUG` | Boolean. Verbose error tracing. Must be `False` in production. |
| `APP_LOG_LEVEL` | structlog verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `APP_CORS_ORIGINS` | Comma-separated list of origins allowed to call the API cross-origin. |

### Settings that change system behaviour (not just wiring)

These are the ones to read before changing anything, all in `backend/src/contextedge/config.py`:

| Variable | Default | What it controls |
|---|---|---|
| `DEFAULT_EMBEDDING_MODEL` | `text-embedding-3-small` (a placeholder — see §4.20) | Must return exactly 3,072 dimensions or every embedding call raises |
| `DEFAULT_CLASSIFICATION_MODEL` / `DEFAULT_EXTRACTION_MODEL` | `vertex_ai/gemini-2.5-flash` | The classification and extraction lanes |
| `PATTERN_MODEL` / `PLAYBOOK_MODEL` | `gemini-2.5-flash` / `gemini-3.7-flash` | Pattern lane deliberately unpromoted pending its own A/B (config.py:59-66) |
| `LLM_FALLBACK_MODEL` | unset | When set, one failed call retries here; usage records the model that actually served |
| `LLM_NUM_RETRIES` | 2 | Each retry is a **fully billed** call, so this multiplies worst-case cost (config.py:91) |
| `LLM_MAX_OUTPUT_TOKENS` | 4096 | Global output ceiling. Overridden per task to 16384 for `playbook`, `extraction`, and `pattern` — read the comment at config.py:96-131 before touching it; a 4096 ceiling once shipped a playbook with zero steps while reporting success |
| `DEFAULT_DAILY_TOKEN_LIMIT` / `DEFAULT_DAILY_COST_CAP_USD` / `DEFAULT_BUDGET_ACTION_ON_EXCEED` | 2,000,000 / $25 / `block` | Applied to any tenant with no explicit budget row. Before these existed, "no row" meant "no limit", so a fresh tenant was the only uncapped one (config.py:191-198) |
| `REDACTION_ENABLED` | `True` | PII and secret scrubbing before any embed or LLM call. Turn off only for local debugging |
| `EPISODE_RESOLUTION_GATE` | `off` (`off` \| `cluster`) | `cluster` defers synthesis for evidence clusters showing no sign of a fix anywhere |
| `EPISODE_AI_REVIEW` | `off` (`off` \| `advisory` \| `auto_approve`) | Whether an AI first pass stamps a verdict on episode drafts, and whether it may approve them. **These three are the only modes.** |
| `RETENTION_PURGE_MODE` / `RETENTION_DEFAULT_DAYS` | `soft_purge` / 365 | Weekly purge behaviour and the base retention window |
| `TENANT_PROMPT_VARIANTS_JSON` | `{}` | Per-tenant prompt A/B: `{"<tenant-uuid>": {"relevance": "v3"}}`. Malformed JSON degrades to defaults with an error log — it can never crash ingest |
| `SMTP_*` / `NOTIFICATION_WEBHOOK_URL` | empty | Notification channels are explicit no-ops until configured, and say so in the log |

---

## 8. Project Directory Structure

Understanding the layout of the code is absolutely crucial for navigating the repository efficiently. The project is structured as a **Monorepo**, meaning it contains both the frontend code and the backend code in the same Git repository, managed by a root `Makefile`.

### Top-Level Folders

- **`.git/`**: Internal Git version control folder. Do not touch.
- **`backend/`**: Contains all the Python code. This includes the FastAPI application, database SQLAlchemy models, AI provider logic, and Celery workers. This is the entire brains of the platform.
- **`codewiki/`**: This is a unique folder. It contains narrative technical blueprints detailing specific, complex architectural choices (e.g., how the chunking algorithm works, how the graph adjacency is designed). It serves as a living, narrative technical diary for developers.
- **`docs/`**: Contains formal, critical documentation files like the Setup Guide, API references, Runbooks, and this Project Overview.
- **`frontend/`**: Contains all the Next.js, React, TypeScript, and Tailwind CSS code that makes up the user interface dashboard.
- **`data/` & `data2/`**: Local directories (which are usually included in `.gitignore`) created by Docker Compose. They are used to persist database records and object storage files on your host machine so you don't lose all your test data every time the containers restart.

### Deep Dive: `backend/` Structure
The backend is highly structured following Domain-Driven Design principles where possible.
- **`backend/alembic/`**: Contains database migration scripts generated by the Alembic tool. Every time the SQLAlchemy models change, a new script is generated here to update the live database schema.
- **`backend/src/contextedge/api/`**: Contains the HTTP entry points (FastAPI Routers). These files define the URL endpoints, accept incoming requests, validate them, and pass them to services.
- **`backend/src/contextedge/models/`**: Contains SQLAlchemy Object-Relational Mapping (ORM) definitions. This defines exactly how Python objects map to PostgreSQL tables and relationships.
- **`backend/src/contextedge/schemas/`**: Contains Pydantic models. These are used strictly to validate incoming HTTP request bodies and to format outgoing JSON responses, ensuring data integrity at the boundaries.
- **`backend/src/contextedge/services/`**: Contains the core business logic. This is where the actual work happens. Services are independent of HTTP or Celery, meaning a service function can be called by an API route or a background worker without modification.
- **`backend/src/contextedge/connectors/`**: Contains the adapter code that knows how to authenticate and communicate with external tools like ServiceNow, Teams, or Gmail.
- **`backend/src/contextedge/workers/`**: Contains the Celery task definitions. These are the entry points for background processing jobs.
- **`backend/src/contextedge/ai/`**: Contains everything related to AI operations: provider wrappers (LiteLLM), prompt templates, token counting logic, and extraction logic.
- **`backend/src/contextedge/search/`**: Contains the complex query building logic required for combining Full-Text Search, Vector Search, and Graph traversal into a single ranked result set.

### Deep Dive: `frontend/` Structure
The frontend follows the standard Next.js App Router conventions.
- **`frontend/src/app/`**: This directory defines the actual URLs and layouts of the dashboard using the Next.js App Router (e.g., `app/dashboard/page.tsx` becomes the `/dashboard` URL).
- **`frontend/src/components/`**: Contains reusable React components (buttons, data tables, navigation bars).
  - **`frontend/src/components/ui/`**: Specifically houses the shadcn/ui base components that provide the accessible, styled foundation.
- **`frontend/src/lib/`**: Contains helper functions, utility code, type definitions, and API clients.
  - **`frontend/src/lib/hooks/`**: Contains custom TanStack Query hooks for fetching data from the backend cleanly (e.g., `usePlaybooks.ts`).
  - **`frontend/src/lib/stores/`**: Contains Zustand state management files for global UI state (like tracking the currently selected tenant across different pages).

---

## 9. Deep Dive: The Ingestion Pipeline

To fully appreciate ContextEdge, one must understand how data actually enters the system. The ingestion pipeline is the unsung hero of the platform.

When a tenant admin configures a new source (say a ServiceNow instance), they provide credentials, which are Fernet-encrypted at rest. Outside development, a missing or placeholder `FERNET_KEY` is a hard startup error rather than a silently-minted transient key — otherwise stored credentials become unrecoverable garbage (backend/src/contextedge/config.py:254-264).

**Step by step, with the real mechanics:**

1. **Discovery** enumerates what the source offers (ServiceNow tables, Zoho modules, Gmail labels) and creates one `source_objects` row per item. Each carries two independent approval flags — `approved_for_backfill` and `approved_for_sync` — so nothing is pulled until a human says so.
2. **Backfill** is the one-time historical sweep. **Incremental sync** is the every-15-minutes catch-up, dispatched by the `sync.trigger_scheduled_syncs` beat entry (backend/src/contextedge/workers/sync_tasks.py:14). Incremental sync with **no checkpoint refuses to run** and reports `skipped_no_checkpoint` — it will never surprise you with a full historical pull on a schedule.
3. **Single-flight.** Each sync takes a transaction-scoped Postgres advisory lock keyed on the source object. A second worker returns `skipped_locked` rather than racing the checkpoint, and because the lock is transaction-scoped, a crashed worker cannot leak it (backend/src/contextedge/services/sync_worker_service.py:379-395).
4. **Raw persistence.** `persist_ingestion_events` writes one `raw_evidence_objects` row per event, with the payload stored **inline as JSONB** and a SHA-256 content hash for deduplication (backend/src/contextedge/services/ingestion_persistence.py:19).
   **Correction to earlier revisions of this document:** raw payloads are *not* all written to object storage. Only payloads larger than `OFFLOAD_THRESHOLD_BYTES = 32_768` go to MinIO, and only then does the database row hold the stub `{"_offloaded": true, "size_bytes": N}` with a key pointing at the blob (ingestion_persistence.py:16, 84-87). Everything smaller lives entirely in Postgres.
   The reason for keeping the raw payload at all is exactly as stated before: if extraction logic changes, we re-process locally instead of re-downloading from the source.
   **The trap this creates**, and it has bitten real backfills: any SQL that filters or sorts on `raw_payload` silently skips the offloaded rows, because they only contain the stub. That means the *longest* tickets and articles — the ones you most want — are exactly the ones a SQL backfill misses. Re-sync rather than backfill by SQL.
5. **Crash-safe handoff.** The job commits the raw rows, *then* enqueues one `normalize_evidence` task per new row, so a worker can never read an uncommitted row. If the broker fails partway through, the un-enqueued ids are parked on the source object under `metadata_extra["pending_normalize_raw_ids"]`, the run is marked failed with a handoff record, and the next successful run drains the backlog first (backend/src/contextedge/services/sync_worker_service.py:301).
6. **Cooperative pause and cancel.** An operator can pause or cancel a running sync. The connector polls a control flag between pages and every 25 detail records, and the check runs on its own fresh database connection — because the sync job's transaction started before the operator's write and literally cannot see it (sync_worker_service.py:398-416). A stop **persists everything already fetched, with its checkpoint**; cancel is not a rollback.
7. **Rate limiting — be precise about this.** Every connector declares a `rate_limit_config()` with requests-per-second and burst size, **but nothing consumes it today** (declared at backend/src/contextedge/connectors/base.py:140 and on each connector; no caller anywhere). There is no Redis token bucket. What actually protects you from being throttled is per-connector retry logic: bounded attempts with backoff that honours the `Retry-After` header on 429 and 5xx responses, and immediate failure on other 4xx.
8. **Error handling.** A connector exception marks the run failed, leaves the checkpoint un-advanced, and lets Celery retry — backfill 3 times at 120s, incremental sync 5 times at 30s (backend/src/contextedge/workers/sync_tasks.py:39, 68).

**A source-specific warning worth internalizing.** The Zoho Desk connector caches OAuth access tokens process-wide, because Zoho allows only a handful of token exchanges per minute and a limited number of live tokens — and **exceeding either returns empty results rather than an error**. The measured symptom was 11 of 20 hydrated threads stored as empty while every task reported success. Any connector whose API answers throttling with silence needs this kind of defence, and the general lesson is worth carrying: a sync that reports success is not evidence that data arrived.

---

## 10. Deep Dive: AI Episode Reconstruction

Once the data is ingested, it is still a chaotic mess of comments. A ticket might have 40 comments spanning three days. Episode reconstruction makes sense of it.

**What is assembled before any model sees anything.** Reconstruction does *not* run on a single ticket. `resolve_episode_cluster` first materializes the connected component over case links and correlation edges — for the Acme VPN incident, that pulls the ServiceNow ticket, the Teams thread, and the quoting email into one set. The cluster is bounded at 50 members, 3 hops, and a 30-day window from the nearest seed, and legal-hold and pending-redaction rows are excluded **in the SQL query itself**, so a withheld record can never reach a model even accidentally.

**The gates that run before spending a call** (`_reconstruct`, backend/src/contextedge/workers/extraction_tasks.py:995), in order:

1. **Debounce, 180 seconds**, re-checked at run time — a thread still filling up is left alone. A starvation guard forces synthesis within 30 minutes anyway, so a never-quiet channel still gets narrated.
2. **Minimum cluster size 3.** Honest caveat: a two-item cluster that never grows is skipped *terminally*, not deferred.
3. **An optional resolution gate**, off by default, that defers clusters showing no sign of a fix anywhere in them. It reads the source system's own `resolved` status first, and only then falls back to matching prose.
4. **A per-cluster advisory lock.** Eight concurrent workers once minted eight identical episodes in 46 seconds.
5. **Draft idempotency** on a fingerprint of the exact member set.
6. **A growth gate:** re-narrating requires the cluster to be at least 1.5× the size of an episode that already covers it. Without it, ten new messages on a ten-item cluster paid for ten full syntheses of which a dedup sweep retired nine.

**The call itself.** Each item is labelled `[ev-1]`, `[ev-2]`, … with its source role, and the whole block is wrapped in untrusted-content markers before being sent. Up to 20 items go in one call; bigger clusters are chunked.

**Quality control — and a correction.** Earlier revisions of this document said "a smaller validation LLM checks the output." That is **not** what happens, and the difference matters: a second model would be another thing that can hallucinate. What actually runs is:

- **A deterministic schema gate** that is strict about structure and lenient about vocabulary. A structurally broken episode is **dropped** with a warning rather than repaired; an unrecognized step type quietly becomes `observation`; confidences are clamped to [0,1]; malformed individual steps drop without taking the episode with them.
- **Citation translation.** `[ev-N]` labels are mapped back to real evidence IDs, and any label the model invented is discarded. **The model cannot mint a reference to evidence that does not exist.** If no valid citation survives, membership falls back to the full cluster — and that fallback is logged, never silent.
- **Provenance stamped by the code, after validation** — which prompt, which version, which model was requested, and the correlation ID that joins to the usage record. The model never supplies its own provenance.

**Then a human — or a gated machine — decides.** Every episode is born `pending_review`. When `EPISODE_AI_REVIEW` is enabled, an hourly sweep either stamps an advisory verdict or, in `auto_approve` mode, approves drafts that clear the model verdict **and** four deterministic floors: at least 2 evidence items, a final outcome of at least 20 characters, a verdict of exactly `approve`, and confidence at least 0.8 (backend/src/contextedge/services/episode_review_service.py:42-44, 89-101). Auto-approved episodes carry no reviewer id, so they remain permanently distinguishable from human approvals. The setting has exactly three values — `off`, `advisory`, `auto_approve` — and a per-dispatch override can only *downgrade*, never escalate.

This process transforms unstructured chat into structured data that can be searched mathematically — and, just as importantly, refuses to do so when the record is not ready.

---

## 11. Deep Dive: The Context Graph

One of the most interesting parts of ContextEdge is the graph. We don't just store flat documents; we link them together.

- **Nodes:** an `episode`, a `pattern`, a `playbook`, an `evidence` item, an `entity` (a CI, a service, a team), a `decision`, an `issue_signature`, and more.
- **Edges:** the connections. `playbook -derived_from-> pattern`, `episode -belongs_to-> pattern`, `pattern -supported_by-> evidence`, `evidence -affects_ci-> entity`.
- Everything lives in one table, `graph_edges` (backend/src/contextedge/models/pattern.py:174-273).

**Three design choices worth understanding:**

- **`weight` and `confidence` are separate columns.** How much a relationship should matter when walking the graph is a different question from how strongly we believe it is true. Conflating them was a real bug (backend/src/contextedge/graph/builder.py:63-72).
- **The edge vocabulary is closed and enforced at write time.** `require_registered` runs inside `add_edge`, `ensure_edge`, `close_edge`, and `replace_edge`. Before that existed, a typo at a write site produced a real, queryable edge that the agent projection silently ignored — the graph knew something nobody could see, and nothing failed (backend/src/contextedge/graph/edge_types.py:1-27). Adding a type is deliberately two decisions: register it, and then either allow the agent to traverse it or record *why not*.
- **Writes go through `ensure_edge`, never a bare INSERT.** It selects, then inserts with `ON CONFLICT DO NOTHING` against a partial unique index on the active edge, then re-selects for the race loser — so two workers racing cannot abort the surrounding transaction (builder.py:50-135).

**Why not Neo4j?**
Graph databases are another datastore to operate, back up, and keep consistent with the relational source of truth. Keeping edges in Postgres means one database, one transaction boundary, and one backup.

**A correction to earlier revisions:** traversal is **not** a recursive Common Table Expression. `get_neighbors` is an **iterative breadth-first search in Python**, issuing one indexed query per hop, capped at 3 hops, with subgraph payloads bounded at 250 nodes and 500 edges because the UI renders the whole response without virtualization (backend/src/contextedge/graph/queries.py:12-17, 20-81).

The agent-facing read path is separate and much more careful: it resolves seeds through several layers, decays relevance 0.72 per hop, and hydrates each node type behind a fail-closed visibility check. See [02_Project_Architecture.md](02_Project_Architecture.md) §8.4.

---

## 12. Deep Dive: The Hybrid Search Ranker

When a user searches for an answer, how do we pick the best playbook out of potentially thousands? We use a hybrid ranker (backend/src/contextedge/search/hybrid_ranker.py:213).

**A correction first:** earlier revisions said the ranker uses Reciprocal Rank Fusion. It does not. It computes a **weighted sum of independent signals**, and every candidate carries the full breakdown so a human can see exactly why it scored what it scored.

Current weights (`RankingWeights`, hybrid_ranker.py:22-31):

| Signal | Weight | What it measures |
|---|---|---|
| semantic | 0.30 | cosine proximity between the query and the playbook's linked evidence |
| keyword | 0.25 | PostgreSQL full-text rank — catches a specific hostname that vector search would blur away |
| graph distance | 0.15 | how connected this playbook is to the evidence the query already matched |
| recency | 0.10 | (assigned the freshness value, so freshness effectively carries 0.15) |
| evidence quality | 0.10 | the reviewed confidence of the published version, plus query-specific evidence support |
| identity | 0.05 | edges from the playbook to identities named in the query |
| freshness | 0.05 | decays over 180 days since last validation; zero once expired |
| negative penalty | −0.05 | contradiction edges and recorded negative knowledge in the domain |

**Mechanics that matter:**

- **The semantic score is gated by the keyword score:** `min(1.0, semantic × (0.6 + 0.4 × keyword))`. Pure vector drift cannot carry a playbook that shares no vocabulary with the query.
- **Search is chunk-aware.** The semantic pass runs over `evidence_chunks`, deliberately oversampled, then diversified with maximal-marginal-relevance *at the chunk level* so forty near-identical chunks from one thread cannot crowd out three distinct sources, then rolled up to one hit per parent record scored by its closest chunk. A second pass over parent embeddings is merged in so evidence that was never chunked still surfaces (backend/src/contextedge/search/vector_search.py:204-243).
- **Abstention is a feature.** Everything below 0.35 is dropped; when candidates existed but all fell short, the ranker logs `ranking.abstained` and returns nothing. "We have no good answer" is a legitimate result and the API says so honestly.
- **Degradation is graceful and per-signal.** A failed query embedding zeroes the semantic signal; a failed per-playbook search zeroes that one playbook's semantic score; a corrupt chunk vector makes the diversification step fall back to plain distance ordering. None of these fail the request.

---

## 12b. Deep Dive: The Full Pipeline, Task by Task

This is the section to keep open while reading the code. It answers "the Acme VPN ticket arrived — what ran, in what order, and where does each step live?"

### The task chain

```text
sync.trigger_scheduled_syncs        [queue: sync]        every 15 minutes
  └─ sync.run_incremental_sync      [queue: sync]        one per approved source object
       └─ persist_ingestion_events                       raw rows; >32KB -> MinIO stub
            └─ (commit) extraction.normalize_evidence    [queue: extraction]  one per raw row
                 ├─ hydration.hydrate_thread             [queue: hydration]   pull the whole thread
                 │    └─ extraction.normalize_evidence   ... one per message (loops back)
                 ├─ artifact.extract_attachment          [queue: extraction]  parse attachments
                 ├─ extraction.chunk_evidence            [queue: embedding]
                 │    └─ extraction.embed_chunks_batch   [queue: embedding]   batches of 32
                 ├─ extraction.compute_evidence_baseline [queue: correlation]
                 └─ extraction.correlate_evidence        [queue: correlation]
                      └─ extraction.reconstruct_episode  [queue: correlation] +180s debounce
                           └─ (approval) evaluation.extract_issue_signature   [queue: evaluation]
                           └─ (approval) pattern.cluster_episodes             [queue: pattern]
                                └─ pattern.generate_playbook_candidate        [queue: pattern]
```

Two structural rules make this safe, and both are worth memorizing:

1. **Every task commits before it dispatches.** A message consumed before its transaction commits would read pending state and quietly no-op with no retry. That is why fan-out always happens in the task *wrapper*, after `run_async` returns (backend/src/contextedge/workers/asyncio_runner.py:31-34).
2. **The hydration loop terminates by construction.** A hydrated message carries a thread id like its parent, but normalization refuses to request hydration for a hydrated message, and re-delivered messages deduplicate at the raw layer. Without that guard, each message would re-hydrate its own thread — measured at 10× amplification.

### What each stage costs, and what stops it

The design is dominated by one theme: **spend the cheap deterministic check first, and only then pay a model.** Every gate below exists because someone measured what it cost to not have it.

| Gate | Where | What it saves |
|---|---|---|
| Hydrated-message noise gate | `services/message_filter.py` | 47% of 18,907 live messages never reach a model at all |
| Quoted-text stripping | `services/thread_text_service.py` | ~92% of raw conversational characters were repetition |
| Relevance skip gate (≥0.75 confidence) | extraction_tasks.py:475-479 | No embedding, identity, decision, or chunking for confidently-irrelevant items |
| Identity candidacy gate | `services/identity_candidacy.py` | Identity work was 78% of all model spend before it |
| Facet-stated applicability | extraction_tasks.py:704-719 | Skips a ~7,200-token call whenever the source already states environment and version |
| Episode debounce / min-cluster / growth gates | extraction_tasks.py:746-834 | Episode synthesis was 29% of tokens with 71% of output later superseded |
| Sweep deferral during bulk ingest | `workers/pattern_tasks.py:736-748` | Stops dedup and AI review churning drafts the next burst regrows |

### Following the Acme VPN incident through

1. **Sync** pulls `INC0010427` from ServiceNow. A raw row lands with the payload inline (it is well under 32 KB).
2. **Normalize** derives the title and body, hashes the raw body, redacts it, and creates the evidence row. `evidence_type` resolves to `incident`, and because ServiceNow states a numeric state, `case_state` resolves to `resolved` once it is closed.
3. Because the payload carries a thread id and this is the parent record, **hydration** fires. The Teams work-notes exchange becomes N message rows. *"Any update on the VPN?"* dies at the noise gate — under 150 characters with no technical signal, and no evidence row is created. *"Restarted IPSec on vpn-gw-east-01, tunnel stable"* survives on the hostname signal and becomes its own evidence item.
4. **Identity resolution** extracts `vpn-gw-east-01`. Because a single-token device name matching the hostname pattern is promoted to a *strong* identifier, it resolves deterministically at confidence 1.0 from its second sighting onward. The engineer's name goes to adjudication and links only at ≥0.95, because people carry the stricter threshold.
5. **Chunking and embedding** make the ticket and each substantive message individually findable.
6. **Correlation** links the email to the ticket at confidence 1.0 — it quotes `INC0010427`, and the ticket-number bridge resolves that into a case membership regardless of which arrived first. The Teams thread joins through its shared `vpn-gw-east-01` identity at 0.75, because a gateway is a rare, non-person entity.
7. **Reconstruction** waits 180 seconds for the thread to settle, resolves the cluster (ticket + thread + email), and narrates one episode instead of three single-source fragments.
8. **Review** — human, or the gated AI first pass.
9. **Issue signature** fingerprints it as roughly `remote_access|tls_certificate|certificate_expired`, so the same failure six months later is recognizable as a recurrence rather than a novelty.
10. **Clustering** groups it with the five previous certificate-expiry episodes into one pattern.
11. **Playbook generation** retrieves the certificate-renewal SOP as a *normative* source alongside the *empirical* episodes, keeps the SOP's "back up the certificate first" step that engineers kept skipping, and records the disagreement in a `conflicts` block for the reviewer.

### Where things silently go wrong, and how to tell

| Symptom | Likely cause |
|---|---|
| Evidence lands but no episodes ever appear | No worker is consuming the `correlation` queue |
| Search returns nothing for records you know exist | No worker is consuming the `embedding` queue, so chunks are written but never embedded |
| Chunks stuck with a NULL embedding | Tenant hit its daily LLM budget with action `block`; check for usage events with `outcome = budget_exceeded` |
| Workers start and immediately exit | Database revision is behind the code's Alembic head; run `alembic upgrade head` |
| A sync reports success but stored nothing | Source-side throttling that answers with empty results rather than errors — the Zoho token-quota failure mode |
| A backfilled column is empty for the largest records only | A SQL backfill over `raw_payload`, which cannot see offloaded rows |

`GET /api/v1/admin/pipeline-health` exists for the first four: it reads queue depth per lane **plus in-flight unacknowledged work**, and counts the graph chain end to end so the first zero in the sequence is the diagnosis. The in-flight number matters — during the reconstruction phase every queue can read zero while thousands of debounced tasks are still churning.

---

## 13. Deep Dive: The Next.js Frontend App Router

The frontend uses the absolute latest React features, specifically the Next.js App Router (introduced in Next.js 13+).

- **Server Components (RSC):** By default, components in Next.js now render entirely on the server. This means less JavaScript is sent to the user's browser, making the app much faster.
- **Client Components:** When we need interactivity (like a button click or a text input), we use the `"use client"` directive at the top of the file to tell Next.js this component must run in the browser.
- **Data Fetching:** We use TanStack Query inside Client Components to fetch data, but for initial page loads (like the main dashboard overview), we fetch data directly in the Server Components to eliminate loading spinners entirely.

---

## 14. Deep Dive: API Middleware Architecture

The FastAPI backend registers exactly **two** middlewares plus CORS. Because Starlette wraps the last-added middleware outermost, the order in `create_app` is the reverse of the order a request travels (backend/src/contextedge/main.py:122-130).

**Effective request order:**

1. **CORS Middleware** — ensures the request is coming from an allowed origin.
2. **`TenantContextMiddleware`** (backend/src/contextedge/middleware/request_context.py:87) — mints or propagates `X-Request-ID`, `X-Correlation-ID`, and `X-Causation-ID` (correlation defaults to the request id if the caller did not supply one), decodes the Bearer JWT or `X-Service-Token` to stamp `request.state.tenant_id` / `user_id` / `roles`, and binds all of it into ContextVars. It echoes the request and correlation ids back on the response. It skips `/health`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/metrics`, and the login route (request_context.py:77-85).
3. **`RequestAuditMiddleware`** (backend/src/contextedge/middleware/request_audit.py:29) — runs **after** the response, and only for `POST`/`PATCH`/`PUT`/`DELETE` under `/api/v1`. It always writes one structured log line, and additionally inserts an `audit_logs` row when a tenant was resolved, using a separate synchronous engine on a worker thread. It swallows its own database failures: **auditing must never turn an allowed request into a failure.**
4. **The route**, and only here does authentication actually get *enforced*, via `Depends(get_current_user)` (backend/src/contextedge/deps.py:72).

**Two things earlier revisions of this document got wrong, both worth correcting:**

- There is no "request took longer than 2 seconds" warning in the middleware. Latency lives in the Prometheus metrics, not in a middleware threshold.
- **The middleware does not enforce the token, and it does not inject `WHERE tenant_id = X` into your queries.** It makes the tenant id *available*; every service query must apply it itself. A missing tenant predicate in a query is a security bug, not a style issue — treat it that way in review.

One detail that looks odd but is deliberate: the global exception handler re-adds CORS headers by hand, because it runs in Starlette's outermost error middleware — *outside* `CORSMiddleware` — and without them a browser cannot read the `request_id` the handler exists to provide (main.py:147-158).

**Unauthenticated probes have a blind spot worth knowing.** A 401 on a mutating route never resolves a tenant, so it produces no `audit_logs` row — it exists only in the structured log. If you are alerting on failed authentication attempts, alert on the log line, not the audit table (request_audit.py:59-64).

---

## 15. Security, Compliance, and RBAC

Security is baked into the foundation of ContextEdge.

- **Role-Based Access Control (RBAC).** The role names actually used are `platform_super_admin`, `tenant_admin`, `domain_admin`, `knowledge_manager`, and `playbook_reviewer` — not generic Viewer/Editor/Admin. Routes call `require_role(...)`, which raises 403 (backend/src/contextedge/deps.py:46-51).
  - **`has_role` gives a blanket pass to `platform_super_admin`, `tenant_admin`, and `admin`** (deps.py:37-44).
  - **Role bindings record a scope, but nothing enforces it.** Login selects role *names* only, so a "domain admin for Networking" holds that role across the entire tenant on every gated route. Finer scoping exists only through service-token domain allowlists, which *are* enforced where routes consult them. This is deliberately documented rather than half-fixed — a partially honoured scoping change is worse than a known limitation. Single-domain tenants are unaffected.
  - **Nav visibility is not authorization.** The frontend treats only `platform_super_admin` as a super-role, so a tenant admin sees fewer links than the API would let them call.
- **Tenant Isolation.** Every domain table carries `tenant_id`, and every query must filter on it. The middleware supplies the value; it does not rewrite SQL, so a service with a missing tenant predicate really can read another tenant's rows. Search surfaces add three further predicates in the same SQL — legal hold, pending redaction, and role-excluded access policies — through one shared helper so every search path agrees (backend/src/contextedge/search/vector_search.py:49-70).
- **Encryption at Rest.** Source credentials are encrypted with Fernet. Outside development, a missing or placeholder `FERNET_KEY` raises at import rather than minting a transient key, because a transient key silently turns every stored credential into unrecoverable bytes (backend/src/contextedge/config.py:254-264). The same guard exists for a default JWT secret (config.py:248-252).
- **Redaction before the model, not after.** PII and secret scrubbing runs during normalization, before the classifier, the embedder, the extractors, and the database write. The LLM only ever sees masked text.
- **Injected content is fenced.** Anything originating in a ticket, chat message, or email is wrapped in explicit untrusted-content markers before it enters a prompt — for identity extraction, decision extraction, episode synthesis, pattern synthesis, and the agent's graph context. Node labels and ticket bodies are text an outsider can write; treating them as instructions would be a prompt-injection channel.
- **Human-in-the-Loop.** AI proposes; a human approves. Where a machine *can* approve — the `auto_approve` episode review mode — it must clear deterministic floors the model has no influence over, and the resulting record is permanently marked as having had no human reviewer.
- **Agent output cannot feed itself.** A decision authored by an agent and still pending review is invisible to the agent projection. Without that rule, an agent's own unreviewed conclusion would come back to it as evidence.

---

## 16. Scaling Strategies

As ContextEdge grows, it is designed to scale horizontally.

- **Web Tier:** The FastAPI application is stateless. You can run many instances behind a load balancer.
- **Worker Tier:** Celery workers scale independently. But scale them **per lane**, not in aggregate: the eight queues exist precisely because a shared FIFO let bulk normalization starve correlation and embedding, silently. Adding workers that do not consume `correlation` and `embedding` makes that failure worse, not better. `backend/dev.py:16` is the authoritative queue list.
- **A real ceiling to plan for:** each worker task opens its own database connections (a fresh NullPool engine per task), so total connections scale at roughly 2-3× worker concurrency. Size Postgres `max_connections` against that, not against the API's pool of 20+10.
- **Provider concurrency is usually the binding constraint**, not CPU. Ticket processing is roughly 95% waiting on the model, so throughput tracks how many concurrent model calls your quota permits — and concurrent thread hydration can get you rate-limited by the *source* system before the model provider notices.
- **Database Tier:** Vector search uses `halfvec` expression HNSW indexes (migration `0032`), not plain-`vector` HNSW — pgvector's HNSW on the `vector` type caps at 2,000 dimensions and ContextEdge stores 3,072, so the earlier indexes never actually existed and every similarity query was a sequential scan until this was fixed. Two operational consequences: the deployment needs pgvector ≥ 0.7 (`docker-compose.yml` pins `pgvector/pgvector:pg16`), and **every similarity query must order by the same `::halfvec(3072)` expression the index was built on** — a plain `cosine_distance` is a guaranteed sequential scan (backend/src/contextedge/search/vector_ops.py:11-15, 40-45). Because the indexes are shared across tenants while queries post-filter by tenant, callers also raise `hnsw.ef_search` to 200 per transaction, or a small tenant's rows can be missing from the candidate set entirely (vector_ops.py:24-37).
- Read replicas can offload heavy search queries from the primary later.

---

## 17. CI/CD and Deployment (Docker & Kubernetes)

ContextEdge is designed to be deployed using modern DevOps practices.
- **Dockerfiles:** Both the frontend and backend have highly optimized, multi-stage Dockerfiles. This ensures the final container images are extremely small and secure (no unnecessary build tools are included in the final image).
- **Makefile:** The `Makefile` at the root of the project provides standardized commands (e.g., `make up`, `make test`, `make lint`) so developers never have to remember complex Docker commands.
- **Kubernetes Readiness:** Because the system is stateless and configured entirely via environment variables, it is natively ready to be deployed to a Kubernetes cluster using standard Helm charts.

---

## 18. Local Development Setup Guide

[SETUP_GUIDE.md](SETUP_GUIDE.md) and [RUNBOOK.md](RUNBOOK.md) are authoritative. The mental model:

1. Ensure you have Docker Desktop and Python 3.12+ installed (the launcher enforces the minimum version).
2. Start the infrastructure containers, then the backend, workers, and frontend.
3. If you change a database model in `backend/src/contextedge/models/`, generate a migration with Alembic and apply it. **Workers refuse to start against a stale schema** — that is a feature, not a bug you should work around.
4. **When you start workers, consume every lane.** `python dev.py worker` does this for you — it passes `-Q default,sync,hydration,extraction,correlation,embedding,pattern,evaluation` (backend/dev.py:16, 102-126) and defaults to `-P solo` on Windows. If you copy a worker command from somewhere else, check the `-Q` list: an older command that omits `correlation` and `embedding` will ingest evidence and then silently build nothing.
5. **On Windows specifically:** the prefork pool does not work, and `-P threads` breaks the LLM lanes because litellm holds asyncio locks bound to the loop that created them. Run several separate `-P solo` processes for parallelism, and exactly one worker for `sync,pattern,evaluation` (clustering has no lock and must serialize). Exactly one beat process — a second beat double-dispatches every scheduled entry.

**A gotcha to expect on a big first backfill.** The default per-tenant budget is 2,000,000 tokens/day with action `block`, which a thread-heavy backfill can exhaust mid-run — the symptom is chunks left un-embedded rather than an error. Provision a real budget row for the tenant, or set the action to `warn` for the duration of the window.

---

## 19. Future Roadmap

The current platform captures and serves knowledge; the next moves are about acting on it and doing so safely.

1. **An executor.** Today there is **no executor and no write-capable agent tool** — all six MAF tools are read-or-propose, and the execution service is a ledger driven by external callers. The safety scaffolding was deliberately built first: approval bound to a specific artifact version by hash, an attempt ledger with real idempotency, scoped trust profiles that can only *veto*, skill and execution-contract registration, rollback plans, and escalation objects. That ordering is the point — honesty before autonomy.
2. **Proactive detection**, so a pattern's known trigger raises a case before a human files a ticket.
3. **Closing the measurement loop.** Several counters exist that nothing can currently feed, because the row they key on has no writer. Until that is closed, MTTR and first-time-right numbers are unmeasurable rather than merely unmeasured — and this document should not imply otherwise.

For what is genuinely open, in detail and with reasons, read [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md). It is maintained as an honest ledger rather than a marketing surface, including entries recording things that were measured, found not to work, and abandoned.

---

## 20. Frequently Asked Questions (FAQ) for Freshers

**Q: Why don't we just use a normal wiki like Confluence?**
A: Wikis require humans to manually write and update them. They go out of date instantly. ContextEdge writes its own documentation by observing reality, and updates it automatically when reality changes.

**Q: What is a Vector exactly?**
A: A vector is just a long list of numbers (e.g., `[0.1, -0.4, 0.9, ...]`). AI models convert words into these numbers. Words with similar meanings get similar numbers. This allows us to search by "meaning" instead of just matching exact letters.

**Q: If the database goes down, what happens to the API?**
A: The API will fail to serve requests and return 500 errors. However, any active background tasks (Celery workers) will safely pause and wait in the Redis queue until the database comes back online, ensuring no work is lost.

**Q: How does the AI know about our company's secrets?**
A: It doesn't. ContextEdge uses "Retrieval-Augmented Generation" (RAG). When we ask the LLM a question, we first search our database for the relevant evidence, and we paste that evidence into the prompt we send to the LLM. The LLM only knows what we explicitly give it in that specific prompt.

**Q: What is Celery Beat?**
A: Imagine you need to run a python script every day at midnight to clean up old files. You could use Linux `cron`. But in a distributed system, `cron` is dangerous (if you have 5 servers, it runs 5 times!). Celery Beat is a centralized scheduler that adds a task to the Redis queue on a timer.
**Important caveat:** Beat gives you "exactly once" only if you run **exactly one beat process**. Two beat instances double-dispatch every scheduled entry — including the retention purge and the AI review sweep. This is a deployment discipline, not something the code can enforce for you.

**Q: Why does the same job sometimes not run when it is scheduled?**
A: Several sweeps deliberately defer themselves. The knowledge-dedup and AI-review sweeps skip a tenant that is mid-ingest — more than 50 new evidence rows or more than 30 new episodes in the last 10 minutes — because consolidating drafts that the next burst is about to regrow wastes model calls and churns the review queue. They report the deferral rather than failing.

**Q: If the AI provider is down, does ingestion stop?**
A: No. Each enrichment inside normalization is individually error-trapped, so a provider outage produces evidence rows that are un-classified, un-embedded, and un-linked — but the rows land, and the raw payloads are kept. Re-processing later is a re-run, not a re-download. The one thing to watch is that a *relevance classifier* failure fails **open** (the item goes down the full path), which is the safe direction: over-processing noise is cheaper than missing a real incident.

**Q: What happens if two workers process the same thing at once?**
A: Every concurrent path has an explicit answer, and none of them is "hope". Two syncs on one source object: the second returns `skipped_locked` from a Postgres advisory lock. Two normalizations of identical content: the loser catches the unique-index violation, adopts the winner's row, and spends no model calls. Two reconstructions of one cluster: a per-cluster advisory lock, added after eight concurrent tasks minted eight identical episodes in 46 seconds. Two writers of the same graph edge: `ON CONFLICT DO NOTHING` plus a re-select, so neither aborts its transaction. An AI reviewer racing a human: the sweep re-reads the row `FOR UPDATE` after its model call, so the human always wins.

**Q: Why is Tailwind CSS better than regular CSS?**
A: It prevents "CSS bloat". In regular CSS, developers constantly add new classes because they are afraid of breaking old ones. Tailwind forces you to use predefined utility classes, meaning your CSS file size never grows, and you can instantly see exactly what styling applies to a component just by looking at its HTML.

---
*End of Extremely Comprehensive Document*

## 21. Appendix A: Glossary of Terms for Freshers

### ContextEdge's own vocabulary

These terms mean specific things in this codebase. Use them precisely; the whole team does.

- **Source** — a configured external system (one ServiceNow instance, one Gmail mailbox). **Source object** — one syncable thing inside it (a table, a module, a label), with its own approval flags and checkpoint.
- **Raw evidence object** — the untouched connector payload, kept so re-processing never means re-downloading.
- **Evidence** — one normalized, redacted, queryable fact with its provenance. Not "a document".
- **Chunk** — a searchable segment of one evidence item, with its own embedding. A 40-message thread can match on the one paragraph that matters.
- **Thread / hydration** — a conversation, and the act of pulling its full message list from the source. Hydration is where cross-message quote stripping happens, because only it holds the whole thread in arrival order.
- **Correlation edge / case link** — two ways records get related. A *case link* is a deterministic identifier match at confidence 1.0; a *correlation edge* also covers gated, scored identity co-occurrence.
- **Episode** — a time-bounded narrative slice: one incident's story, synthesized from a cluster of evidence, with ordered steps and an outcome.
- **Issue signature** — a generalized fingerprint of an episode's *problem*, with hostnames and ticket numbers deliberately stripped, so the same failure is recognizable when it returns.
- **Recurrence** — a low-confidence precedent link between a new occurrence and an earlier case with the same signature. **Similar problem, never the same occurrence** — the cluster resolver refuses to merge through it.
- **Pattern** — a recurring structure learned across episodes.
- **Playbook / playbook version** — a governed, versioned operational procedure. Only *approved* playbooks with a *published* version are served at runtime.
- **Negative knowledge** — a recorded "this did not work" or "these are not related", fed back so the system does not re-propose a known dead end.
- **Grounded vs best-practice step** — a playbook step that survived citation validation, versus one the model added with nothing behind it. The distinction is enforced structurally, not by asking the model.
- **Tenant / workspace / domain** — the isolation boundary, and two levels of scoping inside it. Always mention the tenant when discussing search or security.
- **Projection (`maf.v1`)** — the bounded, visibility-filtered view of the graph an AI agent is allowed to see. Not the graph itself.

### General technical terms

- **API (Application Programming Interface):** A set of rules that allows one piece of software to talk to another. Like a menu in a restaurant; you ask for something from the menu, and the kitchen (backend) gives it to you.
- **Backend:** The part of the software that runs on the server, hidden from the user. It handles the database, logic, and security.
- **Frontend:** The part of the software the user sees and interacts with in their web browser (buttons, text, colors).
- **Cache:** A temporary, extremely fast storage area. Used so the system doesn't have to repeatedly calculate or fetch the same data over and over.
- **Continuous Integration / Continuous Deployment (CI/CD):** Automated systems that test code every time a developer saves it, and automatically deploy it to servers if the tests pass.
- **Container / Docker:** A way to package an application and all its required files into a single box (container) so it runs exactly the same on any computer.
- **Database Schema:** The blueprint of the database. It defines what tables exist and what columns each table has (like a giant Excel spreadsheet's headers).
- **Environment Variable (.env):** Settings that change depending on where the code is running (e.g., your laptop vs. production server). Passwords and API keys are stored here.
- **Endpoint / Route:** A specific URL where the API listens for requests (e.g., `https://api.contextedge.com/v1/playbooks`).
- **Framework:** A pre-built skeleton of code that developers use so they don't have to start from scratch. FastAPI is a backend framework; Next.js is a frontend framework.
- **JSON (JavaScript Object Notation):** A standard, easy-to-read text format for sending data between the frontend and backend. Looks like this: `{"name": "John", "age": 30}`.
- **Load Balancer:** A traffic cop for servers. If a website gets millions of visitors, a load balancer splits the traffic evenly across multiple backend servers so no single server crashes.
- **Monolith:** An application where all the code for the backend is written in one single large project.
- **Microservices:** Breaking a large application into dozens of tiny, separate applications that communicate over a network.
- **Middleware:** Code that runs *in the middle* of a request. Before the main logic runs, middleware might check if the user is logged in (Authentication) or record the time (Logging).
- **Object Storage (S3 / MinIO):** A place to store files (like images, PDFs, JSON text) rather than structured database rows.
- **ORM (Object-Relational Mapper):** Code that translates database tables into Python objects, so developers don't have to write raw SQL commands.
- **Queue / Broker:** A waiting line for background tasks. When the system is too busy, tasks wait in the queue until a worker is free to process them. Redis is our queue broker.
- **Vector / Embedding:** Transforming a paragraph of text into a mathematical coordinate (a list of numbers) so that an AI can calculate how similar two paragraphs are by measuring the distance between their coordinates.
- **JWT (JSON Web Token):** A digital badge given to a user when they log in. It proves who they are for all subsequent requests without needing a password every time.

## 22. Appendix B: Recommended Reading

For freshers looking to get up to speed on the technologies used in ContextEdge, we highly recommend reading the official documentation for the following tools before touching the codebase:

1. **FastAPI Official Docs:** Start with the "First Steps" and "Path Parameters" tutorials.
2. **Next.js App Router Docs:** Read about "Routing Fundamentals" and "Server vs Client Components."
3. **SQLAlchemy 2.0 Quickstart:** Understand how `Session` and `Engine` work.
4. **Celery First Steps:** Learn how task queues operate in Python.
5. **pgvector GitHub Readme:** See how vector similarity search actually works at the SQL level.
6. **Tailwind CSS Utility-First Concept:** Read the introductory page to understand why we don't write custom CSS.

## 23. Final Sign-off

This document should serve as your primary compass when navigating the ContextEdge project. Welcome to the team!

---
*End of Appendix*

## 24. Appendix C: Common Local Development Errors & Troubleshooting

When freshers first join the project and try to run `make dev`, they occasionally run into environmental issues. Below is a detailed list of common errors, why they happen, and exactly how to fix them.

### Error: "Port 5432 is already in use"
**What it means:** You are trying to start the Docker container for PostgreSQL, but your computer (the host) already has another PostgreSQL instance running on port 5432.
**Why it happens:** You probably installed PostgreSQL directly on your Windows or Mac machine previously, and it starts automatically in the background.
**How to fix it:** Stop your local PostgreSQL service. On Windows, open "Services", find PostgreSQL, and click "Stop". Alternatively, you can change the port mapping in `docker-compose.yml` from `5432:5432` to `5433:5432`, but you will also need to update your `.env` `DATABASE_URL` to point to port 5433.

### Error: "ModuleNotFoundError: No module named 'pydantic_settings'"
**What it means:** Python cannot find the `pydantic-settings` library when trying to run the FastAPI server.
**Why it happens:** You either forgot to activate your Python virtual environment (`.venv`), or you haven't installed the dependencies yet.
**How to fix it:** 
1. Ensure your virtual environment is activated: `source .venv/bin/activate` (Mac/Linux) or `.venv\Scripts\activate` (Windows).
2. Install the dependencies using the package manager defined in the project: `pip install -e .[dev]` from the `backend/` directory.

### Error: "redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379"
**What it means:** The backend code is trying to talk to Redis, but it cannot reach it.
**Why it happens:** Your Redis Docker container has crashed, or you forgot to start it.
**How to fix it:** Run `docker ps` to see if `redis` is running. If not, run `make up` to start the infrastructure containers.

### Error: "Alembic: Target database is not up to date"
**What it means:** You are trying to run the server, but your local database structure is older than the code expects.
**Why it happens:** Another developer added a new database column (a migration) and merged it to the `main` branch. You pulled their code, but you didn't apply their database changes to your local Postgres container.
**How to fix it:** Run `make migrate` from the root directory to execute all pending Alembic migrations.

### Error: "LiteLLM AuthenticationError" on a model call
**What it means:** The code tried to ask a model to classify or summarize something, and the provider rejected the request.
**Why it happens:** Credentials for the configured provider are missing or wrong. The shipped default provider is **Vertex AI**, not OpenAI — so the usual culprit is `GOOGLE_APPLICATION_CREDENTIALS` pointing at a missing or unauthorized service-account file, or `GOOGLE_CLOUD_PROJECT` being unset. `OPENAI_API_KEY` only matters if you have routed a task lane to OpenAI.
**How to fix it:** Fix the credential for whichever provider your `DEFAULT_*_MODEL` settings actually name, then restart. Note that ingestion will *keep working* while this is broken — each enrichment is individually error-trapped, so you get evidence rows that are un-classified and un-embedded rather than a loud failure.

### Symptom: "Evidence is arriving but no episodes or search results appear"
**What it means:** Normalization is running and the stages after it are not.
**Why it happens:** Almost always a worker fleet that is not consuming the `correlation` and `embedding` queues. Those two lanes were added after the original worker commands were written, and a command copied from an older document omits them.
**How to fix it:** Start workers with the full lane list from `backend/dev.py:16`, or just use `python dev.py worker`. Confirm with `GET /api/v1/admin/pipeline-health`, which reports depth per lane plus in-flight work.

### Symptom: "Chunks exist but their embeddings are NULL"
**What it means:** Chunking succeeded and embedding did not.
**Why it happens:** Either the `embedding` queue has no consumer (see above), or the tenant hit its daily LLM budget with action `block`.
**How to fix it:** Check for `llm.usage` events with `outcome = budget_exceeded` and look at `GET /api/v1/admin/tenant-budget/status`. Provision a budget row for the tenant, or switch the action to `warn` for the duration of a bulk ingest. Embedding is idempotent — the rows are picked up on the next replay once the block clears.

### Symptom: "Workers start and immediately exit"
**What it means:** The database schema is behind the code.
**Why it happens:** The worker startup gate compares `alembic_version` to the code's bundled head and raises `SystemExit` on a mismatch, so a supervisor restart-loops (backend/src/contextedge/workers/celery_app.py:83-139). This is deliberate: a worker consuming the normalize queue against a stale schema corrupts ingestion mid-transaction.
**How to fix it:** Run `alembic upgrade head`. Never quote a head *number* from a document — run `alembic heads` and trust that.

### Error: "Next.js Hydration Mismatch"
**What it means:** The HTML generated on the server (Server-Side Rendering) does not match the HTML generated on the client (the browser) during the first render.
**Why it happens:** Usually occurs when you use browser-specific APIs (like `window.localStorage` or `Date.now()`) directly in the initial render of a React component without waiting for the component to mount (`useEffect`).
**How to fix it:** Ensure any code that relies on the browser `window` object is wrapped inside a `useEffect` hook, or dynamically import the component with `ssr: false`.

## 25. Document Version Control

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | — | Initial comprehensive release: a foundational deep dive for new engineering and operational staff. |
| 1.1.0 | 2026-08-19 | Accuracy pass against the working tree, with `file:line` citations. Corrected: the raw-payload storage rule (inline JSONB, MinIO only above 32 KB), the middleware chain order and what it does *not* enforce, connector rate limiting (declared but unconsumed), episode-output validation (a deterministic schema gate, not a second model), graph traversal (iterative BFS, not recursive CTEs), the hybrid ranker (weighted sum with abstention, not Reciprocal Rank Fusion), the runtime Redis cache (an explain payload written after the match, not a request short-circuit), the configured model lineup (Vertex Gemini), the RBAC role names and the unenforced role scope, and the ANN index story (`halfvec` expression HNSW because 3,072 dimensions exceed pgvector's 2,000-dimension `vector` HNSW cap). Added: the Acme VPN running example, the pipeline stage map (§12b), the settings that change behaviour, and the worker-lane deployment trap. |

---
*End of Comprehensive ContextEdge Project Overview — accurate as of 2026-08-19*
