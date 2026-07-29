# ContextEdge — Project Overview

This document provides a comprehensive, extremely detailed overview of the ContextEdge platform. It is designed for new team members, junior developers, and stakeholders who want to understand the platform from the ground up. Every technical term is explained simply, ensuring that even a complete beginner can grasp the architecture and flows. This document is meant to be a deep-dive, leaving no stone unturned.

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

It achieves the following five core objectives:
1. **Ingestion and Discovery:** It connects to external sources (like Teams, Gmail, ServiceNow, Jira) and ingests operational evidence (tickets, chats, alerts). It does this safely, respecting data privacy and tenant boundaries.
2. **Episode Reconstruction:** It uses AI to read this fragmented evidence and reconstruct a structured "episode"—a step-by-step timeline of what happened, what was diagnosed, what failed, and how it was ultimately fixed. An episode takes chaotic chat logs and turns them into a clean story.
3. **Pattern Recognition:** It looks across many episodes to find patterns. If the same VPN issue happens 50 times, ContextEdge recognizes it as a pattern. It clusters these similar episodes together.
4. **Playbook Generation and Governance:** It generates a proposed "playbook" (a set of instructions or automations to fix the issue) based on these patterns. Crucially, a human reviewer must approve this playbook before it becomes active. This ensures "Human-in-the-Loop" safety.
5. **Runtime Retrieval:** When a new issue occurs, downstream systems or human analysts can query ContextEdge. It returns the best-matching, human-approved playbook, along with a confidence score and the exact evidence that justifies why this playbook is the right choice.

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

#### 19. Large Language Models (GPT-4o, Claude 3.5, Gemini)
- **What they are:** These are the actual AI models hosted by providers like OpenAI, Anthropic, and Google. They are neural networks trained on vast amounts of text.
- **Why they are used:** They perform the heavy lifting of reading messy human text (chat logs, ticket descriptions), classifying it, extracting structured steps, determining root causes, and generating the final clean playbooks. 

#### 20. Embedding Models (text-embedding-3-small)
- **What it is:** These are specialized AI models that don't generate text, but instead convert text into a mathematical vector (a long list of numbers).
- **Why it is used:** This enables semantic search. It takes the text of a playbook, runs it through the model, and saves the resulting numbers in Postgres via pgvector.

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
6. **Middleware Execution Pipeline:** 
   - *Audit Middleware* logs that a request started and begins tracking response time.
   - *Auth Middleware* intercepts the token, cryptographically verifies its signature, and determines the identity of the caller.
   - *Tenant Context Middleware* extracts the `tenant_id` and role permissions from the identity and stores them in Python ContextVars securely for this specific request thread.
7. **Controller Validation:** The route function receives the payload. Pydantic validates that the payload has the correct structure (e.g., ensuring `query` is a string and not empty).
8. **Service Layer Handoff:** The router immediately hands the validated data off to the Business Service layer (e.g., `runtime_service.py`), keeping the HTTP logic separate from business logic.
9. **Vector Embedding Generation:** 
   - The Service realizes it needs to mathematically understand the meaning of "User laptop is severely slow and overheating". 
   - It calls the AI Provider wrapper (LiteLLM) to pass this text to an embedding model (like `text-embedding-3-small`).
   - The model returns a vector (a list of floating-point numbers like `[0.012, -0.443, 0.881, ...]`).
10. **Database & Hybrid Graph Search:** 
    - The Service then asks the Hybrid Ranker to search the PostgreSQL database. 
    - The query automatically appends `WHERE tenant_id = X` to guarantee data isolation.
    - PostgreSQL uses `pgvector` to find playbooks with similar embeddings to the query vector.
    - Simultaneously, it uses Full-Text Search (FTS) to look for exact keyword matches.
    - It traverses the Context Graph (adjacency projection) to see if there are related historical patterns, connected symptoms, or negative knowledge (contradictions) associated with the matched playbooks.
11. **Repository & ORM (SQLAlchemy):** SQLAlchemy executes these incredibly complex SQL queries and returns clean Python ORM models back to the Service layer.
12. **Agentic Reasoner (Optional):** If this is part of an advanced agentic workflow, a small reasoning step might occur here to determine if the retrieved context is sufficient, or if the agent needs to ask the user a clarifying question before proceeding.
13. **Business Logic & Policy Application:** The Service calculates final confidence scores based on the hybrid search results. It checks token budget limits, applies tenant-specific risk policies (e.g., "Do not return playbooks involving database deletion"), and bundles the best playbook, the evidence trace, and any rollback caveats into a clean Response object.
14. **Serialization & Response:** FastAPI takes the Python Response object, uses Pydantic to serialize it rapidly into JSON, and sends the HTTP 200 OK response back over the network to the frontend.
15. **Frontend State Update:** TanStack Query on the frontend receives the JSON, updates its internal cache, and triggers a re-render of the React Component. 
16. **User Visibility:** The user or agent now sees the highly-contextual, historically-backed recommended playbook on their screen!

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
    
    API->>SVC: Call match_playbook(query, tenant_id)
    
    SVC->>Redis: Check if identical query is cached
    alt Cache Miss
        SVC->>LLM: Request Embedding for query text
        LLM-->>SVC: Return Vector [0.01, 0.45, -0.12...]
        
        SVC->>DB: Execute Hybrid Search Query
        Note over SVC,DB: Query combines Vector Similarity (pgvector),<br/>Full-Text Search, and Graph Traversal.<br/>Strictly filtered by tenant_id!
        DB-->>SVC: Return Matching Playbooks & Trace Evidence
        
        SVC->>SVC: Apply Risk Policies, Filter, Calculate Final Confidence
        SVC->>Redis: Store result in short-lived Cache
    else Cache Hit
        Redis-->>SVC: Return cached result instantly
    end
    
    SVC-->>API: Return Structured Result Object
    API-->>FE: HTTP 200 JSON Response (Serialized)
    
    FE->>FE: Update TanStack Query Cache & UI State
    FE-->>User: Render Playbook UI and Confidence Score
```

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
| `OPENAI_API_KEY` | The secret API key required to authenticate with OpenAI for generating text (LLM) and creating vector embeddings. |
| `SERVICE_TOKENS_JSON` | A JSON-formatted string defining static API tokens for machine-to-machine communication. It defines the token string, the assigned role, and allowed domains for each service account. |
| `DEFAULT_LLM_PROVIDER` | A string that tells the LiteLLM wrapper which AI provider to use as the default fallback (e.g., `openai`, `anthropic`, `vertex_ai`). |
| `APP_ENV` | Determines the execution environment. Valid values: `development`, `staging`, `production`. This controls logging verbosity, Swagger UI availability, and security strictness. |
| `APP_DEBUG` | Boolean (`True`/`False`). If `True`, enables verbose error tracing and developer tools. Must be `False` in production. |
| `APP_LOG_LEVEL` | Sets the structlog verbosity level. Standard values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Usually set to `INFO` in production to balance insight and performance. |
| `APP_CORS_ORIGINS` | A comma-separated list of web URLs that are allowed to make cross-origin API requests to the backend. A critical web security measure (e.g., `http://localhost:3000,https://app.contextedge.com`). |

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

When a tenant admin configures a new source (say, a Jira Service Desk project), they provide an API key. 
1. **The Sync Worker** wakes up on a schedule. It authenticates with Jira and pulls down all tickets modified since the last check.
2. It does not try to process these tickets immediately. Instead, it strips out the most useless data and saves the raw JSON payload into **MinIO** (our object storage). 
3. It creates a simple record in PostgreSQL called an `Evidence` record, containing just the metadata (Title, Author, Date) and a pointer to the MinIO file.
4. By saving the raw file to MinIO, if our AI extraction logic changes in the future, we don't have to re-download millions of tickets from Jira; we just re-process them from our local MinIO bucket. This saves immense amounts of time and API costs.
5. **The Rate Limiter:** To prevent getting banned by external systems, the sync worker respects rate limits using Redis-backed token buckets.
6. **Error Handling:** If an API fails, the task is retried with an exponential backoff (e.g., wait 5 seconds, then 15, then 45, etc.).

---

## 10. Deep Dive: AI Episode Reconstruction

Once the data is ingested, it is still just a chaotic mess of comments. A Jira ticket might have 40 comments spanning 3 days. AI Episode Reconstruction is the process of making sense of it.

1. **Extraction Worker:** A Celery worker takes the raw MinIO file and feeds it to an LLM (like Claude 3.5 Sonnet). 
2. The LLM is given a strict prompt: "Read this ticket. Ignore pleasantries ('Hi Bob, thanks'). Extract exactly what the root cause was, what steps were taken to diagnose it, and what the final resolution was."
3. The LLM returns a structured JSON object representing an "Episode".
4. This Episode is then embedded (turned into a vector) and saved in the database. 
5. **Quality Control:** Before saving, a smaller validation LLM checks the output to ensure it matches the schema and hasn't hallucinated steps that weren't in the original text.

This process transforms unstructured "chat" into structured "data" that can be searched mathematically.

---

## 11. Deep Dive: The Context Graph (Adjacency Matrix)

One of the most complex parts of ContextEdge is the Graph. We don't just store flat documents; we link them together. 

- **Nodes:** A node can be an `Episode`, a `Playbook`, a `Symptom`, or an `Error Code`.
- **Edges:** The connections between them. For example, `Episode 123` (Node) -> `RESOLVES` (Edge) -> `Error Code 500` (Node). 

**Why not Neo4j?**
Graph databases are notoriously hard to scale and maintain. By using an adjacency projection table inside PostgreSQL, we keep all our data in one place. When a search happens, PostgreSQL uses recursive Common Table Expressions (CTEs) to quickly trace these edges. This means if you search for "Error Code 500", the database instantly knows which playbook has historically solved the most episodes linked to that error code.

---

## 12. Deep Dive: The Hybrid Search Ranker

When a user searches for an answer, how do we pick the best playbook out of potentially thousands? We use a Hybrid Ranker.

1. **Semantic Search (Vector):** Using `pgvector`, we calculate the cosine distance between the user's query and the playbook text. This tells us if they "mean" the same thing. 
2. **Lexical Search (BM25/FTS):** We use PostgreSQL's built-in full-text search to look for exact keyword matches. If the user types a specific server name, vector search might ignore it, but FTS will catch it perfectly.
3. **Graph Boost:** We look at the graph edges. If Playbook A has been successfully used 50 times in the past month (many edges), and Playbook B has only been used once, Playbook A gets a massive score boost.
4. **Final Scoring:** The system uses Reciprocal Rank Fusion (RRF) to combine these three different scores into one final master ranking, ensuring the absolute most relevant playbook is always at the top.

---

## 13. Deep Dive: The Next.js Frontend App Router

The frontend uses the absolute latest React features, specifically the Next.js App Router (introduced in Next.js 13+).

- **Server Components (RSC):** By default, components in Next.js now render entirely on the server. This means less JavaScript is sent to the user's browser, making the app much faster.
- **Client Components:** When we need interactivity (like a button click or a text input), we use the `"use client"` directive at the top of the file to tell Next.js this component must run in the browser.
- **Data Fetching:** We use TanStack Query inside Client Components to fetch data, but for initial page loads (like the main dashboard overview), we fetch data directly in the Server Components to eliminate loading spinners entirely.

---

## 14. Deep Dive: API Middleware Architecture

The FastAPI backend uses a layered middleware approach. When a request comes in, it passes through several layers of security checks before it ever hits the actual business logic.

1. **CORS Middleware:** Ensures the request is coming from an allowed domain (like our frontend URL).
2. **Audit & Tracing Middleware:** Generates a unique `request_id` for every call. If the request takes longer than 2 seconds, it logs a warning. This `request_id` is passed down to all log statements so developers can trace exactly what happened during a specific user interaction.
3. **Authentication Middleware:** Extracts the JWT from the `Authorization: Bearer <token>` header. It validates the cryptographic signature. If the signature is fake or the token is expired, it immediately returns a 401 Unauthorized error.
4. **Tenant Context Middleware:** Once the user's identity is known, it extracts their `tenant_id`. It injects this ID into Python's `ContextVar` system. This is a brilliant mechanism that allows deep database layers to automatically filter `WHERE tenant_id = X` without developers having to manually pass the `tenant_id` down through every single function call.

---

## 15. Security, Compliance, and RBAC

Security is baked into the foundation of ContextEdge.
- **Role-Based Access Control (RBAC):** Users are assigned roles (e.g., Viewer, Editor, Admin). The backend uses dependency injection (e.g., `Depends(require_role('Admin'))`) on every route to ensure users can only do what they are allowed to do.
- **Tenant Isolation:** As mentioned earlier, `tenant_id` is enforced at the middleware layer. Even if a developer writes a bad database query in a Service, the middleware ensures that the query cannot access data belonging to another tenant.
- **Encryption at Rest:** All sensitive data (like the API keys used to connect to Jira) is encrypted in the database using Fernet symmetric encryption. Even if an attacker steals the database file, they cannot read the API keys without the master `FERNET_KEY`.
- **Human-in-the-Loop (HITL):** ContextEdge does not allow AI to automatically enforce playbooks on the company. AI proposes playbooks; a human expert must review, edit, and click "Approve". This ensures compliance with enterprise governance standards.

---

## 16. Scaling Strategies

As ContextEdge grows, it is designed to scale horizontally.
- **Web Tier:** The FastAPI application is completely stateless. You can spin up 1, 10, or 100 instances of the web server behind a load balancer to handle traffic spikes.
- **Worker Tier:** The Celery workers can be scaled independently of the web tier. If there is a massive backlog of Jira tickets to process, we simply launch more Extraction Workers.
- **Database Tier:** PostgreSQL is highly tuned, but eventually, if vector search becomes a bottleneck, the `pgvector` indexes (HNSW) allow for highly efficient nearest-neighbor lookups even across tens of millions of rows. Additionally, read-replicas can be added to offload the heavy search queries from the primary write database.

---

## 17. CI/CD and Deployment (Docker & Kubernetes)

ContextEdge is designed to be deployed using modern DevOps practices.
- **Dockerfiles:** Both the frontend and backend have highly optimized, multi-stage Dockerfiles. This ensures the final container images are extremely small and secure (no unnecessary build tools are included in the final image).
- **Makefile:** The `Makefile` at the root of the project provides standardized commands (e.g., `make up`, `make test`, `make lint`) so developers never have to remember complex Docker commands.
- **Kubernetes Readiness:** Because the system is stateless and configured entirely via environment variables, it is natively ready to be deployed to a Kubernetes cluster using standard Helm charts.

---

## 18. Local Development Setup Guide

For a new developer joining the team, here is the exact mental model for setting up the project locally:
1. Ensure you have Docker Desktop and Python 3.12 installed.
2. Run `make dev`. This launches PostgreSQL, Redis, MinIO, the Celery workers, the FastAPI backend, and the Next.js frontend all at once, hot-reloading when you change code.
3. If you need to make a database change, you edit the SQLAlchemy models in `backend/src/contextedge/models/`.
4. Then, run `make migrate-new msg="added column X"`. Alembic will read your changes and generate a migration script.
5. Then, run `make migrate` to apply the script to your local PostgreSQL instance.

---

## 19. Future Roadmap

While the current platform is powerful, the roadmap includes:
1. **Automated Remediation:** Allowing approved playbooks to not just be "read" by a human, but actively executed by the system (e.g., clicking a button to restart the server directly from the ContextEdge UI).
2. **Proactive Alerting:** Watching external systems and triggering a playbook *before* a human even creates a ticket based on anomalous log patterns.
3. **Advanced Graph Analytics:** Using the Context Graph to show visual maps of how different IT systems depend on each other based on historical failures.

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
A: Imagine you need to run a python script every day at midnight to clean up old files. You could use Linux `cron`. But in a distributed system, `cron` is dangerous (if you have 5 servers, it runs 5 times!). Celery Beat is a centralized scheduler that ensures a task is added to the Redis queue exactly once, exactly on time.

**Q: Why is Tailwind CSS better than regular CSS?**
A: It prevents "CSS bloat". In regular CSS, developers constantly add new classes because they are afraid of breaking old ones. Tailwind forces you to use predefined utility classes, meaning your CSS file size never grows, and you can instantly see exactly what styling applies to a component just by looking at its HTML.

---
*End of Extremely Comprehensive Document*

## 21. Appendix A: Glossary of Terms for Freshers

To ensure absolutely no confusion, here is a consolidated glossary of every technical term used in this document, explained simply:

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

### Error: "LiteLLM AuthenticationError: OpenAI API key invalid"
**What it means:** The code tried to ask the AI to summarize an episode, but OpenAI rejected the request.
**Why it happens:** Your `OPENAI_API_KEY` in the `.env` file is missing, expired, or incorrect.
**How to fix it:** Log in to the OpenAI Developer Dashboard, generate a new API key, and paste it into your `.env` file. Restart the backend server.

### Error: "Next.js Hydration Mismatch"
**What it means:** The HTML generated on the server (Server-Side Rendering) does not match the HTML generated on the client (the browser) during the first render.
**Why it happens:** Usually occurs when you use browser-specific APIs (like `window.localStorage` or `Date.now()`) directly in the initial render of a React component without waiting for the component to mount (`useEffect`).
**How to fix it:** Ensure any code that relies on the browser `window` object is wrapped inside a `useEffect` hook, or dynamically import the component with `ssr: false`.

## 25. Document Version Control

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0.0 | Current | ContextEdge Documentation Agent | Initial comprehensive release, providing a foundational 800+ line deep dive for new engineering and operational staff. |

---
*End of Comprehensive ContextEdge Project Overview*
