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


## `admin`

### `GET /api/v1/admin/llm-usage`

Admin Llm Usage

Per-tenant LLM usage + cost aggregation.

Returns headline totals (requests, tokens split by prompt/completion/
cached, estimated USD, cache hit rate) plus a top-N breakdown by
(model, task). Intended for the admin cost dashboard.

- **Auth**: required
- **Response**: `LlmUsageResponse`
- **Query parameters**: `window_hours`, `top_n_breakdown`

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
- **Query parameters**: `session_id`, `decision_type`, `agent_step`, `status`, `min_confidence`, `max_confidence`, `sort`, `limit`, `offset`

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

- **Auth**: required
- **Response**: `list[EpisodeResponse]`
- **Query parameters**: `domain_id`, `status`, `reviewer_state`, `limit`, `offset`

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
- **Query parameters**: `query`, `source_id`, `relevance_state`, `evidence_type`, `domain_id`, `limit`, `offset`

### `POST /api/v1/evidence/bulk-delete`

Bulk Delete Evidence

Permanently delete multiple evidence items.

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

Retrieve resolved source name, domain name, and linked Episode/Pattern knowledge graph context.

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


## `graph`

### `POST /api/v1/graph/agent-subsets`

Create Agent Graph Subset

Return a ranked, bounded, authorization-filtered agent graph projection.

- **Auth**: required
- **Request body**: `AgentGraphRequest`
- **Response**: `AgentGraphSubset`

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
- **Query parameters**: `entity_type`, `active_only`, `query`, `limit`, `offset`

### `POST /api/v1/identities/merge`

Merge Identities

- **Auth**: required
- **Request body**: `IdentityMergeRequest`
- **Response**: `IdentityResponse`

### `PATCH /api/v1/identities/{identity_id}`

Update Identity

- **Auth**: required
- **Request body**: `IdentityUpdate`
- **Response**: `IdentityResponse`


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

### `POST /api/v1/patterns/cluster`

Trigger Episode Clustering

Trigger background clustering of approved episodes into patterns.

- **Auth**: required
- **Response**: `TaskDispatchResponse`
- **Query parameters**: `domain_id`

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
- **Query parameters**: `lifecycle_state`, `domain_id`, `limit`, `offset`

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

Close Session. Accepts an optional body asserting the outcome
(`outcome_status`, `resolution_summary`, `confirmed_root_cause`,
`successful_action`, `failed_actions`, `user_confirmed`,
`fix_results`) — with it the close records a `CaseOutcome`; without it
only the state transition is recorded. Re-closing an already-closed
session is a history no-op.

- **Auth**: required
- **Response**: `ResolutionSessionResponse`

### `POST /api/v1/sessions/{session_id}/events`

Create Session Event

- **Auth**: required
- **Request body**: `DecisionTraceEventCreate`
- **Response**: `DecisionTraceEventResponse`


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

### `GET /api/v1/sources/{source_id}/sync-runs`

List Sync Runs

- **Auth**: required
- **Response**: `list[SyncRunResponse]`
- **Query parameters**: `limit`, `offset`


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

*146 endpoints across 32 groups, generated from the OpenAPI schema.*
