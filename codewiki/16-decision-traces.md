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

### DecisionOption (table: `decision_options`)

Options that were considered during the decision:

| Field | Type | Purpose |
|-------|------|---------|
| `action` | string | What would be done (e.g., "restart_workflow") |
| `suitability` | float | Estimated fitness 0–1 |
| `risk_level` | string | `low`, `medium`, `high` |
| `preconditions` | JSONB | Required conditions |
| `rejection_reason` | text | Why not selected (null if selected) |
| `selected` | boolean | Whether this option was chosen |

### DecisionOutcome (table: `decision_outcomes`)

What actually happened after the decision was executed:

| Field | Type | Purpose |
|-------|------|---------|
| `action_executed` | string | What was actually done |
| `execution_result` | string | `success`, `failure`, `partial`, `timeout` |
| `result_details` | JSONB | Structured result payload |
| `follow_up_needed` | boolean | Whether a follow-up decision is needed |
| `follow_up_decision_id` | FK | Chains to next decision |
| `feedback_received` | text | Human feedback on the decision |

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
| GET | `/decisions` | List decisions (filter by session, type, step, status) |
| GET | `/decisions/{id}` | Get decision with options and outcomes |
| POST | `/decisions/{id}/outcome` | Record an outcome |
| GET | `/decisions/{id}/chain` | Get full decision chain |
| GET | `/decisions/similar` | Find similar decisions by type + context |
| GET | `/decisions/effectiveness` | Aggregate outcome stats for a decision type |

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
| `backend/src/contextedge/models/decision.py` | ORM models |
| `backend/src/contextedge/schemas/decision.py` | Pydantic schemas |
| `backend/src/contextedge/services/decision_trace_service.py` | Core service logic |
| `backend/src/contextedge/api/v1/decisions.py` | API routes |
| `backend/src/contextedge/graph/builder.py` | Graph edge helpers |
| `backend/src/contextedge/workers/decision_tasks.py` | Analytics workers |
| `frontend/src/components/decisions/decision-detail.tsx` | Detail component |
| `frontend/src/components/decisions/decision-chain.tsx` | Chain timeline |
| `frontend/src/app/(dashboard)/decisions/page.tsx` | Decisions page |
