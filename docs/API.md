# ContextEdge — API reference

HTTP API base path: **`/api/v1`**. On a local dev server, **OpenAPI** is at **`http://localhost:8000/docs`** (Swagger UI) and **`http://localhost:8000/redoc`**.

For system architecture and domain model, see [Technical blueprint](TECHNICAL_BLUEPRINT.md). For running migrations, Docker, and troubleshooting, see [Runbook](RUNBOOK.md).

---

## Authentication

### Human users (JWT)

1. `POST /api/v1/auth/login` with credentials → JSON including an **access token**.
2. Send `Authorization: Bearer <access_token>` on subsequent requests.

JWT claims used by the app include `sub` (user id), `tenant_id`, `email`, `roles`, `workspace_ids`.

### Service accounts / integrations

- Header: **`X-Service-Token: <opaque-token>`**
- Tokens are mapped via environment variable **`SERVICE_TOKENS_JSON`** (see [`.env.example`](../.env.example)) to `tenant_id`, `user_id`, `email`, `roles`, and optionally **`allowed_domain_ids`** (list of domain UUID strings).
- When `allowed_domain_ids` is set for a service token, **`/runtime/match`** and **`/runtime/playbooks/{stable_key}`** are limited to those domains plus tenant-wide playbooks (playbook `domain_id` null). Omit the key for full-tenant access; use `[]` for tenant-wide-only.
- Implementation: `contextedge.security_tokens.service_token_context`, `contextedge.deps.get_current_user`.

### Request context middleware

`TenantContextMiddleware` decodes the same Bearer JWT or service token into `request.state` for auditing and logging. Paths that skip this include `/health`, `/ready`, `/docs`, `/redoc`, `/openapi.json`, `/metrics`, and **`/api/v1/auth/login`**.

### RBAC

`CurrentUser.has_role` grants all roles if `platform_super_admin` is present. Routes use `require_role(...)` or ad hoc checks (for example policies listing).

### SSO / enterprise IdP

OIDC/SAML helpers and stubs live in `middleware/auth.py`. Day-to-day development uses JWT from login or seed users, not full per-tenant SSO flows.

---

## Router index

Prefixes are relative to **`/api/v1`** (see `contextedge/api/v1/__init__.py`).

| Prefix | Tag | Concern |
| --- | --- | --- |
| `/auth` | auth | Login, tokens |
| `/tenants`, `/workspaces`, `/domains`, `/users` | … | Multi-tenant admin |
| `/audit-logs` | audit | Audit trail |
| `/sources`, `/sync-runs` | sources, sync | Connectors and sync |
| `/evidence`, `/threads`, `/episodes` | … | Evidence and reconstruction |
| `/patterns`, `/playbooks` | … | Knowledge and governance |
| `/runtime` | runtime | Match, explain, playbook-by-key, feedback |
| `/evaluations` | evaluations | Datasets and runs |
| `/policies` | policies | Tenant policies CRUD + list |
| `/drift` | drift | Drift alerts |

---

## Runtime

Base path: **`/api/v1/runtime`**. Implementation: `contextedge.api.v1.runtime`.

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/match` | Build query text from symptoms, entities, optional context; hybrid-rank **approved** playbooks; returns `match_id`, `results`, `filters_applied`, optional `fallback_guidance`. Caches payload in Redis under `runtime:match:{match_id}` (TTL on the order of **1 hour**). |
| `GET` | `/explain/{match_id}` | Returns cached scoring/context for the same **tenant**. **404** if cache miss or expired; **403** if `tenant_id` does not match. |
| `GET` | `/playbooks/{stable_key}` | Returns a **published** version only (`published_at` set). Default: if `current_version_id` is unpublished, falls back to the **latest published** version by `published_at`. Query **`version`**: exact semantic version, also must be published. Optional **`domain_id`**. |
| `POST` | `/feedback` | Records `RetrievalFeedback` for evaluation and analytics. |

### Risk tier cap (caller-based)

Effective maximum risk tier is derived from **roles** (and principal type), **not** from `tenant_policies.config` in the current implementation:

| Caller | Effective cap |
| --- | --- |
| `platform_super_admin`, `tenant_admin`, `domain_admin` | No cap (all tiers) |
| `knowledge_manager`, `service_account` | **high** |
| Other roles | **medium** |

Tier ordering: `minimal` &lt; `low` &lt; `medium` &lt; `high` &lt; `critical` (`contextedge.search.risk_policy`). Both **`POST /match`** (via `rank_playbooks`) and **`GET /playbooks/{stable_key}`** enforce the cap with `risk_within_cap`.

`POST /match` includes `filters_applied` (for example `max_risk_tier`, `domain_id`, `risk_cap_source`) in the response and in the Redis payload for explain.

### Domain scoping

- **Match**: optional `domain_id` in the JSON body. Must belong to the caller’s tenant or the API returns **400**. Passed into `rank_playbooks` to restrict eligible playbooks.
- **Playbook by key**: optional `domain_id` query parameter. If the playbook has a non-null `domain_id`, it must equal the query value; tenant-wide playbooks (`domain_id` null) remain retrievable when a domain filter is supplied. Invalid cross-tenant domain → **400**; wrong domain for a domain-bound playbook → **403**.

### Hybrid ranking (summary)

`contextedge.search.hybrid_ranker.rank_playbooks` combines retrieval signals (FTS, vector, and other factors as implemented) and applies **`max_risk_tier`** and **`domain_id`**. Each result can include **`scoring_breakdown`** for transparency.

---

## Tenant policies (`/policies`)

Model: `TenantPolicy` / table `tenant_policies` (`contextedge.models.policy`).

- **`policy_type`**: `retention` | `classification` | `access` | `approval`
- Fields: `name`, `description`, JSONB **`config`**, `is_active`, tenant scope

### HTTP surface

- **`GET /policies`**: Returns grouped lists (`retention_policies`, `classification_policies`, `access_policies`, `approval_policies`). Allowed roles: **`tenant_admin`**, **`domain_admin`**, **`knowledge_manager`**.
- **Create / update / delete**: Implemented in `api/v1/policies.py` with appropriate admin-style checks.

### Assignments on other resources

- **Sources**: `classification_policy_id`, `retention_policy_id` on create/update — validated via `contextedge.services.policy_assignment`.
- **Evidence**: `PATCH` access-policy endpoint and `access_policy_id` on evidence items (see `api/v1/evidence.py`).

**Note:** Assigning policies to sources/evidence is supported; **runtime risk caps** remain **role-based** until product logic reads policy `config`.

---

## Drift (`/drift`)

- **`GET /api/v1/drift/alerts`** — Read-only drift/freshness signals for the UI. Does **not** change playbook lifecycle.
- **Celery `detect_drift`** — Calls `check_playbook_drift`: builds alerts (including `past_expiry` while playbooks are still `approved`), then applies `approved` → `expired` for past `expiry_at`, and returns `alerts`, `alert_count`, and `expired_transition_count`.

---

## Observability (HTTP)

- **`GET /health`**, **`GET /ready`** — Liveness-style responses from `main.py`.
- **`/metrics`** — Prometheus metrics (FastAPI instrumentator).

---

## Related code paths

| Concern | Location |
| --- | --- |
| App mount | `contextedge.main` → `include_router(..., prefix="/api/v1")` |
| Auth dependency | `contextedge.deps` |
| Runtime | `contextedge.api.v1.runtime` |
| Policies | `contextedge.api.v1.policies` |
| Schemas | `contextedge.schemas.*` |

When you add or rename routers, update the **Router index** in this file and the link from [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md).
