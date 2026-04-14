# ContextEdge Code Wiki

This folder holds technical blueprint explainers and business-readable walkthroughs. The pages are written so a PM, operator, or business stakeholder can understand the product flow without losing the engineering anchor points that let developers jump straight into the code.

## How to use this wiki

### Business and product readers

Start here to understand what ContextEdge does, who uses it, and how operational knowledge flows through the product:

1. [00-business-capability-map.md](./00-business-capability-map.md) — What the product does end to end, which personas own which parts, and the business value at each stage.
2. [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) — What users actually see in the product: a day-in-the-life walkthrough of the dashboard.
3. [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — How one incident travels from raw ticket to approved playbook to runtime recommendation.
4. [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) — How organizations, roles, and policies are structured.

Each article includes an **"Example: Acme VPN data at this stage"** section with concrete data showing what goes in and what comes out at that layer — so you can trace a single VPN outage through the entire system without reading code.

### Engineering readers

Start with the system architecture, then follow the numbered deep dives:

1. [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — Full pipeline from connector to audit trail.
2. [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) through [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) — Vertical slices covering API, ingestion, search, AI, episodes, workers, graph, governance, retention, identity, and evaluation.
3. [08-workers-celery-queues.md](./08-workers-celery-queues.md) — Background task topology (useful for operations and debugging).

### Maintainers

- Read [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) before editing pages so vocabulary, voice, the shared Acme VPN scenario, and the example input/output format stay consistent.
- Check [KNOWN_GAPS.md](./KNOWN_GAPS.md) when the UI, worker wiring, or policy model behaves differently from the idealized flow in the articles.
- Use [PLAN.md](./PLAN.md) for scope, cross-links, and maintenance expectations.

## Article index

| Doc | Description |
| --- | --- |
| [00-business-capability-map.md](./00-business-capability-map.md) | Business-first map of personas, outcomes, and product capabilities |
| [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) | Voice, template, vocabulary, example format, and the Acme VPN scenario |
| [KNOWN_GAPS.md](./KNOWN_GAPS.md) | Implementation caveats, UI gaps, and operational footnotes |
| [PLAN.md](./PLAN.md) | Scope table, cross-links, maintenance, and article ordering |
| [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) | Full journey from sync to evidence to search to playbooks to audit |
| [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) | FastAPI, middleware, JWTs, service tokens, and request audit |
| [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) | Connectors, sync jobs, discovery, and handoff to normalization |
| [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) | Raw vs normalized evidence, object storage, and dedupe |
| [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) | FTS, vectors, hybrid ranking, access policies, and risk caps |
| [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) | Embeddings, classifiers, extractors, and model orchestration |
| [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) | Episodes, patterns, playbook lifecycle, and knowledge promotion |
| [08-workers-celery-queues.md](./08-workers-celery-queues.md) | Celery queues, task routing, retries, and beat |
| [09-graph-and-correlation.md](./09-graph-and-correlation.md) | Correlation edges, graph structure, and contradictions |
| [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) | Sessions, execution safety, operational events, and audit |
| [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) | Retention, legal hold, memory class, and event history |
| [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) | Canonical identities, alias matching, and thread hydration |
| [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) | Offline evaluation, drift alerts, and the feedback loop |
| [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) | Tenant hierarchy, roles, policies, and admin control plane behavior |
| [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) | Dashboard navigation and how business workflows map to APIs |

## Following one incident end to end

Every article uses the same **Acme Corp VPN outage** scenario. To trace a single incident through the entire system, read the "Example: Acme VPN data at this stage" sections in this order:

| Stage | Article | What you will see |
| --- | --- | --- |
| 1. Connect sources | [03](./03-ingestion-connectors-and-sync.md) | Jira connector fetches two VPN tickets; sync run records checkpoint |
| 2. Normalize evidence | [04](./04-evidence-normalization-and-storage.md) | Raw Jira JSON becomes a clean, searchable evidence item |
| 3. Resolve identities | [12](./12-identity-resolution-and-thread-hydration.md) | "jsmith," "John Smith," and "J. Smith (IT)" resolve to one person |
| 4. Classify and extract | [06](./06-ai-extraction-and-embeddings.md) | AI marks evidence as operational; proposes an episode with steps |
| 5. Build episodes | [07](./07-episodes-patterns-playbooks.md) | Draft episode links to pattern; playbook candidate enters review |
| 6. Connect relationships | [09](./09-graph-and-correlation.md) | Correlation edges link Jira ticket to Teams thread; graph scores playbooks; Graph Explorer visualizes the context network |
| 7. Search and rank | [05](./05-search-hybrid-and-access.md) | Analyst searches "VPN certificate expired"; hybrid ranking returns results |
| 8. Resolve and execute | [10](./10-governance-sessions-execution-audit.md) | Responder opens session; playbook execution enforces safety caps |
| 9. Monitor quality | [13](./13-evaluation-drift-and-feedback.md) | Evaluation run scores accuracy; drift scan flags negative feedback |
| 10. Manage retention | [11](./11-retention-and-operational-events.md) | Nightly job archives stale chat but preserves legal-hold items |

## Relationship to `docs/`

| `docs/` | `codewiki/` |
| --- | --- |
| Setup, API tables, runbooks | Why the product exists, how it fits together, and how business workflows map to code |
| Concise architecture snapshot | Deeper mental models, design tradeoffs, and module-level code maps |

Maintain this folder as an explanation layer, not a second runbook. Link to `docs/` for setup, commands, and route tables; keep `codewiki/` focused on product understanding, design reasoning, and navigation across the repo.
