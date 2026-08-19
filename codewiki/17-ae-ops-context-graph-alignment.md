# 17 — AE Ops Context Graph Alignment

## Overview

Migration `0029_ae_ops_concept_alignment` aligned the ContextEdge schema with the **AE Ops Context Graph design** (`ae_ops_context_graph_design.md`) — an external design document for a banking-grade AutomationEdge operations support memory layer.

The codebase already had a mature execution + evidence + decision platform. What it lacked was the design's **governance spine**: first-class entities (workflow / agent_machine / schedule), evidence-backed claims with a validation lifecycle, action-keyed policies with explicit verdicts, error signatures with success/failure counters, and case-level outcomes distinct from per-decision outcomes.

`0029` deliberately shipped **schema only** — tables, columns, indexes, and no service code. That was the right call for a large additive migration, and it also created a specific hazard: for a while the schema *read* like shipped capability to anyone auditing it, while several of the columns were written by nothing at all.

This article is the engineering narrative for the alignment, updated **2026-08-19**. It covers what was missing, what each new table is for, **which parts have since gained real writers and which are still dormant**, and what the next wave still needs to do.

> **Read the status section before the data model.** `0029` is no longer the current head — the chain has moved on many revisions past it, and several things this article once listed as "next wave" have landed in later migrations and services. As always: trust `alembic heads`, not a number in a document.

## Status as of 2026-08-19 — what is live, what is dormant

The single most useful thing this article can tell you is which halves of the alignment are load-bearing today. The authority is `backend/tests/test_governance_column_writers.py`, a CI guard that scans every `mapped_column` under `src/contextedge` for a writer and asserts **set equality** against a register of known-unwritten columns, each carrying an owner and a reason. Set equality runs both ways, so a column that later gains a writer fails CI until its register entry is removed — the register cannot quietly go stale.

**One limit of that guard, worth knowing before you trust it:** the scan matches on **column name**, not on model. When two tables share a column name and one of them gains a writer, the other becomes invisible to the scan. The register keeps six such columns as prose in `SHADOWED_BY_NAME` (`tests/test_governance_column_writers.py:222-236`), and this article names eight more, found by reading the constructors directly rather than by grep:

- `ApprovalRequest.case_id` and `ExecutionStepRun.case_id` — `case_id` is written, but on `CaseOutcome` and `CaseStateTransition` (`services/case_outcome_service.py:113-115, 161-163`).
- `DecisionTraceEvent.tool_name` — written, but on `ToolInvocation` (`services/execution_service.py:1184`).
- `ApprovalRequest.executed_by` — the only `executed_by` write in the whole of `src/` is on `ExecutionStepRun` (`services/execution_service.py:816`).
- `ResolutionSession.title` / `description` / `priority` / `environment` — each name is written dozens of times across episodes, patterns, playbooks and action policies; none of those writes touches a session. The only `ResolutionSession(...)` constructor sets tenant, initiator, domain, symptoms, entities, external case ids, notes and status, and nothing else assigns to a session afterwards (`services/session_service.py:49-58`).

A name collision costs visibility, not truth.

| `0029` object | Status today | Who writes it |
| --- | --- | --- |
| `entities` | **Live**, but populated with ticket-system nouns, not AE nouns | `servicenow_reference_service.py:291`, `inventory_diff_service.py:126`, `event_evidence_service.py:166`; the types actually written are `configuration_item`, `business_service`, `assignment_group`, `topic` (plus Zoho's `customer_account` / `knowledge_category`) |
| `claims` | **Live** — created, never validated | `claim_service.persist_extracted_claims` (`services/claim_service.py:48, 77`), called from the inline relevance pass and from manual re-classification (`workers/extraction_tasks.py:452-455, 668-672`) |
| `claim_evidence`, `decision_claims`, `decision_evidence` | **Dormant** — no constructor anywhere | Nothing. Models exist (`models/claim.py:111, 145, 183`); the claim↔evidence relationship is carried by a `supported_by` **graph edge** instead |
| `action_policies` | **Live and enforcing** | Authored via `api/v1/action_policies.py:144`; evaluated per step inside `start_execution` (`services/action_policy_service.py:156`; `services/execution_service.py:206-286`, called at `:788`) |
| `error_signatures` | **Live** — key, message, examples only | `error_signature_service.fingerprint_evidence` (`services/error_signature_service.py:176, 216`), called on every normalize (`workers/extraction_tasks.py:520-527`) |
| `fix_patterns` | **Dormant** — nothing constructs a row | Nothing. Read by six modules and written by none; see "The `FixPattern` hole" below |
| `case_outcomes`, `case_state_transitions` | **Live**, but only at the two ends of a session | `case_outcome_service.record_case_transition` / `record_case_outcome` (`services/case_outcome_service.py:101, 113, 126, 161`), driven from session open and session close (`services/session_service.py:62-69, 198-238`) |
| `execution_step_runs.idempotency_key` | **Live** since F8 (migration `0060`) | `_assign_idempotency_keys` (`services/execution_service.py:403-493, 828`), keys derived in `services/idempotency_service.py:62-79` |
| `approval_requests` SoD columns | **Dormant** (`recommended_by`, `sod_check_status`, `sod_violation_reason`, `decision_trace_id`, `approval_channel`, `approval_note`) | Nothing; owner F7 in the register (`tests/test_governance_column_writers.py:70-80`) |
| Case / decision **anchors** on `approval_requests`, `execution_step_runs`, `decision_trace_events` | **Dormant** — the chain is still run-shaped, not case-shaped | Nothing. `ApprovalRequest(...)` (`services/execution_service.py:1250`), `ExecutionStepRun(...)` (`:799-817`), and `DecisionTraceEvent(...)` (`services/session_service.py:154-162`) set none of them |
| `decisions.decision_intent` / `risk_level` / `policy_result` | **Live** | F1 wired intent and risk; F3b writes the run's strictest action-policy verdict into `policy_result` (`services/execution_service.py:1005`) |
| `decisions.decision_summary` | **Retire candidate** — `rationale_summary` already carries it | Nothing (`tests/test_governance_column_writers.py:65-68`) |
| `resolution_sessions` case-spine columns | **Dormant** — the whole spine, not part of it | Nothing. The register names `case_number`, `case_type`, `issue_type`, `severity` and the user / request / agent entity FKs, owner F3 (`tests/test_governance_column_writers.py:149-161`); `workflow_entity_id` is in `SHADOWED_BY_NAME`; and `title` / `description` / `priority` / `environment` are shadowed too — see the name-shadow list above |
| `evidence_items.evidence_time` | **Live** on the event path | `event_evidence_service.py:146` — state-transition events, where subject time genuinely differs from ingest time |
| `evidence_items.source_type` | **Live** — and it found an unplanned consumer | Stamped from the parent `Source` row so the **chunker registry** has a connector key to dispatch on (`workers/extraction_tasks.py:83-97`) |
| `evidence_items.collected_by`, `redaction_status` | **Dormant** — redaction runs but does not stamp the per-row marker | Nothing (`tests/test_governance_column_writers.py:167-171`) |
| `graph_edges.valid_from` / `valid_to` / `confidence` | **Live** | `graph/builder.py:43, 108` stamp `valid_from` on every create, `close_edge` sets `valid_to` at `:166`, and `cmdb_topology_service.py:235` end-dates edges when upstream deletes a relationship; `ensure_edge` takes `confidence` separately from `weight` |

Two constraint changes landed shortly after `0029` and supersede what this article originally said about its uniqueness rules. Migration `0031_maf_context_graph_hardening` dropped the **global** `uq_entities_type_system_external_id` and replaced it with the tenant-scoped `uq_entities_tenant_type_system_external_id` (`0031_maf_context_graph_hardening.py:78-94`), and did the same for `resolution_sessions.case_number`, which is now a partial unique index on `(tenant_id, case_number) WHERE case_number IS NOT NULL` (`0031:96-101`). A natural key that was unique across the whole installation would have let one tenant's `MG22` collide with another's.

### The `FixPattern` hole, stated plainly

`fix_patterns` is the one place where the alignment's "add the recommender axis" promise is still unfulfilled, and it is worth naming because *everything around it is built*:

- `fix_cohort_service.record_fix_outcome` increments `FixPattern.success_count` / `failure_count` and every cohort statistic (`services/fix_cohort_service.py:61, 77-79`).
- `case_outcome_service` validates and links fix results into `case_outcome_fix_patterns` (`services/case_outcome_service.py:205-246`).
- `fix_applicability_service`, `execution_verification_service`, and the maf.v1 materializer and hydrators all read fix patterns.

And nothing anywhere calls `FixPattern(...)` — a grep across `src/` and `tests/` returns exactly one hit, the class declaration at `models/error_signature.py:99`. The tenant-scoped existence check in the outcome writer (`case_outcome_service.py:213-232`) therefore filters every submitted `fix_pattern_id` out, logs `case_outcome.fix_result_unknown_pattern`, and writes no link. The B4 applicability join, the B5 cohort counters, and the verification write-back are **dormant, not merely unexercised** — nothing can mint the row the whole chain keys on. This is tracked in [KNOWN_GAPS.md](./KNOWN_GAPS.md) and needs an owner.

## Design Principles

Each principle carries the tradeoff it accepted — the alignment made real choices, not free ones.

1. **Strictly additive.** No rename, no drop, no type change on any existing column; every new column nullable; every new constraint guarded by `IF NOT EXISTS` or a `pg_constraint` lookup.
   *Why:* a large migration against a live banking-grade platform must be safe to roll forward with running service code and safe to roll back.
   *Tradeoff:* nullable-everywhere means the schema cannot enforce that anything is populated, which is precisely how twenty-odd columns went two months without a writer while looking shipped. The answer was not to tighten the columns but to add the F1 writer register that now fails CI on an unexplained gap.

2. **Coexist, don't migrate.** `CanonicalIdentity` keeps its identity-resolution role (people, aliases); the new `entities` table is the home for operational nouns.
   *Why:* the two have genuinely different query patterns — alias resolution with fuzzy matching versus natural-key upsert — and merging them would have degraded both.
   *Tradeoff:* two tables that both answer "what is this thing", so every consumer must know which one to ask, and a person-shaped operational noun (an approval group, say) has an ambiguous home.

3. **Distinct concepts get distinct tables.** `Pattern` is "there's a recurring issue here"; `ErrorSignature` is "this exact log shape". `Playbook` is "the ordered steps"; `FixPattern` is "the recommender's evidence that this fix works". `DecisionOutcome` is "did this decision execute OK"; `CaseOutcome` is "is the case actually resolved".
   *Why:* collapsing them would force one row to answer questions with different lifecycles, different writers, and different confidence semantics.
   *Tradeoff:* more tables to keep in sync, and a real cost when one of them never gets a writer — the split is only a win once both halves exist. A third, later fingerprint concept, `IssueSignature` (migration `0045`), now sits *between* `Pattern` and `ErrorSignature`, and its `error_signature_id` bridge column is still unwritten (`services/issue_signature_service.py:168-177`), so the deterministic and LLM fingerprint systems run in parallel, unjoined.

4. **The verdict lives on the decision.** `Decision.policy_result` is the value the executor checks (`allowed_auto` / `approval_required` / `recommendation_only` / `restricted` / `manual_only`); `policy_refs JSONB[]` keeps the pointers.
   *Why:* an executor should read one field, not re-derive a verdict from a list of policy references at dispatch time.
   *Tradeoff:* a snapshot can go stale relative to the policy rows behind it. F3's `policy_checks` table is the answer — one append-only row per evaluation, keyed to the policy **version**, recorded on the deny path as well as the allow path, so the history of what a run was judged under cannot be rewritten by a later policy edit.

5. **Idempotency at the action layer.** `ExecutionStepRun.idempotency_key` with a partial unique index (only NOT-NULL keys constrained) is the alignment's most important banking-grade safety control: without it, retried orchestrator calls can rerun finance workflows and produce duplicate output.
   *Why:* at-least-once delivery is a property of every queue in the system, so "did I already do this" has to be answerable from stored state.
   *Tradeoff:* a key is only a control once something writes it, and for a long time nothing did — the unique index guarded an always-NULL column and the control was inert (`services/idempotency_service.py:1-6`). F8 closed it. **The key shape changed in the process:** it is no longer a readable string like `rerun_workflow:WF_MG22:REQ_98231:2026-04-26` but a SHA-256 of `tenant:case:artifact_hash` prefixed `idem_` (`idempotency_service.py:62-79`), because the index is global and a readable key would leak tenant identity into a column other tenants' rows share. Only side-effecting steps get one, and skills whose contract declares `NATIVE` idempotency get none — a suppression the tool did not ask for is a bug wearing a safety control's clothes (`idempotency_service.py:47-59`).

## Data Model

### `entities` — operational-noun graph node

Polymorphic entity keyed on `(tenant_id, entity_type, external_system, external_id)` UNIQUE (`models/entity.py:67-75`, tightened from `0029`'s global key at `0029:88` by `0031`). Designed to hold AE workflows, agent machines, schedules, and output locations.

| Field | Type | Purpose |
|-------|------|---------|
| `entity_type` | string | `workflow`, `workflow_request`, `agent_machine`, `schedule`, `output_location`, `application`, `database`, `file_share`, `business_service`, `incident`, `sop`, … |
| `external_system` / `external_id` | string | Source-system natural key (e.g. `automation_edge` / `MG22`) |
| `name` | string | Display name |
| `environment` / `business_unit` / `data_domain` | string | Tenant separation axes (Section 43.2 of the design) |
| `manufacturer` / `model` / `os_name` / `os_version` | string | Normalized traits added by `0043` — the deciding dimensions for fix applicability. NULL means the source did not provide the trait; absent is absent, never guessed (`models/entity.py:94-99`) |
| `attributes` | JSONB | Per-type extensions (workflow_code, criticality, ci_class, output_channel, …) |
| `source_ref` | JSONB | Lineage pointer back to source-of-truth |
| `confidence` | numeric(5,4) | Belief in the entity's correctness |
| `last_synced_at` | timestamptz | Freshness marker — the CMDB topology cache uses it as a 7-day TTL |

`ENTITY_TYPES` is the registry (`models/entity.py:32-62`), and `tests/test_entity_type_registry.py` scans every reference service and fails the moment one writes a type the registry does not know. That test exists because Zoho enrichment was writing `customer_account`, `knowledge_category`, and `topic` unregistered — exactly the ontology drift the tuple is for.

**Reality check:** what populates this table today is ticket-system enrichment, not AutomationEdge. `configuration_item` and `assignment_group` come from ServiceNow reference fields (`services/servicenow_reference_service.py:256-291`), `business_service` from Jira components and Zoho products, and the CMDB hybrid write-through-caches a CI's ±1-hop neighborhood here. `workflow`, `agent_machine`, `schedule`, and `output_location` are registered vocabulary with no writer, because the AutomationEdge connector does not exist (see "What's Deliberately Not in 0029").

One upsert detail matters when reading that code: `_ensure_entity` finds-or-creates on `(tenant_id, external_system, external_id)` and **deliberately leaves `entity_type` out of the lookup**, so a CI whose class mapping changes later updates the one existing row instead of forking a duplicate (`services/servicenow_reference_service.py:256-266`). A rename refreshes the display name, but a `sys_id` fallback name never overwrites a real one, and a trait present upstream wins while an absent one never clears what an earlier sync captured (`:279-289`).

### `claims` + `claim_evidence` + `decision_evidence` — the evidence spine

The design's "evidence before claim, policy before action" rule (Section 27.3) requires a relational claim object. Before `0029` there wasn't one — `Decision.rationale_summary` was a free-text blob and `Pattern.root_causes[]` was loose JSON.

| Table | Purpose | Writer status |
|-------|---------|---------------|
| `claims` | First-class evidence-backed assertion. `claim_type` ∈ {`probable_root_cause`, `confirmed_root_cause`, `symptom`, `risk`, `recommended_action`, `failed_step`, `dependency_issue`, `user_impact`, `policy_interpretation`}. `validation_status` lifecycle: `unverified` → `machine_verified` → `human_validated` → `rejected` → `superseded`. `superseded_by_claim_id` self-FK chains supersessions. | **Written.** `persist_extracted_claims` mints 0–3 atomic assertions per relevant evidence item from the **relevance call that already read the body** — no extra LLM call (`services/claim_service.py:1-24, 48-90`). Deduplicated tenant-wide on normalized lowercase text, so a re-asserted claim gains another support edge instead of a duplicate row. |
| `claim_evidence` | Claim ↔ `evidence_items` link with `support_type` (`supports` / `contradicts` / `weakens`) and `weight`. `UNIQUE (claim_id, evidence_id)`. | **No constructor.** The link is written as a `claim -[supported_by]-> evidence` **graph edge** instead (`claim_service.py:93-105`). The relational table stays empty, so `support_type` has no writer. |
| `decision_evidence` | Decision ↔ `evidence_items` relational link, supplementing the `Decision.evidence_summary JSONB` cache. The JSONB stays for cheap "render the rationale" reads; the link table answers "which decisions cited this evidence?". | **No constructor.** |

Two consequences to state honestly. First, **nothing validates a claim**: every row lands `unverified` and stays there, so `validated_by` / `validated_at` / `validation_note` and the whole `VALIDATION_STATUSES` lifecycle are unreachable (`tests/test_governance_column_writers.py:110-122`). Second, that is not purely a gap — the maf.v1 projection's visibility gate admits a claim only when `validation_status` is `machine_verified` or `human_validated` (`graph/agent/hydrators.py:167-171`), so the graph accumulates candidate assertions without the agent ever seeing an unreviewed one. Validation is what would promote a claim into the projection; today nothing does, so no claim is agent-visible. That is a safe failure, not a silent one — but it does mean the claim spine is currently write-only.

The partial index on `claims (tenant_id, created_at DESC) WHERE validation_status IN ('unverified', 'machine_verified')` (`0029_ae_ops_concept_alignment.py:132-134`) exists for a "claims awaiting validation" dashboard tile — a tile that would have plenty to show and no way to act on it, and which no screen renders today.

### `action_policies` — action-keyed verdict

Distinct from `TenantPolicy`, which stays as the generic config bucket (`retention` / `classification` / `access` / `approval` types). The new table answers a different question: *"for this action, on this workflow, in this environment, what does policy say?"*

| Field | Purpose |
|-------|---------|
| `action_name` | Controlled identifier matching the executor (e.g. `rerun_workflow`, `resend_existing_output`, `restart_agent_service`) |
| `workflow_entity_id` | Optional FK into `entities` for per-workflow overrides |
| `environment` / `business_unit` / `data_domain` | Scope axes |
| `risk_level` | `low` / `medium` / `high` / `restricted` |
| `policy_result` | **Verdict** — `allowed_auto` / `approval_required` / `recommendation_only` / `restricted` / `manual_only` |
| `required_approver_roles` | JSONB list of role names |
| `allowed_execution_mode` | `auto` / `human_approved_auto` / `human_approved_only` / `manual` / `recommendation_only` |
| `priority` / `policy_scope` / `conflict_resolution` | Precedence controls (Section 43.12) |

Indexed on `(tenant_id, action_name, workflow_entity_id, environment)` for the common lookup.

**This table is now enforcing.** F3b built the engine (`services/action_policy_service.py`) and migration `0064` versioned the table on the same rules-not-labels terms as `tenant_policies` — a version bump tracks a change to the rules, not a rename. Three ordered rules decide which policy applies (`action_policy_service.py:13-33`):

1. **Scope filter** — a policy applies when every axis it *declares* matches; a NULL axis means "any", so a policy naming no environment governs all of them (`_matches`, `:87`).
2. **Specificity** — among applicable policies, the one that declared more matching axes wins, so a narrow rule is worth writing (`specificity`, `:98`).
3. **Conflict resolution** — only on a genuine specificity tie. The default is `most_restrictive`, and an **unknown verdict ranks most restrictive**, so a typo can never read as `allowed_auto` (`restrictiveness`, `:75`). `highest_priority` is available, and ties there fall back to most-restrictive rather than row order, because row order is not a decision anyone made.

Enforcement runs per step inside `start_execution`: `_apply_action_policy` (`services/execution_service.py:206-286`) is called once per step in the step loop, and its verdicts are collected in `action_verdicts` (`:763, 788-796`). Every evaluation writes a `policy_checks` row — `pass`, `fail`, or `not_applicable` when no policy matches — with the action name, step index, verdict, environment, and workflow scope in `input_snapshot` (`execution_service.py:241-279`). A blocking verdict raises `ExecutionPolicyError` naming the policy and its version (`:280-285`). The run's strictest verdict lands on `Decision.policy_result` (`execution_service.py:1005`).

The rule that keeps this safe: **`allowed_auto` grants nothing.** It means *this policy does not object*, not *this may run unattended*. Safety class, role, and trust have already had their say, and a policy that could overturn them would be a way to grant privilege by writing a row (`execution_service.py:221-225`; the same rule F10's trust model follows). Two residuals: `decision_action_policies` still has no constructor, so nothing links a decision to the specific policy row it was judged under — `policy_checks` carries that instead; and the engine reads `environment` from the playbook, so business-unit and data-domain scoping only bite once those axes are populated.

### `error_signatures` + `fix_patterns` — counted recommender

Two tables that **don't replace** `Pattern` and `Playbook` — they add the recommender axis on top.

| Table | Purpose | Writer status |
|-------|---------|---------------|
| `error_signatures` | Normalised error fingerprint. `signature_key` unique per tenant via `uq_error_signature_key` (`models/error_signature.py:41-42`) — different tenants may classify the same error differently. Carries `patterns JSONB[]` (regex), `usual_causes`, `recommended_actions`, `risk_notes`, plus `success_count` / `failure_count` / `confidence`, and an optional FK back to `Pattern`. | **Partially written.** `fingerprint_evidence` runs on every normalize and writes `signature_key`, `display_name`, `error_type`, `normalized_message`, `patterns`, and up to three `example_messages` (`services/error_signature_service.py:206-247`). `usual_causes`, `recommended_actions`, `risk_notes`, and `last_used_at` have no writer. |
| `fix_patterns` | Statistical "this fix is known to work for this issue", shaped `(issue_type, workflow_entity_id, error_signature_id, failed_step)`, with its own counters, confidence, and an optional `recommended_playbook_id` FK. Multiple rows can share one `Playbook` with different precondition contexts. | **No constructor anywhere.** See "The `FixPattern` hole" above. |

The fingerprinting pass is worth understanding because it is the cheapest thing in the pipeline. It is **pure normalization — no LLM call, no embedding** — so it runs on every ingested evidence item at effectively zero marginal cost, including items the relevance gate skipped: a confidently-irrelevant thread can still carry a pasted stack trace worth indexing (`services/error_signature_service.py:1-15`; dispatched at `workers/extraction_tasks.py:520-539`). Precision beats recall by design, because a junk signature poisons exact-match lookups in a way a missed one does not, so extraction only fires on unambiguous shapes: exception class names, vendor codes (`ORA-`, `SQLSTATE`, hex codes, `errno`), HTTP 5xx, and `ERROR`/`FATAL`/`SEVERE` log lines anchored to the severity token so prose containing the word "error" does not match (`error_signature_service.py:41-53`). Scans are bounded to 40,000 characters / 500 lines, at most 3 signatures per evidence item (`:34-38`). Variables are stripped in a deliberate order — UUIDs before hex, dates before the generic digit-run rule, which would otherwise shred them into key-polluting fragments (`:55-60`). Each hit writes an `evidence -[exhibits]-> error_signature` graph edge (`:248-256`), and the whole thing is fail-soft: the caller wraps it, and a fingerprinting failure never breaks normalize.

Counters were always intended to be incremented from the case-outcome write path. That code now exists — `record_fix_outcome` increments the fix's global counters and every cohort statistic, then re-evaluates promotions (`services/fix_cohort_service.py:61-123`) — it simply has no rows to increment.

### `case_outcomes` + `case_state_transitions` — case-level resolution

`DecisionOutcome` (existing) records "did this specific decision execute OK?". `CaseOutcome` records "is the case actually resolved, and what should we learn?".

| `case_outcomes` field | Purpose |
|-----------------------|---------|
| `outcome_status` | `resolved` / `unresolved` / `workaround_applied` / `escalated` / `duplicate` / `false_alarm` |
| `confirmed_root_cause` | Final RCA |
| `successful_action` | Single action name that worked (feeds the FixPattern counter) |
| `failed_actions` | JSONB list of action names that didn't (also feeds counters) |
| `mttr_minutes` | Derived from the session's own timeline |
| `should_create_or_update_pattern` | Defaults true; a closure can opt out of pattern learning (nothing does yet) |

**Both tables are now written**, which supersedes any earlier text describing this as schema-only. The writers live in `services/case_outcome_service.py` and are driven from the source of truth for case lifecycle — the resolution session:

- A transition row is appended by `record_case_transition` (`:101-123`), which has exactly two callers: session creation, writing `NULL → open` (`services/session_service.py:62-69`), and session close, writing `<previous> → closed` (`:216-224`). The intermediate `CASE_STATUSES` — `triaging`, `diagnosing`, `executing`, `monitoring` — have no writer, because no surface moves a session through them. The history is real at both ends and empty in the middle.
- A close records the transition, and — **only when the caller asserts an outcome** — a `CaseOutcome` row (`services/session_service.py:203-238`). An unstated outcome is unknown, not "resolved". Re-closing an already-closed session is a no-op for history, because appending `closed → closed` on every repeat call would be noise wearing the costume of lifecycle (`session_service.py:208-215`).
- `mttr_minutes` is computed as `closed_at - session.created_at` in minutes, with a `TypeError` guard for naive/aware timestamp mismatches (`case_outcome_service.py:152-159`).
- `fix_results` entries are validated (`successful` / `failed` / `partial`), checked to exist **in this tenant** so an id from elsewhere cannot accrue statistics, and deduplicated in Python so a repeated entry is a skip rather than an `IntegrityError` 500 (`case_outcome_service.py:177-246`).

The module is explicit about what it refuses: it does not infer outcomes from ticket text. An outcome row asserts "the case is actually resolved and this is what we learned" — that is a caller's claim (a human close, or an agent close with user confirmation), never a regex's (`case_outcome_service.py:13-16`).

Read surface: `GET /api/v1/sessions/{session_id}/history` composes the history via `get_case_history` — transitions oldest-first because it reads as a timeline, outcomes newest-first because the latest is the operative one and earlier rows are reopen-and-close history (`services/case_outcome_service.py:38-58`; `api/v1/sessions.py:102-117`).

`CASE_STATUSES` constants live in `models/case_outcome.py` (`new`, `triaging`, `diagnosing`, `awaiting_user_clarification`, `awaiting_approval`, `approved`, `executing`, `monitoring`, `resolved`, `closed`, `escalated`, `cancelled`, `reopened`).

## Existing-Table Extensions

All columns added by `0029` are nullable. No backfill was required.

### `resolution_sessions` — case spine

| Column | Why | Status |
|--------|-----|--------|
| `case_number` | Partial-unique, now tenant-scoped: `(tenant_id, case_number) WHERE case_number IS NOT NULL` (`0031:96-101`). The `CG-YYYY-NNNNNN` shape comes from the **external AE design document, not from code** — no generator exists to check it against | Unwritten — no generator exists |
| `case_type` / `issue_type` | The intended vocabulary (`output_not_received`, `workflow_failed`, `agent_not_reachable`, `workflow_stuck`, `credential_failure`, `input_file_issue`, `db_connectivity_issue`, `schedule_not_triggered`) is **design intent from the AE document, not an enforced vocabulary**. The columns are plain `String(60)` and `String(80)`, nullable, with **no CHECK constraint and no enum** (`models/session.py:54-55`) — whatever eventually writes them may write anything | Unwritten |
| `title` / `description` / `priority` / `environment` | First-class columns instead of fishing them out of `entities[]` JSONB | **Unwritten**, and invisible to the register: every one of those names is written on some other model, so the name-based scan reports them as covered. The only `ResolutionSession(...)` sets tenant, initiator, domain, symptoms, entities, external case ids, notes and status (`services/session_service.py:49-58`), and no later code assigns to them |
| `severity` | Same rationale | Unwritten |
| `user_entity_id` / `workflow_entity_id` / `request_entity_id` / `agent_entity_id` | Four FKs into `entities` — the structured query path the design's `cg_case` mandates | Unwritten (owner F3 — these are the axes action-policy scoping will key on) |

### `evidence_items` — temporal, lineage, and lifecycle markers

| Column | Why | Status |
|--------|-----|--------|
| `evidence_time` | The time the evidence subject occurred (a log line at 10:42 vs the source object created at 10:45). Distinct from `created_at_source` (record creation) and `ingested_at` (graph storage time) | Written on the state-transition-event path (`services/event_evidence_service.py:146`) |
| `collected_by` | Agent or human that captured the evidence (for SoD / lineage) | Unwritten |
| `source_type` | Controlled vocab, indexed for filter queries | Written — stamped from the parent `Source` so the chunker registry has a connector key to dispatch on (`workers/extraction_tasks.py:83-97`) |
| `redaction_status` | Per-row marker; free-text `String(30)` with no CHECK behind it (`models/evidence.py:106`) | **Read but unwritten.** All three readers fence on the same pair of values, `pending` and `pending_redaction`: the agent projection hides evidence in either state (`graph/agent/hydrators.py:163-164`), and vector search and episode clustering apply the same fence in SQL (`search/vector_search.py:59-60`; `services/episode_cluster_service.py:87-88`). Redaction runs without stamping the row, so the column is NULL everywhere and those gates can never fire |

Four later columns joined the same table and are worth knowing when reading `0029` alongside current code: `knowledge_support` (`0057`, F4 — validation support that re-ranks retrieval and never filters), `knowledge_state` (`0067` — the source system's own published/draft/retired verdict, withheld rather than demoted), and `case_state` + `source_facets` (`0068` — the source's own resolution verdict and its human-assigned root-cause taxonomy) (`models/evidence.py:146-170`).

### `decisions` — governance axis + verdict

The existing `decision_type` enum is action-oriented (`classify_issue`, `restart_workflow`, …); the design's enum is governance-oriented (`diagnosis`, `recommendation`, `remediation`, `escalation`, `approval_request`, …). Rather than widen the existing enum, `0029` added a parallel axis.

| Column | Why | Status |
|--------|-----|--------|
| `decision_intent` | Design vocab, queryable alongside `decision_type` (`models/decision.py:135-143`) | Written (F1) — `services/decision_trace_service.py:134` |
| `decision_summary` | A single canonical human-readable summary (today it is split across `rationale_summary` / `explanation` / `compact_trace`) | Unwritten — **retire candidate**, since `rationale_summary` already carries it |
| `risk_level` | **Trace-level** risk, distinct from per-option risk on `DecisionOption` (`models/decision.py:146` vs `:193`): "options considered had what risk" vs "the path we took has what risk" | Written (F1) — both, at `services/decision_trace_service.py:135` and `:152` |
| `policy_result` | The verdict the executor checks (`models/decision.py:149`) | Written — the strictest action-policy verdict across the run's steps (`services/execution_service.py:1005`) |

### `decision_trace_events` — cg_decision_step role

| Column | Why | Status |
|--------|-----|--------|
| `decision_id` | Optional FK so a session-scoped trace event can also anchor to a specific decision (`models/session.py:125`) | **Unwritten.** `record_decision_trace_event` sets tenant, session, type, inputs, outputs, reasoning, and confidence — and no decision anchor (`services/session_service.py:154-162`) |
| `tool_name` | The tool a reasoning step invoked (`models/session.py:131`) | **Unwritten on this table.** The `tool_name` the codebase does write belongs to `ToolInvocation`, a different model (`services/execution_service.py:1184-1187`) — another name shadow |
| `tool_input_ref` / `tool_output_ref` | I/O references the design wants on each reasoning step (`models/session.py:132-133`) | Unwritten (owner F5 — these belong with generation provenance) |

All four columns give `decision_trace_events` the design's `cg_decision_step` shape, and none of them is populated. A trace event today is a session-scoped narrative row, not a tool-call ledger — the tool-call ledger is `tool_invocations` plus `execution_attempts`, which is a different table with a different lifecycle.

### `approval_requests` — case/decision anchor + role + channel + SoD

Anchoring was the real gap: `ApprovalRequest.execution_run_id` ties approval to *playbook execution*; the design ties approval to a *decision* (and transitively a case).

| Column | Why | Status |
|--------|-----|--------|
| `case_id` / `decision_trace_id` | Approval addressable from case, decision, or execution | **Both unwritten.** `ApprovalRequest(...)` sets neither (`services/execution_service.py:1250`); approvals are still anchored only through `execution_run_id`. Note the writer register does not flag `case_id`, because its scan is name-based and other models write a column of that name — a name collision costs visibility, not truth |
| `action_name` | Controlled identifier (free-text `requested_action` stays) (`models/execution.py:207`) | Written (F1) |
| `approver_role` | The role consulted, not the user — e.g. `Finance Process Owner`, `AE L2 Support` (`models/execution.py:208`) | Written (F1) |
| `approval_channel` | `teams` / `email` / `servicenow` / `portal` / `manual` | Unwritten — approvals only flow through the API, so there is no channel to record |
| `approval_note` | Reason text from the approval surface | Unwritten — that surface is not built |
| `recommended_by` / `executed_by` / `sod_check_status` / `sod_violation_reason` | Segregation of duties (Section 43.13): the agent that recommended recorded separately from approver and executor | **All four unwritten.** The register flags three; `executed_by` escapes it because `ExecutionStepRun.executed_by` is written and the scan matches on name (`services/execution_service.py:816` is the only `executed_by` write in `src/`). **SoD is enforced only on the initiator↔approver axis** via `forbid_self_approval` (`services/approval_policy_service.py:14, 47`) — never recommender↔approver |

Approvals gained a control `0029` did not anticipate. F7 (migration `0059`) binds an approval to the exact artifact it approved: `artifact_version`, `artifact_hash` (an RFC 8785 canonicalization of the step in its version), `policy_snapshot`, and `expires_at`, plus a trigger making a published version's `steps` immutable. The hash is written at `request_approval` and re-checked at `record_tool_invocation`; a mismatch or an expired approval blocks and emits `approval.binding_violated`. It is a self-consistency check, not a signature — it proves the payload did not change between approval and execution, not who produced it.

### `execution_step_runs` — action_name + idempotency + anchors

| Column | Why | Status |
|--------|-----|--------|
| `case_id` / `decision_trace_id` | Anchors so any action row is queryable from either side of the chain | **Both unwritten.** The step-run constructor sets `execution_run_id` and the F1 governance columns, and neither anchor (`services/execution_service.py:799-817`). A step is reachable from its run, not from its case |
| `action_name` | The controlled identifier the policy engine matches against; `step_title` stays as the human label (`models/execution.py:112-115`) | Written (F1) — and it is what unblocked the action-policy engine, since a lookup key that does not exist cannot be looked up |
| `action_type` | `diagnostic` / `remediation` / `notification` / `escalation` / `approval` / `manual` | Written (F1) |
| `execution_mode` | Denormalised from `ExecutionRun.automation_mode` so each step row is self-describing without a join | Written (F1) |
| `executed_by` | Promoted from `ExecutionRun.initiated_by` to step level, since execution mode can change per step | Written (F1) |
| `idempotency_key` | Partial unique index (`UNIQUE WHERE idempotency_key IS NOT NULL`); NULL keys unconstrained so legacy rows and read-only steps need none (`models/execution.py:123`) | **Written since F8** (`services/execution_service.py:403-493`) |
| `duplicate_check_status` | `passed` / `duplicate` / `not_applicable` (`services/idempotency_service.py:42-44`) | **Written since F8** |

F8 also added `execution_attempts` (migration `0060`) — one row per try, with `deduplicated` / `timeout` / `cancelled` kept distinct from `failed`. A duplicate is *skipped and recorded*, then refused again at `record_tool_invocation`. The request body for those routes deliberately carries no attempt number and no idempotency key: both are derived from what is already recorded, because a caller that can hand in the key the duplicate check tests against can defeat the control by asserting the answer.

### `graph_edges` — temporal validity

| Column | Why | Status |
|--------|-----|--------|
| `valid_from` / `valid_to` | Enables "what was true at incident time?" (Section 43.1.4). `valid_to` nullable, so an open edge behaves as "valid since creation, no expiry"; a `ck_graph_edges_valid_window` check refuses a window that closes before it opens (`models/pattern.py:183-186, 272-278`) | Written — `graph/builder.py:43, 108` on create, `close_edge` at `:166`, and `cmdb_topology_service.py:235` end-dates upstream-deleted relationships on TTL refresh |
| `confidence` | Semantically distinct from `weight` (importance for traversal): confidence is the belief in the relation, range-checked `0..1` by `ck_graph_edges_confidence_range` (`models/pattern.py:179-182, 279-281`) | Written — `ensure_edge` takes it as a separate argument, and the weight-as-confidence conflation it was added to fix was found in code written days earlier in this same repo |

Two composite indexes carry the temporal lookup — `ix_graph_edges_temporal_source` and `ix_graph_edges_temporal_target`, each `(tenant_id, node_type, node_id, valid_from, valid_to)`, so a point-in-time question can be asked from either end of an edge (`models/pattern.py:216-231`). Three more indexes serve the far more common "current edges only" case, all partial on `WHERE valid_to IS NULL`: a **unique** one, `uq_graph_edges_active_logical`, which is what makes "one open edge per logical tuple" a database guarantee rather than a convention, plus a source-side and a target-side lookup index (`:187-215`). One honesty note carried from the graph review: `as_of` semantics are not fully coherent yet — historical edges combine with *current* node facts, so a caller must not draw historical operational conclusions from a point-in-time traversal.

## What's Deliberately Not in 0029

`0029` was the **first-class** layer. This is what was deferred, and what has happened since.

| Out-of-scope in `0029` | Status 2026-08-19 |
|--------------|-------------------|
| Service-code population of new fields | **Largely landed** via Epic F. The remainder is tracked column-by-column in `tests/test_governance_column_writers.py`, each with an owner |
| `ErrorSignature` / `FixPattern` counter increments | **Code landed, data blocked.** `record_fix_outcome` exists (`services/fix_cohort_service.py:61`); nothing mints a `FixPattern` for it to count |
| `mttr_minutes` calculation | **Landed.** Derived from the session's own timeline at close (`services/case_outcome_service.py:152-159`) |
| Action policy precedence engine (`priority`, `policy_scope`, `conflict_resolution`) | **Landed as F3b.** Scope filter → specificity → conflict resolution, enforced per step (`services/action_policy_service.py:75-203`; `services/execution_service.py:206-286, 788`); versioned by `0064` |
| `cg_source_sync_state`, `cg_audit_log` (extended), `cg_ai_inference_run`, `cg_redaction_event`, `cg_confidence_history`, `cg_workflow_version`, `cg_policy_override`, `cg_output_artifact`, `cg_schedule_observation`, `cg_claim_conflict`, `cg_eval_case`, `cg_tool_call`, `cg_cost_event`, `cg_data_quality_issue` | Section 43 production-hardening tables. Several have local equivalents rather than the design's names — `operational_events` covers cost and inference events, `policy_checks` covers policy evaluation history, `execution_attempts` and `tool_invocations` cover tool calls |
| AutomationEdge connector | **Still absent.** `backend/src/contextedge/connectors/` holds gmail, jira_sm, manageengine, sapphireims, servicenow, teams, zoho_desk (`connectors/registry.py:100-110`) — no `automation_edge`. Blocked on access, not design; the schema is ready when the connector arrives |
| MCP server façade (`context.*` tools) | Still a separate concern. Every design-mandated tool has a REST analogue, full or partial |

Newer deferrals worth adding to the same list: nothing validates a claim, nothing constructs a `FixPattern`, nothing links a decision to the action policy it was judged under (`decision_action_policies`), and no case-number generator exists. Each is in the writer register with an owner.

## Migration Behaviour

| Property | Value |
|----------|-------|
| Revision | `0029_ae_ops_concept_alignment` |
| Down-revision | `0028_orm_ddl_drift_alignment` |
| Position in the chain | Long superseded — this is **not** the head. Run `alembic heads`; never quote a number from a document |
| Idempotent | Yes — `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `pg_constraint` lookups guard FK adds |
| Reversible | Yes — `downgrade()` drops in reverse order, `IF EXISTS` everywhere |
| Backfill required | No — every new column is nullable |
| Service code changes | None **in `0029`**. The writers landed later, per the status table above |
| Constraints captured in the drift snapshot | Yes — `tests/test_review_orm_ddl_drift.py::_EXPECTED_MARKERS` was updated with it |
| Test impact **at the time `0029` landed** | 459/459 passed after the snapshot update. That number is historical — the suite has grown by roughly a factor of three since; do not read it as a current count |
| Superseded by | `0031_maf_context_graph_hardening` tenant-scoped two of its uniqueness rules (`entities`, `resolution_sessions.case_number`); `0043` added normalized entity traits; `0060` made the idempotency key live |

## Acme VPN incident (this layer)

When Acme's `INC0010427` — VPN users unable to connect to `vpn-gw-east-01` — flows through the pipeline, six things `0029` provisioned get written and three stay empty. That contrast is the fastest way to see where the alignment stands.

**Input** — an evidence item from the ServiceNow ticket, after normalization:

```json
{
  "id": "b1f0…",
  "title": "INC0010427 — VPN users unable to connect",
  "body_text": "…gateway log: 2026-03-15 09:21:44 ERROR ike_auth: AUTH_CERT_EXPIRED cert serial 0x4A2F for vpn-gw-east-01 …",
  "source_type": "servicenow",
  "evidence_type": "ticket",
  "relevance_state": "operational",
  "case_state": null
}
```

**Output** — what the `0029` layer writes for it:

```json
{
  "entities": [
    {"entity_type": "configuration_item", "external_system": "servicenow",
     "external_id": "3f1a…(cmdb_ci sys_id)", "name": "vpn-gw-east-01",
     "source_ref": {"system": "servicenow", "sys_id": "3f1a…"}, "confidence": 1.0}
  ],
  "error_signatures": [
    {"signature_key": "HEX_CODE_ERROR_IKE_AUTH_AUTH_CERT_EXPIRED_CERT_SERIAL_VPN",
     "error_type": "HEX_CODE",
     "normalized_message": "<date> <time> ERROR ike_auth: AUTH_CERT_EXPIRED cert serial <hex> for vpn-gw-east-01",
     "display_name": "2026-03-15 09:21:44 ERROR ike_auth: AUTH_CERT_EXPIRED cert serial 0x4A2F for vpn-gw-east-01",
     "example_messages": ["2026-03-15 09:21:44 ERROR ike_auth: AUTH_CERT_EXPIRED cert serial 0x4A2F for vpn-gw-east-01"]}
  ],
  "claims": [
    {"claim_type": "probable_root_cause",
     "claim_text": "The VPN gateway certificate expired",
     "confidence": 0.72,
     "validation_status": "unverified",
     "created_by": "relevance_v3", "created_by_type": "agent"}
  ],
  "graph_edges": [
    {"source": "evidence:b1f0…", "edge_type": "exhibits", "target": "error_signature:HEX_CODE_ERROR_IKE_AUTH_…"},
    {"source": "claim:…",        "edge_type": "supported_by", "target": "evidence:b1f0…"},
    {"source": "evidence:b1f0…", "edge_type": "affects_ci",   "target": "entity:vpn-gw-east-01"}
  ],
  "claim_evidence": [],
  "fix_patterns": []
}
```

When the responder closes the resolution session and asserts what it meant, the case layer fills in too:

```json
{
  "case_state_transitions": [
    {"from_status": "open", "to_status": "closed", "transitioned_by": "eng-42"}
  ],
  "case_outcomes": [
    {"outcome_status": "resolved",
     "confirmed_root_cause": "Expired TLS certificate on vpn-gw-east-01",
     "successful_action": "renew_certificate_and_restart_radius",
     "failed_actions": ["restart_vpn_service"],
     "mttr_minutes": 96.4,
     "user_confirmed": true}
  ],
  "case_outcome_fix_patterns": []
}
```

The signature key is worth a second look, because it shows the classifier's tier order rather than the reader's intuition. The line looks like a log-severity line, and `LOG_ERROR` is a real `error_type` — but `_classify_line` checks the **vendor-code** tier before the log-severity tier, and `0x4A2F` matches the hex-code rule, so the type is `HEX_CODE` (`services/error_signature_service.py:97-121`). The key is then the type plus the first six salient tokens of the normalized message, with placeholders, stopwords and bare numbers dropped (`:124-146`). Note also that `<host>` only ever replaces an IP address, so a hostname like `vpn-gw-east-01` survives normalization and becomes part of the identity — two gateways produce two signatures, which is the intended precision-over-recall trade.

Note also `from_status: "open"` rather than anything richer. The session went straight from `open` to `closed` because those are the only two statuses anything sets — the middle of `CASE_STATUSES` is vocabulary, not behaviour.

Then read the three empty arrays. `claim_evidence` is empty because the claim↔evidence relationship is carried by a graph edge, and the relational link table has no constructor. `fix_patterns` is empty because nothing mints one, ever. And `case_outcome_fix_patterns` is empty *for that reason*: `successful_action` names an action that no `FixPattern` row represents, so the outcome is recorded, the learning loop is not closed, and the counters in `fix_cohort_service` have nothing to count. Everything needed to close that loop exists except the thing that mints the row.

Meanwhile the `resolution_sessions` case spine stays NULL for this incident, because nothing writes any of it: not `case_number`, `case_type`, `issue_type` or `severity`; not `title`, `description`, `priority` or `environment`; and not the four entity FKs that would let you ask "every case on `vpn-gw-east-01` this quarter" as a structured query rather than a graph traversal.

## Key Files

| File | Purpose |
|------|---------|
| `backend/alembic/versions/0029_ae_ops_concept_alignment.py` | The migration (`entities` created at `:70`, its original global key at `:88`, the claims partial index at `:132-134`) |
| `backend/alembic/versions/0031_maf_context_graph_hardening.py:78-101` | Tenant-scoped the `entities` and `case_number` natural keys |
| `backend/src/contextedge/models/entity.py:32-62, 65-101` | `ENTITY_TYPES` registry, `Entity`, tenant-scoped unique key, `0043` traits |
| `backend/src/contextedge/models/claim.py:34, 46, 57, 111, 145, 183` | `CLAIM_TYPES`, `VALIDATION_STATUSES`, `Claim`, `ClaimEvidence`, `DecisionEvidence`, `DecisionClaim` |
| `backend/src/contextedge/models/action_policy.py:37-54, 133` | `RISK_LEVELS`, `POLICY_RESULTS`, `EXECUTION_MODES`, `ActionPolicy`, `DecisionActionPolicy` |
| `backend/src/contextedge/models/error_signature.py:39, 99` | `ErrorSignature` (unique per tenant at `:42`), `FixPattern` |
| `backend/src/contextedge/models/case_outcome.py:34, 43, 60, 110, 136` | `OUTCOME_STATUSES`, `CASE_STATUSES`, `CaseOutcome`, `CaseStateTransition`, `CaseOutcomeFixPattern` |
| `backend/src/contextedge/models/session.py:51-60, 125-133` | Case-spine columns + four entity FKs on `ResolutionSession`; decision anchor and tool I/O on `DecisionTraceEvent` |
| `backend/src/contextedge/models/evidence.py:94-106, 146-170` | `0029`'s temporal/lineage columns plus the later lifecycle columns |
| `backend/src/contextedge/models/decision.py:135-149, 193` | `decision_intent` / `decision_summary` / `risk_level` / `policy_result`, and `DecisionOption.risk_level` for contrast |
| `backend/src/contextedge/models/execution.py:112-138, 203-216, 229-241` | Step action name + idempotency; approval action/role/channel/SoD; the case and decision anchors on both |
| `backend/src/contextedge/models/pattern.py:174-186, 272-281` | `GraphEdge` check constraints, validity window and confidence |
| **Writers** | |
| `backend/src/contextedge/services/claim_service.py:44-107` | `normalize_claim_text`, `persist_extracted_claims` — claims + `supported_by` edges, no extra LLM call |
| `backend/src/contextedge/services/error_signature_service.py:149-256` | `extract_error_fingerprints`, `fingerprint_evidence` — deterministic, fail-soft, runs on every item |
| `backend/src/contextedge/services/case_outcome_service.py:38-248` | `get_case_history`, `record_case_transition`, `record_case_outcome` |
| `backend/src/contextedge/services/session_service.py:198-238` | Where a session close becomes a transition and an outcome |
| `backend/src/contextedge/services/action_policy_service.py:75-203` | `restrictiveness`, `specificity`, `select_policy`, `evaluate_action` |
| `backend/src/contextedge/services/execution_service.py:206-286, 403-493, 788, 828, 1005` | Action-policy enforcement and its per-step call site, idempotency-key assignment and its call site, strictest verdict onto the decision |
| `backend/src/contextedge/services/idempotency_service.py:47-96` | `needs_idempotency_key`, `derive_idempotency_key`, `find_duplicate` |
| `backend/src/contextedge/services/fix_cohort_service.py:61-123` | `record_fix_outcome` — built, currently unreachable |
| `backend/src/contextedge/api/v1/action_policies.py:144` | The authoring surface — a policy table nobody can author is a vocabulary, not a control |
| **Guards** | |
| `backend/tests/test_governance_column_writers.py:47-236` | The writer register: every unwritten column with an owner and a reason, plus `SHADOWED_BY_NAME` for columns a name collision hid from the scan |
| `backend/tests/test_review_orm_ddl_drift.py` | DDL drift snapshot |
| `backend/tests/test_entity_type_registry.py` | Fails if a reference service writes an unregistered entity type |
| `backend/tests/test_orm_migration_column_parity.py` | Fails when a model declares a column no migration creates |
| **Docs** | |
| `docs/MIGRATIONS.md` | Notable revision entry for `0029` |
| `docs/TECHNICAL_BLUEPRINT.md` §11 | Logical data model — group 9 |

## Further reading

- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the live gap ledger, including the `FixPattern` and claim-validation entries
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — how approvals, policy checks, and the step ledger behave at run time
- [16-decision-traces.md](./16-decision-traces.md) — the decision object this migration added a governance axis to
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — edge registry, validity windows, and the maf.v1 projection
- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) — which of these node types an operator can actually see in Graph Explorer
- [BACKLOG.md](./BACKLOG.md) — Epic F milestones that carried most of the writers listed here
- [docs/MIGRATIONS.md](../docs/MIGRATIONS.md) — how to run and verify the chain
