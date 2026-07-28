# Microsoft Agent Framework Context Graph Integration Plan

**Status:** Implemented on the feature branch; verification complete except browser screenshots  
**Branch:** `feature/maf-context-graph-integration`  
**Prepared:** 2026-07-28  
**Target:** Microsoft Agent Framework (MAF) Python 1.12.x  
**Schema baseline:** Alembic `0030_evidence_chunks`; implementation head `0031_maf_context_graph_hardening`

## 1. Objective

Add a pluggable Context Graph integration for Microsoft Agent Framework that:

1. Proactively injects relevant Context Graph knowledge into a MAF agent.
2. Lets a MAF agent query Context Graph through a native function tool.
3. Produces a compact, ranked, security-filtered graph subset optimized for agent use.
4. Supports in-process and remote ContextEdge deployments through the same client contract.
5. Keeps the graph projection layer independent of MAF so it can be reused by other agent frameworks.

The implementation must preserve tenant, domain, workspace, access-policy, legal-hold, and runtime risk boundaries.

The architecture was reviewed before development. Implementation now follows
this plan on `feature/maf-context-graph-integration`.

### Implementation verification

- Backend: 497 tests pass in `backend\venv`.
- MAF: provider and `FunctionTool.invoke` smoke tests pass against
  `agent-framework-core 1.12.1`.
- Database: migration `0031` passes downgrade, upgrade, repeat downgrade, and
  final upgrade on `pgvector/pgvector:pg16`.
- Frontend: six Vitest tests, feature ESLint, TypeScript, and the production
  Next.js build pass.
- Browser screenshots remain unverified because no browser backend is connected
  to this session; `/graph-explorer` responds successfully from the existing
  local development server.

## 2. Current State

ContextEdge already has:

- A PostgreSQL adjacency model in `GraphEdge`.
- Tenant and optional domain-scoped graph traversal.
- Context Graph edges for episodes, patterns, playbooks, identities, decisions, approvals, and executions.
- Hybrid playbook ranking and identity resolution.
- Service-account authentication with domain allowlists.
- Evidence access policies and legal-hold filtering helpers.
- Runtime memory and resolution-session context.

The existing graph API is designed for interactive visualization. It does not currently:

- Hydrate every graph node with useful agent-facing content.
- Rank and prune a graph under a model-context budget.
- Apply an agent-specific node, relationship, and metadata allowlist.
- Fully enforce agent-runtime visibility rules across every hydrated node.
- Expose a MAF `ContextProvider`.
- Expose a MAF-native function tool.

### 2.1 Schema coverage review

The current schema has broad conceptual coverage, but several concepts added in
`0029_ae_ops_concept_alignment` are not yet operational graph coverage. A table counts
as covered for this integration only when it is:

1. Tenant and domain safe.
2. Reachable through a typed graph relationship.
3. Hydratable from a stable node type.
4. Filterable by lifecycle and access policy.
5. Compatible with current and point-in-time traversal.

| Area | Current coverage | Gap | Required action |
| --- | --- | --- | --- |
| Evidence, episodes, patterns, playbooks, identities | Persisted and partially connected | Agent metadata and visibility rules are missing | Add typed hydrators and profile filters |
| Decisions, options, outcomes | First-class nodes with graph edges | Session, execution, approval, claim, and action-policy paths are incomplete | Materialize the missing relationships |
| Operational entities | `entities` table exists | Natural-key uniqueness is global, not tenant-scoped | Replace with tenant-scoped uniqueness |
| Claims | Claim and evidence-link tables exist | No domain scope and no decision-to-claim association | Add `domain_id` and `decision_claims` |
| Action policies | Table and precedence fields exist | No domain scope or relational decision application link | Add `domain_id` and `decision_action_policies` |
| Error signatures and fix patterns | Tables and internal FKs exist | No domain scope and no graph edges are populated | Add domain scope and materialized relationships |
| Full-text search columns | Generated columns are owned by migration `0007` | ORM placeholder defaults use `now()` for `tsvector`, breaking clean PostgreSQL bootstrap | Remove invalid ORM defaults and retain the generated-column migration as source of truth |
| Case outcomes | Outcome and transition tables exist | No structured outcome-to-fix validation link | Add `case_outcome_fix_patterns` |
| Graph edge integrity | Weight, confidence, and temporal columns exist | No FKs, checks, active-edge uniqueness, or traversal indexes | Harden `graph_edges` in migration `0031` |
| Temporal graph | `valid_from` and `valid_to` are nullable | Queries ignore the window and duplicate active edges are possible | Backfill `valid_from`, constrain windows, and add current-edge uniqueness |
| Synthetic enrichment nodes | Deterministic virtual nodes exist | Virtual node type `entity` collides with first-class `Entity` | Rename to `entity_term` and backfill existing edges |
| Case and playbook identifiers | Identifiers exist | `case_number` and `stable_key` are globally unique | Make both tenant-scoped |
| Migration reproducibility | Existing deployments can advance from `0030` | Dynamic `0001` is not a reproducible greenfield snapshot | Add migration-chain validation as a release gate; do not rewrite applied history in this feature |

### 2.2 Confirmed relationship coverage

Relationships currently written by service code include:

- Episode to pattern and identity.
- Playbook to pattern and identity.
- Playbook contradiction evidence.
- Evidence to actor and target identities.
- Decision to evidence, episode, pattern, option, generic tenant policy, approval,
  outcome, and child decision.
- Session to executed playbook.
- Approval to deciding user.
- Execution run to playbook outcome.

The following required paths are absent:

- Session to its structured operational entities.
- Session to execution run.
- Execution run to approval request.
- Decision to claim.
- Claim to case and typed supporting or contradicting evidence.
- Decision to action policy.
- Action policy to governed workflow entity.
- Error signature to pattern.
- Fix pattern to error signature, workflow entity, and recommended playbook.
- Case to case outcome.
- Case outcome to the fix pattern it validated or invalidated.

### 2.3 Schema decisions

`GraphEdge` will remain a materialized adjacency projection. Domain tables and
association tables remain the source of truth. A relationship materializer will
write and reconcile graph edges from those records.

A second `graph_nodes` table will not be added. Node metadata already has an
authoritative home in domain tables, and duplicating it would introduce stale
labels, lifecycle state, access policy, and summaries. The hydrator registry
provides a unified node interface without creating a second source of truth.

### 2.4 Frontend impact assessment

The existing Graph Explorer is the correct UI integration surface. It already
has Statistics, Subgraph, and Neighbors tabs backed by `/graph/*` endpoints and
uses React Flow for graph rendering. The schema and backend changes create the
following UI impacts:

| Backend or schema change | Current UI gap | Required frontend action |
| --- | --- | --- |
| Temporal edge versioning | Existing queries do not distinguish current from historical edges | Default all graph views to active edges and add a shared Current / As of control |
| New first-class node types | Node options and colors omit claims, action policies, signatures, fixes, and case outcomes | Extend the controlled node taxonomy, labels, colors, legend, and node routing |
| New canonical relationships | Unknown edges render with a fallback and are absent from the legend | Register all canonical relationships and retain a safe unknown-type fallback |
| Synthetic `entity` rename | The UI currently treats every `entity` as the same concept | Add `entity_term` as a visualization-only type and keep it out of `maf.v1` |
| Domain-scoped traversal | The APIs accept `domain_id`, but the explorer never sends it | Add a domain selector using the existing `useDomains` hook |
| Agent subset endpoint | There is no way to inspect the exact context supplied to an agent | Add an Agent Context Preview tab within Graph Explorer |
| Versioned subset metadata | No UI exists for budgets, truncation, warnings, or projection IDs | Add compact result status and a selected-node inspector |
| Existing graph deep links | Decision links provide `node_type` and `node_id`, but Graph Explorer ignores them | Hydrate filters and open the requested tab from URL search parameters |
| Graph contracts | Subgraph and neighbor response types are declared inside components | Move graph contracts into a shared typed module and add API helpers |
| Frontend verification | `npm test` is currently a no-op | Configure focused Vitest and Testing Library coverage for graph behavior |

The new subset endpoint does not require the rest of the application to consume
agent graph data. Existing graph response shapes remain backward compatible.
The UI preview is required for operator observability and acceptance testing,
not for MAF runtime operation.

## 3. MAF Compatibility Baseline

The implementation will follow the current MAF Python integration patterns:

- A custom `ContextProvider` using `before_run`.
- Per-run context injection using `SessionContext`.
- Native function tools declared with `@tool`.
- Runtime-only identity and authorization values supplied through
  `FunctionInvocationContext`, outside the model-visible tool schema.
- Provider-specific state stored under a stable provider source ID.

MAF will be an optional dependency:

```toml
[project.optional-dependencies]
maf = [
    "agent-framework-core>=1.12,<2",
]
```

Installing base ContextEdge must not install or import MAF.

Primary references:

- [MAF context providers](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/context-providers)
- [MAF function tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools)
- [MAF Python 1.12.0 release](https://github.com/microsoft/agent-framework/releases/tag/python-1.12.0)

## 4. Architectural Principles

### 4.1 Framework-neutral graph core

Graph selection, authorization, hydration, ranking, pruning, and serialization will not import MAF.

### 4.2 Adapter-based integration

MAF-specific lifecycle behavior will live under `contextedge.integrations.maf`.

### 4.3 Secure runtime scope

Tenant IDs, caller roles, credentials, domain allowlists, and workspace visibility will never be model-controlled tool arguments.

### 4.4 Server-controlled projections

The caller may select only a registered projection profile. It cannot submit arbitrary field or node-type allowlists.

### 4.5 Bounded and explainable output

Every result will report its profile version, applied budgets, seed nodes, truncation state, and safe provenance.

### 4.6 Fail closed for authorization

Authorization errors must not fall back to broader graph access.

### 4.7 Fail soft for proactive enrichment

If optional Context Graph enrichment is unavailable, the context provider will log the failure and inject nothing, allowing the base agent to continue.

## 5. High-Level Architecture

```text
                       Microsoft Agent Framework agent
                         /                       \
            proactive context                  on-demand lookup
             ContextProvider                    @tool function
                         \                       /
                    ContextGraphMAFPlugin
                              |
                    ContextGraphClient port
                       /                \
             in-process client       HTTP client
                       \                /
                  AgentGraphProjectionService
                              |
         +--------------------+---------------------+
         |                    |                     |
   Seed resolver       Graph repository       Node hydrators
   and ranker          batched traversal       per node type
         \                    |                     /
          MAFProjectionProfile + visibility policy
                              |
                ranked, connected, bounded subset

        Relational source of truth
                  |
       GraphRelationshipMaterializer
                  |
                       hardened GraphEdge projection

       ContextEdge web application
                  |
          Graph Explorer tabs
          /               \
 current/as-of views   Agent Context Preview
          \               /
       shared graph contracts and presentation
```

## 6. Proposed Module Layout

```text
backend/src/contextedge/
  graph/agent/
    __init__.py
    contracts.py
    profiles.py
    repository.py
    hydrators.py
    visibility.py
    materializer.py
    selector.py
    serializers.py
    service.py

  integrations/
    __init__.py
    maf/
      __init__.py
      client.py
      provider.py
      tools.py
      plugin.py

  api/v1/
    graph.py

backend/tests/
  test_maf_graph_schema_migration.py
  test_graph_relationship_materializer.py
  test_agent_graph_contracts.py
  test_agent_graph_profiles.py
  test_agent_graph_visibility.py
  test_agent_graph_selector.py
  test_agent_graph_service.py
  test_agent_graph_api.py
  test_maf_context_provider.py
  test_maf_context_tool.py
  test_maf_plugin.py

docs/
  MAF_CONTEXT_GRAPH_INTEGRATION.md

backend/alembic/versions/
  0031_maf_context_graph_hardening.py

frontend/src/
  lib/
    graph-api.ts
    types/
      graph.ts
  components/graph/
    graph-canvas.tsx
    graph-query-controls.tsx
    graph-subgraph.tsx
    graph-neighbors.tsx
    graph-stats.tsx
    agent-context-preview.tsx
    agent-context-inspector.tsx
    graph-constants.ts
  app/(dashboard)/graph-explorer/
    page.tsx

frontend/
  vitest.config.ts
  src/test/
    setup.ts
    graph-api.test.ts
    graph-constants.test.ts
    graph-explorer.test.tsx
    agent-context-preview.test.tsx
```

## 7. Core Interfaces

### 7.1 Graph repository

`AgentGraphRepository` will be a protocol responsible for data access:

```python
class AgentGraphRepository(Protocol):
    async def resolve_seeds(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
    ) -> list[RankedGraphSeed]: ...

    async def load_edges(
        self,
        frontier: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
        as_of: datetime | None,
    ) -> list[GraphEdgeRecord]: ...

    async def hydrate_nodes(
        self,
        nodes: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
    ) -> list[HydratedGraphNode]: ...
```

The SQLAlchemy implementation will load each frontier in batches rather than issuing one query per node.

### 7.2 Relationship materializer

`GraphRelationshipMaterializer` will project relational source-of-truth records
into `GraphEdge`:

```python
class GraphRelationshipMaterializer(Protocol):
    async def materialize_node(
        self,
        node: GraphNodeRef,
        scope: AgentGraphAccessScope,
    ) -> MaterializationResult: ...

    async def reconcile_tenant(
        self,
        tenant_id: UUID,
        *,
        batch_size: int = 500,
    ) -> ReconciliationResult: ...
```

Materialization rules will be registered by source model. Normal application
writes will materialize affected relationships in the same transaction. A
bounded reconciliation path will repair pre-existing rows and operational drift.

Graph edges are a projection, so deleting or superseding a relational link will
close the corresponding active edge by setting `valid_to`; historical edges will
remain available to `as_of` queries.

### 7.3 Projection profile

`AgentGraphProjectionProfile` will define:

- Allowed node types.
- Allowed relationship types.
- Per-node metadata projection rules.
- Per-relationship metadata projection rules.
- Node visibility predicates.
- Default and maximum budgets.
- Ranking weights and hop decay.
- Serializer defaults.

The initial registered profile will be `maf.v1`.

### 7.4 Client port

`ContextGraphClient` will isolate MAF adapters from graph deployment details:

```python
class ContextGraphClient(Protocol):
    async def get_agent_subset(
        self,
        request: AgentGraphRequest,
        runtime: AgentGraphRuntimeContext,
    ) -> AgentGraphSubset: ...
```

Implementations:

- `InProcessContextGraphClient`
- `HttpContextGraphClient`

### 7.5 Required Schema Expansion

Add Alembic revision `0031_maf_context_graph_hardening`, revising
`0030_evidence_chunks`.

#### 7.5.1 Tenant-safe natural keys

Replace global uniqueness with tenant-scoped uniqueness:

```text
entities:
  UNIQUE (tenant_id, entity_type, external_system, external_id)

resolution_sessions:
  UNIQUE (tenant_id, case_number) WHERE case_number IS NOT NULL

playbooks:
  UNIQUE (tenant_id, stable_key)
```

The migration will run duplicate preflight queries before replacing constraints.
It will fail with a diagnostic instead of silently deleting tenant data.

#### 7.5.2 Domain scope for standalone governance nodes

Add nullable `domain_id` foreign keys and tenant/domain indexes to:

- `claims`
- `action_policies`
- `error_signatures`
- `fix_patterns`

Backfill order:

1. Claims inherit the linked case domain.
2. Action policies inherit the workflow entity domain.
3. Error signatures inherit the linked pattern domain.
4. Fix patterns prefer workflow entity, then recommended playbook, then error
   signature domain.

Rows that cannot be resolved remain tenant-wide. Future service writes must set
the effective domain explicitly.

#### 7.5.3 Missing association tables

Add `decision_claims`:

```text
id
tenant_id
decision_id -> decisions.id ON DELETE CASCADE
claim_id -> claims.id ON DELETE CASCADE
use_type                 supports | contradicts | risk | precondition
weight
created_at
UNIQUE (decision_id, claim_id, use_type)
```

Add `decision_action_policies`:

```text
id
tenant_id
decision_id -> decisions.id ON DELETE CASCADE
action_policy_id -> action_policies.id ON DELETE CASCADE
policy_result_snapshot
created_at
UNIQUE (decision_id, action_policy_id)
```

Add `case_outcome_fix_patterns`:

```text
id
tenant_id
case_outcome_id -> case_outcomes.id ON DELETE CASCADE
fix_pattern_id -> fix_patterns.id ON DELETE CASCADE
result                    successful | failed | partial
confidence
created_at
UNIQUE (case_outcome_id, fix_pattern_id, result)
```

These tables close the reasoning chain:

```text
Decision -> Claim -> Evidence
Decision -> ActionPolicy -> Workflow Entity
Case -> CaseOutcome -> FixPattern -> Playbook
```

#### 7.5.4 `graph_edges` integrity and performance

Add foreign keys:

- `tenant_id -> tenants.id ON DELETE CASCADE`
- `domain_id -> domains.id ON DELETE CASCADE`

Backfill `valid_from = COALESCE(valid_from, created_at)`, set a server default,
and make `valid_from` non-null.

Add checks:

```text
weight >= 0
confidence IS NULL OR confidence BETWEEN 0 AND 1
valid_to IS NULL OR valid_to > valid_from
```

Add one-active-edge uniqueness using PostgreSQL 16 `NULLS NOT DISTINCT`:

```text
UNIQUE (
  tenant_id,
  domain_id,
  source_node_type,
  source_node_id,
  target_node_type,
  target_node_id,
  edge_type
) NULLS NOT DISTINCT
WHERE valid_to IS NULL
```

Add current and temporal traversal indexes:

```text
(tenant_id, source_node_type, source_node_id, domain_id)
  WHERE valid_to IS NULL

(tenant_id, target_node_type, target_node_id, domain_id)
  WHERE valid_to IS NULL

(tenant_id, source_node_type, source_node_id, valid_from, valid_to)

(tenant_id, target_node_type, target_node_id, valid_from, valid_to)
```

The materializer and `ensure_edge` will target active edges only. A relationship
change closes the active row and inserts a new version. Concurrent creation is
resolved by the unique index and a re-read after `IntegrityError`.

Existing visualization queries are part of this temporal contract. Neighbors,
subgraph, pattern graph, and statistics queries will select only active edges by
default. When `as_of` is provided, they will use:

```text
valid_from <= as_of
AND (valid_to IS NULL OR valid_to > as_of)
```

This prevents replacement edges from appearing twice in the existing UI.

#### 7.5.5 Synthetic node namespace correction

Rename the virtual pattern-enrichment node type:

```text
entity -> entity_term
```

The data migration will update only edges matching the synthetic shape:

```text
source_node_type = 'entity'
edge_type = 'involved_in'
source_node_id does not exist in entities.id
```

First-class operational entities keep node type `entity`. The MAF profile will
exclude `entity_term`, `trigger`, `error`, and `root_cause` nodes and project
their useful labels as pattern facts.

#### 7.5.6 Relationship backfill

After schema hardening, materialize the missing relationships in bounded,
idempotent batches:

| Source | Relationship |
| --- | --- |
| Resolution session entity FKs | `involves_user`, `targets_workflow`, `tracks_request`, `runs_on_agent` |
| Execution run session FK | `session -> has_execution -> execution_run` |
| Execution run playbook FK | `execution_run -> executes -> playbook` |
| Approval request execution FK | `execution_run -> requires_approval -> approval_request` |
| Claim case FK | `claim -> asserted_in -> session` |
| Claim evidence link | `claim -> supported_by/contradicted_by/weakened_by -> evidence` |
| Claim supersession FK | `claim -> superseded_by -> claim` |
| Decision evidence link | `decision -> based_on -> evidence` |
| Decision claim link | `decision -> supported_by_claim -> claim` |
| Decision action-policy link | `decision -> applied_policy -> action_policy` |
| Action policy workflow FK | `action_policy -> governs -> entity` |
| Error signature pattern FK | `error_signature -> aggregated_by -> pattern` |
| Fix pattern error FK | `fix_pattern -> addresses -> error_signature` |
| Fix pattern workflow FK | `fix_pattern -> applies_to -> entity` |
| Fix pattern playbook FK | `fix_pattern -> recommends -> playbook` |
| Case outcome case FK | `session -> resulted_in -> case_outcome` |
| Outcome fix link | `case_outcome -> validated_fix/invalidated_fix -> fix_pattern` |

Existing edge names remain readable for backward compatibility. The `maf.v1`
profile will normalize legacy `executed_playbook` and `execution_outcome`
relationships into the canonical execution path when serializing.

#### 7.5.7 Migration safety

- Use guarded, additive DDL where possible.
- Do not rewrite or squash applied migrations.
- Run uniqueness and active-edge duplicate preflight checks.
- Validate foreign keys after orphan audits.
- Keep backfills restartable and deterministic.
- Add a PostgreSQL 16 migration test from a realistic `0030` schema.
- Add a separate documented greenfield migration-chain check because dynamic
  revision `0001` is not a stable schema snapshot.

## 8. Agent Subset Contract

### 8.1 Request

The framework-neutral request will support:

```text
query               Current task or question
seeds               Optional typed node references
session_id          Optional ContextEdge resolution session
entities            Optional operational entity terms
domain_id           Optional requested domain
max_depth           Requested depth, clamped by profile
budget              Requested limits, clamped by profile
profile             Registered profile name, default maf.v1
as_of               Optional temporal graph point
```

The MAF tool will expose only safe model-controlled fields:

```text
query
seeds
entities
max_depth
```

Session, tenant, domain, principal, roles, workspaces, and credentials will be injected at runtime.

### 8.2 Response

```json
{
  "schema_version": "1.0",
  "profile": "maf.v1",
  "projection_id": "uuid",
  "generated_at": "timestamp",
  "query": "why did the payment workflow fail?",
  "seeds": [],
  "nodes": [],
  "relationships": [],
  "budget": {
    "max_nodes": 24,
    "max_relationships": 48,
    "max_depth": 2,
    "max_characters": 12000
  },
  "usage": {
    "nodes": 0,
    "relationships": 0,
    "characters": 0
  },
  "truncated": false,
  "truncation_reasons": [],
  "warnings": []
}
```

### 8.3 Agent node shape

```text
key                 Stable type:id identifier
type                Controlled node type
id                  Node UUID
label               Compact human-readable label
summary             Agent-facing summary
facts               Allowlisted typed metadata
confidence          Optional node confidence
freshness           Optional freshness signal
relevance           Projection-time relevance score
provenance          Safe source type and timestamps
```

### 8.4 Agent relationship shape

```text
source              Source node key
target              Target node key
type                Controlled relationship type
direction           Direction relative to traversal
weight              Graph traversal weight
confidence          Relationship confidence
relevance           Projection-time relevance score
metadata            Allowlisted relationship metadata
```

## 9. `maf.v1` Projection Profile

### 9.1 Included node types

- `session`
- `decision`
- `decision_option`
- `decision_outcome`
- `approval_request`
- `execution_run`
- `playbook`
- `pattern`
- `episode`
- `evidence`
- `identity`
- `entity`
- `user` (display name and account status only; no email or authentication data)
- `tenant_policy`
- `action_policy`
- `claim`
- `error_signature`
- `fix_pattern`
- `case_outcome`

Nodes are included only when the corresponding visibility and lifecycle rules pass.

### 9.2 Included relationship types

The profile will expose only registered semantic relationships:

- Evidence and reasoning: `based_on`, `supported_by`, `contradicted_by`,
  `weakened_by`, `supported_by_claim`, `records_decision`, `records_action_on`.
- Decision structure: `considered`, `chose`, `applied_policy`,
  `required_approval`, `resulted_in`, `followed_by`.
- Case and execution: `asserted_in`, `has_execution`, `executes`,
  `requires_approval`, `approved_by`, `denied_by`, `modified_by`.
- Operational context: `involves_user`, `targets_workflow`, `tracks_request`,
  `runs_on_agent`, `governs`, `applies_to`.
- Learned memory: `belongs_to`, `affects`, `derived_from`, `contradicts`,
  `aggregated_by`, `addresses`, `recommends`, `validated_fix`,
  `invalidated_fix`, `superseded_by`.

Unknown edge types are excluded by default. Registering a new relationship
requires an explicit direction, metadata allowlist, ranking weight, and test.

### 9.3 Lifecycle visibility

- Playbooks must be approved and have a published version.
- Patterns and operational entities must be active.
- Episodes must have an accepted reviewer state for proactive injection.
- Claims must be `machine_verified` or `human_validated`; rejected and
  superseded claims are excluded from current context.
- Action policies, error signatures, and fix patterns must be active.
- Evidence marked `legal_hold` is excluded from model-facing paths.
- Evidence with pending redaction is excluded.
- Restricted evidence access policies are enforced before traversal continues.
- Historical `as_of` queries apply lifecycle state as it was represented by the
  selected edge window; current node deletion or deactivation still fails closed
  unless a future immutable node snapshot feature is added.

### 9.4 Excluded content

- Raw evidence bodies
- Raw source payloads
- Embeddings and search vectors
- Storage keys and attachment locations
- Credentials and authentication data
- User email addresses and unrelated PII
- Arbitrary JSONB blobs
- Full audit or operational-event payloads
- Internal database identifiers that are not needed for graph references
- Visualization-only enrichment nodes when the same information can be projected as pattern facts

### 9.5 Node metadata examples

| Node type | Exposed metadata |
| --- | --- |
| Playbook | title, stable key, description, lifecycle, risk tier, automation mode, published version, compact triggers and steps |
| Pattern | title, description, confidence, freshness, root causes, resolution steps |
| Episode | title, status, reviewed root cause, final outcome, extraction confidence |
| Evidence | title, body summary, evidence type, source type, evidence time, relevance |
| Decision | type, agent step, actor type, rationale, status, confidence, risk, policy result |
| Decision option | action, suitability, risk, preconditions, selected state |
| Decision outcome | action, result, compact result details, follow-up state |
| Session | case number, status, summary, environment, business impact, timestamps |
| Identity | canonical name, entity type, active state |
| Entity | entity type, canonical name, environment, active state |
| Approval | action, status, safety class, decision time |
| Execution | status, automation mode, outcome summary |
| Tenant policy | policy type, applicable verdict, safe policy summary |
| Action policy | policy name, action, risk, result, execution mode, priority, safe conditions and restrictions |
| Claim | claim type, claim text, confidence, creator type, validation status and time |
| Error signature | display name, error type, normalized message, usual causes, recommended actions, success/failure counts, confidence |
| Fix pattern | pattern name, issue type, failed step, recommended fix, preconditions, risk, approval requirement, success/failure counts, confidence |
| Case outcome | status, resolution summary, confirmed root cause, successful and failed actions, user confirmation, MTTR, close time |

## 10. Seed Resolution and Ranking

Seed resolution will combine:

1. Explicit caller-provided node references.
2. The active resolution session.
3. Recent decisions connected to that session.
4. Existing approved-playbook hybrid ranking for the query.
5. Canonical identity resolution for supplied entities.

Candidate scoring will be deterministic:

```text
candidate score =
    anchor relevance
    * hop decay
    * edge weight
    * relationship confidence
    * node quality
    * freshness factor
```

The implementation will:

- Prefer closer and better-supported nodes.
- Preserve paths from retained nodes back to a seed.
- Deduplicate nodes and relationships.
- Apply a stable tie-breaker using node type and ID.
- Report truncation when any budget is reached.

Initial defaults:

```text
max nodes           24
max relationships   48
max depth           2
max characters      12,000
```

Server maximums will be stricter than the existing visualization API's general traversal limits.

## 11. Authorization and Visibility

The projection service will build an immutable `AgentGraphAccessScope` containing:

- Tenant ID
- Principal ID and type
- Roles
- Workspace IDs
- Requested domain
- Service-account domain allowlist
- Effective playbook risk cap

The following rules will be applied before serialization:

1. Every database query is tenant-scoped.
2. A requested domain must belong to the tenant.
3. Service-account domain allowlists are enforced.
4. Tenant-wide nodes and edges remain visible when otherwise authorized.
5. Workspace-scoped nodes must be in the caller's visible workspaces. When a
   node has only `domain_id`, workspace scope is resolved through
   `domains.workspace_id`.
6. Evidence access-policy restrictions are resolved from caller roles.
7. Legal-hold evidence is excluded.
8. Evidence awaiting redaction is excluded.
9. Evidence uses summaries only.
10. Candidate, expired, or unpublished playbooks are excluded.
11. Playbooks above the caller's risk cap are excluded.
12. Rejected, superseded, and insufficiently validated claims are excluded.
13. Inactive patterns, identities, entities, policies, signatures, and fixes
    are excluded.
14. A target node is authorized before it can enter the next traversal
    frontier, preventing traversal through a hidden node to a visible one.
15. Relationships are removed if either endpoint is removed.
16. Arbitrary graph-edge metadata is never passed through directly.
17. Current requests use only active edges. `as_of` requests require
    `valid_from <= as_of` and `valid_to IS NULL OR valid_to > as_of`.

## 12. MAF Adapter

### 12.1 Context provider

`ContextGraphProvider` will:

1. Read only current external input messages.
2. Build a compact query.
3. Resolve runtime scope from configured client/session state.
4. Request a `maf.v1` graph subset.
5. Serialize it into compact prompt context.
6. Add it under a stable source ID using `context.extend_instructions`.
7. Store only projection fingerprints and IDs in provider state.

The provider will not persist injected graph content into conversation history.

### 12.2 Function tool

`ContextGraphTools.query_context_graph` will:

- Use the MAF `@tool` decorator.
- Use explicit Pydantic input schema descriptions.
- Receive `FunctionInvocationContext` as an injected parameter.
- Hide authorization and deployment details from the model schema.
- Return structured JSON-compatible data.
- Clamp all model-provided limits.
- Return safe, actionable error results for unavailable context.
- Propagate authorization failures without broadening access.

### 12.3 Plugin bundle

`ContextGraphMAFPlugin` will provide:

```python
plugin = ContextGraphMAFPlugin(...)

agent = Agent(
    client=client,
    name="operations-agent",
    instructions="...",
    context_providers=plugin.context_providers,
    tools=plugin.tools,
)
```

Configuration will allow:

- Provider-only mode
- Tool-only mode
- Provider and tool mode
- In-process client
- Remote HTTP client
- Custom registered projection profile

## 13. HTTP API

Add:

```text
POST /api/v1/graph/agent-subsets
```

The route will:

- Reuse normal ContextEdge authentication.
- Construct runtime access scope from `AuthUser`.
- Validate domain membership and service-token restrictions.
- Accept only registered projection profiles.
- Return the versioned subset contract.
- Emit an operational event containing counts and projection metadata, not graph content.

The same endpoint can be wrapped as a MAF function tool by Python or .NET consumers.

Extend the existing read-only visualization endpoints without changing their
response shapes:

```text
GET /api/v1/graph/neighbors?domain_id=...&as_of=...
GET /api/v1/graph/subgraph/{node_type}/{node_id}?domain_id=...&as_of=...
GET /api/v1/graph/stats?domain_id=...&as_of=...
GET /api/v1/patterns/{pattern_id}/graph?domain_id=...&as_of=...
```

`as_of` is optional. Its absence means the current active graph. The server will
parse it as an offset-aware timestamp, normalize it to UTC, and reject future
timestamps beyond a small clock-skew allowance. Domain authorization remains
server-side.

## 14. Frontend Architecture and UX

### 14.1 Shared contracts and API boundary

Add a dedicated `lib/types/graph.ts` for:

- Visualization nodes, relationships, neighbor results, and statistics.
- `AgentGraphRequest`, budgets, node references, and projection profiles.
- `AgentGraphSubset`, nodes, relationships, usage, warnings, and truncation.
- Current and point-in-time filter state.

Add `lib/graph-api.ts` as a thin typed wrapper over the existing API client. It
will serialize optional query parameters consistently, convert local datetime
input to an offset-aware UTC ISO timestamp, and keep request bodies free of
tenant IDs, roles, workspaces, credentials, or service-account restrictions.

The backend Pydantic contract remains authoritative. Frontend types mirror the
versioned wire contract and contract tests will use representative backend
fixtures to detect drift.

### 14.2 Reusable graph presentation

Extract the repeated React Flow mapping and Dagre layout into a reusable graph
canvas or focused presentation helpers. Subgraph, Pattern Graph, and Agent
Context Preview will share:

- Stable `type:id` node keys.
- Controlled node labels, colors, and edge styles.
- A safe fallback for forward-compatible unknown types.
- Stable canvas sizing and fit behavior.
- Selected-node callbacks and count indicators.

Expand `graph-constants.ts` for all first-class schema nodes and canonical
relationships. The palette will distinguish functional groups rather than map
the expanded graph to one hue. Visualization-only `entity_term` remains
selectable in the general explorer but is explicitly absent from the `maf.v1`
profile.

### 14.3 Shared scope and time controls

Add a compact graph query control used by Statistics, Subgraph, and Neighbors:

- Domain: All visible domains or one domain from the existing `useDomains` hook.
- Time: Current or As of.
- As of date and time: enabled only in historical mode and serialized in UTC.

Changing these controls invalidates only graph query keys with the affected
scope. Current mode is the default and preserves current behavior. The UI will
show the selected historical timestamp near graph counts so an old graph cannot
be mistaken for the current state. Historical mode will also state that edge
topology is point-in-time while node facts remain current-state in this release.

### 14.4 Agent Context Preview

Add an `Agent Context` tab to the existing Graph Explorer rather than a new
top-level route. The preview submits `POST /graph/agent-subsets` and displays the
exact allowlisted projection returned by the server.

Inputs:

- Query text.
- Optional typed seed and UUID, with support for adding more than one seed.
- Optional resolution-session UUID.
- Optional entity terms.
- Domain and Current / As of scope.
- Depth and node, relationship, and character budgets, constrained to the
  documented server maximums.
- A profile indicator initially fixed to `maf.v1`.

Results:

- Profile, schema version, projection ID, generation time, and applied scope.
- Used versus allowed node, relationship, depth, and character budgets.
- Truncation state, reasons, and safe warnings.
- Reusable graph visualization of the returned nodes and relationships.
- A selected-node inspector showing only returned label, summary, typed facts,
  confidence, freshness, relevance, and safe provenance.
- Relationship details showing direction, weight, confidence, relevance, and
  allowlisted metadata.

The desktop layout will use an unframed graph workspace with an adjacent
inspector. On narrow screens, the inspector will open in the existing Sheet
component. Controls wrap into predictable rows, the canvas keeps a stable
height, and long labels or metadata values wrap without changing node geometry.

Required states:

- Initial state with no request.
- Loading with previous results clearly marked stale.
- Empty authorized result.
- Validation error.
- Authentication or authorization error.
- Backend unavailable.
- Truncated result with reasons.
- Successful result with no warnings.

### 14.5 Navigation and authorization

Graph Explorer will read and preserve:

```text
tab
node_type
node_id
domain_id
as_of
```

This fixes the existing Decision "View in Graph" link and allows future entity
detail pages to deep-link consistently. Invalid URL parameters fall back to safe
defaults and never trigger an unbounded request.

The Graph Explorer remains visible to authenticated users, matching current
navigation. No frontend role check is treated as authorization. The server
derives tenant and principal scope from the access token and validates every
requested domain. The preview is read-only and cannot invoke agent actions,
approvals, executions, or graph mutations.

### 14.6 Deferred UI work

The initial delivery will not add:

- CRUD screens for claims, action policies, error signatures, fix patterns, or
  case outcomes.
- An agent configuration or deployment screen.
- A profile authoring UI or arbitrary field allowlist editor.
- A separate MAF administration route.
- Raw JSONB, evidence bodies, prompt text, credentials, or hidden authorization
  context in the inspector.

## 15. Observability

Structured logs and operational events will capture:

- Projection ID
- Tenant ID
- Principal type
- Profile and schema version
- Seed count
- Candidate count
- Returned node and relationship counts
- Applied budgets
- Truncation reasons
- Duration
- Provider or tool invocation mode
- Error category

Logs must not contain graph summaries, evidence text, credentials, or unrestricted metadata.

The Agent Context Preview will surface safe response-level diagnostics already
returned by the endpoint. It will not fetch operational logs or expose hidden
authorization decisions.

## 16. Implementation Phases

Implementation status as of 2026-07-28: phases 0-6 are implemented and verified
in the project venv. Phase 7 is implemented and passes unit, lint, type, and
production-build checks; browser screenshot verification remains pending because
this coding session exposed no browser backend. Phase 8 documentation and full
automated verification are complete for the Python/HTTP integration; the
optional .NET wrapper and codewiki expansion remain follow-up documentation.

### Phase 0: Schema hardening

- [x] Add migration `0031_maf_context_graph_hardening`.
- [x] Run duplicate and orphan preflight checks.
- [x] Correct tenant-scoped entity, case, and playbook uniqueness.
- [x] Add domain scope to claims, action policies, error signatures, and fix patterns.
- [x] Add decision-claim, decision-action-policy, and outcome-fix association tables.
- [x] Add graph-edge FKs, checks, active-edge uniqueness, and traversal indexes.
- [x] Backfill `valid_from` and normalize synthetic `entity_term` nodes.
- [x] Make existing visualization queries active-edge aware before edge
  versioning can produce multiple historical rows.
- [x] Update ORM models and the ORM-DDL drift snapshot.
- [x] Add upgrade, downgrade, and data-preservation migration tests.

### Phase 1: Relationship materialization

- [x] Add the relationship-rule registry.
- [x] Add active-edge create, close, and replace operations.
- [x] Add same-transaction materialization to affected service write paths.
- [x] Add bounded reconciliation for existing tenants.
- [x] Backfill missing relationships from relational source tables.
- [x] Preserve and normalize legacy execution relationships.
- [x] Add idempotency, concurrency, temporal-version, and reconciliation tests.

### Phase 2: Contracts and profiles

- [x] Add graph subset Pydantic contracts.
- [x] Add access-scope and runtime-context contracts.
- [x] Add projection profile protocol and registry.
- [x] Add `maf.v1` allowlists and budgets.
- [x] Add contract and profile tests.

### Phase 3: Repository and hydration

- [x] Add batched edge traversal.
- [x] Add temporal edge filtering using `valid_from` and `valid_to`.
- [x] Share the temporal predicate with neighbors, subgraph, pattern graph, and
  graph statistics queries.
- [x] Add per-node hydrator registry.
- [x] Add safe metadata projections.
- [x] Add lifecycle and visibility filters.
- [x] Authorize each target before expanding the next frontier.
- [x] Add repository and hydrator tests.

### Phase 4: Selection and serialization

- [x] Add seed resolution.
- [x] Add deterministic candidate scoring.
- [x] Add connected-subgraph pruning.
- [x] Add character and topology budgets.
- [x] Add compact JSON and prompt serializers.
- [x] Add selector and serializer tests.

### Phase 5: Service and API

- [x] Add projection orchestration service.
- [x] Add authorization-scope builder.
- [x] Add `POST /graph/agent-subsets`.
- [x] Add optional `as_of` to existing visualization endpoints while preserving
  response contracts.
- [x] Add structured logs and operational events.
- [x] Add API authorization and contract tests.

### Phase 6: MAF adapter

- [x] Add optional MAF dependency group.
- [x] Add in-process and HTTP clients.
- [x] Add `ContextGraphProvider`.
- [x] Add `query_context_graph` tool.
- [x] Add `ContextGraphMAFPlugin`.
- [x] Add MAF import, schema, provider, and tool tests.

### Phase 7: Frontend integration

- [x] Add shared graph wire types and typed API functions.
- [x] Expand node and relationship presentation constants, including
  `entity_term` and every `maf.v1` type.
- [x] Extract reusable graph layout and presentation behavior.
- [x] Add domain and Current / As of controls to existing graph tabs.
- [x] Read graph deep-link parameters and open the requested exploration state.
- [x] Add the Agent Context Preview tab, result summary, graph view, and
  selected-node or relationship inspector.
- [x] Implement loading, empty, error, stale, warning, and truncation states.
- [ ] Verify stable responsive layouts on desktop and mobile.
- [x] Replace the no-op `npm test` script with minimal Vitest, jsdom, Testing
  Library, and jest-dom setup, then add focused component tests.
- [ ] Run frontend tests, ESLint, TypeScript through Next build, and browser
  smoke checks.

### Phase 8: Documentation and verification

- [x] Add Python provider-only example.
- [x] Add Python tool-only example.
- [x] Add combined-mode example.
- [x] Add remote HTTP client example.
- [ ] Add .NET function-tool wrapper example.
- [ ] Update `README.md`, `docs/API.md`, and codewiki.
- [x] Document Graph Explorer temporal mode and Agent Context Preview.
- [x] Run focused and full backend and frontend verification.

## 17. Test Matrix

### Unit tests

- Relationship-rule registration and canonical edge naming
- Active-edge idempotency and temporal replacement
- Synthetic node namespace normalization
- Contract validation and versioning
- Profile registration and unknown-profile rejection
- Metadata allowlists
- Ranking and deterministic tie-breaking
- Hop decay and relationship confidence
- Node and relationship budgets
- Character-budget truncation
- Path-preserving pruning
- Temporal graph filtering
- Serializer output stability

### Migration tests

- Upgrade from a representative `0030` database
- Downgrade back to `0030`
- Tenant-scoped uniqueness replacement
- Duplicate preflight diagnostics
- Orphan audit before foreign-key validation
- Domain backfill precedence
- `valid_from` backfill and temporal checks
- Active-edge duplicate detection
- Synthetic `entity` to `entity_term` data correction
- Association-table constraints and cascades
- Relationship backfill idempotency

### Security tests

- Cross-tenant seed rejection
- Invalid tenant-domain combinations
- Service-account domain allowlists
- Workspace visibility
- Restricted evidence access policies
- Legal-hold evidence exclusion
- Pending-redaction evidence exclusion
- Claim validation-state filtering
- Evidence summary-only output
- Unpublished and unapproved playbook exclusion
- Role-based risk caps
- Relationship pruning after endpoint removal
- Runtime-only tool arguments absent from the model schema

### MAF compatibility tests

- Base ContextEdge imports without MAF installed
- MAF extra imports against supported versions
- Provider source ID and state isolation
- `before_run` adds bounded context
- Provider failure injects no stale context
- Tool registration with `@tool`
- Tool input schema excludes tenant and credentials
- Function invocation runtime scope reaches the client
- Provider and tool can be enabled independently

### Frontend tests

- Graph API helpers omit unset values and serialize `as_of` as UTC
- Frontend request bodies never include tenant, role, workspace, or credentials
- New and legacy node and relationship types map to stable presentation
- Unknown graph types use the safe visual fallback
- Current mode omits `as_of`; historical mode includes it in every graph query
- Domain changes participate in React Query cache keys
- URL parameters open the correct tab, node, domain, and time scope
- Invalid deep-link parameters fall back without issuing malformed requests
- Agent Context Preview renders loading, empty, error, success, and stale states
- Budget usage, warnings, and truncation reasons are visible
- Selecting a node or relationship renders only the returned allowlisted fields
- Mobile inspector uses a Sheet and does not overlap the graph controls

### Integration tests

- In-process and HTTP clients return equivalent contracts
- API authentication and response models
- SQLAlchemy repository against PostgreSQL
- Relationship materializer reconciliation against PostgreSQL
- Concurrent active-edge creation
- Current and point-in-time traversal
- Session, decision, playbook, pattern, and evidence mixed graph
- Claim, policy, signature, fix, and case-outcome reasoning chain
- Empty graph and disconnected seed behavior
- Existing visualization endpoints return active edges by default
- Visualization and agent endpoints agree on `as_of` edge membership
- Frontend representative fixtures validate against the backend response models

### Browser smoke tests

- Open a decision deep link directly into Subgraph
- Exercise current and historical traversal
- Preview a normal and a truncated `maf.v1` subset
- Select dense nodes and long metadata on desktop and mobile viewports
- Confirm no control, label, canvas panel, or inspector overlap
- Confirm no browser console errors or failed graph requests

## 18. Verification Commands

Expected verification after implementation:

```powershell
python -m pytest tests/test_maf_graph_schema_migration.py
python -m pytest tests/test_graph_relationship_materializer.py
python -m pytest tests/test_agent_graph_contracts.py
python -m pytest tests/test_agent_graph_profiles.py
python -m pytest tests/test_agent_graph_visibility.py
python -m pytest tests/test_agent_graph_selector.py
python -m pytest tests/test_agent_graph_service.py
python -m pytest tests/test_agent_graph_api.py
python -m pytest tests/test_maf_context_provider.py
python -m pytest tests/test_maf_context_tool.py
python -m pytest
python -m ruff check src tests
cd ..\frontend
npm test
npm run lint
npm run build
```

A separate environment will install the `maf` optional dependency and run a real MAF registration smoke test.

The completed UI will also be exercised against the local backend at desktop
and mobile viewports. Screenshots are review evidence, not committed product
assets.

## 19. Acceptance Criteria

The feature is complete when:

1. A MAF agent can enable proactive Context Graph context with one provider object.
2. A MAF agent can query Context Graph with one registered function tool.
3. Both modes use the same framework-neutral graph subset service.
4. The subset is ranked, connected, deterministic, and bounded.
5. Only `maf.v1` approved node types, relationships, and metadata are returned.
6. The claim, action-policy, error/fix, execution, and case-outcome paths are
   traversable from their relational sources.
7. Entity, case-number, and playbook identifiers are tenant-safe.
8. Only one current copy of a logical edge can exist, while historical edge
   versions remain queryable.
9. Current and `as_of` traversal use the correct validity windows.
10. Tenant, domain, workspace, access-policy, legal-hold, redaction, claim
    validation, and risk boundaries are tested.
11. Authorization details are absent from the model-visible tool schema.
12. ContextEdge works normally without MAF installed.
13. In-process and HTTP deployment modes are documented and tested.
14. Migration upgrade, downgrade, backfill, and reconciliation tests pass.
15. Existing graph views show active edges by default and honor an authorized
    point-in-time selection.
16. Graph Explorer supports domain filtering and consumes supported deep links.
17. Operators can preview the exact `maf.v1` subset, inspect returned safe facts,
    and see applied budgets, warnings, and truncation.
18. The expanded graph taxonomy renders consistently, with safe fallback styling
    for unknown future types.
19. The Graph Explorer remains usable without MAF installed or enabled.
20. Desktop and mobile graph layouts have no overlapping controls or text.
21. Backend tests and Ruff checks, plus frontend tests, lint, and build, pass.

## 20. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| MAF API changes | Pin the supported major range and isolate imports in one adapter package |
| Context overflow | Enforce node, relationship, depth, and character budgets |
| Sensitive graph leakage | Use per-type allowlists and fail-closed visibility filters |
| Slow graph traversal | Batch frontier queries and cap traversal depth |
| Noisy relevance | Use existing retrieval anchors, hop decay, freshness, and confidence |
| Orphan relationships | Prune every edge whose endpoint is removed |
| Relational/graph projection drift | Materialize in the source transaction and provide bounded reconciliation |
| Migration finds duplicate natural keys | Run preflight checks and fail with diagnostics instead of deleting data |
| Active-edge constraint conflicts with legacy duplicates | Audit and reconcile duplicates before creating the unique index |
| Historical edge query returns current node content | Label node facts as current-state; defer immutable node snapshots |
| Dynamic `0001` breaks greenfield migration replay | Keep a release-gate migration test and document the supported bootstrap path |
| Provider availability affects agent | Fail soft only after authorization scope has been established |
| Divergence between local and remote use | Keep both clients behind one contract and parity-test responses |
| Historical edges leak into current visualizations | Reuse one tested temporal predicate across every graph query |
| Frontend taxonomy drifts from projection profiles | Centralize graph contracts and test representative backend fixtures |
| Agent preview exposes sensitive fields | Render only the versioned subset contract; never request or display runtime scope |
| Dense graph subsets become unreadable | Keep bounded defaults, stable node dimensions, fit controls, and an inspector |
| URL deep links trigger unsafe queries | Validate node types, UUIDs, domains, timestamps, depth, and budgets before fetching |
| New frontend test setup adds maintenance cost | Keep Vitest configuration minimal and tests focused on graph contracts and states |

## 21. Non-Goals for the Initial Delivery

- Replacing PostgreSQL with a dedicated graph database
- Allowing agents to mutate Context Graph data
- Exposing execution or approval actions through this read-only tool
- Returning full evidence bodies or raw source payloads
- Building a general-purpose MCP server
- Adding a duplicate `graph_nodes` metadata registry
- Adding a standalone agent administration screen
- Adding CRUD interfaces for the newly connected schema concepts
- Letting the browser define projection field allowlists or authorization scope
- Implementing graph summarization through an additional LLM call
- Providing immutable historical snapshots of every node's descriptive fields

The client and projection interfaces intentionally leave room for a future MCP adapter without making MCP a dependency of the initial MAF integration.
