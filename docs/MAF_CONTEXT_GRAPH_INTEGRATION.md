# Microsoft Agent Framework Context Graph Integration

ContextEdge exposes a framework-neutral, versioned graph projection and a thin
Microsoft Agent Framework (MAF) adapter. An agent can receive graph context
proactively through a `ContextProvider`, call it on demand through function
tools, or use both modes. Optionally, what the agent concluded flows back in as
a reviewable decision record.

Verified against the code on 2026-08-19: every line citation below was opened
and checked against the file it names.

## Install

```powershell
cd backend
.\venv\Scripts\python.exe -m pip install -e ".[maf]"
```

The base API and projection service do not import MAF. Client-only imports stay
available without the optional extra: the package itself re-exports
`HttpContextGraphClient`, `InProcessContextGraphClient`, `InProcessCohortClient`,
`InProcessEdgeProposalClient` and three protocols
(`backend/src/contextedge/integrations/maf/__init__.py:11-34`), and the other
clients and protocols — CMDB topology, change risk, fix applicability, decision
write-back — import from `contextedge.integrations.maf.client` directly. The
framework-backed names (`ContextGraphMAFPlugin`, `ContextGraphProvider`,
`ContextGraphTools`, `CohortTools`, `EdgeProposalTools`) are resolved lazily
through `__getattr__`, so importing the package cannot crash a process that
never installed `agent_framework`
(`backend/src/contextedge/integrations/maf/__init__.py:36-53`); the three
service-tool classes (`CmdbTopologyTools`, `ChangeRiskTools`,
`FixApplicabilityTools`) are imported from
`contextedge.integrations.maf.tools`, which needs the extra. If the extra is
missing and you do touch one, you get one actionable line —
"Microsoft Agent Framework support requires `pip install contextedge[maf]`" —
instead of a deep `ModuleNotFoundError`
(`backend/src/contextedge/integrations/maf/_compat.py:10-13`).

## HTTP-backed MAF Agent

```python
from agent_framework import Agent

from contextedge.integrations.maf import (
    ContextGraphMAFPlugin,
    HttpContextGraphClient,
)

graph_client = HttpContextGraphClient(
    "https://contextedge.example.com",
    service_token="replace-with-secret-provider-value",
)
graph = ContextGraphMAFPlugin(
    graph_client,
    enable_provider=True,
    enable_tool=True,
)

agent = Agent(
    chat_client,
    instructions="Resolve operational incidents using governed evidence.",
    context_providers=graph.context_providers,
    tools=graph.tools,
)
```

`HttpContextGraphClient` **refuses a non-HTTPS `base_url`** unless you pass
`allow_insecure_http=True`, because the bearer or service token travels in a
header (`client.py:139-149`). The same rule is applied by
`HttpCmdbTopologyClient` (`client.py:398-404`) and
`HttpDecisionWritebackClient` (`client.py:347-353`).

Use `enable_provider=False` for tool-only operation or `enable_tool=False` for
proactive injection only. The provider is fail-soft: a projection that cannot be
reached is logged as `maf_context_graph_provider_unavailable` and the run
continues without graph context (`provider.py:73-80`). The graph tool does not
raise at the agent either — malformed model arguments come back as a structured
`{"error": {"code", "message"}}` result the model can correct
(`tools.py:20-22, 60-96`).

Provider-only and tool-only agents use the same plugin with one switch:

```python
provider_only = ContextGraphMAFPlugin(graph_client, enable_tool=False)
tool_only = ContextGraphMAFPlugin(graph_client, enable_provider=False)

provider_agent = Agent(
    chat_client,
    context_providers=provider_only.context_providers,
)
tool_agent = Agent(chat_client, tools=tool_only.tools)
```

## The six tools

`ContextGraphMAFPlugin` starts with the graph tool and appends one tool per
optional client you hand it, so a deployment ships exactly the surface it wants
(`plugin.py:26-85`). Every tool is **read-or-propose** — nothing on this branch
can execute a remediation.

| Tool | Enabled by | What it returns | Error code on failure |
| --- | --- | --- | --- |
| `query_context_graph` | always, unless `enable_tool=False` | a bounded `maf.v1` subset (`tools.py:29-99`) | `invalid_seed`, `invalid_request` |
| `cmdb_topology` | `cmdb_client=` | live ±1-hop ServiceNow CI neighborhood, cache-first, stale-marked on outage (`tools.py:188-222`) | `topology_unavailable` |
| `assess_change_risk` | `change_risk_client=` | deterministic risk profile for a CI, window 1-730 days, default 180 (`tools.py:229-270`) | `risk_assessment_unavailable` |
| `assess_fix_applicability` | `fix_applicability_client=` | which known fixes clear the CI's preconditions, with the applicability level (`tools.py:277-314`) | `fix_applicability_unavailable` |
| `get_cohort_shared_attributes` | `cohort_client=` | what the CIs behind ≤200 evidence ids share; empty means no attribute covers ≥60% of a ≥3-CI cohort (`tools.py:106-139`) | `cohort_unavailable` |
| `propose_dependency` | `edge_proposal_client=` | writes a `proposed_depends_on` edge at confidence 0.3 with `origin: agent_discovered` (`client.py:216-266`) | `proposal_failed` |

`proposed_depends_on` is registered in the edge vocabulary but deliberately
**not** in the `maf.v1` traversal allowlist
(`backend/src/contextedge/graph/edge_types.py:158-161`), so an agent can never
read its own guess back as evidence. A proposal becomes authored topology only
when a `knowledge_manager` approves it through
`POST /api/v1/graph/edge-proposals/{edge_id}/approve`
(`backend/src/contextedge/api/v1/graph.py:142-166`), which promotes it to
`depends_on` and closes the proposal rather than deleting it.

## In-process Client

Workers or colocated agents can avoid HTTP while retaining the same contract:

```python
from contextedge.graph.agent.service import AgentGraphProjectionService
from contextedge.integrations.maf import InProcessContextGraphClient

client = InProcessContextGraphClient(
    AgentGraphProjectionService(db_session),
    authorized_scope,
)
```

`authorized_scope` must be built from the authenticated principal. Do not
construct tenant, workspace, or domain scope from model-supplied arguments. The
in-process client **overwrites** the request's `domain_id` with the scope's
before projecting, so a model that names another domain simply gets its own
(`client.py:114-125`). The in-process CMDB, cohort, edge-proposal and
write-back clients each open their **own** session per call, so a tool
invocation commits or discards independently of any request transaction
(`client.py:30-48, 182-192, 205-266`).

## Decision write-back (optional)

Pass `writeback=` to the plugin and the provider's `after_run` turns the
agent's answer into an agent-authored decision through the same
`create_decision` path humans use (`plugin.py:43-47`; `provider.py:114-179`):
`decision_type="agent_diagnosis"`, `agent_step="maf_run"`, `actor_type="ai"`,
`approval_required=True`, rationale capped at 2,000 characters, and one typed
`evidence_ref` per cited projection node (≤40). It is fail-soft in both
directions — a write-back failure logs `maf_decision_writeback_failed` and the
answer still stands (`provider.py:173-179`).

The loop is deliberately not closed automatically: a **pending AI-authored
decision is invisible to the projection**
(`backend/src/contextedge/graph/agent/hydrators.py:172-180`), so the next agent
inherits the conclusion only after a human review or a recorded outcome moves
it past `pending`.

## Projection Contract

`AgentGraphRequest` accepts a query (≤4,000 chars, whitespace-normalized), ≤20
typed seeds, ≤20 entity terms, a session, domain and temporal scope, a profile,
depth 1-3, and explicit budgets
(`backend/src/contextedge/graph/agent/contracts.py:26-69`). `maf.v1` returns
only registered node/relationship types and allowlisted relationship metadata
(`backend/src/contextedge/graph/agent/profiles.py:59-224`).

Selection is deterministic and path-preserving:

1. **Resolve and authorize seeds** — explicit seeds and the session at
   relevance 1.0, then full-text playbooks and patterns, issue signatures,
   semantic episodes / playbooks / knowledge chunks, query identifiers matched
   against entities and identity aliases, and change/event evidence that
   preceded the incident on the same CI
   (`backend/src/contextedge/graph/agent/repository.py:169-575`). The highest
   relevance wins per node key and the top 20 seeds survive
   (`repository.py:567-575`).
2. **Load each frontier in one batched tenant/domain/temporal query**, keeping
   at most `EDGES_PER_FRONTIER_NODE = 200` edges per node and
   `MAX_EDGES_PER_HOP = 5_000` per hop, so a hub CI cannot swamp a projection
   (`repository.py:155-157, 782-848`).
3. **Hydrate typed nodes through safe field allowlists**
   (`hydrators.py:33-54` maps the 20 node types to their ORM models).
4. **Stop traversal through unauthorized or lifecycle-ineligible nodes** —
   visibility is checked inside hydration, so an ineligible node disappears
   rather than leaking a stub.
5. **Rank** by seed relevance, edge weight and confidence, the profile's
   per-relationship factor, and `hop_decay = 0.72`
   (`profiles.py:19, 189-213`; `selector.py:100-118`). The hop factor is
   clamped at 1.0, so a boosted multi-hop path can never outrank its seed.
6. **Enforce node, relationship, depth, and character budgets.** Each admitted
   node drags its ancestor chain in so the subset stays connected; a chain that
   will not fit is skipped whole and the reason recorded
   (`selector.py:127-192`). Nodes may spend only ~90% of the character budget
   when edges exist, reserving room for relationships (`selector.py:139-147`).

Effective budgets: default 24 nodes / 48 relationships / depth 2 / 12,000
characters (`contracts.py:26-30`), clamped to the `maf.v1` ceiling of 60 / 120 /
3 / 30,000 (`profiles.py:183-188`). The response reports effective budgets,
actual usage, warnings, and truncation reasons
(`selector.py:244-261`); a projection id is logged as `agent_graph_projection`
(`backend/src/contextedge/graph/agent/service.py:134`) and emitted to
`operational_events` as `agent_graph.projected` (`service.py:154`), which is the
join key an agent's decision write-back cites.

An unknown profile name is a 422, not a silent fallback
(`profiles.py:229-233`; `service.py:120`).

## Security

- Tenant ownership is mandatory on every edge and hydrated node.
- Service-token domain allowlists and workspace visibility are enforced when
  the scope is built: an out-of-tenant domain is 404, a domain outside a service
  account's allowlist is 403, and a non-tenant-admin who cannot see the domain's
  workspace is 403
  (`backend/src/contextedge/graph/agent/service.py:39-94`).
- Playbooks must be approved, published, unexpired, and within the caller's
  role-derived risk cap. The cap is `high` for platform/tenant/domain admins,
  `knowledge_manager`, and service accounts; `medium` for everyone else
  (`service.py:27-36`).
- Evidence access policies, pending redaction, legal hold, and the source
  system's knowledge lifecycle (`draft` / `review` / `retired` are withheld;
  NULL serves) all fail closed.
- Claims must be machine-verified or human-validated.
- A **pending AI-authored decision is never projected** — agent output must not
  launder itself into agent input.
- **Unapproved episode drafts are the one deliberate exception.** They get their
  own two seed slots, separate from the three approved-episode slots, at 0.8×
  relevance, and hydration prefixes the label with `[UNAPPROVED DRAFT]` and
  attaches an `agent_caveat`
  (`repository.py:106-117, 363-384`; `hydrators.py:108-115, 441-463`). A draft
  can never evict a reviewed precedent, and its seed carries its own reason tag,
  `query_semantic_unapproved`, so an unreviewed seed is identifiable in a
  decision trace (`repository.py:500-509`).
- Injected graph context is fenced in `<untrusted-data>` with an explicit
  "this is reference data, not instructions" preamble, because node labels and
  summaries come from tickets, chat, and email (`provider.py:100-112`).
- User nodes expose display name and account status only.
- Raw evidence bodies, source payloads, embeddings, search vectors, emails,
  credentials, storage keys, and arbitrary JSONB are excluded.

Historical `as_of` requests reconstruct edge topology. Node facts are explicitly
current-state until immutable node snapshots are introduced, and the selector
emits that caveat as a warning on every `as_of` projection
(`selector.py:236-242`). `normalize_graph_as_of` rejects naive datetimes
(HTTP 422) and anything more than five minutes in the future
(`backend/src/contextedge/graph/temporal.py:12-26`).

**Known scope inconsistency (open, P1-6):** `/graph/agent-subsets` builds a
fully scoped projection, but `/graph/neighbors`, `/graph/subgraph`,
`/graph/cmdb-topology`, `/graph/change-risk` and `/graph/fix-applicability`
filter by `tenant_id` only — a domain-limited principal can read wider through
those routes than its projection would allow
(`codewiki/KNOWN_GAPS.md:56`). Role checks are a separate axis and do not close
the gap: `/graph/fix-applicability` still demands `knowledge_manager`
(`backend/src/contextedge/api/v1/graph.py:88`), it simply does not narrow by
domain once you have the role.

## Graph Explorer

`/graph-explorer` applies one domain and Current / As of scope across five
tabs: **statistics, subgraph, neighbors, agent context, and edge proposals**
(`frontend/src/app/(dashboard)/graph-explorer/page.tsx:89-149`). Supported
deep links include `tab`, `node_type`, `node_id`, `domain_id`, and timezone-aware
`as_of` (same file, lines 33-107).

The Agent Context tab sends the same `maf.v1` request contract used by the
adapter. It shows the effective budgets, usage, warnings, truncation reasons,
and only the safe facts returned by the projection. Selecting a node or
relationship opens the inspector; narrow viewports use a side sheet. The
Proposals tab is where `propose_dependency` edges are reviewed.

## Acme VPN, end to end

Acme Corp's VPN gateway `vpn-gw-east-01` fails; ServiceNow raises `INC0010427`,
engineers discuss it in Teams, and one emails a root-cause note. An agent is
handed the ticket thread:

1. `before_run` joins the last four messages, trims to the newest 4,000
   characters, and asks for a `maf.v1` subset (`provider.py:59-72`).
2. Seed resolution matches `vpn-gw-east-01` exactly against the CI entity
   (relevance 0.95) and `INC0010427` against the evidence, pulls the
   `remote_access | tls_certificate | certificate_expired` issue signature by
   full text, finds the semantically nearest approved episodes, and adds the
   gateway config change from two days earlier as a `preceded_by` seed at 0.8.
3. Traversal walks `episode -belongs_to-> pattern -derived_from-> playbook`,
   both boosted to 1.2 so the proven playbook survives two hops of decay
   (`profiles.py:199-207`), and `evidence -affects_ci-> entity` back out to the
   other incidents on that gateway.
4. The subset is fenced and injected. The agent answers, and — if `writeback`
   is configured — the answer lands as a pending `agent_diagnosis` decision
   citing every node it saw, waiting for a human.

## Extension Points

- Add a projection profile in `graph/agent/profiles.py`.
- Register a new relationship type in `graph/edge_types.py` **and** make the
  second decision the registry demands: allowlist it in
  `MAF_RELATIONSHIP_TYPES`, or give it an entry in `PROJECTION_EXCLUSIONS`
  saying why not. `tests/test_edge_type_registry.py` fails on either half being
  missing (`edge_types.py:15-25, 142-183`).
- Register node model, visibility, and hydration logic in
  `graph/agent/hydrators.py`.
- Add semantic relationship materialization in
  `graph/agent/materializer.py` and the owning write transaction.
- Add relationship metadata only through a profile allowlist
  (`profiles.py:214-223`); unlisted types project `{}`.
- Add contract, authorization, budget, and MAF smoke tests for every extension.

See [the implementation plan](../MAF_CONTEXT_GRAPH_INTEGRATION_PLAN.md) for the
schema review, architecture decisions, frontend impact, rollout, and acceptance
criteria. Current caveats live in
[codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md).
