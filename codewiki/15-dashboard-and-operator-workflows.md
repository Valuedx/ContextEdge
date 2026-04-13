# Dashboard and operator workflows

## Summary

This page explains the product the way a business user experiences it: as a dashboard with workflows for onboarding sources, investigating evidence, curating knowledge, resolving incidents, and governing live automation. It also maps those screens back to the backend APIs that actually enforce the rules.

## Business picture

For most users, ContextEdge is not "the ingestion pipeline" or "the Celery topology." It is a control tower. The left navigation organizes work into a business sequence:

| Workflow | Main pages | What the user is trying to accomplish |
| --- | --- | --- |
| Monitor | Overview, Sources, Sync Operations | See whether evidence is flowing and whether review queues are growing |
| Investigate | Evidence, Episodes, Identities, Correlations, Contradictions | Understand what happened and what evidence supports it |
| Curate | Patterns, Playbooks, Negative Knowledge | Turn investigation results into reusable and governed memory |
| Resolve | Runtime, Sessions, Execution | Match live incidents to playbooks, capture decisions, and process approvals |
| Govern | Drift, Evaluations, Policies, Audit Log, Settings | Keep memory current, controlled, and compliant |

The dashboard is therefore a business workflow shell over an API-first backend. The UI helps users move from raw signals to governed action, but most permission, search, ranking, and retention rules still live on the server.

## Technical walkthrough

1. **Global shell and navigation** - `SidebarNav` defines the main information architecture, while `AppHeader` adds the theme toggle, account actions, and `NotificationBell`. The bell currently polls unread notifications and lets the user mark them read from a dropdown. In code: `frontend/src/components/shell/sidebar-nav.tsx`, `frontend/src/components/shell/app-header.tsx`, `backend/src/contextedge/api/v1/notifications.py`, `backend/src/contextedge/services/notification_service.py`.

2. **Overview is the operator's health snapshot** - `OverviewPage` aggregates counts from `/sources`, `/evidence`, `/episodes`, and `/playbooks` and derives lightweight freshness signals from playbook metadata. It is intentionally heuristic and fast, not a full observability console. In code: `frontend/src/app/(dashboard)/overview/page.tsx`, `frontend/src/lib/api.ts`.

3. **Source onboarding mixes governance and convenience** - `SourcesPage` lists configured sources. `AddSourceDialog` supports a local-folder ingest path using the browser Folder Picker, then calls `/sources` and `/sources/local-ingest`. `SourceDetailPage` exposes sync state, policy attachment, credential rotation, and recent sync runs. `DiscoveryPage` lets tenant admins approve discovered source objects for sync and backfill. In code: `frontend/src/app/(dashboard)/sources/`, `frontend/src/components/sources/add-source-dialog.tsx`, `backend/src/contextedge/api/v1/sources.py`, `backend/src/contextedge/api/v1/sync.py`.

4. **Evidence views keep provenance visible** - The Evidence explorer is where analysts search and browse normalized records. `EvidenceDetailPage` shows provenance, thread summary, attachment extraction results, and optional access policy assignment. If a thread is only partially available, the page can request hydration. In code: `frontend/src/app/(dashboard)/evidence/page.tsx`, `frontend/src/app/(dashboard)/evidence/[id]/page.tsx`, `backend/src/contextedge/api/v1/evidence.py`, `backend/src/contextedge/api/v1/threads.py`.

5. **Knowledge curation is split into successive pages** - Episodes capture incident narratives, Patterns highlight recurrence, Playbooks store governed procedures, Negative Knowledge captures ineffective or prohibited steps, and Identities / Correlations / Contradictions refine the system's memory graph. The dashboard keeps these as separate pages because they are different business decisions, even when they share the same underlying evidence. In code: `frontend/src/app/(dashboard)/episodes/page.tsx`, `frontend/src/app/(dashboard)/patterns/page.tsx`, `frontend/src/app/(dashboard)/playbooks/page.tsx`, `frontend/src/app/(dashboard)/negative-knowledge/page.tsx`, `frontend/src/app/(dashboard)/identities/page.tsx`, `frontend/src/app/(dashboard)/correlations/page.tsx`, `frontend/src/app/(dashboard)/contradictions/page.tsx`.

6. **Runtime, sessions, and execution support live incident work** - `RuntimePage` is a sandbox over the production runtime APIs. It lets a user submit symptoms and entities, inspect ranked playbooks, fetch explain payloads, and submit retrieval feedback. `SessionsPage` manages resolution sessions and trace review. `ExecutionPage` handles pending approval requests for higher-risk execution steps. In code: `frontend/src/app/(dashboard)/runtime/page.tsx`, `frontend/src/app/(dashboard)/sessions/page.tsx`, `frontend/src/app/(dashboard)/execution/page.tsx`, `backend/src/contextedge/api/v1/runtime.py`, `backend/src/contextedge/api/v1/sessions.py`, `backend/src/contextedge/api/v1/execution.py`.

7. **Governance pages stay close to day-to-day work** - Drift and Evaluations show whether stored memory still performs well. Policies and Audit Log show who changed rules or resources. Settings holds tenant, workspace, domain, and user context. This keeps governance inside the same product flow rather than hiding it in a separate admin tool. In code: `frontend/src/app/(dashboard)/drift/page.tsx`, `frontend/src/app/(dashboard)/evaluations/page.tsx`, `frontend/src/app/(dashboard)/policies/page.tsx`, `frontend/src/app/(dashboard)/audit/page.tsx`, `frontend/src/app/(dashboard)/settings/page.tsx`.

8. **The frontend is intentionally thin** - The shared `api` client adds the bearer token and request ids, then all page components call the backend directly. This means the UI is easy to reason about, but it also means backend capability can exist before there is a polished screen for it. In code: `frontend/src/lib/api.ts`, `frontend/src/lib/stores/auth-store.ts`.

## Design decisions

- **Thin client over API-first backend** - Why: the same rules should apply to the dashboard, service integrations, and future clients. Tradeoff: some product capabilities appear in the API before the UI catches up.

- **Separate screens match separate business decisions** - Why: source approval, evidence review, pattern curation, runtime retrieval, and execution approval are owned by different roles. Tradeoff: users must learn the navigation model instead of relying on one giant page.

- **Local-file ingest exists for fast demos and reconstruction** - Why: teams often want to load a folder of logs or exported threads without standing up every connector first. Tradeoff: the current Add Source flow is more complete for local ingest than for cloud connector setup.

- **Governance is visible, not hidden** - Why: policies, drift, evaluations, audit, and approvals are core operational behaviors, not afterthoughts. Tradeoff: the dashboard surface area is larger than a simple search console.

- **Notifications are lightweight today** - Why: the current in-app dropdown is enough to surface important review and drift signals early. Tradeoff: there is not yet a full inbox, live push channel, or workflow routing console.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Left navigation | `frontend/src/components/shell/sidebar-nav.tsx` | `navItems`, `SidebarNav` | Every dashboard view |
| Header and notifications | `frontend/src/components/shell/app-header.tsx` | `AppHeader`, `NotificationBell` | Every dashboard view |
| Shared API client | `frontend/src/lib/api.ts` | `ApiClient`, `api` | Every frontend API call |
| Overview | `frontend/src/app/(dashboard)/overview/page.tsx` | `OverviewPage`, `playbookNeedsAttention` | Dashboard home |
| Source list | `frontend/src/app/(dashboard)/sources/page.tsx` | `SourcesPage`, `SourceActions` | Source management |
| Add source flow | `frontend/src/components/sources/add-source-dialog.tsx` | `AddSourceDialog`, `handleBrowseFolder` | Source creation |
| Source detail | `frontend/src/app/(dashboard)/sources/[id]/page.tsx` | `SourceDetailPage` | Source governance |
| Discovery inventory | `frontend/src/app/(dashboard)/sources/[id]/discovery/page.tsx` | `DiscoveryPage` | Source object approval |
| Evidence detail | `frontend/src/app/(dashboard)/evidence/[id]/page.tsx` | `EvidenceDetailPage` | Investigation |
| Runtime sandbox | `frontend/src/app/(dashboard)/runtime/page.tsx` | `RuntimePage`, `FeedbackTab` | Live retrieval and feedback |
| Sessions | `frontend/src/app/(dashboard)/sessions/page.tsx` | `SessionsPage`, `NewSessionDialog`, `SessionDetail` | Incident coordination |
| Execution approvals | `frontend/src/app/(dashboard)/execution/page.tsx` | `ExecutionPage`, `DecideDialog` | Human approval queue |
| Negative knowledge | `frontend/src/app/(dashboard)/negative-knowledge/page.tsx` | `NegativeKnowledgePage`, `NKDialog` | Curating failed steps |
| Policies | `frontend/src/app/(dashboard)/policies/page.tsx` | `PoliciesPage`, `PolicySection` | Governance admin |
| Settings | `frontend/src/app/(dashboard)/settings/page.tsx` | `SettingsPage`, `NewWorkspaceDialog`, `NewDomainDialog` | Tenant admin |

## Acme VPN incident (this layer)

In the Acme VPN outage, the dashboard is the business user's path through the product: an admin sees the source health on Overview, a responder opens Evidence and Sessions to understand the outage, a knowledge manager updates Negative Knowledge and Playbooks after learning what failed, and an approver uses Execution to allow or deny a higher-risk recovery action. The same incident appears as one governed workflow rather than a disconnected set of backend APIs.

## Further reading

- [00-business-capability-map.md](./00-business-capability-map.md) - business-first orientation
- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) - source and sync internals
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) - evidence retrieval behavior behind the UI
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) - sessions, execution, and audit internals
- [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) - settings, users, roles, and policies
