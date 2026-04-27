# 17 — AE Ops Context Graph Alignment

## Overview

Migration `0029_ae_ops_concept_alignment` aligns the ContextEdge schema with the **AE Ops Context Graph design** (`ae_ops_context_graph_design.md`) — an external design document for a banking-grade AutomationEdge operations support memory layer.

The codebase already had a mature execution + evidence + decision platform. What it lacked was the design's **governance spine**: first-class entities (workflow / agent_machine / schedule), evidence-backed claims with a validation lifecycle, action-keyed policies with explicit verdicts, error signatures with success/failure counters, and case-level outcomes distinct from per-decision outcomes.

This article is the engineering narrative for the alignment: what was missing, what each new table is for, what's deliberately *not* in this migration, and what the next wave needs to do.

## Design Principles

1. **Strictly additive.** No rename, no drop, no type change on any existing column. Every new column is nullable. Every new constraint is guarded by `IF NOT EXISTS` / `pg_constraint` lookup. Existing service code continues to work unchanged because it never touches the new fields.
2. **Coexist, don't migrate.** `CanonicalIdentity` keeps its identity-resolution role (people, aliases). The new `entities` table is the home for operational nouns (workflow, agent_machine, schedule, output_location). They have different query patterns; mixing them would have hurt both.
3. **Distinct concepts get distinct tables.** `Pattern` is "there's a recurring issue here"; `ErrorSignature` is "this exact log shape" — separate table. `Playbook` is "the ordered steps to do it"; `FixPattern` is "the recommender's evidence that this fix works" — separate table. `DecisionOutcome` is "did this specific decision execute OK?"; `CaseOutcome` is "is the case actually resolved?" — separate table.
4. **The verdict lives on the decision.** `Decision.policy_result` is the value the executor checks (`allowed_auto` / `approval_required` / `recommendation_only` / `restricted` / `manual_only`). `policy_refs JSONB[]` keeps the pointers; `policy_result` is the answer.
5. **Idempotency at the action layer.** `ExecutionStepRun.idempotency_key` (partial unique index, only NOT-NULL keys constrained) is the single most important banking-grade safety control in the alignment. Without it, retried orchestrator calls can rerun finance workflows and produce duplicate output.

## Data Model

### `entities` — operational-noun graph node

Polymorphic entity with the design's natural key `(entity_type, external_system, external_id) UNIQUE`. Holds AE workflows, agent machines, schedules, output locations, and the rest of the operational-noun catalogue.

| Field | Type | Purpose |
|-------|------|---------|
| `entity_type` | string | `workflow`, `workflow_request`, `agent_machine`, `schedule`, `output_location`, `application`, `database`, `file_share`, `business_service`, `incident`, `sop`, … |
| `external_system` / `external_id` | string | Source-system natural key (e.g. `automation_edge` / `MG22`) |
| `name` | string | Display name |
| `environment` / `business_unit` / `data_domain` | string | Tenant separation axes (Section 43.2 of the design) |
| `attributes` | JSONB | Per-type extensions (workflow_code, criticality, output_channel, …) |
| `source_ref` | JSONB | Lineage pointer back to source-of-truth |
| `confidence` | numeric(5,4) | Belief in the entity's correctness |
| `last_synced_at` | timestamptz | Freshness marker |

`Entity` constants live in `contextedge.models.entity.ENTITY_TYPES`. Coexists with `CanonicalIdentity`; the two never share rows.

### `claims` + `claim_evidence` + `decision_evidence` — the missing spine

The design's "evidence before claim, policy before action" rule (Section 27.3) requires a relational claim object. Before `0029` there wasn't one — `Decision.rationale_summary` was a free-text blob and `Pattern.root_causes[]` was loose JSON.

| Table | Purpose |
|-------|---------|
| `claims` | First-class evidence-backed assertion. `claim_type` ∈ {`probable_root_cause`, `confirmed_root_cause`, `symptom`, `risk`, `recommended_action`, `failed_step`, `dependency_issue`, `user_impact`, `policy_interpretation`}. `validation_status` lifecycle: `unverified` → `machine_verified` → `human_validated` → `rejected` → `superseded`. `superseded_by_claim_id` self-FK chains supersessions. |
| `claim_evidence` | Claim ↔ `evidence_items` link with `support_type` (default `supports`, also `contradicts` / `weakens`) and `weight`. `UNIQUE (claim_id, evidence_id)`. |
| `decision_evidence` | Decision ↔ `evidence_items` relational link, **supplementing** the existing `Decision.evidence_summary JSONB` cache. The JSONB stays for cheap "render the rationale" reads; the link table answers the inverse question "which decisions cited this evidence?". `UNIQUE (decision_id, evidence_id)`. |

Partial index on `claims (tenant_id, created_at DESC) WHERE validation_status IN ('unverified', 'machine_verified')` powers the "claims awaiting validation" dashboard tile (Appendix C item 11).

### `action_policies` — action-keyed verdict

Distinct from `TenantPolicy`, which stays as the generic config bucket (`retention` / `classification` / `access` / `approval` types). The new table answers a different query: *"for this action, on this workflow, in this environment, what does policy say?"*

| Field | Purpose |
|-------|---------|
| `action_name` | Controlled identifier matching the executor (e.g. `rerun_workflow`, `resend_existing_output`, `restart_agent_service`) |
| `workflow_entity_id` | Optional FK into `entities` for per-workflow overrides |
| `environment` / `business_unit` / `data_domain` | Scope axes |
| `risk_level` | `low` / `medium` / `high` / `restricted` |
| `policy_result` | **Verdict** — `allowed_auto` / `approval_required` / `recommendation_only` / `restricted` / `manual_only` |
| `required_approver_roles` | JSONB list of role names |
| `allowed_execution_mode` | `auto` / `human_approved_auto` / `human_approved_only` / `manual` / `recommendation_only` |
| `priority` / `policy_scope` / `conflict_resolution` | Precedence groundwork (Section 43.12); engine TBD |

Indexed on `(tenant_id, action_name, workflow_entity_id, environment)` for the common policy-check lookup.

### `error_signatures` + `fix_patterns` — counted recommender

Two new tables that **don't replace** `Pattern` and `Playbook` — they add the missing recommender axis on top.

| Table | Purpose |
|-------|---------|
| `error_signatures` | Normalised error fingerprint. `signature_key` UNIQUE per tenant (e.g. `SMTP_TIMEOUT_AFTER_OUTPUT_GENERATED`). Carries `patterns JSONB[]` (regex), `usual_causes`, `recommended_actions`, `risk_notes`, plus `success_count` / `failure_count` / `confidence`. Optional FK back to `Pattern` for aggregation. |
| `fix_patterns` | Statistical "this fix is known to work for this issue" record. `(issue_type, workflow_entity_id, error_signature_id, failed_step)` shape. Carries its own `success_count` / `failure_count` / `confidence` plus an optional `recommended_playbook_id` FK pointing at the executable. Multiple `FixPattern` rows can share the same `Playbook` with different precondition contexts. |

Counters are intended to be incremented from the case-outcome write path: `case_outcomes.successful_action` increments the matching fix pattern; `case_outcomes.failed_actions[]` increments failure counts. That wire-up is the next wave's job — `0029` adds the columns; the increments happen in service code later.

### `case_outcomes` + `case_state_transitions` — case-level resolution

`DecisionOutcome` (existing) records "did this specific decision execute OK?". `CaseOutcome` records "is the case actually resolved, and what should we learn?".

| `case_outcomes` field | Purpose |
|-----------------------|---------|
| `outcome_status` | `resolved` / `unresolved` / `workaround_applied` / `escalated` / `duplicate` / `false_alarm` |
| `confirmed_root_cause` | Final RCA |
| `successful_action` | Single action name that worked (feeds the FixPattern counter) |
| `failed_actions` | JSONB list of action names that didn't (also feeds counters) |
| `mttr_minutes` | Mean time to resolve, populated by service code from `opened_at` / `closed_at` |
| `should_create_or_update_pattern` | Defaults true; a closure can opt out of pattern learning |

`case_state_transitions` is the optional history complement to `resolution_sessions.status`. Without it the column is current-state only and the lifecycle (Section 43.21 of the design) is unobservable.

`CASE_STATUSES` constants live in `contextedge.models.case_outcome` (`new`, `triaging`, `diagnosing`, `awaiting_user_clarification`, `awaiting_approval`, `approved`, `executing`, `monitoring`, `resolved`, `closed`, `escalated`, `cancelled`, `reopened`).

## Existing-Table Extensions

All columns added by `0029` are nullable. No backfill required.

### `resolution_sessions` — case spine

| Column | Why |
|--------|-----|
| `case_number` | Partial-unique (`UNIQUE WHERE NOT NULL`); generator like `CG-YYYY-NNNNNN` |
| `case_type` / `issue_type` | The design's controlled vocab — `output_not_received`, `workflow_failed`, `agent_not_reachable`, `workflow_stuck`, `credential_failure`, `input_file_issue`, `db_connectivity_issue`, `schedule_not_triggered` |
| `title` / `description` / `priority` / `severity` / `environment` | First-class columns instead of fishing them out of `entities[]` JSONB |
| `user_entity_id` / `workflow_entity_id` / `request_entity_id` / `agent_entity_id` | Four FKs into `entities` — the structured query path the design's `cg_case` mandates |

### `evidence_items` — temporal + lineage + redaction marker

| Column | Why |
|--------|-----|
| `evidence_time` | The time the evidence subject occurred (a log line at 10:42 vs the source object created at 10:45). Distinct from existing `created_at_source` (record creation) and `ingested_at` (graph storage time) |
| `collected_by` | Agent or human that captured the evidence (for SoD / lineage) |
| `source_type` | Controlled vocab: `AE_API` / `AE_AGENT_LOG` / `SERVICENOW` / `TEAMS` / `EMAIL` / `SOP` / `MONITORING` / `HUMAN_NOTE`. Free text in DDL but indexed for filter queries |
| `redaction_status` | Per-row marker (`not_required` / `pending` / `applied`). The `redaction_event` table is on the design roadmap; this column is the marker that belongs on the row |

### `decisions` — governance axis + verdict

The existing `decision_type` enum is action-oriented (`classify_issue`, `restart_workflow`, …); the design's enum is governance-oriented (`diagnosis`, `recommendation`, `remediation`, `escalation`, `approval_request`, …). Rather than widen the existing enum, `0029` adds a parallel axis:

| Column | Why |
|--------|-----|
| `decision_intent` | Design vocab. Stays alongside `decision_type` so both axes are queryable |
| `decision_summary` | Single canonical human-readable summary (today the summary is split across `rationale_summary` / `explanation` / `compact_trace`) |
| `risk_level` | **Trace-level** risk distinct from per-option risk on `DecisionOption`. Different question: "options considered had what risk" vs "the path we took has what risk" |
| `policy_result` | The verdict the executor checks. `policy_refs JSONB[]` keeps the pointers |

### `decision_trace_events` — cg_decision_step role

| Column | Why |
|--------|-----|
| `decision_id` | Optional FK so a session-scoped trace event can also anchor to a specific decision |
| `tool_name` / `tool_input_ref` / `tool_output_ref` | Tool I/O references the design wants on each reasoning step |

### `approval_requests` — case/decision anchor + role + channel + SoD

Anchoring was the real gap: `ApprovalRequest.execution_run_id` ties approval to *playbook execution*; the design ties approval to a *decision* (and transitively a case). `0029` adds the anchors:

| Column | Why |
|--------|-----|
| `case_id` / `decision_trace_id` | Approval is now addressable from any of: case, decision, execution |
| `action_name` | Controlled identifier (vs free-text `requested_action` which stays) |
| `approver_role` | The role consulted, not the user (e.g. `Finance Process Owner`, `AE L2 Support`) |
| `approval_channel` | `teams` / `email` / `servicenow` / `portal` / `manual` |
| `approval_note` | Reason text from the approval surface |
| `recommended_by` / `executed_by` / `sod_check_status` / `sod_violation_reason` | Segregation of duties (Section 43.13) — agent that recommended is recorded separately from approver and executor |

### `execution_step_runs` — action_name + idempotency + anchors

| Column | Why |
|--------|-----|
| `case_id` / `decision_trace_id` | Anchors so any action row is queryable from either side of the chain |
| `action_name` | Controlled identifier the policy engine matches against. `step_title` stays as the human label |
| `action_type` | `diagnostic` / `remediation` / `notification` / `escalation` / `approval` / `manual` |
| `execution_mode` | Denormalised from `ExecutionRun.automation_mode` so each step row is self-describing without a join |
| `executed_by` | Promoted from `ExecutionRun.initiated_by` to step-level (execution mode can change per step) |
| `idempotency_key` | **The banking-grade safety control.** Partial unique index (`UNIQUE WHERE idempotency_key IS NOT NULL`). NULL keys are unconstrained (legacy rows + read-only steps don't need one). Keys typically shaped like `rerun_workflow:WF_MG22:REQ_98231:2026-04-26` |
| `duplicate_check_status` | Marker for "we ran the duplicate check and it passed/failed" |

### `graph_edges` — temporal validity

| Column | Why |
|--------|-----|
| `valid_from` / `valid_to` | Enables "what was true at incident time?" queries (Section 43.1.4 of the design). Both nullable: existing edges keep behaving as "valid since creation, no expiry" |
| `confidence` | Semantically distinct from `weight` (importance for traversal): confidence is the belief in the relation. Nullable so existing rows aren't forced to commit |

A composite index on `(valid_from, valid_to)` supports the temporal lookup pattern.

## What's Deliberately Not in 0029

The migration is the **first-class** layer of the alignment. Several next-wave concerns from the design's Section 43 are intentionally out of scope:

| Out-of-scope | Why deferred |
|--------------|--------------|
| Service-code population of new fields | Schema-first; population can land per-feature |
| `ErrorSignature` / `FixPattern` counter increments | Needs case-outcome write path wired up |
| `mttr_minutes` calculation | Needs to read `resolution_sessions.opened_at` (mapped to `created_at` today) and `case_outcomes.closed_at` |
| Action policy precedence engine (`priority`, `policy_scope`, `conflict_resolution`) | Columns provisioned, evaluator not yet built |
| `cg_source_sync_state`, `cg_audit_log` (extended), `cg_ai_inference_run`, `cg_redaction_event`, `cg_confidence_history`, `cg_workflow_version`, `cg_policy_override`, `cg_output_artifact`, `cg_schedule_observation`, `cg_claim_conflict`, `cg_eval_case`, `cg_tool_call`, `cg_cost_event`, `cg_data_quality_issue` | Section 43 production-hardening tables — separate landing |
| AutomationEdge connector | No `connectors/automation_edge/` exists; the alignment doesn't ship it. The schema is ready when the connector arrives |
| MCP server façade (`context.*` tools) | All 12 design-mandated MCP tools have a REST analogue (full or partial); the MCP wrapper is a separate concern |

## Migration Behaviour

| Property | Value |
|----------|-------|
| Revision | `0029_ae_ops_concept_alignment` |
| Down-revision | `0028_orm_ddl_drift_alignment` |
| Idempotent | Yes — `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `pg_constraint` lookups guard FK adds |
| Reversible | Yes — `downgrade()` drops in reverse order, `IF EXISTS` everywhere |
| Backfill required | No — every new column is nullable |
| Service code changes | None |
| New constraints captured in drift snapshot | Yes — `tests/test_review_orm_ddl_drift.py::_EXPECTED_MARKERS` updated |
| Test impact | 459/459 tests pass after snapshot update |

## Key Files

| File | Purpose |
|------|---------|
| `backend/alembic/versions/0029_ae_ops_concept_alignment.py` | The migration |
| `backend/src/contextedge/models/entity.py` | `Entity` + `ENTITY_TYPES` constants |
| `backend/src/contextedge/models/claim.py` | `Claim`, `ClaimEvidence`, `DecisionEvidence`, lifecycle constants |
| `backend/src/contextedge/models/action_policy.py` | `ActionPolicy`, `RISK_LEVELS`, `POLICY_RESULTS`, `EXECUTION_MODES` |
| `backend/src/contextedge/models/error_signature.py` | `ErrorSignature`, `FixPattern` |
| `backend/src/contextedge/models/case_outcome.py` | `CaseOutcome`, `CaseStateTransition`, `OUTCOME_STATUSES`, `CASE_STATUSES` |
| `backend/src/contextedge/models/session.py` | Case-spine columns + 4 entity FKs on `ResolutionSession`; decision-anchor columns on `DecisionTraceEvent` |
| `backend/src/contextedge/models/evidence.py` | `evidence_time` / `collected_by` / `source_type` / `redaction_status` on `EvidenceItem` |
| `backend/src/contextedge/models/decision.py` | `decision_intent` / `decision_summary` / `risk_level` / `policy_result` on `Decision` |
| `backend/src/contextedge/models/execution.py` | Action-name + idempotency + anchors on `ExecutionStepRun`; channel + role + SoD + anchors on `ApprovalRequest` |
| `backend/src/contextedge/models/pattern.py` | `valid_from` / `valid_to` / `confidence` on `GraphEdge` |
| `backend/src/contextedge/models/__init__.py` | Registers all new models + enum constants |
| `backend/tests/test_review_orm_ddl_drift.py` | Drift-guard snapshot updated with the new constraints |
| `docs/MIGRATIONS.md` | Notable revision entry for `0029` |
| `docs/TECHNICAL_BLUEPRINT.md` §11 | Logical data model — group 9 added |
