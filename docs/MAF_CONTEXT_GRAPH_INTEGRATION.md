# Microsoft Agent Framework Context Graph Integration

ContextEdge exposes a framework-neutral, versioned graph projection and a thin
Microsoft Agent Framework (MAF) adapter. An agent can receive graph context
proactively through a `ContextProvider`, call it on demand as a `FunctionTool`,
or use both modes.

## Install

```powershell
cd backend
.\venv\Scripts\python.exe -m pip install -e ".[maf]"
```

The base API and projection service do not import MAF. Client-only imports also
remain available without the optional extra.

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

Use `enable_provider=False` for tool-only operation or `enable_tool=False` for
proactive injection only. The provider is fail-soft: projection unavailability
is logged and does not fail the agent run. The tool propagates API errors.

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
construct tenant, workspace, or domain scope from model-supplied arguments.

## Projection Contract

`AgentGraphRequest` accepts a query, typed seeds, a session, entity terms,
domain and temporal scope, a profile, depth, and explicit budgets. `maf.v1`
returns only registered node/relationship types and allowlisted metadata.

Selection is deterministic and path-preserving:

1. Resolve and authorize seeds.
2. Load each frontier in a batched tenant/domain/temporal query.
3. Hydrate typed nodes through safe field allowlists.
4. Stop traversal through unauthorized or lifecycle-ineligible nodes.
5. Rank by seed relevance, edge weight/confidence, freshness, and hop decay.
6. Enforce node, relationship, depth, and character budgets.

The response reports effective budgets, actual usage, warnings, and truncation
reasons. A projection ID is logged and emitted to the operational event stream.

## Security

- Tenant ownership is mandatory on every edge and hydrated node.
- Service-token domain allowlists and workspace visibility are enforced.
- Playbooks must be approved, published, unexpired, and within the caller's
  role-derived risk cap.
- Evidence access policies, pending redaction, and legal hold fail closed.
- Claims must be machine-verified or human-validated.
- User nodes expose display name and account status only.
- Raw evidence bodies, source payloads, embeddings, search vectors, emails,
  credentials, storage keys, and arbitrary JSONB are excluded.

Historical `as_of` requests reconstruct edge topology. Node facts are explicitly
current-state until immutable node snapshots are introduced.

## Graph Explorer

`/graph-explorer` applies one domain and Current / As of scope across statistics,
subgraph, neighbors, and agent preview tabs. Supported deep links include
`tab`, `node_type`, `node_id`, `domain_id`, and timezone-aware `as_of`.

The Agent Context tab sends the same `maf.v1` request contract used by the
adapter. It shows the effective budgets, usage, warnings, truncation reasons,
and only the safe facts returned by the projection. Selecting a node or
relationship opens the inspector; narrow viewports use a side sheet.

## Extension Points

- Add a projection profile in `graph/agent/profiles.py`.
- Register node model, visibility, and hydration logic in
  `graph/agent/hydrators.py`.
- Add semantic relationship materialization in
  `graph/agent/materializer.py` and the owning write transaction.
- Add relationship metadata only through a profile allowlist.
- Add contract, authorization, budget, and MAF smoke tests for every extension.

See [the implementation plan](../MAF_CONTEXT_GRAPH_INTEGRATION_PLAN.md) for the
schema review, architecture decisions, frontend impact, rollout, and acceptance
criteria.
