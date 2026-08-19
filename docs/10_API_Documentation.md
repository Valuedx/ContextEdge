# ContextEdge — API Documentation

> Generated from the FastAPI OpenAPI schema by `backend/generate_docs.py`.
> Do not hand-edit — re-run the script after route changes.
> The interactive reference is always available at `/docs` (Swagger UI)
> and `/redoc` on a running backend.

## API Overview

- **Base URL**: `/api/v1`
- **Authentication**: JWT Bearer token in the `Authorization` header
  (obtained via `POST /api/v1/auth/login` with `{"email", "password"}`),
  or `X-Service-Token` for configured service accounts.
- **Error format**: `{"detail": "message"}` with a matching HTTP status.


## `action-policies`

### `GET /api/v1/action-policies`

List Action Policies

- **Auth**: required
- **Response**: `list[ActionPolicyResponse]`
- **Query parameters**: `action_name`

### `POST /api/v1/action-policies`

Create Action Policy

- **Auth**: required
- **Request body**: `ActionPolicyCreate`
- **Response**: `ActionPolicyResponse`

### `PATCH /api/v1/action-policies/{policy_id}`

Update Action Policy

- **Auth**: required
- **Request body**: `ActionPolicyUpdate`
- **Response**: `ActionPolicyResponse`

### `DELETE /api/v1/action-policies/{policy_id}`

Delete Action Policy

- **Auth**: required


## `admin`

### `GET /api/v1/admin/llm-usage`

Admin Llm Usage

Per-tenant LLM usage + cost aggregation.

Returns headline totals (requests, tokens split by prompt/completion/
cached, estimated USD, cache hit rate) plus a top-N breakdown by
(model, task). Intended for the admin cost dashboard.

- **Auth**: required
- **Response**: `LlmUsageResponse`
- **Query parameters**: `window_hours`, `top_n_breakdown`, `all_time`, `sync_run_id`

### `GET /api/v1/admin/pipeline-health`

Admin Pipeline Health

Queue depths, throughput, latency and the graph chain, in one read.

Separate from `/llm-usage` because the question is different: that one
asks what the run cost, this one asks whether it is getting anywhere.
A run can be spending steadily and producing nothing — that is exactly
the failure this exists to make visible.

- **Auth**: required

### `GET /api/v1/admin/tenant-budget`

Get Tenant Budget

Return the caller's tenant's LLM budget, or ``null`` if none
is configured (= uncapped).

- **Auth**: required

### `PUT /api/v1/admin/tenant-budget`

Put Tenant Budget

Create or replace the caller's tenant's LLM budget.

- **Auth**: required
- **Request body**: `TenantBudgetUpsert`
- **Response**: `TenantBudgetResponse`

### `GET /api/v1/admin/tenant-budget/status`

Get Tenant Budget Status

Live view: budget config + current-day usage + whether the next
LLM call would be allowed. Powers the dashboard header — no extra
round trip to compose cap + usage.

- **Auth**: required
- **Response**: `TenantBudgetStatus`


## `audit-logs`

### `GET /api/v1/audit-logs`

List Audit Logs

- **Auth**: required
- **Response**: `list[AuditLogResponse]`
- **Query parameters**: `action`, `resource_type`, `actor_id`, `from_date`, `to_date`, `limit`, `offset`


## `auth`

### `POST /api/v1/auth/login`

Login

- **Auth**: not required
- **Request body**: `LoginRequest`
- **Response**: `TokenResponse`


## `contradictions`

### `GET /api/v1/contradictions`

List Contradictions

- **Auth**: required
- **Response**: `list[ContradictionResponse]`
- **Query parameters**: `resolution_status`, `contradiction_type`, `limit`, `offset`

### `PATCH /api/v1/contradictions/{contradiction_id}/status`

Update Contradiction Status

- **Auth**: required
- **Request body**: `ContradictionStatusUpdate`
- **Response**: `ContradictionResponse`


## `correlations`

### `GET /api/v1/correlations`

List Correlations

- **Auth**: required
- **Response**: `list[CorrelationEdgeResponse]`
- **Query parameters**: `evidence_id`, `correlation_type`, `limit`, `offset`

### `POST /api/v1/correlations`

Create Manual Correlation

- **Auth**: required
- **Request body**: `CorrelationEdgeCreate`
- **Response**: `CorrelationEdgeResponse`

### `GET /api/v1/correlations/fleet-suggestions`

List Fleet Suggestions

- **Auth**: required
- **Query parameters**: `status`, `limit`

### `POST /api/v1/correlations/fleet-suggestions/{suggestion_id}/accept`

Accept Fleet Suggestion

Reviewer accept (B6): mints the parent fleet case and attaches
every member — grouping only ever happens through this gate.

- **Auth**: required

### `POST /api/v1/correlations/fleet-suggestions/{suggestion_id}/reject`

Reject Fleet Suggestion

- **Auth**: required
- **Response**: `StatusResponse`

### `GET /api/v1/correlations/suggestions`

List Suggestions

- **Auth**: required
- **Response**: `list[CorrelationSuggestionResponse]`
- **Query parameters**: `status`, `limit`, `offset`

### `GET /api/v1/correlations/suggestions/stats`

Suggestion Stats

Reviewer-outcome aggregates per source pair and corroborator
type (C1). The per-pair learned floors derive from these counts —
visible here so a raised bar is never a mystery.

- **Auth**: required

### `POST /api/v1/correlations/suggestions/{suggestion_id}/accept`

Accept Correlation Suggestion

- **Auth**: required
- **Response**: `CorrelationEdgeResponse`

### `POST /api/v1/correlations/suggestions/{suggestion_id}/reject`

Reject Correlation Suggestion

- **Auth**: required
- **Response**: `StatusResponse`

### `PATCH /api/v1/correlations/{correlation_id}`

Update Correlation

- **Auth**: required
- **Request body**: `CorrelationEdgeUpdate`
- **Response**: `CorrelationEdgeResponse`

### `DELETE /api/v1/correlations/{correlation_id}`

Delete Correlation

- **Auth**: required

### `POST /api/v1/correlations/{correlation_id}/decision`

Decide Correlation

- **Auth**: required
- **Request body**: `CorrelationDecisionRequest`
- **Response**: `StatusResponse`


## `decisions`

### `GET /api/v1/decisions`

List Decisions

- **Auth**: required
- **Response**: `list[DecisionResponse]`
- **Query parameters**: `session_id`, `decision_type`, `agent_step`, `status`, `actor_type`, `min_confidence`, `max_confidence`, `sort`, `limit`, `offset`

### `POST /api/v1/decisions`

Create Decision

- **Auth**: required
- **Request body**: `DecisionCreate`
- **Response**: `DecisionResponse`

### `GET /api/v1/decisions/effectiveness`

Get Effectiveness

- **Auth**: required
- **Query parameters**: `decision_type`, `workflow`, `environment`, `impacted_dependency`

### `GET /api/v1/decisions/similar`

Find Similar Decisions

- **Auth**: required
- **Response**: `list[DecisionResponse]`
- **Query parameters**: `decision_type`, `workflow`, `environment`, `impacted_dependency`, `query_decision_id`, `query_text`, `limit`

### `GET /api/v1/decisions/similar/aggregate`

Find Similar Decisions With Aggregate

Top-N similar decisions + total count + outcome aggregate in one call.

Powers Zone 5's "based on 143 similar tickets, 87% succeeded" provenance
line alongside the top few examples — no client-side fan-out across
`/similar`, `/effectiveness`, and a count endpoint. When `query_decision_id`
or `query_text` is set, the top-N list is ordered semantically via
pgvector cosine distance; count and outcomes remain scoped by
`decision_type` + structural context filters.

- **Auth**: required
- **Response**: `SimilarDecisionsAggregateResponse`
- **Query parameters**: `decision_type`, `workflow`, `environment`, `impacted_dependency`, `query_decision_id`, `query_text`, `limit`

### `GET /api/v1/decisions/{decision_id}`

Get Decision

- **Auth**: required
- **Response**: `DecisionResponse`

### `GET /api/v1/decisions/{decision_id}/chain`

Get Decision Chain

- **Auth**: required
- **Response**: `DecisionChainResponse`

### `POST /api/v1/decisions/{decision_id}/outcome`

Create Outcome

- **Auth**: required
- **Request body**: `DecisionOutcomeCreate`
- **Response**: `DecisionOutcomeResponse`

### `GET /api/v1/decisions/{decision_id}/provenance`

Get Decision Provenance

Hydrate a decision's `based_on` references for Zone 5 drill-in.

- **Auth**: required
- **Response**: `DecisionProvenanceResponse`
- **Query parameters**: `evidence_limit`, `episode_limit`, `pattern_limit`

### `POST /api/v1/decisions/{decision_id}/reject`

Reject Decision

Reject an AI-recommended decision with a structured reason code.

- **Auth**: required
- **Request body**: `DecisionRejectRequest`
- **Response**: `DecisionOutcomeResponse`


## `domains`

### `GET /api/v1/domains`

List Domains

- **Auth**: required
- **Response**: `list[DomainResponse]`
- **Query parameters**: `workspace_id`, `limit`, `offset`

### `POST /api/v1/domains`

Create Domain

- **Auth**: required
- **Request body**: `DomainCreate`
- **Response**: `DomainResponse`

### `GET /api/v1/domains/{domain_id}`

Get Domain

- **Auth**: required
- **Response**: `DomainResponse`

### `PATCH /api/v1/domains/{domain_id}`

Update Domain

- **Auth**: required
- **Request body**: `DomainUpdate`
- **Response**: `DomainResponse`


## `drift`

### `GET /api/v1/drift/alerts`

Get Drift Alerts

Evaluate drift heuristics for this tenant and return playbook-level alerts.

Read-only: does not change playbook lifecycle. Expiry transitions run on the Celery drift task.

- **Auth**: required
- **Response**: `list[DriftAlertResponse]`


## `episodes`

### `GET /api/v1/episodes`

List Episodes

Episodes for review, newest first.

Superseded episodes are excluded unless asked for. Reconstruction
replaces its own drafts as more of a thread arrives, and a superseded
row is by definition the version that was replaced — 138 of 253 on
the live graph, 54%. Returned alongside current ones with nothing but
a status badge to tell them apart, they are indistinguishable from
the answer, and the replaced draft is usually the WORSE one: the
ActiveMQ example conflated two incidents and recorded two complaints
and no remediation, where its replacement recorded the broker bounce
and the seven failed executions.

Asking for them explicitly still works, either through
``include_superseded`` or by naming the state — a reviewer auditing
what changed needs to see them.

- **Auth**: required
- **Response**: `list[EpisodeResponse]`
- **Query parameters**: `domain_id`, `status`, `reviewer_state`, `include_superseded`, `sort`, `limit`, `offset`

### `POST /api/v1/episodes/ai-review`

Dispatch Ai Review

Dispatch the AI first-pass review sweep for this tenant.

The sweep reviews pending drafts in review-priority order and stamps
each with a verdict; in ``auto_approve`` mode it also approves the
subset clearing the deterministic floors. The configured mode is a
deployment setting (EPISODE_AI_REVIEW); this endpoint can run a
weaker mode than configured, never a stronger one — with the setting
``off`` the dispatch always runs advisory-only.

- **Auth**: required
- **Response**: `TaskDispatchResponse`
- **Query parameters**: `limit`, `advisory`

### `POST /api/v1/episodes/bulk-approve`

Bulk Approve Episodes

- **Auth**: required
- **Request body**: `EpisodeBulkApproveRequest`

### `POST /api/v1/episodes/reconstruct`

Trigger Manual Reconstruction

Manually trigger episode reconstruction from evidence.

- **Auth**: required
- **Request body**: `ReconstructRequest`
- **Response**: `TaskDispatchResponse`

### `GET /api/v1/episodes/{episode_id}`

Get Episode

- **Auth**: required
- **Response**: `EpisodeDetail`

### `PATCH /api/v1/episodes/{episode_id}`

Update Episode

- **Auth**: required
- **Request body**: `EpisodeUpdate`
- **Response**: `EpisodeResponse`

### `DELETE /api/v1/episodes/{episode_id}`

Delete Episode

Permanently delete an episode and its steps.

- **Auth**: required

### `POST /api/v1/episodes/{episode_id}/approve`

Approve Episode

- **Auth**: required
- **Response**: `EpisodeResponse`

### `POST /api/v1/episodes/{episode_id}/evidence/{evidence_id}`

Add Episode Evidence

Reviewer action (P0): attach evidence the cluster missed. Updates
both the JSONB list and the normalized provenance link.

- **Auth**: required
- **Response**: `EpisodeResponse`

### `DELETE /api/v1/episodes/{episode_id}/evidence/{evidence_id}`

Remove Episode Evidence

Reviewer action (P0): detach evidence that does not belong to
this episode (mis-correlated or split-off content).

- **Auth**: required
- **Response**: `EpisodeResponse`

### `PATCH /api/v1/episodes/{episode_id}/steps/{step_id}`

Update Episode Step

- **Auth**: required
- **Request body**: `EpisodeStepUpdate`
- **Response**: `EpisodeStepResponse`


## `evaluations`

### `GET /api/v1/evaluations/datasets`

List Datasets

- **Auth**: required
- **Response**: `list[EvalDatasetResponse]`
- **Query parameters**: `limit`

### `POST /api/v1/evaluations/datasets`

Create Dataset

- **Auth**: required
- **Request body**: `EvalDatasetCreate`
- **Response**: `EvalDatasetResponse`

### `GET /api/v1/evaluations/runs`

List Runs

- **Auth**: required
- **Response**: `list[EvalRunResponse]`
- **Query parameters**: `limit`

### `POST /api/v1/evaluations/runs`

Create Run

- **Auth**: required
- **Request body**: `EvalRunCreate`
- **Response**: `EvalRunResponse`

### `GET /api/v1/evaluations/runs/{run_id}`

Get Run

- **Auth**: required
- **Response**: `EvalRunResponse`


## `evidence`

### `GET /api/v1/evidence`

Search Evidence

- **Auth**: required
- **Response**: `list[EvidenceItemResponse]`
- **Query parameters**: `query`, `source_id`, `relevance_state`, `evidence_type`, `source_type`, `domain_id`, `limit`, `offset`

### `POST /api/v1/evidence/bulk-delete`

Bulk Delete Evidence

Permanently delete multiple evidence items.

Authorization happens on the RESOLVED set, not the request: dependency
deletion used to run against caller-supplied UUIDs before any tenant
check, so a caller could delete another tenant's correlation edges and
attachments by guessing (or leaking) evidence ids. Every id must resolve
inside the caller's tenant before anything is touched, and evidence under
legal hold refuses deletion outright — a hold that can be cleared by the
delete button is not a hold.

- **Auth**: required
- **Request body**: `EvidenceBulkDeleteRequest`

### `DELETE /api/v1/evidence/purge`

Purge Evidence

Permanently delete ALL evidence records for the current tenant.

- **Auth**: required

### `GET /api/v1/evidence/{evidence_id}`

Get Evidence

- **Auth**: required
- **Response**: `EvidenceItemDetail`

### `DELETE /api/v1/evidence/{evidence_id}`

Delete Evidence

Permanently delete an evidence item.

- **Auth**: required

### `PATCH /api/v1/evidence/{evidence_id}/access-policy`

Update Access Policy

- **Auth**: required
- **Request body**: `EvidenceAccessPolicyUpdate`
- **Response**: `EvidenceItemDetail`

### `GET /api/v1/evidence/{evidence_id}/attachments`

List Evidence Attachments

- **Auth**: required
- **Response**: `list[AttachmentArtifactResponse]`

### `GET /api/v1/evidence/{evidence_id}/context`

Get Evidence Context

Retrieve resolved source name, domain name, and linked Episode/Pattern
knowledge graph context.

- **Auth**: required
- **Response**: `EvidenceContextResponse`

### `PATCH /api/v1/evidence/{evidence_id}/relevance`

Update Relevance

- **Auth**: required
- **Response**: `StatusResponse`
- **Query parameters**: `relevance_state`


## `execution`

### `GET /api/v1/execution/approvals/pending`

List Pending Approvals

- **Auth**: required
- **Response**: `list[ApprovalRequestResponse]`
- **Query parameters**: `limit`

### `POST /api/v1/execution/runs`

Create Execution Run

Start governed execution of an approved playbook.

- **Auth**: required
- **Request body**: `StartExecutionRequest`
- **Response**: `ExecutionRunResponse`

### `GET /api/v1/execution/runs`

List Runs

- **Auth**: required
- **Response**: `list[ExecutionRunResponse]`
- **Query parameters**: `session_id`, `playbook_id`, `status`, `limit`

### `GET /api/v1/execution/runs/{run_id}`

Get Run

- **Auth**: required
- **Response**: `ExecutionRunResponse`

### `POST /api/v1/execution/runs/{run_id}/abort`

Abort Run

Abort a running execution.

- **Auth**: required
- **Response**: `ExecutionRunResponse`

### `POST /api/v1/execution/runs/{run_id}/approvals/{approval_id}/decide`

Decide On Approval

Approve or deny a pending approval request.

- **Auth**: required
- **Request body**: `ApprovalDecision`
- **Response**: `ApprovalRequestResponse`

### `POST /api/v1/execution/runs/{run_id}/approvals/{approval_id}/modify`

Modify On Approval

Approve a pending approval request with modifications to the step's inputs.

- **Auth**: required
- **Request body**: `ApprovalModificationRequest`
- **Response**: `ApprovalRequestResponse`

### `POST /api/v1/execution/runs/{run_id}/complete`

Complete Run

Mark execution as completed with an outcome.

- **Auth**: required
- **Response**: `ExecutionRunResponse`
- **Query parameters**: `outcome`, `outcome_summary`

### `POST /api/v1/execution/runs/{run_id}/steps/{step_run_id}/complete`

Complete Step

Close a step run. An `error_message` records it as failed.

A step still awaiting its approval cannot be reported complete — that
would let `complete_execution`'s open-steps check pass with an undecided
approval sitting under it.

- **Auth**: required
- **Request body**: `StepCompletionRequest`
- **Response**: `ExecutionStepRunResponse`

### `POST /api/v1/execution/runs/{run_id}/steps/{step_run_id}/invocations`

Record Invocation

Record a tool call the executor made for this step.

This is where F7 and F8 actually bite: the approved artifact is
re-checked at the last moment before the call is accepted, a step already
recognised as a duplicate is refused, and the attempt is numbered from
what is already recorded rather than from anything the caller says. Both
refusals are 409 — the request is well-formed, the state says no.

- **Auth**: required
- **Request body**: `ToolInvocationRequest`
- **Response**: `ToolInvocationResponse`


## `graph`

### `POST /api/v1/graph/agent-subsets`

Create Agent Graph Subset

Return a ranked, bounded, authorization-filtered agent graph projection.

- **Auth**: required
- **Request body**: `AgentGraphRequest`
- **Response**: `AgentGraphSubset`

### `GET /api/v1/graph/change-risk`

Change Risk

Deterministic change-risk profile for a CI from operational history:
change→incident blame rate, incident pressure, alert activity, and
cached blast radius — every factor explained.

- **Auth**: required
- **Query parameters**: `ci`, `window_days`

### `GET /api/v1/graph/cmdb-topology`

Cmdb Topology

Live ±1-hop CMDB neighborhood for a CI, write-through cached into
entities / graph_edges; falls back to the cached view (marked stale)
when ServiceNow is unreachable.

- **Auth**: required
- **Query parameters**: `ci`

### `GET /api/v1/graph/edge-proposals`

List Edge Proposals Endpoint

Pending agent-proposed dependencies awaiting review. Proposals
never enter the maf.v1 projection; this queue is how they become
authored topology (or audit history).

- **Auth**: required
- **Query parameters**: `limit`

### `POST /api/v1/graph/edge-proposals/{edge_id}/approve`

Approve Edge Proposal Endpoint

Promote a proposal to an authored depends_on edge with review
provenance; the proposal edge closes (supersede, never delete).

- **Auth**: required
- **Query parameters**: `note`

### `POST /api/v1/graph/edge-proposals/{edge_id}/reject`

Reject Edge Proposal Endpoint

- **Auth**: required
- **Query parameters**: `note`

### `GET /api/v1/graph/fix-applicability`

Fix Applicability

Deterministic fix-applicability assessment for a CI: which known
fixes validate against its recorded traits, at which level of the
7-level ladder, and whether review is required (B4).

- **Auth**: required
- **Query parameters**: `ci`

### `POST /api/v1/graph/fix-outcomes`

Record Fix Outcome Endpoint

Record a fix outcome against a CI (B5): updates per-cohort
counters and mints review-gated promotion candidates when the
ladder's thresholds are met. Scope only broadens via review.

- **Auth**: required
- **Query parameters**: `fix_pattern_id`, `ci`, `success`

### `GET /api/v1/graph/neighbors`

Graph Neighbors

Return neighboring nodes reachable via graph edges up to *max_depth* hops.

- **Auth**: required
- **Query parameters**: `node_type`, `node_id`, `edge_type`, `max_depth`, `domain_id`, `as_of`

### `GET /api/v1/graph/stats`

Graph Stats

Return aggregate edge-type and node-type counts for the tenant.

- **Auth**: required
- **Query parameters**: `domain_id`, `as_of`

### `GET /api/v1/graph/subgraph/{entity_type}/{entity_id}`

Graph Subgraph

Return the subgraph around any entity as nodes + edges suitable for visualization.

- **Auth**: required
- **Query parameters**: `max_depth`, `domain_id`, `as_of`


## `health`

### `GET /health`

Health

- **Auth**: not required


## `identities`

### `GET /api/v1/identities`

List Identities

- **Auth**: required
- **Response**: `list[IdentityResponse]`
- **Query parameters**: `entity_type`, `active_only`, `query`, `resolution_state`, `limit`, `offset`

### `POST /api/v1/identities/merge`

Merge Identities

- **Auth**: required
- **Request body**: `IdentityMergeRequest`
- **Response**: `IdentityResponse`

### `GET /api/v1/identities/merge-proposals`

List Merge Proposals

Pairs a reconciliation pass believes are the same thing.

Per-mention resolution cannot find these — it only ever sees
candidates sharing a substring with the incoming name, so an acronym
and its expansion are never presented together and fork into two
identities.

- **Auth**: required
- **Response**: `list[IdentityMergeProposalResponse]`
- **Query parameters**: `status`, `limit`

### `POST /api/v1/identities/merge-proposals/{proposal_id}/decide`

Decide Merge Proposal

Accept a proposal (performing the merge) or reject it.

A rejection is as valuable as an acceptance: it is what stops the
next scheduled run from raising the same pair again.

- **Auth**: required
- **Request body**: `IdentityMergeProposalDecision`
- **Response**: `StatusResponse`

### `PATCH /api/v1/identities/{identity_id}`

Update Identity

- **Auth**: required
- **Request body**: `IdentityUpdate`
- **Response**: `IdentityResponse`


## `inventory`

### `POST /api/v1/inventory/report`

Report Inventory

- **Auth**: required
- **Request body**: `InventoryReport`


## `knowledge-supersessions`

### `GET /api/v1/knowledge-supersessions`

List Supersession Proposals

- **Auth**: required
- **Response**: `list[SupersessionProposalResponse]`
- **Query parameters**: `status`, `limit`, `offset`

### `POST /api/v1/knowledge-supersessions/scan`

Scan For Supersessions

Run the filename heuristic over the tenant's knowledge corpus.

Deliberately on demand rather than on a schedule: nothing has reviewed a
proposal yet, and a queue filling itself before anyone reads it is how a
review surface becomes noise. Re-running is safe — an already-decided pair
is never re-proposed, which is what makes a rejection durable.

- **Auth**: required
- **Response**: `list[SupersessionProposalResponse]`
- **Query parameters**: `domain_id`

### `POST /api/v1/knowledge-supersessions/{proposal_id}/decide`

Decide Supersession Proposal

Accept (writes the ``superseded_by`` edge) or reject (durably).

- **Auth**: required
- **Request body**: `SupersessionDecision`
- **Response**: `SupersessionProposalResponse`


## `metrics`

### `GET /metrics`

Metrics

Endpoint that serves Prometheus metrics.

- **Auth**: not required


## `negative-knowledge`

### `GET /api/v1/negative-knowledge`

List Negative Knowledge

- **Auth**: required
- **Response**: `list[NegativeKnowledgeResponse]`
- **Query parameters**: `domain_id`, `status`, `limit`, `offset`

### `POST /api/v1/negative-knowledge`

Create Negative Knowledge

- **Auth**: required
- **Request body**: `NegativeKnowledgeCreate`
- **Response**: `NegativeKnowledgeResponse`

### `PATCH /api/v1/negative-knowledge/{item_id}`

Update Negative Knowledge

- **Auth**: required
- **Request body**: `NegativeKnowledgeUpdate`
- **Response**: `NegativeKnowledgeResponse`

### `DELETE /api/v1/negative-knowledge/{item_id}`

Delete Negative Knowledge

- **Auth**: required


## `notifications`

### `GET /api/v1/notifications`

Get Notifications

- **Auth**: required
- **Response**: `list[NotificationResponse]`
- **Query parameters**: `notification_type`, `unread_only`, `limit`, `offset`

### `PATCH /api/v1/notifications/{notification_id}/read`

Update Notification Read State

- **Auth**: required
- **Request body**: `NotificationReadUpdate`
- **Response**: `NotificationResponse`


## `patterns`

### `GET /api/v1/patterns`

List Patterns

- **Auth**: required
- **Response**: `list[PatternResponse]`
- **Query parameters**: `domain_id`, `active_only`, `limit`, `offset`

### `POST /api/v1/patterns/audit-domains`

Audit Pattern Domains Endpoint

C7: flag pre-guard patterns whose members belong to other
domains. Flags for review via operational events - never deletes;
reviewers fix membership through the pattern-link APIs.

- **Auth**: required

### `POST /api/v1/patterns/cluster`

Trigger Episode Clustering

Trigger background clustering of approved episodes into patterns.

- **Auth**: required
- **Response**: `TaskDispatchResponse`
- **Query parameters**: `domain_id`

### `POST /api/v1/patterns/deduplicate`

Deduplicate Patterns Endpoint

Scan and merge duplicate patterns and playbooks for the user's tenant.

- **Auth**: required

### `POST /api/v1/patterns/discover`

Discover Pattern

Analyze episodes to synthesize a recurring knowledge pattern.

- **Auth**: required
- **Request body**: `PatternDiscoverRequest`
- **Response**: `PatternResponse`

### `GET /api/v1/patterns/{pattern_id}`

Get Pattern

- **Auth**: required
- **Response**: `PatternResponse`

### `DELETE /api/v1/patterns/{pattern_id}`

Delete Pattern

Delete a pattern and its associated evidence links.

- **Auth**: required

### `POST /api/v1/patterns/{pattern_id}/approve`

Approve Pattern

Approve a synthesized knowledge pattern and activate it.

- **Auth**: required
- **Response**: `PatternResponse`

### `GET /api/v1/patterns/{pattern_id}/evidence-links`

List Pattern Evidence Links

- **Auth**: required
- **Response**: `list[PatternEvidenceLinkResponse]`

### `POST /api/v1/patterns/{pattern_id}/evidence-links`

Create Pattern Evidence Link

- **Auth**: required
- **Request body**: `PatternEvidenceLinkCreate`
- **Response**: `PatternEvidenceLinkResponse`

### `DELETE /api/v1/patterns/{pattern_id}/evidence-links/{link_id}`

Delete Pattern Evidence Link

- **Auth**: required

### `GET /api/v1/patterns/{pattern_id}/graph`

Get Pattern Graph

- **Auth**: required
- **Query parameters**: `domain_id`, `as_of`


## `playbooks`

### `GET /api/v1/playbooks`

List Playbooks

- **Auth**: required
- **Response**: `list[PlaybookResponse]`
- **Query parameters**: `lifecycle_state`, `domain_id`, `q`, `limit`, `offset`

### `POST /api/v1/playbooks`

Create Playbook

- **Auth**: required
- **Request body**: `PlaybookCreate`
- **Response**: `PlaybookResponse`

### `POST /api/v1/playbooks/generate`

Generate Playbook

Generate a playbook candidate from a knowledge pattern using AI.

- **Auth**: required
- **Request body**: `GeneratePlaybookRequest`
- **Response**: `PlaybookResponse`

### `GET /api/v1/playbooks/{playbook_id}`

Get Playbook

- **Auth**: required
- **Response**: `PlaybookResponse`

### `PATCH /api/v1/playbooks/{playbook_id}`

Update Playbook

- **Auth**: required
- **Request body**: `PlaybookUpdate`
- **Response**: `PlaybookResponse`

### `GET /api/v1/playbooks/{playbook_id}/references`

Get Playbook References

Retrieve full lineage references (source Pattern, member Episodes, and
Evidence items) for a playbook.

- **Auth**: required

### `POST /api/v1/playbooks/{playbook_id}/rollback`

Rollback Playbook

- **Auth**: required
- **Request body**: `PlaybookRollbackRequest`
- **Response**: `PlaybookVersionResponse`

### `POST /api/v1/playbooks/{playbook_id}/transition`

Transition

- **Auth**: required
- **Request body**: `PlaybookTransition`
- **Response**: `PlaybookResponse`

### `GET /api/v1/playbooks/{playbook_id}/versions`

List Versions

- **Auth**: required
- **Response**: `list[PlaybookVersionResponse]`

### `POST /api/v1/playbooks/{playbook_id}/versions`

Create Version

- **Auth**: required
- **Request body**: `PlaybookVersionCreate`
- **Response**: `PlaybookVersionResponse`

### `GET /api/v1/playbooks/{playbook_id}/versions/{version_id}/diff`

Get Playbook Version Diff

- **Auth**: required
- **Response**: `PlaybookVersionDiffResponse`
- **Query parameters**: `base_version_id`


## `policies`

### `GET /api/v1/policies`

List Policies

- **Auth**: required
- **Response**: `PoliciesGroupedResponse`

### `POST /api/v1/policies`

Create Policy

- **Auth**: required
- **Request body**: `PolicyCreate`
- **Response**: `PolicyRecordResponse`

### `PATCH /api/v1/policies/{policy_id}`

Update Policy

- **Auth**: required
- **Request body**: `PolicyUpdate`
- **Response**: `PolicyRecordResponse`

### `DELETE /api/v1/policies/{policy_id}`

Delete Policy

- **Auth**: required


## `policy-assignments`

### `GET /api/v1/policy-assignments`

List Policy Assignments

- **Auth**: required
- **Response**: `list[PolicyAssignmentResponse]`
- **Query parameters**: `resource_type`, `resource_id`

### `POST /api/v1/policy-assignments`

Assign Policy

- **Auth**: required
- **Request body**: `PolicyAssignmentRequest`
- **Response**: `PolicyAssignmentResponse`

### `DELETE /api/v1/policy-assignments`

Delete Policy Assignment

- **Auth**: required
- **Response**: `PolicyAssignmentResponse`
- **Query parameters**: `resource_type`, `resource_id`, `policy_type`


## `ready`

### `GET /ready`

Ready

Readiness: DB reachable, migrations at head, Redis reachable.

Object storage is reported but does not gate readiness — blob
access is lazy and most request paths work without it.

- **Auth**: not required


## `review-queue`

### `GET /api/v1/review-queue/{session_id}/context`

Get Review Context

- **Auth**: required
- **Response**: `ReviewQueueContext`
- **Query parameters**: `decisions_limit`, `execution_runs_limit`, `events_limit`, `no_cache`


## `runtime`

### `GET /api/v1/runtime/explain/{match_id}`

Runtime Explain

Return stored scoring breakdown and query context for a prior `/match` call.

- **Auth**: required
- **Response**: `RuntimeExplainResponse`

### `POST /api/v1/runtime/feedback`

Submit Feedback

Submit structured feedback on a runtime match result.

- **Auth**: required
- **Request body**: `FeedbackSubmission`
- **Response**: `MutationAckResponse`

### `GET /api/v1/runtime/feedback`

List Feedback

- **Auth**: required
- **Response**: `list[RetrievalFeedbackResponse]`
- **Query parameters**: `playbook_id`, `feedback_type`, `match_id`, `limit`, `offset`

### `POST /api/v1/runtime/match`

Runtime Match

Match case context against approved playbooks with hybrid ranking.

- **Auth**: required
- **Request body**: `RuntimeMatchRequest`
- **Response**: `RuntimeMatchResponse`

### `GET /api/v1/runtime/playbooks/{stable_key}`

Get Runtime Playbook

Fetch an approved playbook by stable key for runtime consumption.

Enforces the same role-based risk tier cap as ``POST /runtime/match`` and optional
domain scope when ``domain_id`` is provided.

- **Auth**: required
- **Response**: `PlaybookVersionResponse`
- **Query parameters**: `version`, `domain_id`


## `sessions`

### `GET /api/v1/sessions`

List Sessions

- **Auth**: required
- **Response**: `list[ResolutionSessionResponse]`
- **Query parameters**: `status`, `domain_id`, `limit`, `offset`

### `POST /api/v1/sessions`

Create Session

- **Auth**: required
- **Request body**: `ResolutionSessionCreate`
- **Response**: `ResolutionSessionResponse`

### `GET /api/v1/sessions/{session_id}`

Get Session

- **Auth**: required
- **Response**: `ResolutionSessionResponse`

### `PATCH /api/v1/sessions/{session_id}/close`

Close Session

- **Auth**: required
- **Response**: `ResolutionSessionResponse`

### `POST /api/v1/sessions/{session_id}/events`

Create Session Event

- **Auth**: required
- **Request body**: `DecisionTraceEventCreate`
- **Response**: `DecisionTraceEventResponse`

### `GET /api/v1/sessions/{session_id}/history`

Session History

Lifecycle history: state transitions (timeline order) and
recorded outcomes (latest first) for one resolution session.

- **Auth**: required


## `skills`

### `GET /api/v1/skills`

List Skills

- **Auth**: required
- **Response**: `list[SkillResponse]`
- **Query parameters**: `skill_key`, `status`, `action_type`, `limit`

### `POST /api/v1/skills`

Create Skill

Register a skill. It lands `draft` and is not invocable until activated.

- **Auth**: required
- **Request body**: `SkillCreate`
- **Response**: `SkillResponse`

### `GET /api/v1/skills/execution-contracts`

List Execution Contracts

- **Auth**: required
- **Response**: `list[ExecutionContractResponse]`

### `POST /api/v1/skills/execution-contracts`

Create Execution Contract

- **Auth**: required
- **Request body**: `ExecutionContractCreate`
- **Response**: `ExecutionContractResponse`

### `GET /api/v1/skills/{skill_id}`

Get Skill

- **Auth**: required
- **Response**: `SkillResponse`

### `PATCH /api/v1/skills/{skill_id}`

Update Skill

Correct a label. Anything that changes what the skill DOES needs a new
version — a playbook was approved against the old definition.

- **Auth**: required
- **Request body**: `SkillUpdate`
- **Response**: `SkillResponse`

### `POST /api/v1/skills/{skill_id}/status`

Change Skill Status

Move a skill through its lifecycle. Retirement is one-way.

- **Auth**: required
- **Request body**: `SkillStatusChange`
- **Response**: `SkillResponse`


## `sources`

### `GET /api/v1/sources`

List Sources

- **Auth**: required
- **Response**: `list[SourceResponse]`
- **Query parameters**: `source_type`, `limit`, `offset`

### `POST /api/v1/sources`

Create Source

- **Auth**: required
- **Request body**: `SourceCreate`
- **Response**: `SourceResponse`

### `POST /api/v1/sources/local-ingest`

Local Ingest

Directly ingest local files from the frontend folder picker.

- **Auth**: required
- **Request body**: `LocalIngestRequest`
- **Response**: `TaskDispatchResponse`

### `GET /api/v1/sources/types`

List Source Types

Selectable source types and whether each one can actually sync.

The source picker renders from this instead of a hardcoded list. The
two had drifted apart in both directions — the UI offered three types
with no connector behind them, and hid two that worked — which is a
drift that a client-side list makes invisible until a user hits it.

- **Auth**: required
- **Response**: `list[SourceTypeResponse]`

### `GET /api/v1/sources/{source_id}`

Get Source

- **Auth**: required
- **Response**: `SourceResponse`

### `PATCH /api/v1/sources/{source_id}`

Update Source

- **Auth**: required
- **Request body**: `SourceUpdate`
- **Response**: `SourceResponse`

### `DELETE /api/v1/sources/{source_id}`

Delete Source

Permanently delete a source and all its associated evidence/logs.

- **Auth**: required

### `POST /api/v1/sources/{source_id}/backfill`

Trigger Backfill

- **Auth**: required
- **Request body**: `BackfillRequest`
- **Response**: `TaskDispatchResponse`

### `POST /api/v1/sources/{source_id}/credentials/rotate`

Rotate Credentials

- **Auth**: required
- **Request body**: `CredentialRotateRequest`
- **Response**: `SourceCredentialResponse`

### `POST /api/v1/sources/{source_id}/discover`

Trigger Discovery

- **Auth**: required
- **Response**: `list[SourceObjectResponse]`

### `GET /api/v1/sources/{source_id}/objects`

List Source Objects

- **Auth**: required
- **Response**: `list[SourceObjectResponse]`
- **Query parameters**: `limit`, `offset`

### `PATCH /api/v1/sources/{source_id}/objects/{object_id}`

Approve Source Object

- **Auth**: required
- **Request body**: `SourceObjectApproval`
- **Response**: `SourceObjectResponse`

### `POST /api/v1/sources/{source_id}/probe-config`

Probe Source Config

D4: verify a config-mapped connector's instance mapping — which
configured endpoints respond and which mapped field names actually
appear in sample payloads. Read-only against the upstream API.

- **Auth**: required

### `GET /api/v1/sources/{source_id}/sync-runs`

List Sync Runs

- **Auth**: required
- **Response**: `list[SyncRunResponse]`
- **Query parameters**: `limit`, `offset`

### `POST /api/v1/sources/{source_id}/sync/control`

Control Sync

Signal the running sync. The job acts on it inside its own loops.

Nothing is killed: a backfill holds records in memory for the length of a
page walk, and terminating the worker would throw away what it has
already paid Zoho for. The cooperative stop persists them and checkpoints,
so `resume` continues instead of restarting.

- **Auth**: required
- **Request body**: `SyncControlRequest`


## `sync-runs`

### `GET /api/v1/sync-runs`

List All Sync Runs

- **Auth**: required
- **Response**: `list[SyncRunResponse]`
- **Query parameters**: `status_filter`, `run_type`, `limit`, `offset`

### `DELETE /api/v1/sync-runs/purge`

Purge Sync Runs

Clear all sync run logs for the tenant.

- **Auth**: required

### `GET /api/v1/sync-runs/{run_id}`

Get Sync Run

- **Auth**: required
- **Response**: `SyncRunResponse`

### `DELETE /api/v1/sync-runs/{run_id}`

Delete Sync Run

Delete a single sync run log.

- **Auth**: required

### `POST /api/v1/sync-runs/{run_id}/retry`

Retry Sync Run

- **Auth**: required


## `tenants`

### `GET /api/v1/tenants`

List Tenants

- **Auth**: required
- **Response**: `list[TenantResponse]`
- **Query parameters**: `limit`, `offset`

### `POST /api/v1/tenants`

Create Tenant

- **Auth**: required
- **Request body**: `TenantCreate`
- **Response**: `TenantResponse`

### `GET /api/v1/tenants/{tenant_id}`

Get Tenant

- **Auth**: required
- **Response**: `TenantResponse`

### `PATCH /api/v1/tenants/{tenant_id}`

Update Tenant

- **Auth**: required
- **Request body**: `TenantUpdate`
- **Response**: `TenantResponse`


## `threads`

### `GET /api/v1/threads`

List Threads

- **Auth**: required
- **Response**: `list[ThreadResponse]`
- **Query parameters**: `source_id`, `hydration_status`, `limit`, `offset`

### `GET /api/v1/threads/{thread_id}`

Get Thread

- **Auth**: required
- **Response**: `ThreadResponse`

### `GET /api/v1/threads/{thread_id}/evidence`

Get Thread Evidence

- **Auth**: required
- **Response**: `list[EvidenceItemResponse]`

### `POST /api/v1/threads/{thread_id}/hydrate`

Trigger Thread Hydration

- **Auth**: required
- **Response**: `TaskDispatchResponse`


## `users`

### `GET /api/v1/users`

List Users

- **Auth**: required
- **Response**: `list[UserResponse]`
- **Query parameters**: `limit`, `offset`

### `POST /api/v1/users`

Create User

- **Auth**: required
- **Request body**: `UserCreate`
- **Response**: `UserResponse`

### `GET /api/v1/users/{user_id}`

Get User

- **Auth**: required
- **Response**: `UserResponse`

### `PATCH /api/v1/users/{user_id}`

Update User

- **Auth**: required
- **Request body**: `UserUpdate`
- **Response**: `UserResponse`

### `POST /api/v1/users/{user_id}/roles`

Assign Role

- **Auth**: required
- **Request body**: `RoleBindingCreate`
- **Response**: `RoleBindingResponse`

### `GET /api/v1/users/{user_id}/roles`

List User Roles

- **Auth**: required
- **Response**: `list[RoleBindingResponse]`

### `DELETE /api/v1/users/{user_id}/roles/{role_binding_id}`

Remove Role

- **Auth**: required


## `workspaces`

### `GET /api/v1/workspaces`

List Workspaces

- **Auth**: required
- **Response**: `list[WorkspaceResponse]`
- **Query parameters**: `limit`, `offset`

### `POST /api/v1/workspaces`

Create Workspace

- **Auth**: required
- **Request body**: `WorkspaceCreate`
- **Response**: `WorkspaceResponse`

### `GET /api/v1/workspaces/{workspace_id}`

Get Workspace

- **Auth**: required
- **Response**: `WorkspaceResponse`

### `PATCH /api/v1/workspaces/{workspace_id}`

Update Workspace

- **Auth**: required
- **Request body**: `WorkspaceUpdate`
- **Response**: `WorkspaceResponse`


---

*192 endpoints across 36 groups, generated from the OpenAPI schema.*
