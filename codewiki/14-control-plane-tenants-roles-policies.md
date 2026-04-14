# Operating model: tenants, roles, and policies

## Summary

This page explains the administrative control plane behind ContextEdge: how organizations are modeled, how users and roles are represented, how tenant policy documents are stored and attached, and which parts are already available in the dashboard versus still API-led.

## Business picture

Before your organization can trust operational memory, it needs clear answers to three questions: **Who owns this knowledge? Who can see it? Who can change the rules?** The control plane sets up these boundaries so every action is scoped, every change is attributable, and every rule is explicit.

ContextEdge models this with a small set of familiar concepts:

| Concept | What it means to the business |
| --- | --- |
| Tenant | Your organization — the top-level boundary that keeps one company's memory completely separate from another's |
| Workspace | A broad operating area such as IT Operations or Customer Support |
| Domain | A narrower subject area such as VPN, Identity, or Endpoint Management |
| User | A person (or service account) who acts inside the tenant |
| Role binding | A granted responsibility — who can administer, curate, or consume knowledge |
| Policy | A reusable rule for retention, classification, access, or approval that can be attached to any resource |

The product ships with a practical role ladder so organizations can delegate authority clearly:

| Role | Typical responsibility |
| --- | --- |
| Platform super-admin | Create and inspect tenants across organizations |
| Tenant admin | Manage tenant settings, workspaces, domains, users, policies, and some approvals |
| Domain admin | Connect sources, run discovery, retry sync, change some policy assignments, approve execution requests |
| Knowledge manager | Curate patterns, playbooks, evidence access policies, identities, correlations, negative knowledge, and evaluations |
| Authenticated user | Search, use runtime, and open sessions within the tenant |
| Service account | Retrieve runtime playbooks with optional domain restrictions |

## Technical walkthrough

1. **Tenant is the hard isolation line** - Core tables carry `tenant_id`, and most API queries filter on `user.tenant_id`. Only `platform_super_admin` can list or create tenants globally. In code: `backend/src/contextedge/models/tenant.py`, `backend/src/contextedge/api/v1/tenants.py`, `backend/src/contextedge/deps.py`.

2. **Workspaces and domains add business structure** - A workspace groups operating areas; a domain can optionally belong to a workspace and is the more common business scope for sources, evidence, patterns, sessions, and playbooks. The dashboard exposes create-and-list flows in Settings. In code: `backend/src/contextedge/api/v1/workspaces.py`, `backend/src/contextedge/api/v1/domains.py`, `frontend/src/app/(dashboard)/settings/page.tsx`.

3. **Users and roles define who can act** - Users live inside a tenant, and `RoleBinding` records the granted role plus optional `scope_type` and `scope_id`. The login flow currently flattens role bindings to role names when it builds the JWT, so most request-time checks are role-name checks, with extra scope arriving through token claims such as `allowed_domain_ids` and `workspace_ids`. In code: `backend/src/contextedge/api/v1/users.py`, `backend/src/contextedge/api/v1/auth.py`, `backend/src/contextedge/deps.py`.

4. **Policies are tenant-owned rule documents** - `TenantPolicy` stores a `policy_type`, name, description, active flag, and JSON config. The product groups these into four buckets: retention, classification, access, and approval. In code: `backend/src/contextedge/models/policy.py`, `backend/src/contextedge/api/v1/policies.py`, `frontend/src/app/(dashboard)/policies/page.tsx`.

5. **Policy definitions are separate from policy assignments** - This is a deliberate split. One API creates the reusable policy document; another attaches it to a source, evidence item, or playbook. `assert_policy_assignment` prevents a tenant from attaching the wrong policy type to the wrong resource. In code: `backend/src/contextedge/services/policy_assignment.py`, `backend/src/contextedge/api/v1/policy_assignments.py`.

6. **Resource attachment follows business intent** - Sources can carry retention and classification policy ids. Evidence items can carry access policy ids. Playbooks can carry approval policy ids. The current dashboard surfaces source and evidence assignment flows directly, while playbook approval assignment remains an API-first capability. In code: `backend/src/contextedge/api/v1/sources.py`, `backend/src/contextedge/api/v1/evidence.py`, `backend/src/contextedge/models/playbook.py`, `frontend/src/app/(dashboard)/sources/[id]/page.tsx`, `frontend/src/app/(dashboard)/evidence/[id]/page.tsx`.

7. **Admin mutations are auditable** - Tenant, workspace, domain, user, and source changes call `log_audit_event`, which means the control plane is not just configurable; it is traceable. In code: `backend/src/contextedge/middleware/audit.py`, `backend/src/contextedge/api/v1/tenants.py`, `backend/src/contextedge/api/v1/workspaces.py`, `backend/src/contextedge/api/v1/domains.py`, `backend/src/contextedge/api/v1/users.py`.

## Example: Acme VPN data at this stage

**Setting up the organization**

```json
{
  "tenant": {
    "tenant_id": "acme-corp",
    "name": "Acme Corporation",
    "sso_provider": "okta",
    "retention_defaults": { "short_term_days": 90, "long_term_days": 365 }
  },
  "workspaces": [
    { "workspace_id": "ws-it-ops", "name": "IT Operations" },
    { "workspace_id": "ws-security", "name": "Security Operations" }
  ],
  "domains": [
    { "domain_id": "vpn-connectivity", "name": "VPN and Connectivity", "workspace_id": "ws-it-ops" },
    { "domain_id": "endpoint-mgmt", "name": "Endpoint Management", "workspace_id": "ws-it-ops" },
    { "domain_id": "identity-access", "name": "Identity and Access", "workspace_id": "ws-security" }
  ]
}
```

**Assigning roles**

```json
{
  "role_bindings": [
    { "user": "admin@acme.com", "role": "tenant_admin", "scope": "tenant:acme-corp" },
    { "user": "vpn-lead@acme.com", "role": "domain_admin", "scope": "domain:vpn-connectivity" },
    { "user": "knowledge@acme.com", "role": "knowledge_manager", "scope": "tenant:acme-corp" },
    { "user": "analyst@acme.com", "role": "analyst", "scope": "workspace:ws-it-ops" }
  ]
}
```

**Attaching policies to resources**

```json
{
  "policy_assignments": [
    {
      "policy_type": "retention",
      "policy_name": "Standard IT retention — 1 year",
      "attached_to": "source:src-jira-01"
    },
    {
      "policy_type": "access",
      "policy_name": "Restricted — security team only",
      "attached_to": "evidence:ev-sensitive-vpn-config"
    },
    {
      "policy_type": "classification",
      "policy_name": "Internal — standard sensitivity",
      "attached_to": "source:src-teams-vpn"
    }
  ]
}
```

**Result — what each role can do during the VPN incident**

| Person | Role | Can do | Cannot do |
| --- | --- | --- | --- |
| admin@acme.com | tenant_admin | Configure sources, manage users, set policies | N/A (full tenant scope) |
| vpn-lead@acme.com | domain_admin | Trigger sync, approve source objects, retry failed jobs for VPN domain | Modify Security Operations sources |
| knowledge@acme.com | knowledge_manager | Review episodes, curate playbooks, manage evidence access | Change tenant settings |
| analyst@acme.com | analyst | Search evidence, use runtime, open sessions within IT Ops | See restricted-access evidence, approve playbooks |

## Design decisions

- **Small, explicit organization model** - Why: business users need a model they can explain without a diagram full of internal abstractions. Tradeoff: some customers may want richer hierarchies than tenant -> workspace -> domain.

- **Policy documents stay generic and typed** - Why: retention, classification, access, and approval share the same storage shape while keeping clear business meaning through `policy_type`. Tradeoff: policy validation lives in code and process, not in a separate policy engine.

- **Definition and attachment are separated** - Why: one policy can be reused across many sources or evidence items. Tradeoff: operators have to understand both the library of policies and where they are attached.

- **Roles are easy to check at request time** - Why: most routes can call `require_role` quickly without loading a large permission graph. Tradeoff: fine-grained scope behavior depends on token claims and endpoint-specific logic, not a universal runtime permission resolver.

- **The dashboard focuses on the common admin path first** - Why: create tenant structure, view policies, and attach common rules quickly. Tradeoff: some advanced admin actions still require direct API use and are called out in [KNOWN_GAPS.md](./KNOWN_GAPS.md).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Tenant model | `backend/src/contextedge/models/tenant.py` | `Tenant`, `Workspace`, `Domain`, `User`, `RoleBinding` | Data modeling |
| Current principal | `backend/src/contextedge/deps.py` | `CurrentUser`, `get_current_user`, `require_role` | Every protected request |
| Tenant API | `backend/src/contextedge/api/v1/tenants.py` | `create_tenant`, `update_tenant` | Platform and tenant admin workflows |
| Workspace API | `backend/src/contextedge/api/v1/workspaces.py` | `create_workspace`, `update_workspace` | Tenant admin workflows |
| Domain API | `backend/src/contextedge/api/v1/domains.py` | `create_domain`, `update_domain` | Tenant admin workflows |
| User and role API | `backend/src/contextedge/api/v1/users.py` | `create_user`, `assign_role`, `list_user_roles` | Tenant admin workflows |
| Login token assembly | `backend/src/contextedge/api/v1/auth.py` | `login`, `_create_token` | Authentication |
| Policy model | `backend/src/contextedge/models/policy.py` | `TenantPolicy`, `POLICY_TYPES` | Governance configuration |
| Policy API | `backend/src/contextedge/api/v1/policies.py` | `list_policies`, `create_policy`, `update_policy` | Admin governance |
| Policy assignment guard | `backend/src/contextedge/services/policy_assignment.py` | `assert_policy_assignment` | Before attachments are saved |
| Policy assignment API | `backend/src/contextedge/api/v1/policy_assignments.py` | `assign_policy`, `delete_policy_assignment` | Resource governance |
| Admin UI | `frontend/src/app/(dashboard)/settings/page.tsx` | `SettingsPage` | Dashboard settings |
| Policy UI | `frontend/src/app/(dashboard)/policies/page.tsx` | `PoliciesPage`, `PolicySection` | Dashboard governance |

## Acme VPN incident (this layer)

Acme Corp only benefits from the VPN incident memory if the business structure is clear: the tenant is Acme, the domain might be "VPN and Connectivity," the domain admin connects ticketing and chat sources, the knowledge manager curates evidence visibility and playbook quality, and tenant policies determine how long the underlying outage data is kept and who may retrieve the most sensitive records.

## Further reading

- [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) - auth, JWTs, service tokens, and middleware context
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) - how access policy affects retrieval
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) - audit and execution controls
- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) - where these admin controls appear in the UI
- [`docs/API.md`](../docs/API.md) - route-level details for tenants, users, policies, and assignments
