# Operating model: tenants, roles, and policies

## Summary

This page explains the administrative control plane behind ContextEdge: how organizations are modeled, how a login turns into an authorization decision, how tenant policy documents are stored, versioned, and attached, how per-tenant LLM spend is capped, and — stated plainly — which parts of the model are enforced today versus stored and awaiting an enforcer.

## Business picture

Before your organization can trust operational memory, it needs clear answers to three questions: **Who owns this knowledge? Who can see it? Who can change the rules?** The control plane sets up those boundaries so every action is scoped, every change is attributable, and every rule is explicit.

ContextEdge models this with a small set of familiar concepts:

| Concept | What it means to the business |
| --- | --- |
| Tenant | Your organization — the top-level boundary that keeps one company's memory completely separate from another's |
| Workspace | A broad operating area such as IT Operations or Customer Support |
| Domain | A narrower subject area such as VPN, Identity, or Endpoint Management |
| User | A person who acts inside the tenant |
| Service account | A non-human caller (an agent, a script) authenticated by a configured token |
| Role binding | A granted responsibility — who can administer, curate, or consume knowledge |
| Policy | A reusable rule for retention, classification, access, or approval that can be attached to resources |
| LLM budget | A daily ceiling on what one tenant may spend on AI calls |

The product ships with a practical role ladder so organizations can delegate authority clearly:

| Role | Typical responsibility |
| --- | --- |
| Platform super-admin | Create and inspect tenants across organizations |
| Tenant admin | Tenant settings, workspaces, domains, users, policies, budgets, automation mode |
| Domain admin | Connect sources, run discovery, control sync, attach policies, approve execution steps |
| Knowledge manager | Curate episodes, patterns, playbooks, identities, correlations, negative knowledge, evaluations |
| Playbook reviewer | Transition a playbook through its lifecycle |
| Authenticated user | Search, use runtime, open sessions inside the tenant |
| Service account | Retrieve runtime playbooks and graph projections, optionally restricted to named domains |

**One caveat that changes how you should read all of this.** A role grant behaves as **tenant-wide**, whatever scope you record on it. Binding someone as domain admin for "VPN and Connectivity" gives them domain-admin authority across every domain in the tenant, because request-time checks look only at the role *name*. Single-domain tenants are unaffected. Multi-domain tenants should treat every grant as tenant-wide until scoped authorization ships — see §3 and [KNOWN_GAPS.md](./KNOWN_GAPS.md).

## Technical walkthrough

### 1. The data model

All five control-plane tables live in `backend/src/contextedge/models/tenant.py`:

- **`Tenant`** — `name`, unique `slug`, `config` JSONB, `sso_config`, `retention_defaults`, `is_active`.
- **`Workspace`** — tenant-scoped. **`Domain`** — tenant-scoped, with an optional `workspace_id`; the domain is the more common business scope for sources, evidence, patterns, sessions, and playbooks.
- **`User`** — `tenant_id`, `email` (**not globally unique** — two tenants can hold the same address), nullable `password_hash` for SSO users, `status` defaulting to `"active"`.
- **`RoleBinding`** — `user_id`, `role` (a plain string, no enum), `scope_type` defaulting to `"tenant"`, nullable `scope_id` (`models/tenant.py:88-108`).
- **`TenantLLMBudget`** — primary key is `tenant_id`, with `daily_token_limit`, `daily_cost_cap_usd NUMERIC(12,4)`, and `action_on_exceed` constrained to `BUDGET_ACTIONS = ("block", "warn")` (`models/tenant.py:111-143`).

> `Tenant.retention_defaults` is accepted on `TenantCreate` / `TenantUpdate` (`backend/src/contextedge/schemas/tenant.py:27,33`) and **read by nothing** — a repo-wide search finds no consumer. The retention window that actually applies comes from an active `TenantPolicy` of type `retention`; see [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) §2.

### 2. Login → JWT

`POST /api/v1/auth/login` (`backend/src/contextedge/api/v1/auth.py:35-101`) is more careful than it looks, and each precaution has a reason:

1. Fetch up to **5** `status="active"` users matching the email, oldest first. Because email is per-tenant rather than global, `scalar_one_or_none()` would raise on a cross-tenant duplicate and turn it into a 500; the cap bounds the bcrypt work an attacker can trigger per call, and hitting the cap logs `auth.candidate_cap_reached` so a hidden sixth account is diagnosable (`auth.py:43-57`).
2. With no candidate, verify against a fixed dummy hash so "email exists" and "email doesn't" take the same time (`auth.py:16-18, 58-64`).
3. bcrypt runs on a worker thread — never on the event loop (`auth.py:66-73`).
4. Same email **and** same password in two tenants returns 401 "Ambiguous account" rather than guessing which tenant you meant (`auth.py:76-89`).
5. Roles are the flat list of `RoleBinding.role` values for that user (`auth.py:92-95`).

The token payload is exactly `{sub, tenant_id, email, roles, exp}` (`auth.py:21-32`), expiring after `jwt_access_token_expire_minutes` (60).

> **Load-bearing consequence:** a human JWT carries **no `workspace_ids` and no `allowed_domain_ids`**. `get_current_user` will read those claims if present (`backend/src/contextedge/deps.py:97-108`), but nothing issues them for a login. In practice, `allowed_domain_ids` arrives only on a **service token**. Both claims do have a consumer once they exist — the MAF agent's access scope refuses a domain outside a service token's `allowed_domain_ids`, and refuses a domain whose workspace is outside `workspace_ids` unless the caller is a tenant admin (`backend/src/contextedge/graph/agent/service.py:59-94`, applied again per row at `graph/agent/hydrators.py:140`) — but nothing under `api/` or `services/` reads `workspace_ids`, and no login ever populates it, so today that branch never fires for a human.

### 3. Principals and role checks

`get_current_user` (`deps.py:72-114`) resolves one of two principal kinds:

- **Service account** — the `X-Service-Token` header wins when present; an invalid token is **403**, not a fall-through to Bearer. Context comes from `service_tokens_json`, parsed in `backend/src/contextedge/security_tokens.py:12-36`, and may carry `allowed_domain_ids`. A service token without that key is tenant-wide.
- **User** — Bearer JWT, decoded with `settings.jwt_secret_key`; any decode failure is 401.

`CurrentUser.has_role(role)` returns **True unconditionally** for `platform_super_admin`, `tenant_admin`, or `admin`, and otherwise checks membership (`deps.py:37-44`). `require_role` raises 403 (`deps.py:46-51`).

Counted on 2026-08-19 across `backend/src/contextedge/api/v1/*.py`, there are **106** route-level `require_role(...)` calls: `knowledge_manager` 49, `tenant_admin` 30, `domain_admin` 24, `platform_super_admin` 2, `playbook_reviewer` 1.

**Where the domain scope is actually honoured:** only where a route explicitly passes it — the agent graph projection, which builds an access scope from the principal (`backend/src/contextedge/api/v1/graph.py:26` → `graph/agent/service.py:39-94`); the edge-proposal routes, which forward `allowed_domain_ids` into `edge_proposal_service` (`graph.py:137,160,183`); inventory, which forwards it into `inventory_diff_service` (`backend/src/contextedge/api/v1/inventory.py:71`); and runtime (`backend/src/contextedge/api/v1/runtime.py:39,138`). There is no universal scope filter.

**The RBAC caveat, in code terms:** `RoleBinding.scope_type` / `scope_id` are stored but never consulted. Login selects role names only, `has_role` is a pure name check, so a domain admin bound to one domain holds that role across the tenant on every `require_role` route. This was deliberately **not** spot-fixed: a partially-honoured scoping change — some routes obeying it, others not — is more dangerous than the documented current state. The real shape is effective grants as `(role, scope_type, scope_id)` enforced through a shared authorization layer, with negative tests per sensitive route ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Role bindings are stored, but login currently flattens roles").

**Frontend and backend disagree about super-roles, on purpose.** The dashboard's `hasRole` treats only `platform_super_admin` as a super-role (`frontend/src/lib/roles.ts:7-9`), while the backend also short-circuits `tenant_admin` and `admin` (`deps.py:37-44`). The mismatch runs both ways and you will see both: a tenant admin sees only nav items that name `tenant_admin` explicitly, even though the API would authorize them for `knowledge_manager` routes; and a **domain admin sees the "Audit Log" nav item** (`frontend/src/components/shell/sidebar-nav.tsx:66`) but the route requires `tenant_admin` (`backend/src/contextedge/api/v1/audit.py:26`) and answers 403. **Nav visibility is UX filtering, not security.**

### 4. Admin surfaces

| Surface | Route | Gate |
| --- | --- | --- |
| List / create tenants | `GET`/`POST /api/v1/tenants` | `platform_super_admin` (`api/v1/tenants.py:21,34`) |
| Read one tenant | `GET /api/v1/tenants/{id}` | own tenant, or `platform_super_admin` for anyone else's (`tenants.py:62`) |
| Update tenant | `PATCH /api/v1/tenants/{id}` | `tenant_admin` (`tenants.py:74`) |
| Workspaces | `POST`/`PATCH /api/v1/workspaces` | `tenant_admin` (`api/v1/workspaces.py:33,72`) |
| Domains | `POST`/`PATCH /api/v1/domains` | `tenant_admin` (`api/v1/domains.py:32,71`) |
| Users | `GET`/`POST`/`PATCH /api/v1/users` | `tenant_admin` (`api/v1/users.py:29,42,83`) |
| Assign / remove a role | `POST /api/v1/users/{id}/roles`, `DELETE .../roles/{binding_id}` | `tenant_admin` (`users.py:115,153`) |
| List policies (grouped) | `GET /api/v1/policies` | `tenant_admin` **or** `domain_admin` **or** `knowledge_manager` (`api/v1/policies.py:57-67`) |
| Create / update / delete a policy | `POST`/`PATCH`/`DELETE /api/v1/policies` | `tenant_admin` (`policies.py:85,127,150`) |
| Policy assignments | `GET`/`POST`/`DELETE /api/v1/policy-assignments` | `domain_admin` (`api/v1/policy_assignments.py:73,125,149`) |
| LLM budget | `GET`/`PUT /api/v1/admin/tenant-budget`, `GET .../status` | `tenant_admin` |

Tenant, workspace, domain, user, and source mutations call `log_audit_event`, so the control plane is traceable as well as configurable (`backend/src/contextedge/middleware/audit.py:10-44`) — and every mutating `/api/v1` call is additionally captured by `RequestAuditMiddleware`. See [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) §6.

### 5. Policies: documents, versions, assignments, and checks

**The document.** `TenantPolicy` stores `policy_type` (one of `POLICY_TYPES = {"retention", "classification", "access", "approval"}`), `name`, `description`, `is_active`, and a JSONB `config` (`backend/src/contextedge/models/policy.py:21-47`). `GET /api/v1/policies` returns them grouped by type through `TYPE_TO_RESPONSE_KEY` (`models/policy.py:62-67`).

**The version.** `version` starts at 1 and **only bumps when `config` changes**: `PATCH /api/v1/policies/{id}` compares the incoming config to the stored one before incrementing (`api/v1/policies.py:133-140`). Renaming a policy or deactivating it does not bump it, because the version tracks **rules, not labels** — a past decision must keep meaning what it meant (`models/policy.py:49-54`). Migration `0056` added this along with `policy_checks`.

**The evaluation record.** `PolicyCheck` is one append-only row per evaluation of one policy **version** against one artifact (`models/policy.py:70-128`). `result` has exactly three values — `pass`, `fail`, `not_applicable` — because each maps to a distinct next action for the executor; there is no "warning" state, since a policy that warns has already allowed. `check_name` names the rule *inside* the policy (`max_automation_mode`, `forbid_self_approval`, `trust_scope`), and `input_snapshot` records what the check actually saw, so a recorded verdict stays reproducible when the inputs move. `policy_id` is `ON DELETE SET NULL`: "judged against a policy that has since been deleted" is a real audit record and losing it would be worse than keeping the orphan.

Writes go through `record_policy_check` (`backend/src/contextedge/services/policy_check_service.py:34`), which is **fail-soft by design** — the gate has already decided, and an audit write must never turn an allowed action into a failed one. A missing check row therefore means the record is incomplete, never that the action was wrong.

**The assignment.** Definition and attachment are separate on purpose: one API creates the reusable document, another attaches it to a source, evidence item, or playbook. `assert_policy_assignment` refuses with **400** when the id is not a policy of the expected type in the caller's tenant (`backend/src/contextedge/services/policy_assignment.py:12-35`). Sources carry retention and classification policy ids; evidence items carry access policy ids; playbooks carry an approval policy id.

**Where an approval policy actually bites.** `approval_policy_service` recognises four optional config keys — `approver_roles`, `forbid_self_approval`, `require_approval_min_safety_class`, `max_automation_mode` — and ignores unknown keys so tenants can carry their own metadata (`backend/src/contextedge/services/approval_policy_service.py:9-25, 43-53`). A dangling, inactive, or wrong-type reference **fails closed**: a broken governance pointer must never silently disable governance (`approval_policy_service.py:22-24`). Enforcement happens at execution start and at approval decision time, and both the allow and the deny are recorded. Details in [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) §2–3.

**`action_policies` is a separate table and engine** (`backend/src/contextedge/services/action_policy_service.py`, authored via `/api/v1/action-policies`, mounted at `backend/src/contextedge/api/v1/__init__.py:59`). It decides by scope filter → specificity → conflict resolution, defaulting to `most_restrictive` (`action_policy_service.py:49-64`, `_matches` 87, `specificity` 98, `select_policy` 117), and an unknown verdict ranks most restrictive so a typo cannot read as `allowed_auto` (`action_policy_service.py:75-79`). It is evaluated per step at execution start (`evaluate_action` 156, called from `execution_service._apply_action_policy` 206): blocking verdicts refuse, `approval_required` gates, and **`allowed_auto` grants nothing**.

### 6. Per-tenant LLM budgets

`TenantLLMBudget` is the spend gate for every LLM call in the system.

- **Pre-call, always.** `ai/provider.llm_complete` (and its JSON and batch variants) calls `check_budget` **before** spending. `allowed=False` with `action="block"` raises `TenantBudgetExceeded`; with `action="warn"` the call proceeds and an `llm.budget_warning` operational event makes that day queryable (`backend/src/contextedge/ai/provider.py:238-279`, and the same block at `759-768` and `842-851`).
- **No row now means deployment defaults, not "unlimited."** `check_budget` falls back to `default_daily_token_limit` (**2,000,000**), `default_daily_cost_cap_usd` (**$25.00**), and `default_budget_action_on_exceed` (**`block`**) through a `_DefaultBudget` stand-in that runs the identical evaluation path — no second copy of the limit logic to drift (`backend/src/contextedge/services/tenant_budget_service.py:107-121, 249-282`; `backend/src/contextedge/config.py:191-198`). It is **deliberately not persisted**: writing a row on first use would create config nobody asked for and then shadow later changes to the defaults. Setting both config values to `None` restores genuinely unlimited (`reason="no_budget"`).
- **Usage has one source of truth.** The current UTC day's `llm.usage` operational events are summed for tokens, and cost is estimated from them — there is no second aggregation column to drift out of step (`tenant_budget_service.py:191-231`).
- **Tokens are checked before cost** (`tenant_budget_service.py:301-320`), so a tenant with only a token cap never sees `cost_cap_exceeded`.
- **Caching and races.** A 60-second module cache (`USAGE_CACHE_TTL_SECONDS`, `tenant_budget_service.py:51`) means at most one over-cap call slips through per minute. A per-tenant `asyncio.Lock` serialises concurrent checks **within one event loop**, and the lock table is keyed **per loop** in a `WeakKeyDictionary` — a single flat dict made the first task's loop own every lock and killed a 499-task sweep with "bound to a different event loop" (`tenant_budget_service.py:53-90`). Cross-worker races remain unbounded pending a Redis counter; that is documented, not hidden. An `after_delete` listener evicts the cache when a budget row is deleted (`tenant_budget_service.py:173-184`).
- **Operator surfaces:** `GET /api/v1/admin/llm-usage`, `GET`/`PUT /api/v1/admin/tenant-budget`, `GET /api/v1/admin/tenant-budget/status`, and `GET /api/v1/admin/pipeline-health` (`backend/src/contextedge/api/v1/admin_cost.py:33,102,113,137,166`); the UI is `BudgetPanel` on `/admin/cost`.

**What hitting the cap looks like:** chunks stuck at `embedding IS NULL` and the tenant's `llm.usage` events stopping dead. There is no `budget_exceeded` outcome anywhere in the backend — `check_budget` raises `TenantBudgetExceeded` at the top of `llm_complete` (`ai/provider.py:242-245`), *before* the `try`/`finally` that records usage, so a blocked call is never recorded at all and `outcome` only ever takes `ok` or `error` (`provider.py:324, 383`). Diagnose with `GET /api/v1/admin/tenant-budget/status`; in `warn` mode you additionally get one `llm.budget_warning` operational event per call (`provider.py:256-275`). Before any bulk backfill, provision a budget row (roughly 100k tokens per thread-heavy ticket) or set the action to `warn` for the window — a live 84-ticket Zoho backfill burned the 2M default in about two hours and froze the pipeline mid-run until an operator intervened (`docs/RUNBOOK.md` §7.12, "Onboarding a new tenant / bulk backfill"). See [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md).

### 7. Per-tenant prompt A/B is config-only

`settings.tenant_prompt_variants_json` maps tenant id → prompt name → version, for example `{"<tenant-uuid>": {"relevance": "v3", "episode": "v2"}}` (`backend/src/contextedge/config.py:238-243`). Resolution precedence is **tenant override → registered default**; an unknown prompt name raises `KeyError` (fail loud), and an override naming an unregistered version falls back to the default with a `prompt_variant_not_registered_falling_back` warning (`backend/src/contextedge/ai/prompts/__init__.py:124-162`). Malformed JSON logs `prompt_variants_config_invalid` and yields an empty map, so a bad config can never crash the ingest path (`ai/prompts/__init__.py:95-114`).

Prompts are immutable versioned dataclasses registered at import time by **eleven family modules** — `applicability, contradiction, decision, episode, episode_review, identity, issue_signature, message_function, pattern, playbook, relevance` (`ai/prompts/__init__.py:189-201`) — carrying **thirteen** distinct prompt names between them (the identity family alone registers `identity`, `identity_adjudication`, and `identity_reconciliation`). `get_prompt` returns the object whose `.version` callers thread into `record_llm_usage`, so every `llm.usage` event records the prompt name and version and the cost dashboard can break spend down by variant (`ai/prompts/__init__.py:174-183`).

### 8. What the dashboard covers

The Settings page has five tabs — **General, Workspaces, Domains, Users, Retention** (`frontend/src/app/(dashboard)/settings/page.tsx:278-285`) — and Policies has its own page gated to `tenant_admin` in the nav (`frontend/src/components/shell/sidebar-nav.tsx:65`).

It is **not yet a complete admin console**, and the gaps are worth naming rather than glossing ([KNOWN_GAPS.md](./KNOWN_GAPS.md)): role-binding CRUD, edit/deactivate flows for workspaces and domains, and the retention console remain API-led or placeholder; generic policy-assignment listing and playbook approval-policy assignment have backend surfaces but no first-class dashboard workflow; and there is no UI at all for placing a legal hold.

Composite permission predicates live in `frontend/src/lib/roles.ts:22-56`. One is deliberately narrower than the rest: `canEditAutomationMode` is `tenant_admin` only, because `suggest_only` caps every caller at `read_only` regardless of role — raising the automation mode is what makes every other approval gate load-bearing, and that is not the same privilege as editing a playbook's text.

## Example: Acme VPN data at this stage

**Setting up the organization**

```json
{
  "tenant": { "id": "acme-corp", "name": "Acme Corporation", "slug": "acme", "is_active": true },
  "workspaces": [
    { "id": "ws-it-ops",  "name": "IT Operations" },
    { "id": "ws-security","name": "Security Operations" }
  ],
  "domains": [
    { "id": "vpn-connectivity", "name": "VPN and Connectivity", "workspace_id": "ws-it-ops" },
    { "id": "endpoint-mgmt",    "name": "Endpoint Management",  "workspace_id": "ws-it-ops" },
    { "id": "identity-access",  "name": "Identity and Access",  "workspace_id": "ws-security" }
  ]
}
```

**Granting roles — and what the grant actually does**

```json
{
  "role_bindings": [
    { "user": "admin@acme.com",     "role": "tenant_admin",      "scope_type": "tenant",    "scope_id": "acme-corp" },
    { "user": "vpn-lead@acme.com",  "role": "domain_admin",      "scope_type": "domain",    "scope_id": "vpn-connectivity" },
    { "user": "knowledge@acme.com", "role": "knowledge_manager", "scope_type": "tenant",    "scope_id": "acme-corp" }
  ]
}
```

The JWT `vpn-lead@acme.com` receives is:

```json
{ "sub": "u-vpn-lead", "tenant_id": "acme-corp", "email": "vpn-lead@acme.com", "roles": ["domain_admin"], "exp": 1755600000 }
```

Note what is **not** in it: no `scope_id`, no `allowed_domain_ids`, no `workspace_ids`. Every `require_role("domain_admin")` route in the tenant accepts this token, including ones about Security Operations sources. That is the open RBAC gap, not a documentation simplification.

**A service token, which is the one principal that can be domain-limited**

```json
{
  "ce_svc_9f2c...": {
    "tenant_id": "acme-corp",
    "user_id": "u-agent-01",
    "email": "agent@acme.local",
    "roles": ["service_account"],
    "allowed_domain_ids": ["vpn-connectivity"]
  }
}
```

**A policy document, and what a config edit does to its version**

```json
{
  "id": "pol-approval-vpn",
  "policy_type": "approval",
  "name": "VPN change approval",
  "is_active": true,
  "version": 2,
  "config": {
    "approver_roles": ["domain_admin"],
    "forbid_self_approval": true,
    "require_approval_min_safety_class": "high_side_effect",
    "max_automation_mode": "supervised"
  }
}
```

Renaming it to "VPN change approval (v2)" leaves `version` at 2. Adding `"low_side_effect"` to `require_approval_min_safety_class` makes it 3 — and every `policy_checks` row already written against version 2 keeps meaning exactly what it meant.

**The check that policy produced during the incident**

```json
{
  "policy_id": "pol-approval-vpn",
  "policy_type": "approval",
  "policy_version": 2,
  "check_name": "forbid_self_approval",
  "evaluated_entity_type": "approval_request",
  "evaluated_entity_id": "apr-step2",
  "result": "pass",
  "input_snapshot": { "decided_by": "u-vpn-lead", "run_initiated_by": "u-responder" },
  "evaluated_at": "2026-08-19T10:41:00Z"
}
```

**Attaching policies to resources**

```json
{
  "policy_assignments": [
    { "policy_type": "retention",      "policy_name": "Standard IT retention",        "attached_to": "source:src-servicenow-01" },
    { "policy_type": "classification", "policy_name": "Internal — standard",          "attached_to": "source:src-teams-vpn" },
    { "policy_type": "access",         "policy_name": "Restricted — security only",   "attached_to": "evidence:ev-sensitive-vpn-config" },
    { "policy_type": "approval",       "policy_name": "VPN change approval",          "attached_to": "playbook:pb-r1s2t3" }
  ]
}
```

**The tenant's LLM budget**

```json
{
  "tenant_id": "acme-corp",
  "daily_token_limit": 8000000,
  "daily_cost_cap_usd": 120.0,
  "action_on_exceed": "warn"
}
```

Without this row, Acme would run under the deployment defaults — 2M tokens, $25, **block** — which is what froze a comparable backfill mid-run.

**What each principal can and cannot do during the VPN incident**

| Principal | Role | Can | Cannot |
| --- | --- | --- | --- |
| admin@acme.com | tenant_admin | manage users, policies, budgets, automation mode | create another tenant (`platform_super_admin` only) |
| vpn-lead@acme.com | domain_admin | trigger sync, control sync runs, attach policies, approve the certificate step | read the audit log (nav shows it; the API answers 403) |
| knowledge@acme.com | knowledge_manager | approve episodes, curate patterns and playbooks, manage evidence access | change tenant settings or budgets |
| agent@acme.local | service_account (`allowed_domain_ids: [vpn-connectivity]`) | fetch runtime playbooks and graph projections for the VPN domain | reach graph or inventory data in other domains |

## Design decisions

- **A small, explicit organization model (tenant → workspace → domain)** — *Why:* business users need a model they can explain without a diagram of internal abstractions, and domain is the natural join for sources, evidence, patterns, and playbooks. *Tradeoff:* some customers want richer hierarchies, and the three-level model has no place to put them.

- **Roles are checked by name at request time** — *Why:* every route can call `require_role` without loading a permission graph, which keeps the 106 call sites cheap and readable (`deps.py:46-51`). *Tradeoff:* this is exactly why stored scope is unenforced; the honest fix is a shared authorization layer over effective grants, which is architecture rather than a patch — and shipping it half-applied would be worse than the documented status quo.

- **Scoped RBAC was deliberately deferred, not forgotten** — *Why:* a change some routes honour and others don't creates a false sense of containment, which is more dangerous than a documented tenant-wide grant. *Tradeoff:* multi-domain tenants carry real risk today and must compensate with who they grant, not with what they scope.

- **Backend super-roles are wider than frontend super-roles** — *Why:* `tenant_admin` should not be blocked from an operation it can perform by definition, while the nav deliberately shows a focused menu. *Tradeoff:* the two disagree in both directions (a domain admin sees an Audit Log link that 403s), so nav visibility must never be read as an authorization statement.

- **Policy documents are generic and typed** — *Why:* retention, classification, access, and approval share one storage shape while keeping distinct business meaning through `policy_type`, and `assert_policy_assignment` stops the shapes being mixed up at attachment time. *Tradeoff:* validation lives in service code rather than in a policy engine, so a config key nobody implemented is silently ignored.

- **The version tracks rules, not labels** — *Why:* editing a policy's config silently rewrote the rules every past decision had been judged under; keying `policy_checks` to the version freezes that history, and skipping the bump on a rename keeps the version meaningful (`api/v1/policies.py:133-140`). *Tradeoff:* two policies can share a name across versions, so tooling must show the version rather than the name alone.

- **Policy checks are recorded on the deny path and written fail-soft** — *Why:* the refusal is the evaluation an audit most wants, and an audit write must never turn an allowed action into a failed one. *Tradeoff:* the ledger can be incomplete without anything being wrong, so "no check row" cannot be read as "no check ran."

- **A missing budget row means deployment defaults, not "no cap"** — *Why:* "no row = no limit" left the normal case — a freshly provisioned tenant — as the only uncapped one, which is exactly backwards (`config.py:191-193`). *Tradeoff:* onboarding a tenant now requires provisioning a budget before a bulk backfill, or the default `block` action stops the pipeline mid-run.

- **The budget default is a stand-in object, not a written row** — *Why:* persisting on first use would create configuration nobody asked for and then shadow later changes to the deployment default (`tenant_budget_service.py:107-116`). *Tradeoff:* there is no row to inspect for an unconfigured tenant, so `GET /admin/tenant-budget/status` is the only way to see what is actually in force.

- **Prompt A/B is configuration, not a database table** — *Why:* prompt versions are immutable code artifacts, so routing them by env config keeps the experiment and the artifact in the same review flow, and every `llm.usage` event records which version served. *Tradeoff:* changing a variant means a config change and a restart, not an admin screen.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Tenant model | `backend/src/contextedge/models/tenant.py` | `Tenant` (12), `Workspace` (30), `Domain` (48), `User` (68), `RoleBinding` (88), `TenantLLMBudget` (116) | ORM |
| Current principal | `backend/src/contextedge/deps.py` | `CurrentUser` (16), `has_role` (37), `get_current_user` (72), `require_role` (117) | Every protected request |
| Service tokens | `backend/src/contextedge/security_tokens.py` | `service_token_context` (12) | `X-Service-Token` requests |
| Login token assembly | `backend/src/contextedge/api/v1/auth.py` | `login` (36), `_create_token` (21) | Authentication |
| Tenant API | `backend/src/contextedge/api/v1/tenants.py` | list (14), create (28), get (56), update (67) | Platform / tenant admin |
| Workspace API | `backend/src/contextedge/api/v1/workspaces.py` | create (31), update (65) | Tenant admin |
| Domain API | `backend/src/contextedge/api/v1/domains.py` | create (30), update (64) | Tenant admin |
| User and role API | `backend/src/contextedge/api/v1/users.py` | create (40), update (81), assign role (111), list roles (140), remove role (151) | Tenant admin |
| Policy model + check ledger | `backend/src/contextedge/models/policy.py` | `POLICY_TYPES` (21), `POLICY_CHECK_RESULTS` (28), `TenantPolicy` (31), `PolicyCheck` (70) | ORM |
| Policy API | `backend/src/contextedge/api/v1/policies.py` | `list_policies` (57), `create_policy` (83), `update_policy` (120), `delete_policy` (148) | Admin governance |
| Policy assignment guard | `backend/src/contextedge/services/policy_assignment.py` | `assert_policy_assignment` (12) | Before an attachment is saved |
| Policy assignment API | `backend/src/contextedge/api/v1/policy_assignments.py` | list (66), assign (119), delete (141) | Resource governance |
| Policy-check writer | `backend/src/contextedge/services/policy_check_service.py` | `record_policy_check` (34) | Every gate evaluation |
| Approval policy engine | `backend/src/contextedge/services/approval_policy_service.py` | `ApprovalPolicy` (44), `load_approval_policy` (63), `check_automation_mode` (106), `check_decider` (127) | Execution start / decide |
| Action policy engine | `backend/src/contextedge/services/action_policy_service.py` | scope → specificity → conflict resolution | Per step at execution start |
| LLM budget service | `backend/src/contextedge/services/tenant_budget_service.py` | `check_budget` (234), `_DefaultBudget` (108), `upsert_budget`, `TenantBudgetExceeded` (123), `USAGE_CACHE_TTL_SECONDS` (51) | Pre-call gate on every LLM call |
| Budget + cost API | `backend/src/contextedge/api/v1/admin_cost.py` | llm-usage (33), tenant-budget get/put (102,113), status (137), pipeline-health (166) | Tenant admin |
| Deployment defaults | `backend/src/contextedge/config.py` | `default_daily_token_limit` (194), `default_daily_cost_cap_usd` (195), `default_budget_action_on_exceed` (198) | Import time |
| Prompt registry | `backend/src/contextedge/ai/prompts/__init__.py` | `resolve_version` (124), `get_prompt` (174), family imports (189-201) | Every LLM call |
| Admin UI | `frontend/src/app/(dashboard)/settings/page.tsx` | Settings tabs (278-285) | Dashboard |
| Policy UI | `frontend/src/app/(dashboard)/policies/page.tsx` | `PoliciesPage` | Dashboard governance |
| Frontend role helpers | `frontend/src/lib/roles.ts` | `hasRole` (7), `canTransitionPlaybook` (28), `canEditAutomationMode` (41) | Nav + button gating |

## Acme VPN incident (this layer)

Acme only benefits from the VPN incident's memory if the structure around it is clear. The tenant is Acme Corporation; the domain is "VPN and Connectivity" inside the IT Operations workspace. The **domain admin** connects the ServiceNow and Teams sources and later approves the certificate-renewal step during the incident. The **knowledge manager** approves the episode that `INC0010427` produced and curates the playbook that comes out of it. The **tenant admin** owns the rules: a `retention` policy that keeps identity-bearing ticket evidence for years, an `access` policy restricting the sensitive VPN configuration evidence, and an `approval` policy whose `forbid_self_approval` is what stopped the responder approving their own step — recorded as a `policy_checks` row against version 2 of that policy. A domain-limited **service token** lets an agent pull the VPN domain's graph projection and nothing else. And because Acme has its own `tenant_llm_budgets` row, the cold-start backfill of the VPN corpus warned instead of freezing when it crossed the deployment default.

## Further reading

- [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) — auth, JWTs, service tokens, and the middleware that mints correlation ids
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — how an access policy affects retrieval
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — where approval and action policies are enforced, and both audit trails
- [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) — how a `retention` policy turns into an archive window
- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) — where these controls appear in the UI
- [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) — the budget gate, usage events, and cost attribution
- [`docs/API.md`](../docs/API.md) — route-level details for tenants, users, policies, and assignments
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the scoped-RBAC blocker and the admin-console coverage list
