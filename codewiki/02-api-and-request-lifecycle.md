# API and request lifecycle

## Summary

You will see how an HTTP call moves through ContextEdge: which middleware runs first, how **tenant and user** identity reach routers and logs, how **JWT** and **service tokens** differ, where **audit** records are written, and how the request's correlation IDs follow work into the Celery workers — so you can reason about security and traceability without reading every router file.

## Business picture

Every customer expects their data kept strictly separate and every action attributable to a real person or integration. When anyone calls the API, the platform immediately identifies **who** they are and **which organization** they belong to. Any change to data automatically leaves an audit trail that compliance officers and operations teams can review later — including changes that were *denied*, which is often what an investigation actually needs. Tracking headers let support staff link related calls into a single story: one ID connects the button an operator clicked, the background work it triggered, and the model spend it caused. That is what makes "who changed visibility on the VPN tickets, and what did it cost" a query instead of an archaeology project.

## Technical walkthrough

1. **Application creation** — `create_app()` builds the FastAPI app and adds middleware in this order: `RequestAuditMiddleware`, then `TenantContextMiddleware`, then `CORSMiddleware` (backend/src/contextedge/main.py:122-130). Starlette runs the *last-added* middleware outermost, so at runtime a request passes CORS → tenant context → request audit → route handler. That ordering is load-bearing: request audit runs closest to the routes, so it can read the `request.state` fields tenant context populated. Prometheus metrics are exposed at `/metrics` via `Instrumentator().instrument(app).expose(app)` (main.py:168), and all 33 routers mount under `/api/v1` (main.py:170-171; the full list is backend/src/contextedge/api/v1/__init__.py:41-83). Lifespan opens the Redis client onto `app.state.redis` and checks the MinIO bucket off-thread — a failed bucket check only degrades (`object_store_ok=False`), it never blocks startup (main.py:44-59).

2. **Tenant context middleware** — `TenantContextMiddleware.dispatch` mints `request_id` (header `x-request-id`, else a fresh UUID), `correlation_id` (`x-correlation-id`, else = request_id), and `causation_id` (`x-causation-id`, else = request_id), stores them on `request.state`, and binds them into a contextvars bag via `bind_request_context` so async service code and audit helpers can read them without threading `Request` through every call (backend/src/contextedge/middleware/request_context.py:88-104). **All three must be UUIDs.** Each header goes through `_parse_uuid`, which returns `None` for anything that is not a UUID, so a human-friendly trace string like `corr-vpn-incident` is silently dropped and a fresh UUID is used instead — a caller who wants their own ID to survive must send a real UUID (request_context.py:23-31, 88-90). The same parsing applies to the tenant and user ids copied out of the token into the contextvars bag (request_context.py:136-141). For a Bearer JWT it *decodes without enforcing* — a bad token is simply ignored here, and downstream dependencies still 401 — and stamps `request.state.tenant_id / user_id / user_email / roles` (request_context.py:111-125). An `X-Service-Token` header fills the same fields via `service_token_context` when the JWT did not (request_context.py:127-134). Exempt from token parsing: `/health`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/metrics`, and `/api/v1/auth/login` (request_context.py:77-85). Responses echo `X-Request-ID` and `X-Correlation-ID` headers (request_context.py:145-146).

3. **Request audit middleware** — after the handler returns, `RequestAuditMiddleware` inspects **mutating** methods (POST/PATCH/PUT/DELETE) under `/api/v1`, excluding `/api/v1/auth/login` (backend/src/contextedge/middleware/request_audit.py:37-42). For each of those it writes a structlog `http.mutating_request` line carrying method, path, status, tenant, user, and the request and correlation IDs (request_audit.py:48-57). When a tenant is known it *also* inserts a row into `audit_logs` — **for denied and failed calls too, not just successes**: `outcome` is `success` below 400, `denied` on 401/403, `failed` otherwise (request_audit.py:59-77). The row's `action` is a slug like `http.post.api.v1.sources.{id}.backfill` (`http.<method>.<path-with-dots>`, capped at 100 characters) with `resource_type='http_request'`, and the `details` JSONB carries path, status, outcome, and all three request IDs (request_audit.py:70-87, 95-101). The insert runs on a lazily created **synchronous** engine, off-thread, and swallows its own failures (`audit_db_error`) — auditing must never break the request it describes (request_audit.py:18-22, 89-119).

   Two blind spots, both worth knowing before you rely on this table. An *unauthenticated* 401 probe never resolves a tenant, so it exists only in the structlog line — alert on `http.mutating_request` with status 401 for those (request_audit.py:59-64). And everything above runs only after `call_next` returns: an unhandled exception is re-raised untouched (request_audit.py:30-35), so a mutation that crashes into the global 500 handler leaves **no** audit line and **no** row, just the handler's own `unhandled_exception` log (main.py:132-144). A raised `HTTPException` is not affected — Starlette turns that into an ordinary response further in, so a 400 or a 403 is audited normally.

4. **Per-route authentication** — protected routes depend on `get_current_user` (backend/src/contextedge/deps.py:72-114). `X-Service-Token` wins when present: a valid token yields a `CurrentUser` with `principal_type="service_account"` and optional `allowed_domain_ids`; an invalid one is a hard 403, with no fallback to JWT (deps.py:76-83). Otherwise the Bearer JWT is decoded and *enforced* — bad or expired tokens 401 here (deps.py:91-114). Service tokens come from the `service_tokens_json` config map (token → `{tenant_id, user_id, email, roles[, allowed_domain_ids]}`; backend/src/contextedge/security_tokens.py:12-36; backend/src/contextedge/config.py:210). Role checks: `CurrentUser.has_role` returns True unconditionally for `platform_super_admin`, `tenant_admin`, or `admin` (deps.py:37-44), and `require_role(...)` is the dependency factory routers use to 403 (deps.py:117-124).

5. **Login** — `POST /api/v1/auth/login` fetches up to 5 active users matching the email, because `User.email` is *not* globally unique (two tenants can hold the same address) and the cap bounds attacker-triggered bcrypt work (backend/src/contextedge/api/v1/auth.py:43-48). The no-candidate path verifies against a dummy bcrypt hash so response timing cannot enumerate emails (auth.py:16-18, 58-64); bcrypt always runs on a thread, never the event loop (auth.py:66-73). The same email + password matching in *two* tenants is rejected as "Ambiguous account" rather than guessing (auth.py:76-89). Roles are the flat `RoleBinding.role` values (auth.py:92-95), and the JWT carries `{sub, tenant_id, email, roles, exp}` (auth.py:25-31), expiring after `jwt_access_token_expire_minutes` = 60 (backend/src/contextedge/config.py:40).

6. **Correlation IDs follow the work into Celery** — when a handler calls `task.delay(...)`, the `before_task_publish` signal `_inject_correlation_headers` copies the three IDs from the contextvar into the outgoing task message headers with `setdefault`, so caller-set headers are never clobbered (backend/src/contextedge/workers/celery_app.py:25-42). On the worker, `task_prerun` rebinds them into the contextvar for the task's duration (celery_app.py:45-68) and `task_postrun` resets (celery_app.py:71-80). Any service code that then calls `append_operational_event` inherits them automatically: correlation, causation, and `actor_id` all default from the request context (backend/src/contextedge/services/event_log_service.py:46-56). This is the whole mechanism behind "one ID joins the click to the spend".

7. **Explicit audit from handlers** — mutations that want a *domain-meaningful* audit record (e.g. `backfill.requested`, `sync.pause`) call `log_audit_event`, which writes an `AuditLog` through the request's **async** session and merges request/correlation/causation IDs from `current_request_context()` into `details` (backend/src/contextedge/middleware/audit.py:10-44). This complements the middleware's generic `http.*` row rather than replacing it. The read surface is `GET /api/v1/audit-logs` (api/v1/__init__.py:46).

8. **Failure and health surfaces** — the global exception handler logs full detail server-side but returns only `{"detail": "Internal server error", "request_id"}`; it manually re-adds CORS headers because it runs in Starlette's outermost error middleware, *outside* `CORSMiddleware`, and without them a browser could never read the request_id it exists to provide (main.py:132-166). `/health` is pure liveness (main.py:173-177). `/ready` probes the database (`SELECT 1`), migrations-at-head (bundled Alembic head vs `alembic_version`), and Redis, each under a 5-second timeout; any failure returns 503 `not_ready` with a per-check dict, while object storage is reported `ok|degraded` but does **not** gate readiness (main.py:179-210). Workers apply the same migration gate at startup and exit on a definite mismatch (workers/celery_app.py:83-139).

9. **`middleware/auth.py` is not the auth path** — despite the name it holds no middleware. It is two pure functions, `configure_oidc_for_tenant` and `configure_saml_for_tenant`, that turn a tenant's SSO settings into an endpoint dict for future authlib wiring; nothing in the request path calls them (backend/src/contextedge/middleware/auth.py:1-11, 14-56). Human and service authentication for the API live in `deps.py` and `request_context.py`.

## Example: Acme VPN data at this stage

When an Acme domain admin triggers a backfill during the VPN incident, the request lifecycle produces traceability at every step.

**Input** (what arrives — the HTTP request from Acme's domain admin):

```
POST /api/v1/sources/6f1c9a52-3d47-4f0e-9a11-8b7c2d5e4a10/backfill
Authorization: Bearer eyJhbGciOi...
X-Correlation-ID: 0a5d3c81-6b2f-4e79-b0c4-2f8a1d6e7c39
```

**Middleware extracts and binds context** (contextvars bag, readable by any service code in this request):

```json
{
  "request_id": "3e9b7d10-5c42-4a8f-91d3-7b6e0f2c4a58",
  "correlation_id": "0a5d3c81-6b2f-4e79-b0c4-2f8a1d6e7c39",
  "tenant_id": "b2f4e6a8-1c3d-4e5f-8a9b-0c1d2e3f4a5b",
  "user_id": "9d8c7b6a-5e4f-4321-9876-0a1b2c3d4e5f",
  "user_email": "admin@acme.com",
  "roles": ["domain_admin"]
}
```

(The correlation ID is a UUID because the middleware discards anything else — see walkthrough step 2.)

**Output** (what the system produces — the middleware's `audit_logs` row, written after the response):

```json
{
  "tenant_id": "b2f4e6a8-1c3d-4e5f-8a9b-0c1d2e3f4a5b",
  "actor_id": "9d8c7b6a-5e4f-4321-9876-0a1b2c3d4e5f",
  "actor_email": "admin@acme.com",
  "action": "http.post.api.v1.sources.6f1c9a52-3d47-4f0e-9a11-8b7c2d5e4a10.backfill",
  "resource_type": "http_request",
  "details": {
    "path": "/api/v1/sources/6f1c9a52-3d47-4f0e-9a11-8b7c2d5e4a10/backfill",
    "status": 202,
    "outcome": "success",
    "request_id": "3e9b7d10-5c42-4a8f-91d3-7b6e0f2c4a58",
    "correlation_id": "0a5d3c81-6b2f-4e79-b0c4-2f8a1d6e7c39",
    "causation_id": "3e9b7d10-5c42-4a8f-91d3-7b6e0f2c4a58"
  }
}
```

The handler also writes an explicit `backfill.requested` audit row with the object IDs and window (api/v1/sources.py:394-406), and the dispatched `sync.run_backfill` task carries the same correlation ID in its message headers — so the `llm.usage` events from classifying that source's evidence later join back to this exact click.

## Design decisions

- **Decode JWT in middleware for context, enforce in dependencies** — *Why:* logging and the HTTP audit row get tenant/user identity even on routes that never call `get_current_user`, and an expired token still produces an attributable log line. *Tradeoff:* an invalid Bearer token does not fail at the middleware; a route that forgets its auth dependency would serve anonymously. Enforcement is therefore per-route by construction (deps.py:72-114).

- **Dual audit paths (middleware sync insert + async `log_audit_event`)** — *Why:* every mutating call gets a baseline `http.*` row even if a developer forgets explicit audit, while rich mutations still attach domain-named actions and structured details. *Tradeoff:* two mechanisms to understand, and the middleware insert uses a separate synchronous engine — deliberately, so it can run after the response and swallow its own failures without touching the request's transaction (request_audit.py:89-119).

- **Audit denied and failed mutations, not just successes** — *Why:* "who *tried* to change the retention policy and was refused" is exactly what a compliance review asks; recording only successes loses it. *Tradeoff:* two blind spots, both structural. Rows require a resolved tenant, so unauthenticated 401 probes live only in structlog — alert on the log stream for those (request_audit.py:59-64). And a mutation that raises an unhandled exception never reaches the audit block at all (request_audit.py:30-35), so crashes are visible as `unhandled_exception` log lines, not as audit rows.

- **Correlation IDs ride Celery message headers, not task arguments** — *Why:* `task.delay(...)` keeps its signature everywhere; the `before_task_publish` / `task_prerun` signal pair makes propagation automatic for every existing and future task (celery_app.py:25-68). *Tradeoff:* the mechanism is invisible at call sites, so it must be documented or it looks like magic; and per-task contextvar tokens are needed because concurrent pools interleave tasks (celery_app.py:16-20).

- **Service tokens in config JSON** — *Why:* a simple ops model for integrations without a full service-user table; a token without `allowed_domain_ids` is tenant-wide, one with them is domain-scoped where routes consult it. *Tradeoff:* rotation and scale-out token management are operator responsibilities, and scoping is claim-based, not binding-based (see the caveat below).

**Load-bearing caveat:** `RoleBinding.scope_type` / `scope_id` are stored but **not enforced** — login flattens bindings to role names (auth.py:92-95) and `has_role` is a pure name check (deps.py:37-44), so a domain admin bound to one domain holds that role tenant-wide on every `require_role` route. This is documented and deliberately not spot-fixed; multi-domain tenants must treat role grants as tenant-wide until the shared authorization layer lands ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Role bindings are stored, but login currently flattens roles").

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| App factory, exception handler, health | `backend/src/contextedge/main.py` | `create_app`, `lifespan`, `/health`, `/ready` | Startup and each process |
| Router index | `backend/src/contextedge/api/v1/__init__.py` | `router`, 33 `include_router` calls | Import time |
| Tenant + IDs middleware | `backend/src/contextedge/middleware/request_context.py` | `TenantContextMiddleware`, `bind_request_context`, `current_request_context` | Each non-exempt API request |
| HTTP mutation audit | `backend/src/contextedge/middleware/request_audit.py` | `RequestAuditMiddleware.dispatch` | After mutating `/api/v1` responses |
| JWT / service auth | `backend/src/contextedge/deps.py` | `get_current_user`, `CurrentUser.has_role`, `require_role` | Per protected endpoint |
| Login | `backend/src/contextedge/api/v1/auth.py` | `login`, `_create_token` | `POST /api/v1/auth/login` |
| Service token registry | `backend/src/contextedge/security_tokens.py` | `service_token_context` | Token validation |
| Async audit helper | `backend/src/contextedge/middleware/audit.py` | `log_audit_event` | Handlers recording domain actions |
| Correlation → Celery plumbing | `backend/src/contextedge/workers/celery_app.py` | `_inject_correlation_headers`, `_bind_worker_context` | Every `task.delay` / task start |
| Operational events | `backend/src/contextedge/services/event_log_service.py` | `append_operational_event` | Service code, API and workers |
| DB session injection | `backend/src/contextedge/database.py` | `get_db` (pooled engine: size 20, overflow 10) | Per request (via `DbSession`) |

## Acme VPN incident (this layer)

When an Acme **domain admin** pauses the ServiceNow sync or triggers a backfill from the dashboard during the VPN outage, `TenantContextMiddleware` tags the call with correlation IDs; `get_current_user` turns the token into a `CurrentUser` carrying the admin's **tenant id**, and each handler scopes its own query with it (`SourceObject.tenant_id == user.tenant_id`, api/v1/sources.py:246-252) — the dependency supplies the tenant, the route enforces it; the middleware's `http.post.*` row and the handler's explicit `sync.pause` / `backfill.requested` rows together answer "who changed what — and who was refused — after the incident"; and the same correlation ID rides into the sync worker, so the ingestion and model-usage events for INC0010427's evidence trace back to the click.

## Further reading

- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — where HTTP fits in the full flow
- [`docs/API.md`](../docs/API.md) — endpoint catalog and auth headers
- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — sources and sync runs behind the API
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the role-scoping caveat in full
