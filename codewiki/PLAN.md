# Plan: codewiki technical blueprint explainers

This document began as the writing backlog for `codewiki/`. The published series is now complete and includes both engineering deep dives and business-facing entry points. New edits should still follow [EDITORIAL-GUIDE.md](./EDITORIAL-GUIDE.md) and reuse the Acme VPN incident thread.

## Guiding principles

- **Give business readers an entry point before the deep internals** so the numbered system pages have context.
- **Explain the pipeline first**, then vertical slices such as search, AI, workers, control plane, and dashboard workflows.
- **Design decisions** must state why the choice exists and what tradeoff or residual risk remains.
- **Function or class detail belongs in a Code map table**, not hidden in long prose paragraphs.
- **Do not duplicate** long command lists from `docs/RUNBOOK.md` or `docs/SETUP_GUIDE.md`; link instead.

## Article map (all published)

| Order | File | Primary focus | Doc + anchor modules |
| --- | --- | --- | --- |
| 0 | [00-business-capability-map.md](./00-business-capability-map.md) | Business outcomes, personas, capability map | `api/v1/__init__.py`, `models/tenant.py`, `services/playbook_service.py`, `services/runtime_service.py`, `frontend/src/components/shell/sidebar-nav.tsx` |
| 1 | [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) | Connector -> evidence -> search -> playbook -> audit | `main.py`, `api/v1/__init__.py`, `ingestion_persistence.py`, `sync_worker_service.py`, `extraction_tasks.py`, `hybrid_ranker.py`, `celery_app.py` |
| 2 | [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) | FastAPI, middleware, JWT and service tokens, routing | `main.py`, `middleware/request_context.py`, `middleware/request_audit.py`, `deps.py`, `security_tokens.py`, `api/v1/__init__.py` |
| 3 | [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) | Connectors, sync jobs, discovery, handoff to normalize | `connectors/base.py`, `connectors/registry.py`, `services/sync_worker_service.py`, `services/sync_ingestion_queue.py`, `api/v1/sync.py`, `api/v1/sources.py` |
| 4 | [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) | Raw offload, `EvidenceItem`, dedupe | `services/object_store.py`, `services/ingestion_persistence.py`, `services/evidence_normalization.py`, `workers/extraction_tasks.py`, `models/evidence.py` |
| 5 | [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) | FTS, vectors, hybrid ranker, access, risk | `search/pg_fts.py`, `search/vector_search.py`, `search/hybrid_ranker.py`, `search/access_control.py`, `search/risk_policy.py`, `api/v1/evidence.py` |
| 6 | [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) | LiteLLM, embeddings, classifiers, extractors | `ai/provider.py`, `ai/embeddings.py`, `ai/extractors/`, `ai/classifiers/`, `workers/extraction_tasks.py` |
| 7 | [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) | Episodes, patterns, playbook lifecycle | `services/episode_service.py`, `services/pattern_service.py`, `services/playbook_service.py`, `models/episode.py`, `models/pattern.py`, `models/playbook.py`, `api/v1/episodes.py`, `api/v1/patterns.py`, `api/v1/playbooks.py` |
| 8 | [08-workers-celery-queues.md](./08-workers-celery-queues.md) | Queues, tasks, beat | `workers/celery_app.py`, `workers/extraction_tasks.py`, `workers/pattern_tasks.py`, `workers/correlation_tasks.py`, `workers/artifact_tasks.py`, `workers/evaluation_tasks.py`, `workers/hydration_tasks.py` |
| 9 | [09-graph-and-correlation.md](./09-graph-and-correlation.md) | Correlation edges, graph, contradictions | `graph/builder.py`, `graph/queries.py`, `services/correlation_service.py`, `services/contradiction_service.py` |
| 10 | [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) | Sessions, execution, audit, operational events | `services/session_service.py`, `services/execution_service.py`, `middleware/audit.py`, `middleware/request_audit.py`, `services/event_log_service.py`, `api/v1/sessions.py`, `api/v1/execution.py` |
| 11 | [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) | Retention, legal hold, memory class | `services/retention_service.py`, `services/memory_service.py`, `models/events.py`, `schemas/tenant.py` |
| 12 | [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) | Canonical identities, alias matching, thread hydration | `services/identity_service.py`, `ai/extractors/identity_extractor.py`, `workers/hydration_tasks.py`, `models/episode.py` |
| 13 | [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) | Offline eval runs, drift alerts, retrieval feedback | `services/evaluation_service.py`, `services/drift_service.py`, `workers/evaluation_tasks.py`, `models/evaluation.py` |
| 14 | [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) | Tenant hierarchy, roles, policies, assignments | `models/tenant.py`, `models/policy.py`, `api/v1/tenants.py`, `api/v1/users.py`, `api/v1/policies.py`, `api/v1/policy_assignments.py`, `frontend/src/app/(dashboard)/settings/page.tsx` |
| 15 | [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) | Dashboard navigation and business workflows | `frontend/src/components/shell/sidebar-nav.tsx`, `frontend/src/components/shell/app-header.tsx`, `frontend/src/app/(dashboard)/overview/page.tsx`, `frontend/src/app/(dashboard)/sources/`, `frontend/src/app/(dashboard)/runtime/page.tsx`, `frontend/src/lib/api.ts` |

## Per-article checklist (for future edits)

```markdown
## Summary
## Business picture
## Technical walkthrough
## Design decisions
## Code map
## Acme VPN incident (this layer)
## Further reading
```

## Cross-linking graph

```mermaid
flowchart LR
  P0[00 Business capability]
  P1[01 Pipeline]
  P2[02 API lifecycle]
  P3[03 Connectors sync]
  P4[04 Evidence storage]
  P5[05 Search access]
  P6[06 AI embeddings]
  P7[07 Episodes patterns playbooks]
  P8[08 Workers]
  P9[09 Graph correlation]
  P10[10 Governance audit]
  P11[11 Retention events]
  P12[12 Identity hydration]
  P13[13 Eval drift feedback]
  P14[14 Control plane]
  P15[15 Dashboard workflows]
  P0 --> P1
  P0 --> P14
  P0 --> P15
  P1 --> P2
  P1 --> P3
  P3 --> P4
  P4 --> P5
  P4 --> P6
  P6 --> P7
  P8 --> P4
  P8 --> P6
  P8 --> P7
  P7 --> P9
  P7 --> P10
  P4 --> P11
  P10 --> P11
  P4 --> P12
  P6 --> P12
  P9 --> P12
  P7 --> P13
  P5 --> P13
  P8 --> P13
  P14 --> P2
  P14 --> P5
  P14 --> P10
  P15 --> P3
  P15 --> P5
  P15 --> P7
  P15 --> P10
  P15 --> P13
```

## Maintenance

- When Alembic head or queue names change, update **01** and **08** and link to `docs/MIGRATIONS.md` or the runbook instead of duplicating procedures.
- When the role model, tenant hierarchy, or policy attachment rules change, update **14** and [KNOWN_GAPS.md](./KNOWN_GAPS.md).
- When dashboard navigation, page names, or major UI workflows change, update **15** and the article index in [README.md](./README.md).
- If a service or page is renamed, grep `codewiki/` for the old path and refresh the affected Code map tables.

## Series completion checklist

- [x] Every planned article exists in `codewiki/`.
- [x] [README.md](./README.md) lists each file with a one-line description.
- [x] Mermaid (or equivalent) diagram exists for the main pipeline and worker topology.
- [x] [00-business-capability-map.md](./00-business-capability-map.md) gives non-engineers an entry point.
- [x] [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) covers tenant structure, roles, and policies.
- [x] [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) covers the business-facing dashboard surface.
