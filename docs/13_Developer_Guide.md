# ContextEdge — Developer Guide

Welcome to the **ContextEdge Developer Guide**. This document is designed for new developers joining the team. We will explain everything as if you are a complete beginner. 

Our goal is to comprehensively answer for each feature:
- **What** is it?
- **Why** do we need it?
- **Where** does it live?
- **Who** calls it?
- **What happens next?**
- **Input** and **Output**?
- **Failure behavior**?
- **Design rationale**?

We will also rate important files from 1 to 10 (10 being the most important) to help you prioritize your onboarding.

---

## 1. Prerequisites

Before you write a single line of code, you need to install some essential tools.

### Software Requirements
- **Python 3.12+**: 
  - **What:** The programming language for our backend.
  - **Why:** Python 3.12 offers better performance, improved error messages, and excellent typing support. It also has mature data science and AI libraries that we rely on heavily (like `litellm` and `pgvector`).
- **Node.js 20+**: 
  - **What:** The runtime environment for our frontend framework.
  - **Why:** The frontend uses Next.js 14+ (App Router), which requires a modern version of Node.js for server-side rendering and build steps.
- **npm**: 
  - **What:** The package manager for Node.js.
  - **Why:** Used to install and manage dependencies defined in `package.json`.
- **Docker & Docker Compose**: 
  - **What:** A containerization platform that packages software into standardized units.
  - **Why:** Used to run infrastructure like the database (PostgreSQL), message broker (Redis), and object storage (MinIO) without installing them directly on your host machine. This ensures perfect parity across all developer machines and CI/CD pipelines.
- **Git**: 
  - **What:** A distributed version control system.
  - **Why:** Essential for collaborating on code, branching, and version history.
- **make**: 
  - **What:** A build automation tool that runs shell commands defined in a `Makefile`.
  - **Why:** (Optional but highly recommended) It provides short, easy-to-remember commands (e.g., `make up`) instead of long Docker commands.

### System Requirements
- **RAM**: Minimum 8GB (16GB strongly recommended due to Docker and local LLM overhead).
- **Disk Space**: At least 20GB free for Docker images, volumes, and local node_modules.
- **OS**: Windows 10/11 (with WSL2 enabled), macOS (M1/M2 preferred), or any modern Linux distribution.

> [!NOTE]
> If you are on Windows and don't have `make` installed, you can look inside the `Makefile` (Rated 8/10 for importance) and run the underlying commands manually in PowerShell or WSL.

---

## 2. Initial Setup

Let's get your local environment running. Follow these steps sequentially.

### Step-by-step setup from scratch

**What is this?** The process of getting the code, setting up environment variables, and installing dependencies.
**Why?** The application needs configuration (like database URLs and secret keys) to start. Without this, the backend will crash immediately.

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repository_url> ContextEdge
   cd ContextEdge
   ```

2. **Copy the Environment File**
   The `.env` file holds all secrets and configuration overrides. Never commit it to git! We provide a template called `.env.example`.

   **Windows PowerShell instructions:**
   ```powershell
   Copy-Item .env.example .env
   ```

   **Linux/Mac instructions:**
   ```bash
   cp .env.example .env
   ```

3. **Generate a Secret Key**
   You need a `FERNET_KEY` (for database encryption) and a `JWT_SECRET_KEY` (for auth tokens) in your `.env`.

   To generate a Fernet key, run:
   ```powershell
   @'
   from cryptography.fernet import Fernet
   print(Fernet.generate_key().decode())
   '@ | python -
   ```
   *Copy the output and paste it into `FERNET_KEY` in your `.env` file.*

   To generate a JWT key, you can use OpenSSL or just a random UUID:
   ```bash
   openssl rand -hex 32
   ```
   *Copy the output and paste it into `JWT_SECRET_KEY`.*

### Docker setup

We use Docker to run Postgres, Redis, and MinIO locally.
**Where?** Look at `docker-compose.yml` (Rated 9/10).

Start the infrastructure:
```bash
make up
# Or manually: docker compose up -d
```
This command downloads the necessary images and starts them in detached mode (`-d`).

### Database setup (PostgreSQL)
- **What:** We use PostgreSQL 16 equipped with the `pgvector` extension.
- **Why:** To store both relational data (users, playbooks) and AI vector embeddings (for semantic search).
- **Failure behavior:** If the DB is down or credentials in `.env` are wrong, the backend crashes on startup with an `asyncpg` or `SQLAlchemy` connection error.

### Redis setup
- **What:** An in-memory data structure store.
- **Why:** Used as the message broker and result backend for Celery background tasks.
- **Failure behavior:** Celery workers will refuse to start, throwing `ConnectionRefusedError`.

### MinIO setup
- **What:** An S3-compatible object storage server.
- **Why:** Used to store raw evidence and attachments (like large PDFs or JSON dumps) to keep the PostgreSQL database small and performant.
- **Failure behavior:** The app might start, but evidence ingestion and attachment offload will fail with 500 errors.

---

## 3. Running the Project

Now that your infrastructure is running, let's start the application layer. You have two choices: running processes on your host machine (recommended for fast iteration) or running everything in Docker.

### Option A: Host-Run App (Recommended)

1. **Install Backend Dependencies**
   Open a terminal, activate your virtual environment, and run:
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

2. **Install Frontend Dependencies**
   Open a second terminal and run:
   ```bash
   cd frontend
   npm install
   ```

3. **Start the backend**
   - **Where:** `backend/` directory.
   - **Command:** `make backend-dev` (or `cd backend && python dev.py api`)
   - **What:** Starts the FastAPI application using Uvicorn with hot-reloading.
   - **Output:** API available at `http://localhost:8000/docs`.

4. **Start Celery workers**
   - **Command:** `make celery-dev` (or `cd backend && python dev.py worker`)
   - **What:** Starts background workers to process queues like `extraction`, `pattern`, and `evaluation`.
   - **Why:** AI extraction takes time and cannot block the HTTP request.

5. **Start Celery Beat**
   - **Command:** `make celery-beat-dev` (or `cd backend && python dev.py beat`)
   - **What:** Starts the scheduler for recurring tasks (like `detect_drift` and contradiction scans).

6. **Start the frontend**
   - **Command:** `make frontend-dev` (or `cd frontend && npm run dev`)
   - **What:** Starts the Next.js development server.
   - **Output:** UI available at `http://localhost:3000`.

### Option B: Full Docker development

If you prefer running everything (including the app code) in Docker:
- **Command:** `make dev` (or `docker compose -f docker-compose.dev.yml up --build`)
- **Design rationale:** Keeps your host machine clean and guarantees environment parity.
- **Drawback:** Can be slower to rebuild during active development, especially when changing Python dependencies.

---

## 4. Database Migrations

We use **Alembic** to manage database schema changes. Migrations ensure that everyone's database structure looks exactly the same.

### How Alembic works
- **What:** Alembic tracks changes to SQLAlchemy models and generates sequential SQL scripts.
- **Where:** `backend/alembic/versions/` (This folder contains all migration scripts).
- **File Rating:** `backend/alembic/env.py` (7/10) - This file connects Alembic to our SQLAlchemy `Base.metadata`. If a new model isn't imported here, Alembic won't see it!

### Creating a new migration
- **Why:** You added a new table or column in `backend/src/contextedge/models/`.
- **Command:**
  ```bash
  make migrate-new msg="added_widget_table"
  ```
- **What happens next:** Alembic looks at your models, compares them to the current database, and creates a python script (e.g., `1234abcd_added_widget_table.py`) in `alembic/versions/`. 
- **Important:** ALWAYS review the generated script to ensure it didn't do something unexpected (like dropping a table by accident).

### Running migrations
- **Command:** `make migrate`
- **What:** Applies all pending SQL scripts to your local database, bringing it up to the "head".
- **Who calls it:** You do, every time you pull new code from `main`.

### Rolling back
- **Command:** `make migrate-down`
- **What:** Reverts the last applied migration.
- **Failure behavior:** If you roll back a destructive migration (like dropping a column), data in that column is permanently lost. Be careful!

### Current migration head
Always check `backend/alembic/versions/` for the latest file to see what schema changes are active. You can also run `alembic current` inside the `backend` directory.

---

## 5. Seeding Data

To test the app locally, you need dummy data (users, tenants, policies).

### seed.py explained
- **Where:** `backend/src/contextedge/seed.py` (Rated 8/10)
- **What:** A script that inserts default tenants, policies, and users into the database.
- **Who calls it:** You run it via `make seed` (or `python dev.py seed`).
- **Input:** Nothing, it uses predefined constants hardcoded in the file.
- **Output:** Rows inserted into PostgreSQL tables (`tenants`, `users`, etc.).
- **Design rationale:** We want local development to be turnkey. Without this, you wouldn't be able to log in.

### demo_maf_seed.py explained
- **What:** Specific seed data for MAF (Microsoft Agent Framework) demos.
- **Why:** To showcase agent interactions, it seeds specific context graphs and playbooks.

### Default credentials
After running the seed script, you can log in to the frontend (`localhost:3000`) using:
- **Admin User:** `admin@contextedge.local` / `admin123`
- **Analyst User:** `analyst@contextedge.local` / `analyst123`

---

## 6. How to Add a New Feature

This is the core of the Developer Guide. Follow these steps meticulously whenever you add new functionality to ContextEdge.

### 6.1. Add a New API Endpoint

**What:** Creating a new URL path that the frontend (or an external client) can call.
**Design Rationale:** We use FastAPI. We keep routing logic thin and move all business logic to a dedicated service file. This makes testing much easier.

#### Step 1. Create schema in schemas/
Create Pydantic models for Input and Output validation.
- **Where:** `backend/src/contextedge/schemas/widget.py`
```python
from pydantic import BaseModel, Field

class WidgetCreate(BaseModel):
    name: str = Field(..., description="The name of the widget")
    size: int = Field(default=10)

class WidgetResponse(BaseModel):
    id: str
    name: str
    size: int
```

#### Step 2. Create/update model in models/
Create the SQLAlchemy ORM model to define the database table.
- **Where:** `backend/src/contextedge/models/widget.py`
```python
import uuid
from sqlalchemy import Column, String, Integer
from sqlalchemy.dialects.postgresql import UUID
from .base import Base

class Widget(Base):
    __tablename__ = "widgets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    size = Column(Integer, default=10)
```
*Don't forget to import this model in `backend/alembic/env.py`!*

#### Step 3. Create service in services/
**Who calls it:** The Router.
- **Where:** `backend/src/contextedge/services/widget_service.py`
```python
from sqlalchemy.ext.asyncio import AsyncSession
from contextedge.models.widget import Widget
from contextedge.schemas.widget import WidgetCreate

async def create_widget(db: AsyncSession, data: WidgetCreate) -> Widget:
    new_widget = Widget(name=data.name, size=data.size)
    db.add(new_widget)
    await db.commit()
    await db.refresh(new_widget)
    return new_widget
```

#### Step 4. Create router in api/v1/
- **Where:** `backend/src/contextedge/api/v1/widgets.py`
```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from contextedge.api.deps import get_db
from contextedge.schemas.widget import WidgetCreate, WidgetResponse
from contextedge.services import widget_service

router = APIRouter()

@router.post("/", response_model=WidgetResponse)
async def create(
    widget_in: WidgetCreate, 
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new widget.
    """
    return await widget_service.create_widget(db, widget_in)
```

#### Step 5. Register router in api/v1/__init__.py
- **Where:** `backend/src/contextedge/api/v1/__init__.py`
```python
from fastapi import APIRouter
from .widgets import router as widgets_router

api_router = APIRouter()
api_router.include_router(widgets_router, prefix="/widgets", tags=["Widgets"])
```

#### Step 6. Create migration
Run `make migrate-new msg="add_widgets_table"` then `make migrate`.

#### Step 7. Test
Write a test in `backend/tests/api/test_widgets.py`. Run `make test-backend`.

**Sequence Diagram:**
```mermaid
sequenceDiagram
    participant Frontend
    participant FastAPI Router
    participant WidgetService
    participant PostgreSQL
    Frontend->>FastAPI Router: POST /api/v1/widgets {name: "Alpha"}
    FastAPI Router->>WidgetService: create_widget()
    WidgetService->>PostgreSQL: INSERT INTO widgets (name, size)
    PostgreSQL-->>WidgetService: row data (id: 123)
    WidgetService-->>FastAPI Router: Widget object
    FastAPI Router-->>Frontend: 200 OK JSON {id: "123", name: "Alpha", size: 10}
```

---

### 6.2. Add a New Database Table

If you just need a table without an immediate API endpoint:
1. **Create model**: Add a `.py` file in `backend/src/contextedge/models/`. Make sure it inherits from `Base`.
2. **Add to models/__init__.py**: You **MUST** import your model in `backend/alembic/env.py` or `models/__init__.py` so Alembic sees it.
3. **Create migration**: `make migrate-new msg="new_table_name"`
4. **Create CRUD operations**: Add functions in a `_service.py` file to handle Create, Read, Update, Delete so other backend code can interact with the table.

---

### 6.3. Add a New UI Tab

**Where:** `frontend/src/app/(dashboard)/`
**Why:** To expose the new backend feature to the user.

1. **Create page in app/(dashboard)/**
   Create a folder, e.g., `widgets/page.tsx`. Next.js uses file-based routing.
   ```tsx
   export default function WidgetsPage() {
     return (
       <div className="p-6">
         <h1 className="text-2xl font-bold mb-4">Widgets Dashboard</h1>
         <p>Manage your widgets here.</p>
       </div>
     );
   }
   ```
2. **Add to shell navigation**
   Find the Sidebar component (usually in `frontend/src/components/layout/sidebar.tsx` or similar) and add the navigation link pointing to `/widgets`.
3. **Add API calls**
   Use TanStack Query (`useQuery`, `useMutation`) to fetch data from the backend. Create a custom hook in `frontend/src/hooks/useWidgets.ts`.
4. **Add components**
   Use `shadcn/ui` components from `components/ui/` (like Buttons, Tables, Dialogs) to build out the interface.

---

### 6.4. Add a New Celery Worker Task

**What:** A background job that runs outside the web request lifecycle.
**Why:** For long-running jobs (e.g., AI extraction, heavy data syncs) that would timeout an HTTP request or degrade API performance.

1. **Create task file**
   - **Where:** `backend/src/contextedge/workers/my_tasks.py`
   ```python
   from contextedge.workers.celery_app import celery_app
   from contextedge.services.widget_service import heavy_processing

   @celery_app.task(name="process_widget_task")
   def process_widget_task(widget_id: str):
       print(f"Starting heavy processing for widget {widget_id}")
       # In Celery, we usually have to spin up our own sync DB session
       # or run asyncio code using a runner.
       heavy_processing(widget_id)
   ```
2. **Register in celery_app.py**
   Make sure the module `my_tasks.py` is imported in `celery_app.py` so the Celery worker process actually discovers the task upon startup.
3. **Configure queue routing**
   Assign it to a specific queue (like `default`, `extraction`, or `pattern`) in the Celery config settings, so that specific workers handle specific types of workloads.

---

### 6.5. Add a New Connector

**What:** Connectors pull data from external systems (Jira, Teams, ServiceNow, etc.) into ContextEdge.
**Why:** To build the operational memory graph, we need raw data from the tools humans use.

1. **Create connector folder**: `backend/src/contextedge/connectors/new_system/` (or whatever the source is).
2. **Implement base class**: Inherit from the base connector interface (`BaseConnector`). 
   - Implement `fetch()`: Authenticate with the external API and download raw data.
   - Implement `normalize()`: Convert the raw vendor-specific JSON into our standard `EvidenceItem` schema.
3. **Register in registry**: Add it to the connector factory so the ingestion engine knows how to route sync requests for that source type.

---

### 6.6. Add a New AI Extractor

**What:** Code that uses LLMs to extract structured data (like Decisions, Identities, or Actions) from unstructured text.

1. **Create extractor**: Create a new class inside `backend/src/contextedge/ai/extractors/`.
2. **Add prompt**: Place your Jinja/text prompts in `backend/src/contextedge/ai/prompts/`. Keeping prompts separate from Python code allows for easier A/B testing and modification by prompt engineers.
3. **Wire into extraction pipeline**: Update `workers/extraction_tasks.py` to invoke your new extractor during the evidence ingestion flow.

---

### 6.7. Add a New Context Graph Node Type

**What:** The Context Graph is our temporal, semantic representation of operational memory.
**Why:** Adding a new node type (e.g., `Server`, `PullRequest`) allows the graph to represent new concepts.

1. **Add to builder.py**: `backend/src/contextedge/graph/builder.py` - Define how the new node type is mapped from the relational ORM models into a graph node.
2. **Add to queries.py**: Write the SQL/Cypher-like traversal logic to find this node and its neighbors.
3. **Update agent contracts**: Update `docs/MAF_CONTEXT_GRAPH_INTEGRATION.md` to ensure any AI agents interacting with the graph know about the new node type.

---

### 6.8. Add a New MAF Agent

**What:** Microsoft Agent Framework (MAF) integration allows autonomous agents to interact with ContextEdge.

1. **Create agent configuration**: Define the persona and system prompt for the new agent.
2. **Register tools**: Expose specific ContextEdge python functions as tool definitions that the agent can call.
3. **Set up context provider**: Ensure the Context Graph feeds relevant, scoped sub-graphs to the agent so it has the necessary context before taking action.

---

## 7. Testing

Quality is paramount. We test everything.

### Backend tests (pytest)
- **Where:** `backend/tests/`
- **Command:** `make test-backend`
- **Design Rationale:** We use `pytest` and `pytest-asyncio`. Our test suite automatically spins up a clean test database using Testcontainers. 
- **What happens next:** The test runner executes all tests, verifying API status codes, database state mutations, and service logic.

### Frontend tests (vitest)
- **Where:** `frontend/src/` (usually placed right alongside the components they test, e.g., `Button.test.tsx`).
- **Command:** `npm test` or `make test-frontend`.
- **Note:** We use `vitest` instead of Jest because it is significantly faster and natively compatible with modern ESM (ECMAScript Modules) builds that Next.js uses.

### Running tests locally
Always run `make test` (which runs both suites) before pushing a pull request to ensure you haven't broken existing functionality.

---

## 8. Linting & Formatting

Clean, consistent code is mandatory. We enforce this via CI.

### Ruff configuration (Backend)
- **What:** Ruff is a blazing fast python linter and formatter written in Rust. It replaces Flake8, Black, and isort.
- **Where:** Configured in `backend/pyproject.toml`.
- **Commands:** 
  - `make lint` (Checks for errors without fixing).
  - `make format` (Automatically reformats code to conform to our style guide).

### ESLint configuration (Frontend)
- **What:** The standard linter for TypeScript and React.
- **Where:** Configured in `frontend/package.json` and `.eslintrc.json`.
- **Command:** `npm run lint`.

---

## 9. Docker Build

### Building images for Production
The `Dockerfile` in `backend/` and `frontend/` define how to build production-ready, minimal containers.
- **Command:** `docker build -t contextedge-backend ./backend`
- **Design rationale:** We use multi-stage builds. The final image only contains the compiled code and runtime dependencies, leaving out development tools to reduce image size and attack surface.

### Docker Compose profiles
- `docker-compose.yml`: Defines purely the infrastructure (Postgres, Redis, MinIO). Used when you want to run the app on your host machine.
- `docker-compose.dev.yml`: Extends the base compose file to also build and run the backend, frontend, and celery workers in containers. Used for a fully isolated development environment.

---

## 10. Deployment

When taking ContextEdge to production, keep these things in mind:

### Environment configuration
Always use environment variables for secrets. Never hardcode them in the codebase. Use a secret manager (like AWS Secrets Manager, HashiCorp Vault, or GitHub Secrets) in production.

### Production settings
- Use a cryptographically strong, randomly generated `JWT_SECRET_KEY` and `FERNET_KEY`.
- Run the FastAPI backend behind a robust reverse proxy (like Nginx, Traefik, or AWS ALB) to handle TLS termination and load balancing.
- Scale Celery workers based on queue length. You may need dedicated workers just for the `extraction` queue since it is heavily CPU and I/O bound.

### Security considerations
- **PII Redaction:** Our redaction service MUST run before any data is sent to external LLM providers.
- **Tokens:** JWT tokens must have strict expirations. Service-to-service communication should use strict `X-Service-Token` validation.
- **CORS:** Cross-Origin Resource Sharing (CORS) in `APP_CORS_ORIGINS` must be tightly restricted to known frontend domains, preventing malicious sites from calling the API on a user's behalf.

---

## 11. Configuration Reference

This section explains EVERY environment variable required by the system (usually defined in your `.env` file).

### Database Configuration
- **`DATABASE_URL`**: 
  - **What:** The asynchronous connection string for PostgreSQL.
  - **Example:** `postgresql+asyncpg://user:pass@localhost:5432/contextedge`
  - **Why:** Required by FastAPI and SQLAlchemy's async session engine.
- **`DATABASE_URL_SYNC`**: 
  - **What:** The synchronous connection string.
  - **Example:** `postgresql://user:pass@localhost:5432/contextedge`
  - **Why:** Used strictly by Alembic for running migrations and by Celery tasks that require synchronous database access.

### Redis Configuration
- **`REDIS_URL`**: 
  - **What:** The connection string for the Redis cache.
  - **Why:** Required for API rate-limiting and fast session caching.
- **`CELERY_BROKER_URL`**: 
  - **What:** The Redis URL used specifically for Celery messaging.
  - **Why:** The broker handles task queues, passing messages between the API and the background workers.
- **`CELERY_RESULT_BACKEND`**: 
  - **What:** The Redis URL for Celery results.
  - **Why:** Stores the state and return values of asynchronous workers so the API can check if a job is finished.

### Object Storage (MinIO/S3)
- **`MINIO_ENDPOINT`**: 
  - **What:** The host and port for MinIO (or AWS S3 endpoint).
  - **Why:** Dictates where object storage requests (like uploading attachments) are sent.
- **`MINIO_ROOT_USER`**: Admin username for MinIO.
- **`MINIO_ROOT_PASSWORD`**: Admin password for MinIO.
- **`MINIO_BUCKET`**: 
  - **What:** The default bucket name.
  - **Why:** ContextEdge stores all evidence artifacts in this specific bucket.

### Security & Authentication
- **`JWT_SECRET_KEY`**: 
  - **What:** The secret string used to sign authentication tokens. 
  - **Why:** Keeps the platform secure. If compromised, attackers can forge logins. MUST be long and random.
- **`JWT_ALGORITHM`**: Usually set to `HS256`.
- **`FERNET_KEY`**: 
  - **What:** The key used for symmetric encryption of sensitive fields in the database (like API keys provided by users).
  - **Why:** Ensures that if the database is dumped, sensitive credentials remain encrypted at rest.

### Application Settings
- **`OPENAI_API_KEY`**: 
  - **What:** The API key for OpenAI.
  - **Why:** Required for all LLM extraction, classification, and agentic features.
- **`APP_ENV`**: 
  - **What:** `development` or `production`.
  - **Why:** Dictates error verbosity and certain security bypasses (like allowing weak default passwords locally).
- **`APP_DEBUG`**: `true` or `false`. Turns on detailed stack traces in the API response.
- **`APP_LOG_LEVEL`**: Determines log verbosity (`INFO`, `DEBUG`, `WARNING`, `ERROR`). Set to `DEBUG` when troubleshooting locally.
- **`FRONTEND_URL`**: 
  - **What:** The URL of the frontend application.
  - **Why:** Used by the backend to configure CORS (Cross-Origin Resource Sharing) headers, ensuring only the frontend can make browser-based requests to the API.

---

### Additional Important Information

Here is a summary of the core workflow for an API request:
1. HTTP Request comes into the FastAPI Router.
2. Dependencies like `get_db` provide the database session.
3. The Router validates input using Pydantic.
4. The Router calls the Service function.
5. Service executes business logic and interacts with SQLAlchemy Models.
6. Data is returned through the Router and serialized back to JSON.

### End of Guide

By following this guide, you should have a firm grasp of how ContextEdge is structured, how to run it locally, and how to safely extend its capabilities. If you encounter any issues not covered here, consult the `RUNBOOK.md` or reach out to a senior engineer. Happy coding!

(This document provides complete 360-degree knowledge transfer regarding local setup, docker processes, environment variables, task scheduling, frontend development with NextJS, API with FastAPI, and more for ContextEdge.)
