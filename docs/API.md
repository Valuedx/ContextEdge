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
| `/graph` | graph | Graph traversal, subgraph visualization, aggregate statistics |
| `/sessions` | sessions | Resolution sessions and decision trace events. `PATCH /sessions/{id}/close` accepts an optional body asserting the outcome (`outcome_status` from the CaseOutcome vocabulary, `resolution_summary`, `confirmed_root_cause`, `successful_action`, `failed_actions`, `user_confirmed`, `fix_results`); with it the close records a `CaseOutcome` (MTTR from the session timeline, fix-pattern links). Without it only the state transition is recorded — an unstated outcome is unknown, not "resolved". Every open/close appends a `CaseStateTransition`; `GET /sessions/{id}/history` returns the transition timeline and recorded outcomes. |
| `/inventory` | inventory | Agent-side state reports (`POST /inventory/report`, `knowledge_manager`). Observations may carry `external_system` + `external_id` (both or neither — a bare `external_id` is rejected, since it can collide across source systems) for exact CI resolution; ambiguous names return `ambiguous_ci` and write nothing; unknown names return `unknown_ci` unless the report sets `create_missing: true`. |
| `/execution` | execution | Governed playbook execution runs and approvals |
| `/decisions` | decisions | First-class decision traces with options, outcomes, and similarity search |
| `/review-queue` | review-queue | Reviewer console bundle — session + top decision + similar aggregate in one call |
| `/admin` | admin | Cost observability (`GET /admin/llm-usage`) and per-tenant LLM budget config (`GET/PUT /admin/tenant-budget`, `GET /admin/tenant-budget/status`). Gated to `tenant_admin` / `platform_super_admin`. See §"Admin — LLM cost & budget" below. |

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

## Graph

Base path: `/api/v1/graph`.

Implementation: `contextedge.api.v1.graph`.

These endpoints expose the context graph for interactive exploration and visualization. All are tenant-scoped via the authenticated user.

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/neighbors` | BFS traversal returning neighboring nodes up to `max_depth` hops |
| `GET` | `/subgraph/{entity_type}/{entity_id}` | Returns nodes and edges around any entity for visualization |
| `GET` | `/stats` | Aggregate edge-type and node-type counts for the tenant |
| `POST` | `/agent-subsets` | Bounded, ranked, authorization-filtered projection for agents |
| `GET` | `/edge-proposals` | Pending agent-proposed dependencies awaiting review (`knowledge_manager`) |
| `POST` | `/edge-proposals/{edge_id}/approve` | Promote a proposal to an authored `depends_on` edge with review provenance; closes the proposal (`knowledge_manager`; optional `note` query param) |
| `POST` | `/edge-proposals/{edge_id}/reject` | Close the proposal with a rejected verdict; writes no topology (`knowledge_manager`; optional `note`) |

Edge proposals come from the MAF `propose_dependency` tool as
`proposed_depends_on` edges — excluded from the maf.v1 projection, so
agents never consume unreviewed topology. Either review verdict closes
the proposal edge (`valid_to`, never deleted); approval records which
proposal, whose review, and the agent's rationale/evidence on the
authored edge's metadata.

All three `GET` endpoints accept optional `domain_id` and timezone-aware
`as_of` parameters. Without `as_of`, only active edges (`valid_to IS NULL`)
are returned. Point-in-time reads apply
`valid_from <= as_of < valid_to`; node facts remain current-state facts.

### `GET /graph/neighbors`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `node_type` | string | yes | Type of origin node (e.g. `pattern`, `playbook`, `episode`) |
| `node_id` | UUID | yes | ID of origin node |
| `edge_type` | string | no | Filter to a specific edge type |
| `max_depth` | int (1–3) | no | BFS traversal depth, default 1 |
| `domain_id` | UUID | no | Scope to a domain (includes domain-less edges) |
| `as_of` | ISO 8601 datetime | no | Point-in-time edge topology; timezone required |

Returns an array of objects: `{node_type, node_id, edge_type, weight, direction, depth}`.

### `GET /graph/subgraph/{entity_type}/{entity_id}`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `max_depth` | int (1–3) | no | BFS traversal depth, default 1 |
| `domain_id` | UUID | no | Scope to a domain |
| `as_of` | ISO 8601 datetime | no | Point-in-time edge topology; timezone required |

Returns `{nodes: [{type, id, title}], edges: [{source, target, type, weight}]}`. Node `source`/`target` strings use the composite format `type:id` suitable for React Flow rendering.

### `GET /graph/stats`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `domain_id` | UUID | no | Scope to a domain |
| `as_of` | ISO 8601 datetime | no | Point-in-time edge topology; timezone required |

Returns `{total_edges, edge_type_counts, node_type_counts}`. Node counts are deduplicated across source and target roles using a `UNION ALL` with distinct counts.

### `POST /graph/agent-subsets`

The strict request accepts `query`, typed `seeds`, `session_id`, entity terms,
`domain_id`, `as_of`, `profile`, `max_depth`, and node/relationship/character
budgets. The only registered profile is `maf.v1`; requested budgets are clamped
to server-controlled maxima.

The response includes a versioned projection ID, safe hydrated node facts,
allowlisted relationship metadata, relevance and provenance, effective budget
and usage, and explicit truncation reasons. Authorization and lifecycle checks
run before traversal expansion. Raw evidence, embeddings, credentials, user
email, storage locations, and arbitrary JSON payloads are never projected.

Service-token `allowed_domain_ids`, tenant ownership, workspace visibility,
evidence access policies, playbook lifecycle/risk, claim validation, redaction,
and legal-hold rules are enforced by the same projection service used by the
MAF adapter.

---

## Evidence — baseline and delta signal

Base path: `/api/v1/evidence`.

Implementation: `contextedge.api.v1.evidence`, `contextedge.schemas.evidence`, `contextedge.services.evidence_baseline_service`, `contextedge.workers.evidence_baseline_tasks`.

Every `EvidenceItem` carries two optional fields that power the reviewer console's Zone 4 evidence cards — current value + delta comparison + color-coded severity:

| Field | Type | Purpose |
| --- | --- | --- |
| `baseline_ref` | JSONB | Shape is open-ended. The generic worker writes `{window_days, first_seen_in_window, prior_evidence_id?, prior_ingested_at?, days_since_prior?, hours_since_prior?, comparison_label, source}`. Connectors with richer semantics (Intune disk-free %, CrowdStrike threat signals) should populate numeric `prior_value` / `current_value` / `delta` keys directly at ingest — the field is deliberately schemaless. |
| `delta_signal` | string | One of `neutral`, `amber`, `red` — the color level the Zone 4 card renders. `neutral` is the default stamped by the generic worker; connectors with domain knowledge upgrade to `amber` / `red` when thresholds are crossed. |

Surfaced on `EvidenceItemResponse` (`delta_signal` only, for list-view color coding) and `EvidenceItemDetail` (both fields, for drill-in).

### Baseline computation

After normalization (and after attachment artifact extraction when applicable), `compute_evidence_baseline_task` fans out alongside `classify_relevance_task` and `correlate_evidence` on the `extraction` queue. It runs a **relationship-only baseline**: finds the most recent prior `EvidenceItem` in the same tenant + `evidence_type` + `source_object_id` within a 7-day window, records "last seen N days ago" or "first observation in 7d window" into `baseline_ref`, and stamps `delta_signal = "neutral"` if not already set.

Skip conditions:

- Evidence has no `source_object_id` — the generic worker needs a stable dedup key; connectors can still populate `baseline_ref` directly at ingest.
- Cross-tenant access — refused for defense-in-depth.
- A connector already stamped `delta_signal` (`amber` / `red`) — the generic worker does **not** overwrite a richer signal.

Introduced in migration `0019_evidence_baseline`.

---

## Evidence — chunking

Base path: `/api/v1/evidence` (data-shape only; no chunk-specific endpoints today).

Implementation: `contextedge.models.evidence.EvidenceChunk`, `contextedge.services.chunkers/`, `contextedge.services.evidence_chunk_service`, `contextedge.workers.chunk_tasks`.

`EvidenceItem` carries two chunking-related fields stamped by the normalize worker after chunks land:

| Field | Type | Purpose |
| --- | --- | --- |
| `chunked_at` | timestamptz (nullable) | Set by `services/evidence_chunk_service.write_chunks` once chunks for the parent's current `chunker_version` are persisted. `NULL` means not-yet-chunked — used by the future backfill scanner via the partial index `ix_evidence_items_chunked_at_null`. |
| `chunk_count` | int (default 0) | Observability-only. The reviewer dashboard / `/admin/cost` surfaces use this to flag items still pending chunking and to spot anomalies (e.g. a runbook with 200 chunks vs. expected ~12). |

`EvidenceChunk` (sibling table, FK `evidence_id → evidence_items(id) ON DELETE CASCADE`):

| Field | Type | Purpose |
| --- | --- | --- |
| `chunk_index` | int | Order within the parent — chunk 0 is the title-prefixed leading chunk. |
| `chunk_kind` | string | Controlled vocab: `body` / `comment` / `message` / `log_event` / `heading_section` / `code_block` / `ocr_text`. |
| `text` | text | Post-redaction chunk body. Inherits the parent's redaction posture; chunks have no independent ACL column. |
| `char_offset_start` / `char_offset_end` | int | Offsets back into `EvidenceItem.body_text` so a citation can be excerpted to the parent. |
| `parent_section` | text (nullable) | Heading breadcrumb for hierarchical chunkers, e.g. `"Postmortem > Timeline > 14:32"`. |
| `embedding` | `vector(3072)` (nullable) | Filled by `embed_chunks_batch_task` after `write_chunks` lands; brief `NULL` window is by design. |
| `content_hash` | string | SHA-256 of `text`, mirrors the parent's hashing rule. Used for chunk-level dedup. |
| `metadata` | JSONB | Per-source enrichment: `author`, `ts`, `severity`, `language`, `symbol`, `version`, `env_tags`, `source_authority` (`runbook` > `ticket` > `email` > `chat` > `gist`). |
| `chunker_version` | int | Coarse-grained schema marker. Bumps only when boundaries change in a way that makes new rows incomparable to old ones. The unique key `(evidence_id, chunk_index, chunker_version)` lets a re-chunk write the new generation alongside the old. |

### What's exposed today vs. deferred

`EvidenceItemResponse` and `EvidenceItemDetail` surface `chunked_at` and `chunk_count`. The chunk rows themselves are **not** yet exposed via a dedicated endpoint — `vector_search.py` still queries `evidence_items.embedding` only, and the chunk-level retrieval rollup (top-K chunk hits + MMR + parent grouping with `chunk_id` preservation) is the next ship. The eventual API surface will likely look like `GET /api/v1/evidence/{id}/chunks` for direct chunk listing and a `chunk_id` field threaded through `evidence_search` responses; both await the search-side rollup. See [codewiki/CHUNKING_DESIGN.md](../codewiki/CHUNKING_DESIGN.md) §6 (search integration).

Introduced in migration `0030_evidence_chunks`.

---

## Playbooks — step schema

Base path: `/api/v1/playbooks`.

Implementation: `contextedge.api.v1.playbooks`, `contextedge.schemas.playbook`.

Playbook step metadata is validated on write through the `PlaybookStep` Pydantic schema and stored inside the existing `PlaybookVersion.steps` JSONB array (no column change). Each step supports these fields — all optional with defaults so pre-M2 payloads keep validating:

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `index` | int | null | Caller-assigned ordinal |
| `title` | string | null | Short label for the step |
| `description` | string | null | Free-text detail |
| `safety_class` | string | null | One of `read_only`, `low_side_effect`, `high_side_effect`, `destructive`; per-step override of playbook-level safety |
| `requires_approval` | bool | false | Mirrors `ExecutionStepRun.requires_approval` |
| `reversible` | bool | false | Whether Undo is available — reviewer console renders an Undo badge |
| `time_estimate_sec` | int ≥ 0 | null | "est. 30 sec" duration badge |
| `verification` | bool | false | Marks a post-action recheck step vs an action step |
| `rollback_hint` | string | null | Free-text Undo guidance for reviewers and operators |
| `tool_ref` | string | null | Connector / tool that executes this step (e.g. `intune.get_device`) |
| `inputs` | dict | `{}` | Per-step inputs passed to the tool |
| `outputs_schema` | dict | null | Declared output shape |

Vendor-specific extras are preserved — the schema uses `extra="allow"` so any additional keys round-trip through `model_dump()` untouched.

### Verification policy

New field `PlaybookVersion.verification_policy` (JSONB, nullable) powers the reviewer console's "auto-close on successful recheck" commitment. Schema (`VerificationPolicy`):

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `auto_close_on_success` | bool | false | Auto-close the resolution session when the recheck passes |
| `recheck_after_sec` | int ≥ 0 | null | Delay between action and verification recheck |
| `recheck_metric` | string | null | Metric name to re-evaluate (e.g. `cert_valid_until`, `disk_free_pct`) |
| `recheck_source` | string | null | Connector / tool that performs the recheck (e.g. `intune`, `crowdstrike`) |

Introduced in migration `0018_playbook_step_metadata`.

---

## Execution approvals

Base path: `/api/v1/execution`.

Implementation: `contextedge.api.v1.execution`.

Governed playbook execution exposes three verbs on a pending approval request — **Approve**, **Deny**, and **Modify** — so reviewers can approve with changes instead of the binary approve/deny choice. Modify uses the same structured reason-code enum as reject so analytics can compare across verbs.

### `POST /execution/runs/{run_id}/approvals/{approval_id}/decide`

Existing endpoint. Body: `ApprovalDecision { decision: "approved" | "denied", comment? }`. Requires `domain_admin`. Creates a first-class `Decision(decision_type="approve" | "deny")` and an `approved_by` / `denied_by` graph edge. Returns `400` if the request is already decided, `404` if missing.

### `POST /execution/runs/{run_id}/approvals/{approval_id}/modify`

Approve-with-changes on a pending approval request.

Request body (`ApprovalModificationRequest`):

```json
{
  "modification_diff": { "inputs": { "ttl_days": 30, "notify": true }, "summary": "shorter ttl" },
  "modification_reason_code": "plan_incomplete",
  "comment": "per cert policy"
}
```

Valid `modification_reason_code` values are the same `REJECTION_REASON_CODES` enum used by reject. `modification_diff` must be a non-empty object.

Behavior:

- `ApprovalRequest.status` flips to `modified`; `modification_diff`, `modification_reason_code`, `decision_comment`, `decided_by`, and `decided_at` are stamped.
- If `modification_diff.inputs` is a dict, it is merged into the step run's `inputs` JSONB (shallow merge, new keys win).
- The execution run and step transition from `awaiting_approval` back to `running` — modify is treated as approved-with-changes, not a new approval cycle.
- An operational event `approval.modified` is emitted with `modification_reason_code` and the diff keys tagged.
- A `modified_by` graph edge is added from the approval request to the deciding user, with `safety_class`, `execution_run_id`, and the reason code in metadata.
- A first-class `Decision(decision_type="modify", actor_type="human")` is created with two `DecisionOption`s: the original action (`selected=false`, `rejection_code=<reason>`) and the modified action (`selected=true`). This keeps the graph's `considered`/`chose` invariant intact alongside approve and reject paths.

Requires `domain_admin`. Returns `400` for invalid codes, empty diffs, or already-decided requests. Returns `404` when the approval request is missing.

---

## Decisions

Base path: `/api/v1/decisions`.

Implementation: `contextedge.api.v1.decisions`.

First-class decision traces with typed graph edges to evidence, options, policies, approvals, and outcomes. See [codewiki/16-decision-traces.md](../codewiki/16-decision-traces.md) for the data-model walkthrough.

### Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/decisions` | List decisions with optional filters, confidence thresholds, and sort order |
| `GET` | `/decisions/{id}` | Retrieve a decision with its options and outcomes |
| `POST` | `/decisions` | Create a decision with typed graph edges |
| `POST` | `/decisions/{id}/outcome` | Record an execution outcome on a decision |
| `POST` | `/decisions/{id}/reject` | Reject an AI-recommended decision with a structured reason code |
| `GET` | `/decisions/{id}/chain` | Walk the parent/child chain for a decision |
| `GET` | `/decisions/{id}/provenance` | Hydrate the decision's `based_on` references with titles, summaries, and source deep-links |
| `GET` | `/decisions/similar` | Find similar decisions by type and optional context keys |
| `GET` | `/decisions/similar/aggregate` | Top-N similar decisions + total count + outcome aggregate + success rate, in one call |
| `GET` | `/decisions/effectiveness` | Aggregate outcome counts per decision type and context |

### `GET /decisions`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `session_id` | UUID | no | Scope to a resolution session |
| `decision_type` | string | no | Exact match on `decision_type` |
| `agent_step` | string | no | One of `diagnostics`, `remediation`, `evaluation`, `triage` |
| `status` | string | no | One of `pending`, `completed`, `superseded`, `reverted` |
| `min_confidence` | float (0–1) | no | Only return decisions with `confidence >= min_confidence` |
| `max_confidence` | float (0–1) | no | Only return decisions with `confidence <= max_confidence` |
| `sort` | string | no | One of `created_desc` (default), `confidence_desc`, `confidence_asc`; both confidence sorts use `NULLS LAST` |
| `limit` | int (1–200) | no | Page size, default 50 |
| `offset` | int | no | Pagination offset, default 0 |

Confidence-based sorting powers the reviewer console queue: `sort=confidence_desc&min_confidence=0.85` surfaces the high-confidence ticket set that can be bulk-approved, while `sort=confidence_asc` focuses human attention on the cases the agent is least sure about.

### `POST /decisions/{id}/reject`

Rejects an AI-recommended decision with a **structured reason code** so the feedback feeds analytics instead of getting lost in free-text.

Request body:

```json
{
  "code": "wrong_diagnosis",
  "comment": "evidence contradicts restart hypothesis"
}
```

Valid `code` values (`contextedge.models.decision.REJECTION_REASON_CODES`):

- `wrong_diagnosis`
- `plan_incomplete`
- `needs_human_judgment`
- `user_context_missing`
- `policy_violation`
- `other`

Behavior:

- The currently selected `DecisionOption` is stamped with `rejection_code` + `rejection_reason = comment` and flipped to `selected = false`.
- A `DecisionOutcome` is created with `execution_result = "rejected"`, `feedback_code = code`, `action_executed = "rejected_by_reviewer"`, and a `resulted_in` graph edge. The outcome carries `feedback_by = <actor_id>` from the caller identity.
- `decision.status` flips to `superseded` and `decision.human_override = true`.
- An operational event `decision.rejected` is emitted with the code + comment tagged for analytics.

Returns `201` with the new `DecisionOutcome`. Returns `400` for unknown codes, `404` when the decision is missing.

### `GET /decisions/{id}/provenance`

Hydrate the `based_on` graph edges so Zone 5 of the reviewer console can render provenance — the specific evidence items, episodes, and patterns that informed the decision, each with a link back to the source system.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `evidence_limit` | int (1–100) | no | Max evidence items, default 20 |
| `episode_limit` | int (1–50) | no | Max episodes, default 10 |
| `pattern_limit` | int (1–50) | no | Max patterns, default 10 |

Response shape (`DecisionProvenanceResponse`):

```json
{
  "decision_id": "...",
  "evidence": [
    {
      "evidence_id": "...",
      "title": "VPN cert expired on vpn-gw-east-01",
      "body_summary": "Gateway auth cert expired at 14:30 UTC",
      "evidence_type": "incident",
      "source_id": "...",
      "source_type": "servicenow",
      "source_display_name": "Acme ServiceNow Prod",
      "external_id": "INC-4521",
      "deep_link": "https://acme.service-now.com/nav_to.do?uri=task.do?sysparm_query=number=INC-4521",
      "delta_signal": "red",
      "ingested_at": "2026-04-19T14:30:00Z"
    }
  ],
  "episodes": [
    { "episode_id": "...", "title": "...", "status": "approved", "final_outcome": "...", "extraction_confidence": 0.87 }
  ],
  "patterns": [
    { "pattern_id": "...", "title": "VPN cert rotation", "pattern_type": "recurring_issue", "confidence": 0.91, "episode_count": 12 }
  ]
}
```

**Deep-link resolution** (`services.source_deep_link_service.build_source_deep_link`):

1. `source.config.deep_link_template` — admin-configurable, supports `{external_id}` and `{thread_id}` substitution. Wins when present, so admins can point at any URL shape without code changes. If a referenced variable is missing, returns null rather than leaking the literal placeholder.
2. Built-in defaults for known `source_type` values:
   - `jira_sm`: `{base_url}/browse/{external_id}` — requires `source.config.base_url`.
   - `servicenow`: `{instance_url}/nav_to.do?uri=task.do?sysparm_query=number={external_id}` — accepts `instance_url` / `tenant_url` / `base_url` in config.
   - `gmail`: `https://mail.google.com/mail/u/0/#all/{thread_id or external_id}`.
   - `teams`: returns null — Teams deep links require tenant + team + channel context not stored on the Source row; admins must supply a `deep_link_template`.
3. Returns null when neither path resolves — the UI renders a non-clickable card.

Returns `404` when the decision is missing.

### `GET /decisions/similar`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `decision_type` | string | yes | Required filter |
| `workflow` | string | no | Match on `context_snapshot.workflow` |
| `environment` | string | no | Match on `context_snapshot.environment` |
| `impacted_dependency` | string | no | Match on `context_snapshot.impacted_dependency` |
| `query_decision_id` | UUID | no | When set, results are ordered semantically using this decision's stored embedding as the query vector (pgvector cosine distance). Self is excluded. |
| `query_text` | string | no | Free-text query embedded on the fly and used as the query vector. Ignored when `query_decision_id` is set. |
| `limit` | int (1–50) | no | Max results, default 10 |

**Ordering:**

- When a query embedding resolves (from `query_decision_id`, that decision's stored embedding; or `query_text`, embedded on the fly), results are ordered by `embedding <=> query` cosine distance and the query is constrained to decisions that have an embedding of their own.
- Otherwise, results fall back to `created_at DESC` ordering. Provider failures during query-text embedding are swallowed and degrade to the fallback path — never 500 the caller.

JSONB containment on `workflow` / `environment` / `impacted_dependency` is applied in both paths as a structural pre-filter, so semantic retrieval can still be scoped to "find the most semantically similar VPN decisions in prod". Paired with `GET /decisions/effectiveness` this renders the "based on N similar tickets, X% succeeded" provenance line — or use `/decisions/similar/aggregate` below for the same three-way join in a single round trip.

### `GET /decisions/similar/aggregate`

Top-N similar decisions + total count + outcome aggregate + success rate, composed server-side so Zone 5 can render provenance in one request instead of fanning out across `/similar`, `/effectiveness`, and a separate count call.

Same query parameters as `/decisions/similar` (`decision_type` required, optional `workflow` / `environment` / `impacted_dependency` / `query_decision_id` / `query_text` / `limit`). Semantic ordering applies to the `decisions` list; `total_count` and `outcomes` remain scoped to `decision_type` + structural context filters only (they are aggregate metrics over the structural slice, not the semantic ordering).

Response shape (`SimilarDecisionsAggregateResponse`):

```json
{
  "decision_type": "execute_playbook",
  "context_filters": { "workflow": "vpn", "environment": "prod" },
  "total_count": 143,
  "outcomes": { "success": 120, "failure": 15, "rejected": 8 },
  "success_rate": 0.839,
  "decisions": [ /* up to limit DecisionResponse items */ ]
}
```

`success_rate` is `success / sum(counted)` where counted outcomes are `success|failure|partial|timeout|rejected`. Unknown outcome labels are preserved in `outcomes` but excluded from the denominator so a rogue label can't skew the ratio. `success_rate` is `null` when no outcomes are recorded.

### `GET /decisions/effectiveness`

Same filter keys as `/decisions/similar`. Returns `{decision_type, context_filters, total, outcomes}` where `outcomes` is a count map over `DecisionOutcome.execution_result` values (`success`, `failure`, `partial`, `timeout`, `rejected`).

---

## Review Queue

Base path: `/api/v1/review-queue`.

Implementation: `contextedge.api.v1.review_queue`.

Single-call bundle endpoint that composes everything a reviewer console needs for one session — so the UI renders in one round trip instead of fanning out across `/sessions`, `/decisions`, `/execution`, and `/audit-logs`.

### `GET /review-queue/{session_id}/context`

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `decisions_limit` | int (1–100) | no | Decisions scoped to session, default 20 |
| `execution_runs_limit` | int (1–50) | no | Execution runs scoped to session, default 10 |
| `events_limit` | int (1–100) | no | Recent operational events scoped to session, default 20 |
| `no_cache` | bool | no | When `true`, skips the Redis read-through cache but still warms on return |

Read-through cached on Redis under `review_queue:{tenant_id}:{session_id}` with a 300-second TTL. The `prefetch_review_context` Celery task warms the cache on session creation (`services.session_service.create_resolution_session`), so the first request after `POST /sessions` hits Redis, not Postgres. On cache miss the handler live-computes via `build_review_context` and back-fills the cache. Corrupt cache entries fall back to live compute rather than 500 the caller.

Caching only engages when the request uses default limits — passing custom `decisions_limit` / `execution_runs_limit` / `events_limit` bypasses the keyed slot so the cached payload can't be poisoned by a shape it doesn't claim.

Every mutation that changes a session's review state invalidates the cached bundle via `services.review_queue_service.invalidate_review_context`: `POST /decisions`, `POST /decisions/{id}/outcome`, `POST /decisions/{id}/reject`, `POST /execution/runs/{run_id}/approvals/{approval_id}/decide`, `POST /execution/runs/{run_id}/approvals/{approval_id}/modify`, and `POST /sessions/{id}/close`. Approve and Modify invalidate transitively through their embedded decision creation. Invalidation fires post-flush but pre-commit, so a narrow race window exists where a concurrent read could re-populate the cache with the pre-commit snapshot; the 300s TTL is the backstop.

Response shape (`ReviewQueueContext`):

```json
{
  "session": { "id": "...", "status": "open", "symptoms": [...], "entities": [...], ... },
  "top_decision": { /* DecisionResponse with options + outcomes */ },
  "top_decision_badge": { "score": 0.92, "level": "green" },
  "similar": {
    "decision_type": "execute_playbook",
    "context_filters": { "workflow": "vpn", "environment": "prod" },
    "total_count": 143,
    "outcomes": { "success": 120, "failure": 15, "rejected": 8 },
    "success_rate": 0.839
  },
  "decisions": [ /* up to decisions_limit */ ],
  "execution_runs": [ /* up to execution_runs_limit */ ],
  "recent_events": [ /* up to events_limit */ ]
}
```

Selection rules:

- **`top_decision`** — latest pending decision with a confidence score; falls back to latest decision with a confidence score; falls back to the latest decision overall; `null` when the session has no decisions.
- **`top_decision_badge.level`** — server-derived thresholds so every consumer agrees: `green >= 0.8`, `amber 0.5–0.8`, `red < 0.5`, `null` when the decision has no confidence recorded.
- **`similar`** — filters are derived from `top_decision.context_snapshot` keys (`workflow`, `environment`, `impacted_dependency` only); composed from `count_similar_decisions` + `get_decision_effectiveness`; `null` when no `top_decision` exists.
- **`similar.success_rate`** — `success / sum(counted_outcomes)` where counted outcomes are `success|failure|partial|timeout|rejected`; unknown outcome labels are ignored so they can't skew the denominator; `null` when no outcomes are recorded.

Returns `404` when the session does not exist.

---

## Admin — LLM cost observability

Base path: `/api/v1/admin`.

Implementation: `contextedge.api.v1.admin_cost`, `contextedge.services.admin_cost_service`, `contextedge.ai.observability`.

Every LLM call in the engine flows through `record_llm_usage` (`ai/observability.py`) which emits:

1. **Prometheus counters** — `contextedge_llm_tokens_total` (labelled by `tenant_id`, `model`, `task`, `token_type ∈ {prompt,completion,cached}`) and `contextedge_llm_requests_total` (labelled by tenant, model, task, `outcome ∈ {ok,error}`). Scrape via `/metrics`.
2. **Structured logs** — one `llm.usage` log line per call with full token breakdown + duration.
3. **Operational events** — persisted row with `event_type="llm.usage"`, queried by the admin dashboard.

Cache-hit tokens are normalised across providers: OpenAI `usage.prompt_tokens_details.cached_tokens` and Anthropic `usage.cache_read_input_tokens` both land in a single `cached_tokens` counter.

### `GET /admin/llm-usage`

Returns aggregated token spend + cache-hit rate + model×task breakdown for the caller's tenant over a configurable window.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `window_hours` | int (1–720) | no | Aggregation window, default 24. Max 30 days. |
| `top_n_breakdown` | int (1–50) | no | How many model×task rows to return (ranked by cost desc), default 10. |

Response shape (`LlmUsageResponse`):

```json
{
  "window_hours": 24,
  "from_time": "...",
  "to_time": "...",
  "totals": {
    "request_count": 1523,
    "prompt_tokens": 842000,
    "completion_tokens": 95000,
    "cached_tokens": 620000,
    "total_tokens": 937000,
    "estimated_cost_usd": 1.83,
    "cache_hit_rate": 0.736
  },
  "by_model_task": [
    {
      "model": "gpt-4o-mini",
      "task": "classification",
      "request_count": 1200,
      "prompt_tokens": 600000,
      "completion_tokens": 30000,
      "cached_tokens": 480000,
      "total_tokens": 630000,
      "estimated_cost_usd": 0.042
    }
  ]
}
```

Cost estimates derive from a rate table in `services.admin_cost_service.MODEL_COST_USD_PER_M_TOKENS`. Non-authoritative — the LLM provider's billing dashboard is the invoice of record. Cache-hit rate = `cached_tokens / prompt_tokens` (0 when no prompt tokens in window).

Requires `tenant_admin` or `platform_super_admin`. Paired with the frontend `/admin/cost` dashboard.

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
| Graph router | `contextedge.api.v1.graph` |
| Decisions router | `contextedge.api.v1.decisions` |
| Review-queue router | `contextedge.api.v1.review_queue` |
| Admin cost router | `contextedge.api.v1.admin_cost` |
| Decision trace service | `contextedge.services.decision_trace_service` |
| Review-queue service | `contextedge.services.review_queue_service` |
| Admin cost service | `contextedge.services.admin_cost_service` |
| Tenant budget service | `contextedge.services.tenant_budget_service` |
| LLM observability | `contextedge.ai.observability` |
| Prompt registry | `contextedge.ai.prompts` |
| Schemas | `contextedge.schemas.*` |

When you add or rename routers, update this file and the document map in [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md).

---

## Admin — LLM cost & budget

All routes gated to `tenant_admin` or `platform_super_admin`. Tenant-scoped: a caller can only see / edit their own tenant's rows, regardless of role.

### `GET /admin/llm-usage`

Query params: `window_hours` (1-720, default 24), `top_n_breakdown` (1-50, default 10). Returns aggregated token + estimated USD spend for the window plus a top-N breakdown by `(model, task)`. Reads from `operational_events` rows with `event_type = "llm.usage"` written by `ai/observability.record_llm_usage`. Cache-hit rate is `cached_tokens / prompt_tokens` — target > 0.5 after worker warm-up.

### `GET /admin/tenant-budget`

Returns the caller's tenant's `TenantLLMBudget` row (daily token limit, daily USD cap, `action_on_exceed` ∈ `{"block", "warn"}`) or `null` when no budget is configured. Tenants without a row are uncapped.

### `PUT /admin/tenant-budget`

Body: `{daily_token_limit: int|null, daily_cost_cap_usd: float|null, action_on_exceed: "block"|"warn"}`. Creates or replaces the row. Nulls mean "don't cap on this axis" — at least one of token limit / cost cap must be set for the row to do anything. `action_on_exceed = "warn"` logs + emits `llm.budget_warning` events but lets calls through; `"block"` raises `TenantBudgetExceeded` from `llm_complete` so callers can degrade. Returns the saved row.

### `GET /admin/tenant-budget/status`

Live view combining the budget config with the current UTC day's usage, computed fresh (not cached). Response includes `current_tokens`, `current_cost_usd`, `allowed`, and `reason` (`"ok"` | `"no_budget"` | `"token_limit_exceeded"` | `"cost_cap_exceeded"`). Powers the header of the `/admin/cost` dashboard page. See `services/tenant_budget_service.py` for the 60-second usage cache semantics used by the pre-call gate, the per-tenant `asyncio.Lock` that serialises check_budget in-process (review F-29), and the SQLAlchemy `after_delete` listener that evicts cache on tenant-delete CASCADE (review F-30).

---

## Shared response shapes

Seven previously-raw-dict endpoints now declare `response_model=` against three shared Pydantic shapes in `contextedge.schemas.common`. Callers can rely on these for frontend type-gen instead of hand-maintaining the shape. Routes that take these are listed in the table in §"Router surface" above.

- **`StatusResponse { status: str, detail?: dict }`** — minimal past-tense ack. Used by `PATCH /evidence/{id}/relevance` (`status="updated"`) and `POST /correlations/{id}/decision` (`status ∈ accepted|rejected|split|merge`, `detail` carries correlation_id + replacement_ids when relevant).
- **`TaskDispatchResponse { status: str, task_id?: str, detail?: dict }`** — enqueue ack. Used by `POST /sources/{id}/backfill`, `POST /sources/local-ingest`, `POST /threads/{id}/hydrate`, `POST /episodes/reconstruct`, `POST /patterns/cluster`. `task_id` populated whenever the dispatch returned one; `detail` carries task-specific supplemental info (`{"object_count": 12}`, `{"evidence_count": 20, "domain_id": "…"}`).
- **`MutationAckResponse { status: str, id: str }`** — create/update ack with the affected entity's UUID. Used by `POST /runtime/feedback` (`status="feedback_recorded"`, `id` = new `RetrievalFeedback.id`).

The remaining four endpoints with raw-dict returns (`GET /patterns/{id}/graph`, `GET /decisions/effectiveness`, three `GET /graph/*`) are intentionally bespoke — they carry rich, observability-shaped payloads that deserve their own schemas rather than being squeezed into one of the three above. Frontend has matching local types in `frontend/src/lib/types/index.ts`.
