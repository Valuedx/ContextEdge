# ContextEdge Code Wiki

This folder holds **technical blueprint explainers**: narrative, business-readable walkthroughs that still name real pipelines, modules, and important functions or classes. They complement the repository’s existing reference docs under `docs/` (especially [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md)).

## How to use this wiki

1. Read **[EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md)** first for voice, vocabulary, and the **shared example** used across articles.
2. Skim **[KNOWN_GAPS.md](./KNOWN_GAPS.md)** for registry, worker queue, and dedupe caveats before debugging ingestion.
3. Follow **[PLAN.md](./PLAN.md)** for writing order, scope, and how pages link.
4. Start with **[01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md)** if you want one pass over the whole system, then drill into numbered topics.

## Article index

| Doc | Description |
| --- | --- |
| [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) | Voice, template, **Acme VPN** scenario |
| [KNOWN_GAPS.md](./KNOWN_GAPS.md) | Connector registry, sync queue, dedupe, retention wiring |
| [PLAN.md](./PLAN.md) | Scope table, cross-links, maintenance |
| [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) | Full journey: sync → evidence → search → playbooks → audit |
| [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) | FastAPI, middleware, JWT/service tokens, audit |
| [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) | Connectors, sync jobs, handoff to normalize |
| [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) | Raw vs evidence, object store, dedupe, normalize worker |
| [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) | FTS, vectors, hybrid ranker, access and risk |
| [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) | LiteLLM, embeddings, classifiers, extractors |
| [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) | Episodes, patterns, playbook lifecycle |
| [08-workers-celery-queues.md](./08-workers-celery-queues.md) | Celery queues, tasks, beat |
| [09-graph-and-correlation.md](./09-graph-and-correlation.md) | Correlation edges, graph, contradictions |
| [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) | Sessions, execution safety, audit & events |
| [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) | Retention, legal hold, memory class, events |

## Relationship to `docs/`

| `docs/` | `codewiki/` |
| --- | --- |
| Setup, API tables, runbooks | **Why** and **how it fits together**, with one coherent story |
| Concise architecture snapshot | Deeper paths into `backend/src/contextedge/…` with class/function callouts |

Maintainers should avoid duplicating long procedural steps from `docs/RUNBOOK.md` or `docs/SETUP_GUIDE.md`; link out instead and keep codewiki focused on mental models and design reasoning.
