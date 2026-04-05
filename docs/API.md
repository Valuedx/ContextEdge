# ContextEdge - API Reference

HTTP API base path: **`/api/v1`**.

On a local dev server:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

For system architecture and the data model, see [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md). For first-time local installation, see [SETUP_GUIDE.md](SETUP_GUIDE.md). For operations, migrations, Docker, and troubleshooting, see [RUNBOOK.md](RUNBOOK.md).

---

## Authentication

### Human users (JWT)

1. `POST /api/v1/auth/login` with credentials
2. Receive a JSON payload containing an access token
3. Send `Authorization: Bearer <access_token>` on subsequent requests

JWT claims used by the app include:

- `sub`
- `tenant_id`
- `email`
- `roles`
- `workspace_ids`

### Service accounts and integrations

- Header: `X-Service-Token: <opaque-token>`
- Tokens are defined in `SERVICE_TOKENS_JSON`
- Supported fields include `tenant_id`, `user_id`, `email`, `roles`, and optional `allowed_domain_ids`

Runtime-specific behavior:

- If `allowed_domain_ids` is set, `/runtime/match` and `/runtime/playbooks/{stable_key}` are limited to those domains plus tenant-wide playbooks
- If `allowed_domain_ids` is omitted, the token gets full-tenant runtime access
- If `allowed_domain_ids` is `[]`, the token is limited to tenant-wide playbooks only

Implementation paths:

- `contextedge.security_tokens.service_token_context`
- `contextedge.deps.get_current_user`

### Request context middleware

`TenantContextMiddleware` decodes the same Bearer JWT or service token into `request.state` for auditing and logging.

Paths skipped by that middleware include:

- `/health`
- `/ready`
- `/docs`
- `/redoc`
- `/openapi.json`
- `/metrics`
- `/api/v1/auth/login`

### RBAC

`CurrentUser.has_role(...)` grants all roles if `platform_super_admin` is present. Routes use `require_role(...)` or explicit checks depending on the endpoint.

### SSO and enterprise IdP

OIDC and SAML helpers remain in `middleware/auth.py`, but normal development still uses login-issued JWTs or seeded users rather than full per-tenant SSO.

---

## Router Index

Prefixes are relative to `/api/v1`.

| Prefix | Tag | Concern |
| --- | --- | --- |
| `/auth` | auth | Login and token issuance |
| `/tenants`, `/workspaces`, `/domains`, `/users` | admin | Multi-tenant administration |
| `/audit-logs` | audit | Audit trail |
| `/sources`, `/sync-runs` | sources, sync | Connectors and synchronization |
| `/evidence`, `/threads`, `/episodes` | evidence | Evidence and reconstruction |
| `/patterns`, `/playbooks` | knowledge | Patterns, playbooks, governance |
| `/runtime` | runtime | Match, explain, playbook fetch, feedback |
| `/evaluations` | evaluations | Datasets and evaluation runs |
| `/policies` | policies | Tenant policies |
| `/drift` | drift | Drift alerts |

---

## Sync Operational Note

The sync API is queue-oriented, not single-flight.

- `POST /sources/{source_id}/backfill` and `POST /sync-runs/{run_id}/retry` enqueue work asynchronously.
- The system does not yet serialize overlapping manual requests for the same `SourceObject`.
- API clients and operational tooling should avoid issuing concurrent backfills or retries for the same source object.

---

## Runtime

Base path: `/api/v1/runtime`.

Implementation: `contextedge.api.v1.runtime`.

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/match` | Builds query text from symptoms, entities, and optional context; hybrid-ranks approved playbooks; returns `match_id`, `results`, `filters_applied`, and optional `fallback_guidance` |
| `GET` | `/explain/{match_id}` | Returns cached scoring and context for the same tenant |
| `GET` | `/playbooks/{stable_key}` | Returns a published playbook version only |
| `POST` | `/feedback` | Records `RetrievalFeedback` |

### Match explain cache

- Cached in Redis under `runtime:match:{match_id}`
- TTL is approximately 1 hour
- `/runtime/explain/{match_id}` returns `404` if the cache entry is gone
- `/runtime/explain/{match_id}` returns `403` if the cached payload belongs to another tenant

### Risk tier cap

Effective maximum risk tier is derived from caller role and principal type, not from `TenantPolicy.config`.

| Caller | Effective cap |
| --- | --- |
| `platform_super_admin`, `tenant_admin`, `domain_admin` | No cap |
| `knowledge_manager`, `service_account` | `high` |
| Other roles | `medium` |

Tier ordering:

`minimal < low < medium < high < critical`

Implementation path:

- `contextedge.search.risk_policy`

### Domain scoping

For `POST /runtime/match`:

- `domain_id` is optional
- If supplied, it must belong to the caller's tenant
- If a service token has `allowed_domain_ids`, the requested domain must be in that allowlist

For `GET /runtime/playbooks/{stable_key}`:

- `domain_id` is optional
- If the playbook is domain-bound, it must match the requested domain
- Tenant-wide playbooks remain accessible when a domain filter is provided
- Service-token allowlists are enforced here as well

### Published version behavior

- Runtime only returns published versions where `published_at` is set
- If `current_version_id` points to an unpublished version, runtime falls back to the latest published version
- Explicit `version=<semantic_version>` also requires that version to be published

### Hybrid ranking summary

`contextedge.search.hybrid_ranker.rank_playbooks(...)` combines:

- keyword / FTS signal
- semantic evidence signal
- graph signal
- quality / freshness / recency heuristics
- caller-based risk filtering
- optional domain filtering

Each result can include a `scoring_breakdown`.

---

## Policies

Base path: `/api/v1/policies`.

Model: `TenantPolicy`.

Supported `policy_type` values:

- `retention`
- `classification`
- `access`
- `approval`

Fields include:

- `name`
- `description`
- `config`
- `is_active`
- tenant scope

### HTTP surface

- `GET /policies` returns grouped lists by policy type
- CRUD routes are implemented in `api/v1/policies.py`

### Current usage

- Sources may reference `classification_policy_id` and `retention_policy_id`
- Evidence items may reference `access_policy_id`
- Runtime risk caps are still role-based today

---

## Drift

Base path: `/api/v1/drift`.

Current behavior:

- `GET /drift/alerts` is read-only and does not mutate lifecycle state
- Celery drift detection uses `check_playbook_drift(...)`
- Scheduled drift can mark approved playbooks past `expiry_at` as `expired`

The Celery drift payload includes:

- `alerts`
- `alert_count`
- `expired_transition_count`

---

## Observability

HTTP endpoints:

- `GET /health`
- `GET /ready`
- `GET /metrics`

---

## Related Code Paths

| Concern | Location |
| --- | --- |
| App mount | `contextedge.main` |
| Auth dependency | `contextedge.deps` |
| Runtime router | `contextedge.api.v1.runtime` |
| Policies router | `contextedge.api.v1.policies` |
| Schemas | `contextedge.schemas.*` |

When you add or rename routers, update this file and the document map in [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md).
