# ContextEdge — Debugging Guide

Welcome to the comprehensive debugging and troubleshooting guide for ContextEdge. This document is written for engineers who are diagnosing issues, debugging locally, or managing the application in a development or production-like environment. Everything here is explained in simple English, assuming you are a beginner to this specific codebase.

## 1. How to Run Each Component

ContextEdge uses a mix of Next.js for the frontend, FastAPI for the backend, Celery for background tasks, and Docker for infrastructure (PostgreSQL, Redis, MinIO).

### What is this?
These are the core services that make up ContextEdge. You must run them to see the app working.

### Why do we need it?
Without these components running, you cannot test changes, view the UI, or execute AI tasks.

### Where is it defined?
- **Docker Compose:** `docker-compose.yml` and `docker-compose.dev.yml`
- **Backend:** `backend/dev.py` and `backend/src/contextedge/main.py`
- **Frontend:** `frontend/package.json`

### Who calls it?
You, the developer, start these components using `make` commands or `docker compose` commands.

### What happens next?
Once started, the frontend communicates with the backend via HTTP REST APIs. The backend stores data in PostgreSQL and MinIO, and sends background tasks to Redis, which Celery workers pick up.

### Input / Output
- **Input:** Start commands (e.g., `make up`, `make backend-dev`).
- **Output:** Running processes, bound ports (3000, 8000, 5432, 6379, 9000), and terminal logs.

### Failure behavior
If a component fails to start, it will crash and print an error to the terminal. Dependent services will usually return `500 Internal Server Error`, `502 Bad Gateway`, or just timeout.

### Design rationale
We separate infrastructure (Docker) from application code (host-run Python/Node) during development so you get fast hot-reloading and can easily attach debuggers, while keeping databases consistent and containerized.

### Practical Steps

#### Infrastructure (PostgreSQL, Redis, MinIO)
Run the infrastructure using Docker. This provides your database, message broker, and object storage.
```bash
make up
# Equivalent to: docker compose up -d
```
Stop them when you are done:
```bash
make down
```

#### Backend Dev Server (FastAPI)
The backend serves the REST API.
```bash
make backend-dev
# Equivalent to: cd backend && python dev.py api
```
Access the API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

#### Frontend Dev Server (Next.js)
The frontend serves the React UI.
```bash
make frontend-dev
# Equivalent to: cd frontend && npm run dev
```
Access the app at [http://localhost:3000](http://localhost:3000).

#### Celery Workers
Workers process asynchronous tasks like syncing sources, extracting text, and calling LLMs.
```bash
make celery-dev
# Equivalent to: cd backend && python dev.py worker
```

#### Celery Beat
Beat is a scheduler that triggers recurring tasks like contradiction scanning or daily syncs.
```bash
make celery-beat-dev
# Equivalent to: cd backend && python dev.py beat
```

---

## 2. Log Configuration

### What is this?
Logging is how the application records what it is doing. ContextEdge uses `structlog` for structured, JSON-friendly logging.

### Why do we need it?
To trace errors, understand application flow, and see request/response cycles without attaching a debugger.

### Where is it defined?
- `backend/src/contextedge/config.py` (Log levels)
- `backend/src/contextedge/middleware/request_audit.py` (HTTP request logging)
- `backend/src/contextedge/middleware/request_context.py` (Correlation IDs)

### Who calls it?
Every part of the Python backend calls `logger.info()`, `logger.error()`, etc.

### What happens next?
In development, logs are printed to the console in a human-readable format. In production, they are output as JSON for ingestion by tools like Datadog or ELK.

### Input / Output
- **Input:** Application events, HTTP requests.
- **Output:** Text in the terminal or JSON strings.

### Failure behavior
If logging fails (rare), the app continues, but you lose visibility.

### Design rationale
Structured logging means we can search logs by `request_id`, `tenant_id`, or `user_id` easily, rather than parsing raw text strings.

### Practical Steps

- **Log Levels:** Controlled by the `APP_LOG_LEVEL` environment variable in `.env`. Set it to `DEBUG` to see everything, or `INFO` for standard output.
- **Structlog Setup:** When `APP_ENV=development`, logs are pretty-printed. Otherwise, they are JSON.
- **Request ID Correlation:** Every HTTP request gets a `X-Request-ID`. This is propagated to Celery tasks (via headers) so you can trace a background task back to the HTTP request that spawned it.
  
```mermaid
sequenceDiagram
    participant User
    participant API
    participant Celery
    User->>API: POST /sync (Generates Request ID: 123)
    API->>Celery: Enqueue Task (Passes Request ID: 123)
    Celery-->>Celery: Log: "Sync started" [req_id: 123]
    API-->>User: 202 Accepted
```

---

## 3. Observability

### What is this?
Observability covers metrics, health checks, and cost tracking.

### Why do we need it?
To know if the system is healthy, how many resources it is using, and how much money the LLMs are costing us.

### Where is it defined?
- `backend/src/contextedge/ai/observability.py`
- `backend/src/contextedge/services/admin_cost_service.py`

### Who calls it?
Prometheus scrapers, load balancers (for health checks), and internal services after LLM calls.

### Practical Steps

#### Health Check Endpoints
- **Liveness:** `GET /health` - Returns 200 OK if the API process is running.
- **Readiness:** `GET /ready` - Returns 200 OK if the API can connect to DB/Redis.
- **Metrics:** `GET /metrics` - Exposes Prometheus metrics (like `contextedge_llm_tokens_total`).

#### Observability File Walkthrough (`observability.py`)
This file is critical for tracking AI costs. When an LLM is called, `record_llm_usage` is triggered.
1. It extracts token usage (prompt, completion, cached).
2. It increments Prometheus counters (`LLM_TOKENS_TOTAL`).
3. It writes a structured log line (`llm.usage`).
4. It inserts an `OperationalEvent` into the database so the Admin Cost Dashboard can show historical spend.

---

## 4. Common Errors & Solutions

### Database connection errors
**Error:** `psycopg2.OperationalError: could not connect to server`
**Solution:** Ensure Docker is running. Run `make up`. Check that `DATABASE_URL` uses `localhost` if running the app on the host.

### Migration errors
**Error:** Missing tables or columns.
**Solution:** Run `make migrate`. If you see errors about duplicate unique indexes (migration `0026`), run the pre-migration dedupe SQL script found in `RUNBOOK.md`.

### Redis connection errors
**Error:** Celery worker hangs or API fails with Redis timeout.
**Solution:** Run `make up`. Verify `REDIS_URL` and `CELERY_BROKER_URL` in `.env`.

### MinIO errors
**Error:** Object-store offload not working, or MinIO unreachable.
**Solution:** MinIO must be running (`make up`). Verify `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` in `.env`. Default port is 9000 for API, 9001 for Console.

### JWT errors
**Error:** Backend crashes on startup with `RuntimeError` about `JWT_SECRET_KEY`.
**Solution:** If `APP_ENV` is not `development`, you *must* change the `JWT_SECRET_KEY` in `.env` to a secure random string.

### CORS errors
**Error:** Frontend console shows "Cross-Origin Request Blocked".
**Solution:** Check `APP_CORS_ORIGINS` in `.env`. It must include the frontend URL (e.g., `http://localhost:3000`).

### LLM API key errors
**Error:** `AuthenticationError` from OpenAI or Anthropic.
**Solution:** Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in your `.env`.

### Embedding errors
**Error:** `embedding IS NULL` persists past the expected window.
**Solution:** This is likely a tripped per-tenant LLM budget cap. Check `llm.usage` events for `outcome = budget_exceeded`. Go to `/admin/cost` in the UI to raise the cap.

### Celery task failures
**Error:** Tasks are queued but never execute.
**Solution:** You forgot to start the worker. Run `make celery-dev` in a new terminal tab.

### Frontend build errors
**Error:** `npm run dev` fails with module not found.
**Solution:** Run `npm install` inside the `frontend` directory.

### Import errors
**Error:** `ModuleNotFoundError: No module named 'contextedge'`
**Solution:** You are running python directly without setting the path. Use `cd backend && python dev.py api` which automatically adds `src/` to your `PYTHONPATH`.

---

## 5. Debugging Backend

### Using print / structlog
If you need to quickly debug a variable, you can use `print()`, but the standard practice is to use `structlog`.
```python
import structlog
logger = structlog.get_logger()

logger.info("debugging_state", my_var=my_var, some_id=some_id)
```
This ensures the log inherits the current `request_id`.

### Breakpoints with debugger
In VS Code, you can attach to the FastAPI process. Set `APP_DEBUG=True` in `.env`. If you run the server directly via Python in your IDE debugger, you can place breakpoints on any route or service.

### Testing API endpoints manually
You can use `curl`, Postman, or the built-in Swagger UI.

### Using Swagger UI
Navigate to [http://localhost:8000/docs](http://localhost:8000/docs). This interactive page lets you authenticate (put your JWT bearer token in the "Authorize" button) and send requests to any endpoint. It is generated automatically from FastAPI Pydantic models.

### Checking database directly
You can connect to the local PostgreSQL database using a tool like DBeaver, pgAdmin, or psql.
```bash
psql postgresql://contextedge:contextedge@localhost:5432/contextedge
```
Useful tables to check:
- `evidence_items`: Where raw text and embeddings live.
- `operational_events`: Where logs, AI usage, and tool executions are saved.
- `tenant_llm_budgets`: Where you can see if a tenant is blocked from using AI.

---

## 6. Debugging Frontend

### Browser DevTools
Press `F12` or `Cmd+Option+I` in your browser. This is your primary tool.

### React DevTools
Install the React Developer Tools browser extension. It allows you to inspect the component tree, see props, and view React state.

### Network tab
If data isn't loading, check the Network tab. 
1. Is the request going to `http://localhost:8000/api/v1/...`?
2. Is the status 200? 401 (Auth)? 403 (Permission)?
3. Look at the Response payload to see exactly what the backend returned.

### Console errors
Check the Console tab for JavaScript runtime errors. If an API returns a 500 error, it will often cause an uncaught promise rejection here.

### Next.js error pages
Next.js provides a helpful overlay when an error occurs during development. It usually points directly to the file and line number that crashed. Ensure you check both the browser overlay and the terminal where `npm run dev` is running, as Server-Side Rendering (SSR) errors appear in the terminal.

---

## 7. Debugging Celery Workers

### What is this?
Celery is a task queue. It takes heavy jobs (like downloading emails or calling slow AI models) out of the HTTP request cycle so the API remains fast.

### Why do we need it?
If we extracted entities from 1000 Jira tickets synchronously during a web request, the browser would timeout.

### Monitoring task queue
You can monitor Celery tasks by looking at the worker terminal output (`make celery-dev`). It prints lines when a task is `Received`, `Succeeded`, or `Failed`.

### Checking task status
For deeper debugging, you can connect to Redis directly to see queued items:
```bash
redis-cli -n 1 keys "*"
```

### Retry behavior
Celery is configured with `task_acks_late=True`. This means if the worker process crashes (e.g., OOM kill) while processing a task, the task remains in Redis and will be picked up by another worker. Tasks that fail due to application exceptions will generally retry based on the `autoretry_for` decorator in the task definition.

### Dead letter queue
ContextEdge does not currently implement a strict dead-letter queue. Failed tasks will exhaust their retries and then disappear from the active queue, leaving an error trace in the structlog output.

---

## 8. Debugging API Issues

### Authentication failures (401)
- Did you pass the `Authorization: Bearer <token>` header?
- Has the token expired? (Default is 60 minutes).
- Did you change `JWT_SECRET_KEY` in `.env` and forget to restart the backend?

### Authorization errors (403)
- The user is authenticated, but lacks the necessary `roles` (e.g., trying to access an admin route without `tenant_admin` role).
- For service tokens, the `allowed_domain_ids` might restrict access to a specific domain.

### Validation errors (422)
FastAPI returns 422 when the JSON body or query parameters do not match the Pydantic schema. Read the response body carefully; it tells you exactly which field is missing or incorrectly typed.

### Internal errors (500)
A 500 error means the Python code crashed. Check the backend terminal running `make backend-dev` for the full stack trace.

### Request/response inspection
The `RequestAuditMiddleware` logs all mutating requests (POST, PUT, DELETE) to the `audit_logs` table. You can query this table to see exactly what API calls were made by a specific user or tenant.

---

## 9. Debugging AI/LLM

### What is this?
ContextEdge uses LLMs heavily for extracting decisions, classifying text, and generating embeddings.

### Where is it defined?
- `backend/src/contextedge/ai/provider.py`
- `backend/src/contextedge/services/tenant_budget_service.py`

### API key issues
Ensure `OPENAI_API_KEY` is correct. If you are using Azure, ensure all `AZURE_OPENAI_*` variables are populated. LiteLLM handles the routing.

### Token limits & Cost tracking
Every tenant has an LLM budget to prevent accidental runaway costs.
- **Where checked:** `check_budget` in `tenant_budget_service.py`.
- **What happens:** If the budget is exceeded, `TenantBudgetExceeded` is raised, and background tasks will fail.
- **How to fix:** Use the Admin Console (`/admin/cost`) to view usage and raise the budget limits for that tenant.

### Prompt debugging
Prompts are defined in `backend/src/contextedge/ai/prompts/`. You can override prompts per-tenant using the `tenant_prompt_variants_json` setting in `.env`. To debug what exact prompt was sent to the LLM, you can temporarily add `logger.info("prompt", text=prompt)` in `provider.py`.

---

## 10. Debugging Vector Search

### What is this?
Vector search allows the app to find evidence that is conceptually similar to a query, even if they don't share exact keywords.

### No results
- **Are there embeddings?** Check the `evidence_items` or `evidence_chunks` table. Is the `embedding` column `NULL`? If yes, the `embed_chunks_batch_task` hasn't run or failed (likely due to budget limits).
- **Are indexes built?** We use pgvector HNSW indexes. Run `make migrate` to ensure migration `0021_hnsw_vector_indexes` ran successfully.

### Dimension mismatches
Ensure your database embedding column size matches your embedding model output. We default to `text-embedding-3-small`, which outputs 1536 dimensions.

---

## 11. Debugging Context Graph

### Missing nodes/edges
The Graph Explorer (`/graph-explorer`) visualizes data. If nodes are missing, it means the background extraction tasks (Identity linking, Decision extraction, Correlation) have not processed the evidence yet.

### Traversal issues
Graph relationships are stored in the `graph_edges` table. 
Check `ix_graph_edges_metadata_extra_gin` index if queries filtering on edge metadata are slow.

---

## 12. Debugging MAF Integration

ContextEdge supports agent orchestration. If a tool fails:
- Check the `operational_events` table for `tool.shadow_executed` or actual execution events.
- Tools operating in shadow mode (`suggest_only`) will not mutate state; they only simulate.

---

## 13. Environment Variables

### Missing variables
If `.env` is completely missing, the app will refuse to start or will crash instantly. Use `.env.example` as a template.

### Wrong values
- Setting `localhost` inside a Docker container will try to connect to the container itself, not the host machine. If you are running the backend in Docker, use the service names (e.g., `postgres`, `redis`).
- If you are running `make backend-dev` (host-run), use `localhost`.

### Secret management
Never commit `.env` to git. Treat `SERVICE_TOKENS_JSON` and `FERNET_KEY` as highly sensitive.

---

## 14. Known Issues & Constraints

Please review `KNOWN_GAPS.md` for a complete list. Key constraints include:

- **Sync Overlap:** Do not trigger manual overlapping backfills for the same source.
- **Graph Explorer:** Currently read-only in the UI.
- **Object Storage Lifecycle:** MinIO blobs are not auto-deleted by the app when evidence is purged; this relies on bucket lifecycle rules.
- **Decision Extraction Limit:** The legacy extractor operates on the first 4000 characters of a document. (Partially mitigated by the new chunking pipeline, but per-chunk decision extraction is still pending).

---

## 15. Useful Commands

| Goal | Command |
| --- | --- |
| Start infrastructure | `make up` |
| Stop infrastructure | `make down` |
| Start backend | `make backend-dev` |
| Start Celery worker | `make celery-dev` |
| Start Celery beat | `make celery-beat-dev` |
| Start frontend | `make frontend-dev` |
| Apply database migrations | `make migrate` |
| Seed initial data | `make seed` |
| Run backend tests | `make test-backend` |
| View docker logs | `docker compose logs -f` |
| Access database CLI | `docker compose exec postgres psql -U contextedge -d contextedge` |

Keep this guide handy while developing. When in doubt, read the structlog output and follow the `request_id`!
