# 16 — First-Class Decision Traces

## Overview

Decision traces are the **institutional reasoning memory** of ContextEdge. Every time the system (or a human operator) makes a choice — selecting a playbook, approving an execution step, escalating to a human, or deferring action — a first-class `Decision` node is created in the context graph with full provenance: what evidence was considered, what options were evaluated, why one was chosen, and what happened when it was executed.

This goes beyond flat event logging (`DecisionTraceEvent`, which is preserved for backward compatibility) by making decisions queryable graph citizens with typed relationships to evidence, episodes, patterns, policies, approvals, and outcomes.

## Design Principles

1. **Decisions are nodes, not logs.** A `Decision` participates in the context graph with typed edges to its evidence, options, and outcomes.
2. **Backward compatibility.** The existing `DecisionTraceEvent` model is preserved. When a `Decision` is created within a session context, a compact trace event is also appended for lightweight audit trails.
3. **Three layers of detail.** (a) Compact trace — one-liner for Teams/ticket display; (b) Structured JSON — full context snapshot, options, evidence refs; (c) Human-readable explanation — multi-paragraph rationale.
4. **Decision chains.** Decisions can link to parent/child decisions via `parent_decision_id`, forming chains that represent multi-step reasoning (e.g., diagnose → select playbook → execute → evaluate outcome → escalate).

## Data Model

### Decision (table: `decisions`)

The primary reasoning entity. Key fields:

| Field | Type | Purpose |
|-------|------|---------|
| `decision_type` | string | `classify_issue`, `execute_playbook`, `approve`, `deny`, `escalate_to_human`, etc. |
| `agent_step` | string | `diagnostics`, `remediation`, `evaluation`, `triage` |
| `actor_type` | string | `ai`, `human`, `hybrid` |
| `context_snapshot` | JSONB | Workflow, environment, severity, failure signature at decision time |
| `evidence_summary` | JSONB | Compact list of `{ref_type, ref_id, description}` |
| `rationale_summary` | text | Structured reasoning summary |
| `confidence` | float | Predicted confidence (0–1) |
| `compact_trace` | text | One-liner for display |
| `policy_refs` | JSONB | Triggered policy identifiers |
| `status` | string | `pending`, `completed`, `superseded`, `reverted` |
| `parent_decision_id` | FK | Self-referencing for decision chains |
| `session_id` | FK | Links to `ResolutionSession` |
| `embedding` | Vector(3072) | pgvector embedding of `decision_type + compact_trace + rationale_summary` for semantic similar-decision retrieval; nullable (provider failures degrade gracefully) |

### DecisionOption (table: `decision_options`)

Options that were considered during the decision:

| Field | Type | Purpose |
|-------|------|---------|
| `action` | string | What would be done (e.g., "restart_workflow") |
| `suitability` | float | Estimated fitness 0–1 |
| `risk_level` | string | `low`, `medium`, `high` |
| `preconditions` | JSONB | Required conditions |
| `rejection_reason` | text | Free-text reason (used for the `other` code + write-in) |
| `rejection_code` | string | Structured code — one of `REJECTION_REASON_CODES` (see below); feeds analytics |
| `selected` | boolean | Whether this option was chosen |

### DecisionOutcome (table: `decision_outcomes`)

What actually happened after the decision was executed:

| Field | Type | Purpose |
|-------|------|---------|
| `action_executed` | string | What was actually done |
| `execution_result` | string | `success`, `failure`, `partial`, `timeout`, `rejected` (reviewer-rejected decisions use `rejected` so analytics can separate them from executed-and-failed) |
| `result_details` | JSONB | Structured result payload |
| `follow_up_needed` | boolean | Whether a follow-up decision is needed |
| `follow_up_decision_id` | FK | Chains to next decision |
| `feedback_received` | text | Free-text human feedback |
| `feedback_code` | string | Structured feedback code — same enum as `rejection_code` |

### Structured rejection and modification codes

The human-in-the-loop flow captures **Approve / Modify / Reject** decisions with structured codes rather than free text, so analytics aggregation (e.g. `get_decision_effectiveness`) can group by failure mode. Codes are defined in `contextedge.models.decision.REJECTION_REASON_CODES`:

- `wrong_diagnosis`
- `plan_incomplete`
- `needs_human_judgment`
- `user_context_missing`
- `policy_violation`
- `other`

When a reviewer rejects via `POST /decisions/{id}/reject`:

1. The currently selected `DecisionOption` is stamped with `rejection_code` and `rejection_reason = comment`, flipped to `selected = false`.
2. A `DecisionOutcome(execution_result="rejected", feedback_code=<code>, action_executed="rejected_by_reviewer")` is created with a `resulted_in` graph edge.
3. `decision.status` flips to `superseded`, `decision.human_override = true`.
4. An operational event `decision.rejected` is emitted tagged with the code for analytics.

The parallel Modify flow (see `ApprovalRequest.modification_diff` and `modification_reason_code` in `models/execution.py`) follows the same code enum — so a rejected decision and a modified execution step produce comparable learning signals. `POST /api/v1/execution/runs/{run_id}/approvals/{approval_id}/modify` (`services.execution_service.modify_approval`) stamps the approval, merges `modification_diff["inputs"]` into the step's inputs, emits a `modified_by` graph edge, and creates a `Decision(decision_type="modify")` with two options (original `selected=false` with `rejection_code=<reason>`, modified `selected=true`). `DECISION_TYPES` includes `"modify"` alongside `"approve"` and `"deny"`.

## Graph Edge Types

All edges use the existing `GraphEdge` table with new `edge_type` values:

| Edge type | Source → Target | Purpose |
|-----------|----------------|---------|
| `based_on` | decision → evidence/episode/pattern | What evidence informed the decision |
| `considered` | decision → decision_option | All options that were evaluated |
| `chose` | decision → decision_option | The option that was selected |
| `applied_policy` | decision → tenant_policy | Policies that constrained the decision |
| `required_approval` | decision → approval_request | Approval gates |
| `resulted_in` | decision → decision_outcome | Execution result |
| `followed_by` | decision → decision | Decision chain links |

## Integration Points

### Execution Service

`start_execution` creates a `Decision` with `decision_type="execute_playbook"`. `decide_approval` creates a `Decision` for the approval/denial. `complete_execution` records the outcome on the execution decision.

### Memory Service

`build_runtime_memory_context` includes recent decisions in the `reasoning` memory bucket, so future runtime queries can see past decision history and their outcomes.

### Session Service

Sessions can eager-load their associated decisions via the `include_decisions` parameter on `get_resolution_session`.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/decisions` | Create a decision |
| GET | `/decisions` | List decisions with filters (session, type, step, status), confidence thresholds (`min_confidence`, `max_confidence`), and sort (`created_desc`, `confidence_desc`, `confidence_asc`) |
| GET | `/decisions/{id}` | Get decision with options and outcomes |
| POST | `/decisions/{id}/outcome` | Record an outcome (accepts `feedback_code`) |
| POST | `/decisions/{id}/reject` | Reject an AI-recommended decision with a structured reason code |
| GET | `/decisions/{id}/chain` | Get full decision chain |
| GET | `/decisions/{id}/provenance` | Hydrate `based_on` edges with titles + summaries + source deep-links for Zone 5 drill-in |
| GET | `/decisions/similar` | Find similar decisions by type + context |
| GET | `/decisions/similar/aggregate` | Top-N similar decisions + total count + outcome aggregate + success rate in one call |
| GET | `/decisions/effectiveness` | Aggregate outcome stats for a decision type |
| GET | `/review-queue/{session_id}/context` | Bundle for reviewer console — session + top decision with confidence badge + similar-decision aggregate + scoped decisions / runs / events, in one round trip |

Confidence filter/sort on `GET /decisions` powers the reviewer queue: `sort=confidence_desc&min_confidence=0.85` surfaces the high-confidence ticket set for bulk-approval, while `sort=confidence_asc` focuses human attention on low-confidence cases.

`GET /decisions/{id}/provenance` hydrates the `based_on` graph edges for Zone 5's drill-in: the evidence items, episodes, and patterns that informed a decision, each with source provenance (source_type, display_name, external_id) and a deep-link back to the origin system (ServiceNow / Jira / Gmail). Deep-link construction is admin-configurable via `source.config.deep_link_template` (with `{external_id}` / `{thread_id}` substitution) with built-in defaults for the known source types. This makes the "why this was recommended" reasoning traceable from the decision all the way to the source ticket without the reviewer context-switching into multiple systems.

`GET /decisions/similar/aggregate` composes top-N similar decisions, the total count, outcome counts, and success rate in one call — server-side three-way join of `find_similar_decisions` + `count_similar_decisions` + `get_decision_effectiveness` via `find_similar_decisions_aggregate`. Zone 5's "based on 143 similar tickets, 87% succeeded" provenance line reads from the single response, no client-side fan-out. The success-rate math ignores unknown outcome labels so a rogue label can't skew the ratio; the raw outcomes dict is preserved for UIs that want to render the full breakdown.

**Semantic similar-decision retrieval.** `Decision.embedding` (Vector(3072)) is populated inline during `create_decision` from `decision_type + compact_trace + rationale_summary`. Both `/similar` and `/similar/aggregate` accept `query_decision_id` (uses that decision's stored embedding as the query vector) or `query_text` (embedded on the fly) and order results by `embedding <=> query` cosine distance via pgvector. JSONB containment on `workflow` / `environment` / `impacted_dependency` still applies as a structural pre-filter, so queries like "most semantically similar VPN decisions in prod" are one request. When no query embedding resolves (neither param, or provider failure), retrieval falls back to `created_at DESC` — the pre-C3 behavior, so rolling this out doesn't break callers. Count + outcome aggregates stay structural (scoped to `decision_type` + context filters), which keeps "N similar tickets" a stable denominator even when the top-K list reshuffles by semantic relevance.

The `/review-queue/{session_id}/context` bundle composes `get_resolution_session`, `list_decisions`, `list_execution_runs`, `list_operational_events`, `count_similar_decisions`, and `get_decision_effectiveness` into a single response shaped by `ReviewQueueContext`. `top_decision_badge.level` is server-derived (`green >= 0.8`, `amber 0.5–0.8`, `red < 0.5`) so every consumer agrees on thresholds. See [`docs/API.md`](../docs/API.md) for the full response shape.

The bundle is **read-through cached** on Redis under `review_queue:{tenant_id}:{session_id}` (TTL 300s). A Celery task `prefetch_review_context` (`workers/review_queue_tasks.py`) is enqueued from `session_service.create_resolution_session`, so the cache is warm before the reviewer ever opens the ticket — the round trip that happens on queue-click hits Redis, not Postgres, which is what makes the sub-2s first-render budget real. The cache key is tenant-scoped so cross-tenant bleed is impossible, and payloads are serialized through `ReviewQueueContext.model_dump_json()` so cached bytes match the wire format with no re-validation on hit. Corrupt cache entries fall back to live compute. Only default limits are cached; custom limits bypass the cache to avoid payload-shape poisoning. Callers can force a live read with `?no_cache=true`.

Every mutation that changes a session's review state invalidates the cache via `invalidate_review_context(tenant_id, session_id)`: `create_decision`, `record_outcome`, `reject_decision`, and `close_resolution_session`. `decide_approval` and `modify_approval` invalidate transitively through their embedded `create_decision` call. The helper manages its own short-lived Redis client and swallows transport errors so a degraded Redis never bubbles into mutation code paths. Invalidation fires post-flush but pre-commit, so a narrow race window exists where a concurrent bundle read could re-populate the cache with the pre-commit snapshot — the 300s TTL backstops this.

## Analytics (Phase 3)

- **Remediation effectiveness**: `get_decision_effectiveness` aggregates success/failure rates per decision type and context — answers "is restart usually effective for this failure type?"
- **Decision pattern mining**: `mine_decision_patterns` worker task surfaces recurring decision/outcome patterns.
- **Confidence calibration**: `calibrate_decision_confidence` worker compares predicted confidence to observed outcomes.

## Frontend

- **Decisions page** (`/decisions`): List, filter, detail panel with options/outcomes/reasoning, chain view, "View in Graph" link.
- **Sessions page**: "Decisions" tab alongside existing trace events, showing linked Decision cards.
- **Graph Explorer**: New node types (`decision`, `decision_option`, `decision_outcome`) and edge types rendered with amber/orange color family.

## Key Files

| File | Purpose |
|------|---------|
| `backend/src/contextedge/models/decision.py` | ORM models; `REJECTION_REASON_CODES` enum lives here; `Decision.embedding` (Vector(3072)) |
| `backend/src/contextedge/ai/embeddings.py` | `embed_decision` — combines `decision_type + compact_trace + rationale_summary`, returns zero vector on empty input |
| `backend/src/contextedge/models/execution.py` | `APPROVAL_STATUSES` (`pending`, `approved`, `denied`, `modified`) and `ApprovalRequest.modification_diff` + `modification_reason_code` |
| `backend/src/contextedge/schemas/decision.py` | Pydantic schemas incl. `DecisionRejectRequest` with enum validation |
| `backend/src/contextedge/schemas/review_queue.py` | Bundle response shapes (`ReviewQueueContext`, `ConfidenceBadge`, `SimilarDecisionAggregate`) |
| `backend/src/contextedge/services/decision_trace_service.py` | Core service logic incl. `reject_decision`, `count_similar_decisions`, confidence filter/sort in `list_decisions` |
| `backend/src/contextedge/services/review_queue_service.py` | `build_review_context` composition; server-side badge derivation and success-rate math; Redis cache helpers (`build_cache_key`, `read_cache`, `write_cache`, `invalidate_cache`, `REVIEW_CONTEXT_CACHE_TTL_SEC`) |
| `backend/src/contextedge/services/source_deep_link_service.py` | `build_source_deep_link` — admin-configurable template wins; built-in defaults for `jira_sm`, `servicenow`, `gmail` |
| `backend/src/contextedge/workers/review_queue_tasks.py` | `prefetch_review_context` Celery task (warms the cache on session creation) |
| `backend/src/contextedge/api/v1/decisions.py` | API routes incl. `POST /decisions/{id}/reject` |
| `backend/src/contextedge/api/v1/review_queue.py` | Reviewer console bundle endpoint |
| `backend/src/contextedge/graph/builder.py` | Graph edge helpers |
| `backend/src/contextedge/workers/decision_tasks.py` | Analytics workers |
| `backend/alembic/versions/0017_rejection_modification_codes.py` | Adds `rejection_code` / `feedback_code` / `modification_*` columns |
| `frontend/src/components/decisions/decision-detail.tsx` | Detail component |
| `frontend/src/components/decisions/decision-chain.tsx` | Chain timeline |
| `frontend/src/app/(dashboard)/decisions/page.tsx` | Decisions page |
| `frontend/src/app/(dashboard)/review/page.tsx` | Reviewer console — queue + session detail with Approve / Reject verbs; consumes `/review-queue/{id}/context` and `/decisions/similar/aggregate` |
