# Dashboard and operator workflows

## Summary

This page explains the product the way a business user experiences it: as a dashboard with workflows for onboarding sources, watching the ingest pipeline, investigating evidence, curating knowledge, resolving live incidents, and governing automation. It also maps each screen and each button back to the API route, service function, and Celery task that actually does the work, so a developer can click through from a screenshot to the code.

## Business picture

For most users, ContextEdge is a **control tower** — not a database or a pipeline diagram. The dashboard organizes work into a business sequence: **monitor** your data sources and the ingest pipeline, **investigate** what happened, **curate** knowledge, **resolve** live incidents, and **govern** the system over time.

| Workflow | Main pages | What the user is trying to accomplish |
| --- | --- | --- |
| Monitor | Overview, Sources, Sync Operations, Pipeline Health | See whether evidence is flowing, whether the graph is being built, and whether review queues are growing |
| Investigate | Evidence, Episodes, Identities, Correlations, Contradictions, Graph Explorer | Understand what happened and what evidence supports it |
| Curate | Patterns, Playbooks, Negative Knowledge, Review Queues | Turn investigation results into reusable, governed memory |
| Resolve | Runtime, Sessions, Review Queue, Execution, Decisions | Match live incidents to playbooks, capture decisions, and process approvals |
| Govern | Drift, Evaluations, Policies, Audit Log, LLM Cost, Settings | Keep memory current, controlled, affordable, and compliant |

The dashboard is a business workflow layer over an API-first backend. The UI helps users move from raw signals to governed action, while permission, search, ranking, cost, and retention rules are enforced on the server.

Two of these pages exist because of a specific, expensive lesson. During a live bulk ingest, every number an operator could see said "healthy" — evidence climbing, tokens climbing, zero task failures — while the graph was not being built at all. **Pipeline Health** answers "is this getting anywhere"; **LLM Cost** answers "what is this spending". They are separate pages because they are separate failure modes: a run can spend steadily, fail nothing, and build nothing.

## Technical walkthrough

### 1. Shell, authentication, and navigation

The dashboard route group's layout is a client component. It calls `isAuthenticated()` in an effect and `router.replace("/login")` when there is no session (`frontend/src/app/(dashboard)/layout.tsx:17-21`), then renders the sidebar, `AppHeader`, and a scrollable main region (`layout.tsx:23-45`). This is convenience gating only — the real enforcement is the API returning 401/403.

`SidebarNav` holds the whole information architecture as one array of 25 `navItems`, each optionally carrying `requiredRoles` (`frontend/src/components/shell/sidebar-nav.tsx:44-70`). An item is visible when the user holds **any** of its required roles (`sidebar-nav.tsx:76-78`).

**A load-bearing asymmetry to know about:** the frontend's `hasRole` treats only `platform_super_admin` as a super-role (`frontend/src/lib/roles.ts:7-9`), while the backend's `CurrentUser.has_role` short-circuits for `platform_super_admin`, `tenant_admin`, **and** `admin` (`backend/src/contextedge/deps.py:37-44`). A tenant admin therefore sees only the nav items that name `tenant_admin` explicitly, even though the API would authorize them for a `knowledge_manager`-gated call. Nav visibility is user-experience filtering, not security. A related caveat: `RoleBinding.scope_type` / `scope_id` are stored but not enforced, so in a multi-domain tenant a role grant behaves as tenant-wide — see [KNOWN_GAPS.md](./KNOWN_GAPS.md).

`AppHeader` carries the theme toggle (`frontend/src/components/shell/app-header.tsx:107`), the account menu with logout, and `NotificationBell`, which polls `GET /notifications?unread_only=true&limit=20` every 60 seconds and marks items read one at a time through `PATCH /notifications/{id}/read` (`app-header.tsx:20-40`).

### 2. Overview — the fast, deliberately heuristic snapshot

`OverviewPage` fires four list calls in parallel — `/sources`, `/evidence`, `/episodes`, `/playbooks`, each capped at 200 rows (`frontend/src/app/(dashboard)/overview/page.tsx:105-117`). Every tile is computed in the browser from those capped lists: connected sources, pending-review episodes, approved playbooks, candidates in review (`overview/page.tsx:122-131`). The "(up to 200 each)" hint is rendered onto the counted tiles on purpose (`overview/page.tsx:120, 155-171`) — these are not tenant totals.

The drift panel is a pure client-side heuristic, `playbookNeedsAttention` (`overview/page.tsx:55-75`): past expiry, expiring within 60 days, validated more than 90 days ago, never validated, or still awaiting governance. Real drift detection is the `evaluation.detect_drift` beat task, every 21,600 seconds across all tenants (`backend/src/contextedge/workers/celery_app.py:282-286`), surfaced on the Drift page.

For true tenant-wide counts, Pipeline Health (step 8) is the honest page — it counts in SQL.

### 3. Source onboarding and the source-type catalog

`SourcesPage` lists configured sources. `AddSourceDialog` renders its connector picker from `GET /sources/types` (`frontend/src/components/sources/add-source-dialog.tsx:165-167`), which the API builds from `source_type_catalog()` (`backend/src/contextedge/api/v1/sources.py:57-76`; `backend/src/contextedge/connectors/registry.py:69-88`).

That catalog exists to stop one specific bug. `connector_available` is computed from the connector registry rather than hand-written, so the picker cannot offer a source type that has no connector — which it used to, letting an operator create a Confluence source that succeeded and then died at sync with "Unknown source type" (`registry.py:24-36`). Each entry reports one of three statuses: `available` (a connector is registered), `planned` (the API accepts the type, nothing can sync it), `manual` (`local_file` — an upload path by design) (`registry.py:16-20`).

Seven connectors are registered today: Teams, Gmail, ServiceNow, Jira Service Management, ManageEngine ServiceDesk Plus, SapphireIMS, and Zoho Desk (`registry.py:100-110`). The dialog now carries real credential and filter forms for the cloud connectors — ServiceNow instance URL / credentials / table filters / alert severity cap, Zoho client id / secret / refresh token / org id / data centre / modules, ManageEngine base URL and API key, Gmail auth method (`add-source-dialog.tsx:36-67`) — alongside the local-folder path that posts to `/sources/local-ingest` (`add-source-dialog.tsx:476`).

`SourceDetailPage` exposes sync state, policy attachment, credential rotation, recent runs, and **cooperative run control**: Pause, Cancel, and Resume buttons post to `POST /sources/{id}/sync/control` (`frontend/src/app/(dashboard)/sources/[id]/page.tsx:136-137, 226-248`). On the server that route requires `domain_admin`, sets `SourceObject.metadata_extra["sync_paused"]`, and — if a run is live — writes `sync_runs.control` so the running job stops cooperatively (`backend/src/contextedge/api/v1/sources.py:295-352`; `backend/src/contextedge/services/sync_control_service.py:64-95`). The connector polls that flag per page and every 25 detail records; the read happens on a **fresh connection**, because the job's own transaction started before the operator's write and cannot see it (`backend/src/contextedge/services/sync_worker_service.py:398-416`). Both stops persist what was already fetched plus the checkpoint, so Resume simply clears the gate and the next run continues from where it stopped (`sync_control_service.py:15-24`). The action is audited as `sync.<action>` (`api/v1/sources.py:354-363`).

**Discovery lives on its own route, not under `/sources`.** `DiscoveryPage` is `frontend/src/app/(dashboard)/inventory/[id]/page.tsx:39` — reached from the Sources list, absent from the sidebar. It posts `/sources/{id}/discover` to enumerate channels, mailboxes, tables, or modules (`inventory/[id]/page.tsx:61`, gated on `canDiscoverSources` = `domain_admin`, `:45`), lists the results from `/sources/{id}/objects` (`:56`), and toggles two independent approvals per object, `approved_for_sync` and `approved_for_backfill` (`:132-176`). The two roles differ: running discovery needs `domain_admin`, but the approval buttons are gated on `isTenantAdmin` (`:44`) — a domain admin can enumerate a source's objects and then only read the approval state as "Yes"/"No" text (`:136-137, 161-162`).

Those two flags are the gate on everything downstream. `sync.trigger_scheduled_syncs` runs on Beat every 15 minutes and fans out one `sync.run_incremental_sync` per object where `approved_for_sync IS TRUE` (`backend/src/contextedge/workers/sync_tasks.py:16-32`); backfill additionally requires `approved_for_backfill` (`backend/src/contextedge/services/sync_worker_service.py:445-446`). An object nobody approved is discovered and then silently ignored forever — which is exactly the state the whole Zoho knowledge path sits in today (see [KNOWN_GAPS.md](./KNOWN_GAPS.md)).

### 4. Evidence views keep provenance visible

`EvidenceDetailPage` fans out five reads for one record: the item (`/evidence/{id}`), its thread summary (`/threads/{thread_id}`), the other messages in that thread (`/threads/{thread_id}/evidence`), attachment extraction results (`/evidence/{id}/attachments`), and a context bundle (`/evidence/{id}/context`) (`frontend/src/app/(dashboard)/evidence/[id]/page.tsx:68-127`). When a thread is only partially present, the page posts `/threads/{thread_id}/hydrate`, which dispatches the `hydration.hydrate_thread` Celery task on the `hydration` queue (`evidence/[id]/page.tsx:98-106`; `backend/src/contextedge/workers/hydration_tasks.py:189`).

Two storage details shape what this page can show. First, a raw payload larger than **32 KB** is written to object storage and the database row keeps only a stub, `{"_offloaded": True, "size_bytes": ...}` (`backend/src/contextedge/services/ingestion_persistence.py:16, 85-87`) — so any SQL filter over `raw_payload` silently skips the biggest records. Second, retrieval no longer reads only the parent row: normalization chunks the body into `evidence_chunks`, and search oversamples chunks, applies MMR at chunk level, then rolls up to one hit per parent. The chunk layer has **no dedicated screen** — the only place an operator sees it is the chunk-coverage tile on Pipeline Health (`frontend/src/app/(dashboard)/admin/pipeline/page.tsx:520-527`).

### 5. Knowledge curation, page by page

Episodes capture incident narratives, Patterns record recurrence, Playbooks store governed procedures, Negative Knowledge captures ineffective or prohibited steps, and Identities / Correlations / Contradictions refine the memory graph. They are separate pages because they are separate business decisions, even when they share the same evidence.

The **Episodes** page is where the most pipeline logic is reachable by a button:

- The list defaults to `sort=review_priority`, not newest-first, and can toggle to newest (`frontend/src/app/(dashboard)/episodes/page.tsx:198-208, 304-313`). Review priority is a SQL expression shared with the AI reviewer so machine and human attention agree on what matters: +40 for a substantive `final_outcome`, +20 for a substantive `root_cause_summary`, +3 per evidence item up to 10, +10 × `extraction_confidence` (`backend/src/contextedge/services/episode_review_service.py:57-86`). Newest-first buried the resolution-bearing multi-evidence drafts under the last trickle of fragments after every bulk ingest.
- **Approve Selected** posts `/episodes/bulk-approve` (`episodes/page.tsx:210-223`). Server-side that sets `status` and `reviewer_state` to `approved` with `reviewer_user_id` set to the human, audits each row, **commits**, and only then dispatches `evaluation.extract_issue_signature` per episode and one `pattern.cluster_episodes` per domain that had approvals (`backend/src/contextedge/api/v1/episodes.py:282-339`). Commit-before-dispatch is deliberate — a task consumed before the commit would read a still-pending episode and no-op without retry.
- **AI review** posts `/episodes/ai-review` (`episodes/page.tsx:242-261`), which requires `knowledge_manager` and dispatches the `evaluation.ai_review_episodes` sweep (`backend/src/contextedge/api/v1/episodes.py:556-607`). The endpoint can only run a **weaker** mode than the deployment setting, never a stronger one: with `EPISODE_AI_REVIEW=off` the dispatch is always advisory (`episodes.py:583-591`). The toast text says which mode ran.
- **Construct pattern** posts `/patterns/cluster`, dispatching `pattern.cluster_episodes` (`episodes/page.tsx:263-276`).

The AI reviewer's output is surfaced, never hidden. The list has an **AI verdict** column rendering the `ai_review` JSON as a pill — `auto-approved`, or `<verdict> <confidence>%` — with the model's reasons and any failed floors in the tooltip (`episodes/page.tsx:143-172`). The detail page repeats it as a banner with the reasons as a bullet list and "Floors not met" spelled out (`frontend/src/app/(dashboard)/episodes/[id]/page.tsx:238-267`).

Behind that pill: `settings.episode_ai_review` is exactly one of `off`, `advisory`, `auto_approve` (`backend/src/contextedge/config.py:185-187`). Advisory stamps a verdict and approves nothing. Auto-approve approves only drafts that clear **both** the model verdict and four deterministic floors — at least 2 evidence items, a `final_outcome` of at least 20 characters, verdict exactly `approve`, and confidence ≥ 0.8 (`backend/src/contextedge/services/episode_review_service.py:42-44, 89-101`). An auto-approval leaves `reviewer_user_id` NULL forever, so it is permanently distinguishable from a human decision (`episode_review_service.py:278-300`). The detail page also renders a "This reconstruction was superseded" banner for drafts replaced by a later, larger reconstruction (`episodes/[id]/page.tsx:216-233`).

### 5b. Graph Explorer — traversing the context graph, and one review queue

Five tabs, declared as one list so the URL can address any of them (`frontend/src/app/(dashboard)/graph-explorer/page.tsx:17, 113-127`):

| Tab | Reads | What it shows |
| --- | --- | --- |
| Statistics | `GET /graph/stats` | Total edges, node-type counts, edge-type distribution (`graph-stats.tsx:107-135`) |
| Subgraph | `GET /graph/subgraph/{type}/{id}` | Any entity type/ID at a chosen depth, rendered with React Flow and dagre auto-layout (`graph-layout.ts:2, 23-34`); clicking a node re-centres the query on it (`graph-subgraph.tsx:389`) |
| Neighbors | `GET /graph/neighbors` | Breadth-first traversal grouped by depth, with edge-type filtering and follow-node navigation (`graph-neighbors.tsx:87-107, 199-235`) |
| Agent Context | `POST /graph/agent-subsets` | A preview of the maf.v1 projection an agent would receive for a seed (`agent-context-preview.tsx:177-193`) |
| Proposals | `GET /graph/edge-proposals` | Agent-discovered dependency edges awaiting a human verdict |

The first four share one scope control — an optional `domain_id` and an optional `as_of` timestamp for historical traversal — both threaded through `graphApi` as query parameters and mirrored into the URL (`frontend/src/lib/graph-api.ts:11-16`; `graph-explorer/page.tsx:55-62, 89-108`). Shared styling lives in `graph-constants.ts` and is reused by the pattern-scoped graph view (`frontend/src/components/patterns/pattern-graph.tsx`).

**Four tabs read; the fifth writes.** Proposals posts `/graph/edge-proposals/{edge_id}/approve` or `/reject` (`frontend/src/components/graph/edge-proposals.tsx:31, 37`), and both routes require `knowledge_manager` on the server (`backend/src/contextedge/api/v1/graph.py:120-129, 142-151, 167-174`). Approving promotes the proposal into an authored `depends_on` edge carrying review provenance, and the proposal edge is **closed rather than deleted** — superseded, so the fact that an agent once proposed it stays in the record (`graph.py:149-150`; `backend/src/contextedge/services/edge_proposal_service.py:168`). Rejecting closes it the same way, stamped `rejected` with the reviewer and their note (`edge_proposal_service.py:183`). That is the one place the operator writes to the graph from this page — every other graph mutation happens in backend services: pattern discovery, playbook generation, contradiction scans, identity linking, decision extraction, and episode graph construction.

The vocabularies cover the decision trail — `session`, `execution_run`, `approval_request`, `user` nodes (`frontend/src/components/graph/graph-constants.ts:11-14, 62-65`) and edges such as `records_decision` (`graph-constants.ts:93`) — plus the AE-alignment node types `action_policy`, `claim`, `error_signature`, `fix_pattern`, and `case_outcome` (`graph-constants.ts:19-23`), so an operator can trace who decided what and which failure fingerprint recurred. Two honest caveats: `issue_signature`, the LLM-derived fingerprint minted on episode approval, is in neither `NODE_TYPE_OPTIONS` nor `nodeColors`, so it falls back to the neutral slate style in `getNodeClassName` (`graph-constants.ts:125-129`); and `fix_pattern` is offered as a filter for a table nothing writes yet (see [17-ae-ops-context-graph-alignment.md](./17-ae-ops-context-graph-alignment.md)).

### 6. Review Queues (`/suggestions`) — three human-decision queues in one console

Role-gated to `knowledge_manager`, `domain_admin`, or `tenant_admin` (`sidebar-nav.tsx:60`). It renders three independent pending queues, each with accept/reject actions whose consequences are stated in the button tooltips (`frontend/src/app/(dashboard)/suggestions/page.tsx`):

| Queue | Reads | Accept does | Reject does |
| --- | --- | --- | --- |
| Correlation suggestions | `/correlations/suggestions?status=pending` (`:38`) | creates a correlation edge (`:94`) | permanent for that pair (`:103`) |
| Fleet groups | `/correlations/fleet-suggestions?status=pending` (`:128`) | groups the incidents under one parent case (`:164`) | permanent for that change (`:173`) |
| Identities needing review | `/identities?resolution_state=needs_review` (`:207`) | marks resolved — the identity becomes trusted for correlation (`:252`) | deactivates a bad extraction (`:261`) |

The identity queue matters more than it looks on a new tenant: correlation's identity tier only trusts `resolved` or `verified` identities, and every first sighting is created `provisional`. Until someone promotes identities here, correlations do not form — and Pipeline Health names exactly that condition in an alert (`backend/src/contextedge/services/pipeline_health_service.py:239-260`).

### 7. Runtime, sessions, review console, and execution

`RuntimePage` is a two-tab sandbox over the production runtime APIs — Sandbox and Feedback (`frontend/src/app/(dashboard)/runtime/page.tsx:241-249`). Sandbox posts symptoms and entities to `/runtime/match`, then fetches `/runtime/explain/{match_id}` and the matched playbook version (`runtime/page.tsx:197-226`); Feedback lists `/runtime/feedback` (`runtime/page.tsx:61`).

`SessionsPage` manages resolution sessions. Creating a session enqueues `review_queue.prefetch_review_context`, which pre-warms the reviewer bundle into Redis (`backend/src/contextedge/workers/review_queue_tasks.py:33`) — the click-to-render path then hits Redis, not Postgres.

The **Review Queue** console (`/review`) is the reviewer's working surface. The queue pane consumes `/decisions?status=pending&sort=confidence_desc` with a 50-row limit and colour-coded confidence badges whose thresholds match the server's (`frontend/src/app/(dashboard)/review/page.tsx:61, 82-99`). Selecting a session loads the whole bundle from `GET /review-queue/{session_id}/context` in one round trip (`review/page.tsx:782-783`) — read-through cached in Redis with a 300-second TTL, which is what makes the click-to-render budget hold. Three verbs are live — Approve, Modify, Reject (`review/page.tsx:420, 516, 677`). Modify pre-fills the pending approval's step inputs as editable JSON and posts to `/execution/runs/{run_id}/approvals/{approval_id}/modify`; Reject writes a structured reason code. Zone 4 (evidence cards) and zone 6 (plan steps) are still deferred — see [KNOWN_GAPS.md](./KNOWN_GAPS.md).

`ExecutionPage` handles the pending approval queue for higher-risk steps. What sits behind those buttons has hardened considerably: approvals are bound to a content hash of the exact step version they approved and re-checked before invocation, side-effecting steps carry a derived idempotency key that refuses a duplicate replay, and action policies are evaluated per step at `start_execution`. See [17-ae-ops-context-graph-alignment.md](./17-ae-ops-context-graph-alignment.md) and [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md).

### 8. Pipeline Health (`/admin/pipeline`) — the "is it getting anywhere" page

Gated to `tenant_admin` on both sides: the nav item (`sidebar-nav.tsx:68`) and `user.require_role("tenant_admin")` on `GET /admin/pipeline-health` (`backend/src/contextedge/api/v1/admin_cost.py:166-175`). The page refetches every 5 seconds (`admin/pipeline/page.tsx:139-143`).

The service does one read of everything (`backend/src/contextedge/services/pipeline_health_service.py:87`):

1. **Queue depth per lane.** Redis `LLEN` over eight lanes in pipeline order — `extraction, correlation, embedding, hydration, pattern, evaluation, sync, default` (`pipeline_health_service.py:43-52`) — plus `HLEN unacked` for in-flight work. In-flight is not a footnote: during the reconstruction phase of a bulk ingest that is where *all* remaining work lives, and a page reading only queue depth called an ingest idle while it burned a dollar a minute (`pipeline_health_service.py:58-84`). Broker failure returns empty depths and never raises (`:82-84`).
2. **The graph chain, counted end to end** in one SQL statement: evidence → correlation edges → episodes → patterns → playbooks (`pipeline_health_service.py:214-221`). The first zero is the diagnosis; `stalled_at` names it and an alert says so in words (`:221-231`). The chain deliberately counts `correlation_edges`, not `case_links` — case links are an *input* to episode reconstruction, and showing them read as "1.3k correlations" while the quantity actually gating episodes was zero (`:112-119`).
3. **The embed gap that matters.** `embed_gap` counts only evidence in `operational` or `possibly_relevant` state with a NULL embedding. Counting `not_relevant` rows showed a permanent backlog that was actually the relevance gate working as designed (`pipeline_health_service.py:99-107`).
4. **Latency and burn.** p50/p95/max over `llm.usage` operational events in the last 10 minutes, a per-prompt breakdown over the last hour, and spend priced per model through `_lookup_rate` (`pipeline_health_service.py:141-205`).
5. **Alerts**, six of them (`pipeline_health_service.py:223-308`): a stalled chain; a cold-start tenant whose identities are all `provisional`; more than 500 tasks on the extraction lane (`BACKLOG_ALERT_DEPTH`, `:55`); and — critical — zero evidence produced in 10 minutes while work is still queued. Two more read the in-flight number rather than the queues: when workers hold more than 50 tasks in flight and the queues are near empty, that is the reconstruction phase, and the alert says so by naming the episodes produced in the last 10 minutes — or, when nothing at all was produced, escalates to critical because the holding workers may be dead (`:279-299`). The last is informational: relevant evidence still missing an embedding is invisible to vector search (`:300-308`).

The page adds one thing the server deliberately does not: a **drain-rate estimate measured in the browser**. `useDrainRate` keeps a two-minute sliding window of `queued + in-flight` samples from consecutive polls, refuses to show an ETA until it has 60 seconds of samples, and reports a non-positive rate as "backlog holding or growing" rather than inventing a completion time (`admin/pipeline/page.tsx:104-130, 236-277`). Multiplying that rate by the hourly burn gives a projected cost to finish.

Card order on the page: alerts, Run projection (`:213`), Graph chain (`:285`), Queue depth (`:331`), Throughput and latency (`:400`), Latency by call (`:456`), Corpus including chunk coverage (`:501`).

**One operational caveat this page cannot show you:** the eight lanes it reads are the eight lanes `task_routes` uses (`backend/src/contextedge/workers/celery_app.py:226-280`), and `backend/dev.py:16` is the authority on which queues a worker consumes. A worker fleet started from an older command that omits `correlation` and `embedding` will show those lanes growing forever with nothing consuming them — which is the exact silent starvation those lanes were split out to fix.

### 9. LLM Cost (`/admin/cost`) — the "what is this spending" page

Also `tenant_admin` on both sides (`sidebar-nav.tsx:67`; `admin_cost.py:33-60`). Five headline KPI cards over a selectable window — estimated USD, total tokens, cache-hit rate, thinking tokens, and average cost per request (`frontend/src/app/(dashboard)/admin/cost/page.tsx:102, 687-763`) — plus a top-N model × task breakdown with a CSS-only stacked bar per row (`:135, 861-866`). The bar has four segments, not three: prompt-non-cached, cached, answer, and thinking — reasoning tokens are already inside `completion`, so the answer segment is what is left after carving them out, and adding them would overstate the bar (`:150-152`). Cache-hit rate is tone-coded green ≥ 50%, amber ≥ 20%, red otherwise (`admin/cost/page.tsx:77`), so a rollout that should have lit up prompt caching and didn't is obvious at a glance.

The window has a third mode worth knowing: alongside a rolling window and "all time", the selector can scope every number to **one sync run's own start and end**, so the answer is "what did that run cost" rather than "what happened in a window that happens to contain it" — and a run still going has no end yet, which is what makes the meter live (`admin/cost/page.tsx:577-593, 642`; `backend/src/contextedge/api/v1/admin_cost.py:50-77`).

`BudgetPanel` reads `GET /admin/tenant-budget/status` and draws today's usage against the configured caps as two progress bars, with a badge for `action_on_exceed` (`admin/cost/page.tsx:188-192`). `BudgetEditForm` writes back through `PUT /admin/tenant-budget` (`admin/cost/page.tsx:399`; `backend/src/contextedge/api/v1/admin_cost.py:113-118`).

The numbers behind the panel: usage is summed from the current UTC day's `llm.usage` operational events rather than a separate counter column, so there is no second aggregation to drift (`backend/src/contextedge/services/tenant_budget_service.py:191-231`). A tenant with **no budget row at all** is still governed — deployment defaults of 2,000,000 tokens/day, $25/day, action `block` are evaluated through the identical path and deliberately not persisted (`backend/src/contextedge/config.py:191-198`; `tenant_budget_service.py:107-121, 249-279`). The token limit is checked before the cost cap, so a tenant with only a token cap never sees `cost_cap_exceeded` (`tenant_budget_service.py:301-311`). The usual rollout is to land a new cap as `warn`, watch `llm.budget_warning` events for a day, then flip to `block`.

### 10. Governance pages and the thin-client contract

Drift and Evaluations show whether stored memory still performs. Policies and Audit Log show who changed rules or resources. Settings holds tenant, workspace, domain, user, and retention context across five tabs — General, Workspaces, Domains, Users, Retention (`frontend/src/app/(dashboard)/settings/page.tsx:280-284`).

The shared `api` client attaches the bearer token and request ids, and every page calls the backend directly (`frontend/src/lib/api.ts`). That keeps the UI easy to reason about, and it also means backend capability routinely exists before a polished screen does. Settings is still not a complete admin console — role-binding CRUD, workspace/domain edit and deactivate, and the retention console remain largely API-led, and generic policy assignment has a backend surface without a first-class workflow (see [KNOWN_GAPS.md](./KNOWN_GAPS.md)).

## Example: Acme VPN data at this stage

One incident, followed through the screens the operators actually opened.

**Morning — the tenant admin opens Pipeline Health during the overnight ingest.**

**Input** (what the page requests): `GET /api/v1/admin/pipeline-health`

**Output** (what comes back, trimmed):

```json
{
  "counts": {
    "evidence": 8412, "evidence_10min": 96, "embedded": 8130,
    "embed_gap": 41, "identities": 1204, "case_links": 1310,
    "episodes": 0, "episodes_pending": 0,
    "chunks_total": 19877, "chunks_embedded": 19502,
    "patterns": 0, "playbooks": 0
  },
  "queues": { "extraction": 8255, "correlation": 0, "embedding": 309,
              "hydration": 12, "pattern": 0, "evaluation": 0,
              "sync": 0, "default": 4 },
  "in_flight": 5788,
  "graph_chain": [
    {"stage": "evidence", "count": 8412},
    {"stage": "correlations", "count": 0},
    {"stage": "episodes", "count": 0}, "..."
  ],
  "stalled_at": "correlations",
  "alerts": [
    {"level": "warning", "message": "The graph chain stops at 'correlations': every stage after it is waiting on work that has not been produced."},
    {"level": "warning", "message": "8,255 tasks queued on the extraction lane. Anything sharing that lane waits behind all of them."}
  ]
}
```

The admin reads that in one glance: evidence is landing, embeddings are keeping up, and the chain has stopped dead at correlations because a 0.25-second correlate task is queued behind 8,255 thirty-second normalizations. They also see the email source needs credential rotation on the Sources page, and pause the noisy ServiceNow backfill from Source Detail while the extraction lane drains.

**Mid-morning — the responder investigates.** They search "VPN authentication failure" in the Evidence explorer:

| Evidence | Source | Relevance | Time |
| --- | --- | --- | --- |
| INC0010427 — VPN users unable to connect to `vpn-gw-east-01` | ServiceNow | Operational | Mar 15, 9:23 AM |
| Engineers discuss AUTH_CERT_EXPIRED on the gateway | Teams | Operational | Mar 15, 9:45 AM |

The ticket's thread is only partially present, so they click hydrate; the page posts `/threads/{id}/hydrate` and a `hydration.hydrate_thread` task pulls the rest. They open a Resolution Session, which records the runtime match (VPN Certificate Rotation playbook, 92% confidence) and their decision to proceed.

**Afternoon — the knowledge manager curates on the Episodes page.** Sorted by review priority, the draft reconstructed from the whole Acme cluster sits near the top. The AI verdict column already carries an advisory pill:

**Input** (what the sweep stamped on `episodes.ai_review`):

```json
{
  "verdict": "approve",
  "confidence": 0.86,
  "reasons": ["steps trace to cited evidence", "outcome states the certificate was renewed"],
  "prompt_version": "v1",
  "mode": "advisory",
  "auto_approved": false,
  "failed_floors": [],
  "reviewed_at": "2026-08-19T13:04:11Z"
}
```

The manager agrees, selects the draft, and clicks **Approve Selected**. The API commits the approval with their user id, then dispatches `evaluation.extract_issue_signature` for the episode and one `pattern.cluster_episodes` for its domain. Minutes later the issue signature `remote_access|tls_certificate|certificate_expired` exists, and the episode joins the "Certificate expiry after Windows updates" pattern. A playbook candidate enters the review queue.

**Next day — the reviewer approves the playbook** with its trigger conditions, branching logic, evidence links, risk tier `medium`, and automation mode `human-confirmed`, publishing version 1.0.0 into runtime retrieval.

**Weekly — the governance check.** The Drift page flags the legacy VPN playbook as past expiry and unvalidated for 180 days; the certificate rotation playbook carries three negative feedback events. The manager retires one and narrows the other's triggers.

**Afternoon — the tenant admin checks LLM Cost.** The budget panel shows the day's usage against the tenant's cap with the `warn` badge. The episode-synthesis lane dominates the model × task breakdown, which is why the ingest-side gates exist at all.

## Design decisions

- **Thin client over an API-first backend.** Why: the same rules must apply to the dashboard, service integrations, and future clients, so authorization, ranking, budget, and retention all live on the server. Tradeoff: capability regularly ships in the API before there is a polished screen for it, and the nav is not a reliable map of what the product can do.

- **Two operator consoles, not one.** Why: "what is this spending" and "is this getting anywhere" have different failure modes, and the founding incident was a run that spent steadily, failed nothing, and built nothing (`pipeline_health_service.py:1-27`). Tradeoff: an operator has to know which page answers their question, and the two pages report overlapping cost numbers computed by different queries.

- **Queue depth is read live from the broker, not from a metrics store.** Why: `LLEN` and `HLEN` are O(1), so the page can poll every five seconds and a stall shows up in seconds rather than at the next scrape. Tradeoff: no history — the page shows now, never last night — and it depends on Celery's Redis key layout, which is why a broker failure degrades to empty depths instead of an error (`pipeline_health_service.py:70-84`).

- **The drain-rate ETA is measured in the browser.** Why: the server stays stateless, and the rate that matters is the pipeline as *currently staffed*, which only consecutive samples can show. Tradeoff: the estimate resets on every reload, differs between two tabs, and needs a warm-up minute before it will show anything (`admin/pipeline/page.tsx:104-130`).

- **AI review is surfaced as an annotation, never as a person.** Why: a reviewer must be able to tell a machine verdict from a colleague's, so the pill says `auto-approved` explicitly, the reasons and failed floors are always visible, and an auto-approved episode keeps `reviewer_user_id` NULL. Tradeoff: the queue now carries a second signal reviewers must learn to weigh, and a wrong-but-confident verdict can anchor a human decision.

- **The AI-review button can only weaken the configured mode.** Why: a UI control that could escalate a deployment into auto-approval would move a safety decision from configuration into a click. Tradeoff: enabling auto-approval genuinely requires a deployment change, so there is no way to try it from the dashboard (`api/v1/episodes.py:583-591`).

- **The source picker is generated from the connector registry.** Why: the picker and the registry had drifted in both directions — offering connectors that did not exist while hiding ones that did — and a created-then-broken source is a worse failure than an absent option. Tradeoff: `planned` types are still listed rather than hidden, so the picker shows things you cannot actually sync yet (`registry.py:24-36`).

- **Separate screens match separate business decisions.** Why: source approval, evidence review, pattern curation, runtime retrieval, and execution approval are owned by different roles. Tradeoff: users must learn a 25-item navigation model instead of one dense console.

- **Governance is visible, not hidden.** Why: policies, drift, evaluations, audit, cost, and approvals are core operational behaviours, not afterthoughts. Tradeoff: the dashboard surface area is much larger than a search console, and several governance surfaces are still API-led.

- **Notifications stay lightweight.** Why: a 60-second poll in the header is enough to surface review and drift signals early without building a delivery system. Tradeoff: no inbox page, no push transport, no workflow routing, and "mark all read" is a loop of single PATCH calls (`app-header.tsx:36-40`).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Auth gate for the dashboard group | `frontend/src/app/(dashboard)/layout.tsx:17-21` | `DashboardLayout` | Every dashboard view |
| Left navigation and role filtering | `frontend/src/components/shell/sidebar-nav.tsx:44-78` | `navItems`, `SidebarNav` | Every dashboard view |
| Frontend role predicates | `frontend/src/lib/roles.ts:7-9, 22-56` | `hasRole`, `canApproveEpisode`, `canEditAutomationMode` | Nav + per-page affordances |
| Backend role check (the real gate) | `backend/src/contextedge/deps.py:37-51` | `CurrentUser.has_role`, `require_role` | Every API request |
| Header, theme, notifications | `frontend/src/components/shell/app-header.tsx:20-40, 107` | `AppHeader`, `NotificationBell` | Polls `/notifications` every 60 s |
| Shared API client | `frontend/src/lib/api.ts` | `ApiClient`, `api` | Every frontend API call |
| Overview snapshot | `frontend/src/app/(dashboard)/overview/page.tsx:55-75, 105-131` | `OverviewPage`, `playbookNeedsAttention`, `StatTile` | Dashboard home |
| Source list and actions | `frontend/src/app/(dashboard)/sources/page.tsx:23, 112` | `SourcesPage`, `SourceActions` | Source management |
| Add-source flow | `frontend/src/components/sources/add-source-dialog.tsx:109, 165-167, 178, 476` | `AddSourceDialog`, `handleBrowseFolder` | Source creation |
| Source-type catalog (picker truth) | `backend/src/contextedge/connectors/registry.py:9-21, 69-88, 100-110` | `SourceTypeInfo`, `source_type_catalog`, `_register_connectors` | `GET /sources/types` |
| Source detail + sync control | `frontend/src/app/(dashboard)/sources/[id]/page.tsx:136-137, 226-248` | `SourceDetailPage`, `controlMut` | Pause / cancel / resume a run |
| Sync control service | `backend/src/contextedge/services/sync_control_service.py:15-24, 64-122` | `signal_run`, `control_check_for` | While a sync run is executing |
| Discovery inventory | `frontend/src/app/(dashboard)/inventory/[id]/page.tsx:39-61, 132-176` | `DiscoveryPage` | Source object discovery and approval (not in the sidebar) |
| Evidence detail | `frontend/src/app/(dashboard)/evidence/[id]/page.tsx:68-127` | `EvidenceDetailPage`, `hydrateMut` | Investigation |
| Episodes queue and bulk actions | `frontend/src/app/(dashboard)/episodes/page.tsx:143-172, 198-276` | `EpisodesPage`, `handleAiReview`, `handleConstructPattern`, `bulkApproveMutation` | Episode curation |
| Episode detail (AI verdict banner) | `frontend/src/app/(dashboard)/episodes/[id]/page.tsx:216-267` | `EpisodeDetailPage` | Reviewing one draft |
| Review-priority ordering + floors | `backend/src/contextedge/services/episode_review_service.py:42-44, 57-101` | `review_priority_expression`, `passes_auto_approve_floors` | Sweep and `GET /episodes?sort=review_priority` |
| AI review dispatch endpoint | `backend/src/contextedge/api/v1/episodes.py:556-607` | `dispatch_ai_review` | `knowledge_manager` clicks AI review |
| Review Queues console | `frontend/src/app/(dashboard)/suggestions/page.tsx:38-43, 128-133, 207` | correlation / fleet / identity panes | Human decision queues |
| Runtime sandbox | `frontend/src/app/(dashboard)/runtime/page.tsx:56-61, 110, 197-249` | `FeedbackTab`, `RuntimePage` | Live retrieval and feedback |
| Sessions | `frontend/src/app/(dashboard)/sessions/page.tsx:108, 330, 619` | `NewSessionDialog`, `SessionDetail`, `SessionsPage` | Incident coordination |
| Reviewer console | `frontend/src/app/(dashboard)/review/page.tsx:61, 101-114, 312, 399-780, 852` | `QueuePane`, `RankedHypotheses`, `findPendingApproval`, `RejectDialog`, `ModifyDialog`, `DecisionBar`, `ReviewPage` | Confidence-ranked pending decisions |
| Execution approvals | `frontend/src/app/(dashboard)/execution/page.tsx:23, 118` | `ExecutionPage`, `DecideDialog` | Human approval queue |
| Pipeline Health page | `frontend/src/app/(dashboard)/admin/pipeline/page.tsx:104-130, 138-149, 213-527` | `PipelineHealthPage`, `useDrainRate` | `tenant_admin`, polls every 5 s |
| Pipeline Health service | `backend/src/contextedge/services/pipeline_health_service.py:43-55, 58-84, 87-322` | `QUEUES`, `BACKLOG_ALERT_DEPTH`, `_queue_depths`, `get_pipeline_health` | `GET /admin/pipeline-health` |
| Admin cost + budget page | `frontend/src/app/(dashboard)/admin/cost/page.tsx:77, 102, 135, 188, 399, 565` | `cacheRateTone`, `KpiCard`, `BreakdownBar`, `BudgetPanel`, `BudgetEditForm`, `AdminCostPage` | `tenant_admin` |
| Budget evaluation | `backend/src/contextedge/services/tenant_budget_service.py:107-121, 191-282, 285-311` | `_DefaultBudget`, `get_current_day_usage`, `check_budget`, `_check_budget_locked` | Before every LLM call |
| Admin cost routes | `backend/src/contextedge/api/v1/admin_cost.py:33, 102, 113, 137, 166` | `admin_llm_usage`, `get_tenant_budget`, `put_tenant_budget`, `get_tenant_budget_status`, `admin_pipeline_health` | Admin pages |
| Policies | `frontend/src/app/(dashboard)/policies/page.tsx` | `PoliciesPage`, `PolicySection` | Governance admin |
| Settings (5 tabs) | `frontend/src/app/(dashboard)/settings/page.tsx:280-284` | `SettingsPage`, `NewWorkspaceDialog`, `NewDomainDialog` | Tenant admin |
| Graph Explorer page | `frontend/src/app/(dashboard)/graph-explorer/page.tsx:17, 30, 155` | `TAB_NAMES`, `GraphExplorerContent`, `GraphExplorerPage` | Graph investigation |
| Graph Explorer tabs | `frontend/src/components/graph/graph-stats.tsx:77`, `graph-subgraph.tsx:287`, `graph-neighbors.tsx:31`, `agent-context-preview.tsx:177`, `edge-proposals.tsx:25` | `GraphStats`, `GraphSubgraph`, `GraphNeighbors`, `AgentContextPreview`, `EdgeProposals` | The five Graph Explorer tabs |
| Graph scope + client | `frontend/src/components/graph/graph-query-controls.tsx:18`; `frontend/src/lib/graph-api.ts:11-50` | `GraphQueryControls`, `graphApi` | `domain_id` / `as_of` on every graph read |
| Edge-proposal review routes | `backend/src/contextedge/api/v1/graph.py:120-129, 142-151, 167-174` | list / approve / reject, all `knowledge_manager` | The only write on the Graph Explorer page |
| Shared graph styling | `frontend/src/components/graph/graph-constants.ts:1-25, 49-76, 125-129` | `NODE_TYPE_OPTIONS`, `nodeColors`, `edgeColors`, `getNodeClassName` | All graph views |

## Acme VPN incident (this layer)

In the Acme VPN outage the dashboard is the business user's path through the product. A tenant admin opens Pipeline Health and sees the graph chain stalled at correlations while evidence keeps landing, pauses the competing backfill from Source Detail, and rotates the expiring email credential. A responder opens Evidence, hydrates the `INC0010427` thread, and starts a Resolution Session. A knowledge manager works the Episodes queue in review-priority order, reads the AI reviewer's advisory verdict on the reconstructed draft, approves it, and watches the issue signature and pattern appear. A reviewer publishes the resulting playbook, and an approver uses Execution to allow the higher-risk recovery step. Graph Explorer then shows the whole trail in one picture — the session that triggered execution, the approval chain, the action, and the `remote_access|tls_certificate|certificate_expired` signature that will make the next occurrence recognisable. Same incident, one governed workflow, rather than a disconnected set of backend APIs.

## Further reading

- [00-business-capability-map.md](./00-business-capability-map.md) — business-first orientation
- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — source, discovery, and sync internals
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — evidence retrieval and chunk rollup behind the UI
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — what the Episodes and Patterns pages act on
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — the eight lanes Pipeline Health reads
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — context graph internals and the `/graph` API
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — sessions, execution, and audit internals
- [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) — settings, users, roles, and policies
- [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) — what the LLM Cost page is measuring
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — current UI gaps, stated rather than glossed
- [docs/RUNBOOK.md](../docs/RUNBOOK.md) — worker topology and operational procedures
