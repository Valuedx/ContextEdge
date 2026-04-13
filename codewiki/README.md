# ContextEdge Code Wiki

This folder holds technical blueprint explainers and business-readable walkthroughs. The pages are written so a PM, operator, or business stakeholder can understand the product flow without losing the engineering anchor points that let developers jump straight into the code.

## How to use this wiki

1. Business readers should start with [00-business-capability-map.md](./00-business-capability-map.md), then [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md), then [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md).
2. Engineers who want the system shape first should start with [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) and follow the numbered deep dives.
3. Maintainers should read [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) before editing pages so vocabulary, voice, and the shared Acme VPN scenario stay consistent.
4. Check [KNOWN_GAPS.md](./KNOWN_GAPS.md) when the UI, worker wiring, or policy model behaves differently from the idealized flow in the articles.
5. Use [PLAN.md](./PLAN.md) for scope, cross-links, and maintenance expectations.

## Article index

| Doc | Description |
| --- | --- |
| [00-business-capability-map.md](./00-business-capability-map.md) | Business-first map of personas, outcomes, and product capabilities |
| [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) | Voice, template, vocabulary, and the Acme VPN scenario |
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

## Relationship to `docs/`

| `docs/` | `codewiki/` |
| --- | --- |
| Setup, API tables, runbooks | Why the product exists, how it fits together, and how business workflows map to code |
| Concise architecture snapshot | Deeper mental models, design tradeoffs, and module-level code maps |

Maintain this folder as an explanation layer, not a second runbook. Link to `docs/` for setup, commands, and route tables; keep `codewiki/` focused on product understanding, design reasoning, and navigation across the repo.
