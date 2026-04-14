# API and request lifecycle

## Summary

You will see how an HTTP call moves through ContextEdge: which middleware runs first, how **tenant and user** identity reach routers and logs, how **JWT** and **service tokens** differ, and where **audit** records are written—so you can reason about security and traceability without reading every router file.

## Business picture

Every customer expects their data kept strictly separate and every action attributable to a real person or integration. When anyone calls the API, the platform immediately identifies **who** they are and **which organization** they belong to. Any change to data automatically leaves an audit trail that compliance officers and operations teams can review later. Optional tracking headers let support staff link related calls into a single story—useful during an incident investigation or a routine compliance check.

## Technical walkthrough

1. **Application creation** — `create_app()` builds the FastAPI app, attaches **CORS**, **Prometheus** instrumentation, and registers middleware in order: **request audit** (outer) then **tenant context** then CORS (see `main.py`). Lifespan opens **Redis** and ensures the object-store bucket exists.

2. **Tenant context middleware** — `TenantContextMiddleware` runs for most `/api` traffic. It assigns `request_id`, `correlation_id`, and `causation_id` from headers or generates them, stores them on `request.state`, and mirrors them into a **contextvars** bag via `bind_request_context` / `update_request_context` so async code and audit helpers can read them without passing `Request` everywhere. For Bearer JWT it decodes (without failing the request on bad tokens—downstream auth still enforces) and sets `tenant_id`, `user_id`, `email`, `roles`. For `X-Service-Token` it uses `service_token_context` when JWT did not set a tenant. Paths like `/health`, `/docs`, and `/api/v1/auth/login` are exempt from JWT parsing. Response headers echo `X-Request-ID` and `X-Correlation-ID`.

3. **Request audit middleware** — After the handler returns, `RequestAuditMiddleware` inspects **mutating** methods on `/api/v1` (POST/PATCH/PUT/DELETE). It logs `http.mutating_request` to **structlog**. If `tenant_id` is known and status is success, it inserts a row into `audit_logs` using a small **synchronous** SQLAlchemy engine (separate from the async app pool)—best-effort; failures are logged, not raised.

4. **Per-route authentication** — Routers use FastAPI `Depends`: `get_current_user` in `deps.py` is the gate for protected endpoints. It accepts either `Authorization: Bearer` (JWT) or `X-Service-Token`. Service tokens are resolved from `settings.service_tokens_json` via `service_token_context` in `security_tokens.py`. The returned `CurrentUser` carries `tenant_id`, roles, workspace allowlists, and optional `allowed_domain_ids` for service principals.

5. **Explicit audit from handlers** — Many mutations also call `log_audit_event` in `middleware/audit.py`, which writes `AuditLog` through the **async** session and merges request/correlation IDs from `current_request_context()`. This complements the middleware's generic HTTP audit row.

6. **Router surface** — `api/v1/__init__.py` mounts routers: auth, tenants, workspaces, domains, users, audit logs, sources, sync runs, evidence, threads, episodes, patterns, playbooks, sessions, runtime, evaluations, policies, drift, execution, graph, etc. Each module owns a slice of the product API; behavior lives in `services/` not in fat routers.

7. **`middleware/auth.py`** — Today this file holds **SSO/OIDC/SAML stubs** and placeholders for future authlib wiring; it is **not** the primary JWT path. Human and service authentication for the API are implemented in `deps.py` and `request_context.py`.

## Example: Acme VPN data at this stage

When an Acme domain admin calls the API to trigger a sync or update evidence policy, the request lifecycle produces traceability at every step.

**Input — HTTP request from Acme's domain admin**

```
POST /api/v1/sources/src-jira-01/sync
Authorization: Bearer eyJhbGciOi...
X-Correlation-ID: corr-vpn-incident-2026-03
```

**Middleware extracts and binds context**

```json
{
  "request_id": "req-8a4f2c",
  "correlation_id": "corr-vpn-incident-2026-03",
  "tenant_id": "acme-corp",
  "user_id": "usr-admin-01",
  "email": "admin@acme.com",
  "roles": ["domain_admin"]
}
```

**Output — audit log row (written after response)**

```json
{
  "audit_log_id": "aud-3b7e9d",
  "tenant_id": "acme-corp",
  "actor_id": "usr-admin-01",
  "action": "POST /api/v1/sources/src-jira-01/sync",
  "correlation_id": "corr-vpn-incident-2026-03",
  "status_code": 202,
  "timestamp": "2026-03-15T10:05:00Z"
}
```

Every subsequent call in this incident can share the same `correlation_id`, letting auditors trace the full chain of admin actions during the VPN outage.

## Design decisions

- **Decode JWT in middleware for context, enforce in dependencies** — *Why:* logging and optional audit get tenant/user hints even when a route does not use `get_current_user`. *Tradeoff:* invalid Bearer tokens do not fail at middleware; callers still get 401 from dependencies where required.

- **Dual audit paths (middleware sync insert + async `log_audit_event`)** — *Why:* mutating traffic gets a baseline row even if a developer forgets explicit audit; rich mutations still attach structured `details`. *Tradeoff:* two mechanisms to understand; sync insert uses a separate DB pool.

- **Service tokens in config JSON** — *Why:* simple ops model for integrations without a full service-user table. *Tradeoff:* rotation and scale-out token management are operator responsibilities (see deployment docs).

- **Context variables for request IDs** — *Why:* services and audit code stay testable and avoid threading `Request` through every function. *Tradeoff:* code must run inside the middleware's context binding (normal for HTTP handlers).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| App factory | `backend/src/contextedge/main.py` | `create_app`, `lifespan`, `_cors_origins` | Startup and each process |
| Router index | `backend/src/contextedge/api/v1/__init__.py` | `router`, `include_router` | Import time |
| Tenant + IDs middleware | `backend/src/contextedge/middleware/request_context.py` | `TenantContextMiddleware`, `bind_request_context`, `current_request_context` | Each API request (non-exempt) |
| HTTP mutation audit | `backend/src/contextedge/middleware/request_audit.py` | `RequestAuditMiddleware.dispatch` | After mutating `/api/v1` responses |
| JWT / service auth | `backend/src/contextedge/deps.py` | `get_current_user`, `CurrentUser`, `require_role` | Per protected endpoint |
| Service token registry | `backend/src/contextedge/security_tokens.py` | `service_token_context` | Token validation |
| Async audit helper | `backend/src/contextedge/middleware/audit.py` | `log_audit_event` | Handlers that record domain actions |
| DB session injection | `backend/src/contextedge/database.py` | `get_db` | Per request (via `DbSession` alias in `deps.py`) |

## Acme VPN incident (this layer)

When an Acme **domain admin** patches evidence access policy or triggers sync from the dashboard, `TenantContextMiddleware` tags the call with correlation IDs; `get_current_user` ensures the admin's **tenant** matches the evidence tenant; `RequestAuditMiddleware` and `log_audit_event` together support the question "who changed visibility on VPN-related tickets after the incident?"

## Further reading

- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — where HTTP fits in the full flow  
- [`docs/API.md`](../docs/API.md) — endpoint catalog and auth headers  
- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — sources and sync runs behind the API  
