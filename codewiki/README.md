# ContextEdge Code Wiki

This folder holds **technical blueprint explainers**: narrative, business-readable walkthroughs that still name real pipelines, modules, and important functions or classes. They complement the repository’s existing reference docs under `docs/` (especially [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md)).

## How to use this wiki

1. Read **[EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md)** first for voice, vocabulary, and the **shared example** used across articles.
2. Follow **[PLAN.md](./PLAN.md)** for the ordered list of planned articles, scope, and primary code anchors.
3. When an article is written, add it to the index below and link it from related pages.

## Article index

| Doc | Status |
| --- | --- |
| [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) | **Ready** — style and recurring scenario |
| [PLAN.md](./PLAN.md) | **Ready** — backlog and writing order |
| `01-end-to-end-pipeline.md` | Planned |
| `02-api-and-request-lifecycle.md` | Planned |
| `03-ingestion-connectors-and-sync.md` | Planned |
| `04-evidence-normalization-and-storage.md` | Planned |
| `05-search-hybrid-and-access.md` | Planned |
| `06-ai-extraction-and-embeddings.md` | Planned |
| `07-episodes-patterns-playbooks.md` | Planned |
| `08-workers-celery-queues.md` | Planned |
| `09-graph-and-correlation.md` | Planned |
| `10-governance-sessions-execution-audit.md` | Planned |
| `11-retention-and-operational-events.md` | Planned |

## Relationship to `docs/`

| `docs/` | `codewiki/` |
| --- | --- |
| Setup, API tables, runbooks | **Why** and **how it fits together**, with one coherent story |
| Concise architecture snapshot | Deeper paths into `backend/src/contextedge/…` with class/function callouts |

Maintainers should avoid duplicating long procedural steps from `docs/RUNBOOK.md` or `docs/SETUP_GUIDE.md`; link out instead and keep codewiki focused on mental models and design reasoning.
