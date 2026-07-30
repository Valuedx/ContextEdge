# ContextEdge UI Tabs - Backend File Map

This report shows which backend files support each UI tab. Use it when explaining where each screen gets its data from.

## Quick Rule

Frontend page = what user sees.

Backend API file = controller/router that receives requests from UI.

Service file = business logic behind the API.

Model file = database tables used by that feature.

## Quick Line Map

Use this table when someone asks: "Where is this tab code?"

`file:line` means the code starts around that line.

| Tab | Frontend page code | Backend API code | Main database/model code |
| --- | --- | --- | --- |
| Overview | `frontend/src/app/(dashboard)/overview/page.tsx:1` | `backend/src/contextedge/api/v1/sources.py:35`, `backend/src/contextedge/api/v1/evidence.py:26`, `backend/src/contextedge/api/v1/episodes.py:24`, `backend/src/contextedge/api/v1/playbooks.py:72`, `backend/src/contextedge/api/v1/runtime.py:89` | `backend/src/contextedge/models/source.py:11`, `backend/src/contextedge/models/evidence.py:47`, `backend/src/contextedge/models/episode.py:166`, `backend/src/contextedge/models/playbook.py:47` |
| Sources | `frontend/src/app/(dashboard)/sources/page.tsx:1` | `backend/src/contextedge/api/v1/sources.py:35` | `backend/src/contextedge/models/source.py:11` |
| Sync Operations | `frontend/src/app/(dashboard)/sync/page.tsx:1` | `backend/src/contextedge/api/v1/sync.py:13`, `backend/src/contextedge/api/v1/sources.py:253` | `backend/src/contextedge/models/source.py:128` |
| Evidence | `frontend/src/app/(dashboard)/evidence/page.tsx:1` | `backend/src/contextedge/api/v1/evidence.py:26`, `backend/src/contextedge/api/v1/threads.py:14` | `backend/src/contextedge/models/evidence.py:47` |
| Sessions | `frontend/src/app/(dashboard)/sessions/page.tsx:1` | `backend/src/contextedge/api/v1/sessions.py:23` | `backend/src/contextedge/models/session.py:11`, `backend/src/contextedge/models/session.py:101` |
| Runtime | `frontend/src/app/(dashboard)/runtime/page.tsx:1` | `backend/src/contextedge/api/v1/runtime.py:89` | `backend/src/contextedge/models/playbook.py:117`, `backend/src/contextedge/models/session.py:11` |
| Review Queue | `frontend/src/app/(dashboard)/review/page.tsx:1` | `backend/src/contextedge/api/v1/review_queue.py:30`, `backend/src/contextedge/api/v1/execution.py:226` | `backend/src/contextedge/models/execution.py:140`, `backend/src/contextedge/models/decision.py:43` |
| Execution | `frontend/src/app/(dashboard)/execution/page.tsx:1` | `backend/src/contextedge/api/v1/execution.py:61` | `backend/src/contextedge/models/execution.py:17`, `backend/src/contextedge/models/execution.py:140` |
| Decisions | `frontend/src/app/(dashboard)/decisions/page.tsx:1` | `backend/src/contextedge/api/v1/decisions.py:134` | `backend/src/contextedge/models/decision.py:43`, `backend/src/contextedge/models/decision.py:176` |
| Episodes | `frontend/src/app/(dashboard)/episodes/page.tsx:1` | `backend/src/contextedge/api/v1/episodes.py:24` | `backend/src/contextedge/models/episode.py:166`, `backend/src/contextedge/models/episode.py:188` |
| Patterns | `frontend/src/app/(dashboard)/patterns/page.tsx:1` | `backend/src/contextedge/api/v1/patterns.py:21` | `backend/src/contextedge/models/pattern.py:23` |
| Playbooks | `frontend/src/app/(dashboard)/playbooks/page.tsx:1` | `backend/src/contextedge/api/v1/playbooks.py:72` | `backend/src/contextedge/models/playbook.py:47`, `backend/src/contextedge/models/playbook.py:117` |
| Negative Knowledge | `frontend/src/app/(dashboard)/negative-knowledge/page.tsx:1` | `backend/src/contextedge/api/v1/negative_knowledge.py:17` | `backend/src/contextedge/models/pattern.py:63` |
| Identities | `frontend/src/app/(dashboard)/identities/page.tsx:1` | `backend/src/contextedge/api/v1/identities.py:27` | `backend/src/contextedge/models/episode.py:33`, `backend/src/contextedge/models/episode.py:71` |
| Correlations | `frontend/src/app/(dashboard)/correlations/page.tsx:1` | `backend/src/contextedge/api/v1/correlations.py:20` | `backend/src/contextedge/models/episode.py:153` |
| Graph Explorer | `frontend/src/app/(dashboard)/graph-explorer/page.tsx:1` | `backend/src/contextedge/api/v1/graph.py:18` | `backend/src/contextedge/models/pattern.py:135` |
| Contradictions | `frontend/src/app/(dashboard)/contradictions/page.tsx:1` | `backend/src/contextedge/api/v1/contradictions.py:17` | `backend/src/contextedge/models/pattern.py:76` |
| Drift | `frontend/src/app/(dashboard)/drift/page.tsx:1` | `backend/src/contextedge/api/v1/drift.py:19` | `backend/src/contextedge/models/playbook.py:47`, `backend/src/contextedge/models/pattern.py:23`, `backend/src/contextedge/models/decision.py:43` |
| Evaluations | `frontend/src/app/(dashboard)/evaluations/page.tsx:1` | `backend/src/contextedge/api/v1/evaluations.py:50` | `backend/src/contextedge/models/evaluation.py:11`, `backend/src/contextedge/models/evaluation.py:25` |
| Policies | `frontend/src/app/(dashboard)/policies/page.tsx:1` | `backend/src/contextedge/api/v1/policies.py:57`, `backend/src/contextedge/api/v1/policy_assignments.py:64` | `backend/src/contextedge/models/policy.py:15` |
| Audit Log | `frontend/src/app/(dashboard)/audit/page.tsx:1` | `backend/src/contextedge/api/v1/audit.py:14` | `backend/src/contextedge/models/audit.py:11` |
| LLM Cost | `frontend/src/app/(dashboard)/admin/cost/page.tsx:1` | `backend/src/contextedge/api/v1/admin_cost.py:31` | `backend/src/contextedge/models/events.py:13`, `backend/src/contextedge/models/tenant.py:116` |
| Settings | `frontend/src/app/(dashboard)/settings/page.tsx:1` | `backend/src/contextedge/api/v1/tenants.py:14`, `backend/src/contextedge/api/v1/workspaces.py:14`, `backend/src/contextedge/api/v1/domains.py:14`, `backend/src/contextedge/api/v1/users.py:22` | `backend/src/contextedge/models/tenant.py:12`, `backend/src/contextedge/models/tenant.py:30`, `backend/src/contextedge/models/tenant.py:48`, `backend/src/contextedge/models/tenant.py:68` |

## 1. Overview

**Frontend page:**
- `frontend/src/app/(dashboard)/overview/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sources.py`
- `backend/src/contextedge/api/v1/evidence.py`
- `backend/src/contextedge/api/v1/episodes.py`
- `backend/src/contextedge/api/v1/playbooks.py`
- `backend/src/contextedge/api/v1/runtime.py`

**Main database/model files:**
- `backend/src/contextedge/models/source.py`
- `backend/src/contextedge/models/evidence.py`
- `backend/src/contextedge/models/episode.py`
- `backend/src/contextedge/models/playbook.py`

**Simple meaning:**
Overview combines counts and health from multiple backend areas.

## 2. Sources

**Frontend page:**
- `frontend/src/app/(dashboard)/sources/page.tsx`
- `frontend/src/app/(dashboard)/sources/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sources.py`
- `backend/src/contextedge/api/v1/policy_assignments.py`

**Service files:**
- `backend/src/contextedge/connectors/*`
- `backend/src/contextedge/services/policy_assignment.py`

**Main database/model files:**
- `backend/src/contextedge/models/source.py`
- `backend/src/contextedge/models/policy.py`

**Simple meaning:**
Sources backend stores where data comes from and how it should be synced.

## 3. Sync Operations

**Frontend page:**
- `frontend/src/app/(dashboard)/sync/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sync.py`
- `backend/src/contextedge/api/v1/sources.py`

**Worker/service files:**
- `backend/src/contextedge/workers/extraction_tasks.py`
- `backend/src/contextedge/workers/hydration_tasks.py`
- `backend/src/contextedge/services/artifact_extraction_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/source.py`
- `backend/src/contextedge/models/evidence.py`

**Simple meaning:**
Sync backend runs imports from sources and tracks success/failure counts.

## 4. Evidence

**Frontend page:**
- `frontend/src/app/(dashboard)/evidence/page.tsx`
- `frontend/src/app/(dashboard)/evidence/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/evidence.py`
- `backend/src/contextedge/api/v1/threads.py`

**Service files:**
- `backend/src/contextedge/search/pg_fts.py`
- `backend/src/contextedge/search/access_control.py`
- `backend/src/contextedge/services/evidence_chunk_service.py`
- `backend/src/contextedge/services/artifact_extraction_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/evidence.py`
- `backend/src/contextedge/models/source.py`
- `backend/src/contextedge/models/policy.py`

**Simple meaning:**
Evidence backend stores logs, tickets, notes, files, and searchable facts.

## 5. Sessions

**Frontend page:**
- `frontend/src/app/(dashboard)/sessions/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/sessions.py`

**Service files:**
- `backend/src/contextedge/services/decision_trace_service.py`
- `backend/src/contextedge/services/memory_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/session.py`
- `backend/src/contextedge/models/decision.py`

**Simple meaning:**
Sessions backend stores the full case file for one issue.

## 6. Runtime

**Frontend page:**
- `frontend/src/app/(dashboard)/runtime/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/runtime.py`

**Service/search files:**
- `backend/src/contextedge/search/hybrid_ranker.py`
- `backend/src/contextedge/services/memory_service.py`
- `backend/src/contextedge/services/decision_trace_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py`
- `backend/src/contextedge/models/evidence.py`
- `backend/src/contextedge/models/pattern.py`
- `backend/src/contextedge/models/session.py`

**Simple meaning:**
Runtime backend is the recommendation engine. It searches evidence, past cases, patterns, and playbooks.

## 7. Review Queue

**Frontend page:**
- `frontend/src/app/(dashboard)/review/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/review_queue.py`
- `backend/src/contextedge/api/v1/execution.py`
- `backend/src/contextedge/api/v1/decisions.py`

**Service files:**
- `backend/src/contextedge/services/review_queue_service.py`
- `backend/src/contextedge/services/execution_service.py`
- `backend/src/contextedge/services/decision_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/execution.py`
- `backend/src/contextedge/models/decision.py`
- `backend/src/contextedge/models/session.py`

**Simple meaning:**
Review Queue backend shows pending human approvals and decision context.

## 8. Execution

**Frontend page:**
- `frontend/src/app/(dashboard)/execution/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/execution.py`

**Service files:**
- `backend/src/contextedge/services/execution_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/execution.py`
- `backend/src/contextedge/models/playbook.py`
- `backend/src/contextedge/models/session.py`

**Simple meaning:**
Execution backend starts, tracks, approves, modifies, denies, and completes playbook runs.

## 9. Decisions

**Frontend page:**
- `frontend/src/app/(dashboard)/decisions/page.tsx`
- `frontend/src/components/decisions/*`

**Backend files:**
- `backend/src/contextedge/api/v1/decisions.py`

**Service files:**
- `backend/src/contextedge/services/decision_service.py`
- `backend/src/contextedge/services/decision_trace_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/decision.py`
- `backend/src/contextedge/models/session.py`

**Simple meaning:**
Decisions backend stores what was decided, why, confidence, evidence, and outcomes.

## 10. Episodes

**Frontend page:**
- `frontend/src/app/(dashboard)/episodes/page.tsx`
- `frontend/src/app/(dashboard)/episodes/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/episodes.py`

**Service/worker files:**
- `backend/src/contextedge/services/episode_service.py`
- `backend/src/contextedge/workers/pattern_tasks.py`
- `backend/src/contextedge/ai/extractors/episode_extractor.py`

**Main database/model files:**
- `backend/src/contextedge/models/episode.py`
- `backend/src/contextedge/models/evidence.py`

**Simple meaning:**
Episodes backend reconstructs incident stories from evidence.

## 11. Patterns

**Frontend page:**
- `frontend/src/app/(dashboard)/patterns/page.tsx`
- `frontend/src/app/(dashboard)/patterns/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/patterns.py`

**Service/worker files:**
- `backend/src/contextedge/services/pattern_service.py`
- `backend/src/contextedge/workers/pattern_tasks.py`
- `backend/src/contextedge/ai/extractors/pattern_extractor.py`

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py`
- `backend/src/contextedge/models/episode.py`

**Simple meaning:**
Patterns backend groups repeated incidents and finds recurring root causes.

## 12. Playbooks

**Frontend page:**
- `frontend/src/app/(dashboard)/playbooks/page.tsx`
- `frontend/src/app/(dashboard)/playbooks/[id]/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/playbooks.py`

**Service/AI files:**
- `backend/src/contextedge/ai/generators/playbook_generator.py`
- `backend/src/contextedge/ai/prompts/playbook.py`

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py`
- `backend/src/contextedge/models/pattern.py`

**Simple meaning:**
Playbooks backend stores approved recovery steps and generated candidate playbooks.

## 13. Negative Knowledge

**Frontend page:**
- `frontend/src/app/(dashboard)/negative-knowledge/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/negative_knowledge.py`

**Related files:**
- `backend/src/contextedge/search/hybrid_ranker.py`
- `backend/src/contextedge/ai/generators/playbook_generator.py`

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py`

**Simple meaning:**
Negative Knowledge backend stores what not to do.

## 14. Identities

**Frontend page:**
- `frontend/src/app/(dashboard)/identities/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/identities.py`

**Service/worker files:**
- `backend/src/contextedge/services/identity_service.py`
- `backend/src/contextedge/services/identity_normalizer.py`
- `backend/src/contextedge/workers/identity_tasks.py`

**Main database/model files:**
- `backend/src/contextedge/models/episode.py`

**Simple meaning:**
Identities backend connects different names for the same real user, system, mailbox, workflow, or device.

## 15. Correlations

**Frontend page:**
- `frontend/src/app/(dashboard)/correlations/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/correlations.py`

**Service/worker files:**
- `backend/src/contextedge/services/correlation_service.py`
- `backend/src/contextedge/workers/correlation_tasks.py`

**Main database/model files:**
- `backend/src/contextedge/models/episode.py`
- `backend/src/contextedge/models/session.py`
- `backend/src/contextedge/models/evidence.py`

**Simple meaning:**
Correlations backend links related evidence items.

## 16. Graph Explorer

**Frontend page:**
- `frontend/src/app/(dashboard)/graph-explorer/page.tsx`
- `frontend/src/components/graph/*`

**Backend files:**
- `backend/src/contextedge/api/v1/graph.py`

**Graph/service files:**
- `backend/src/contextedge/graph/queries.py`
- `backend/src/contextedge/graph/builder.py`
- `backend/src/contextedge/graph/agent/service.py`
- `backend/src/contextedge/graph/agent/repository.py`
- `backend/src/contextedge/graph/agent/hydrators.py`
- `backend/src/contextedge/graph/agent/materializer.py`

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py`
- `backend/src/contextedge/models/episode.py`
- `backend/src/contextedge/models/evidence.py`
- `backend/src/contextedge/models/decision.py`
- `backend/src/contextedge/models/session.py`
- `backend/src/contextedge/models/execution.py`

**Simple meaning:**
Graph Explorer backend shows relationships between evidence, sessions, decisions, playbooks, identities, and policies.

## 17. Contradictions

**Frontend page:**
- `frontend/src/app/(dashboard)/contradictions/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/contradictions.py`

**Service files:**
- `backend/src/contextedge/services/contradiction_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/pattern.py`
- `backend/src/contextedge/models/playbook.py`
- `backend/src/contextedge/models/evidence.py`

**Simple meaning:**
Contradictions backend finds conflicting evidence or playbook claims.

## 18. Drift

**Frontend page:**
- `frontend/src/app/(dashboard)/drift/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/drift.py`

**Service files:**
- `backend/src/contextedge/services/drift_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/playbook.py`
- `backend/src/contextedge/models/pattern.py`
- `backend/src/contextedge/models/decision.py`

**Simple meaning:**
Drift backend detects when old playbooks or patterns may no longer be safe/current.

## 19. Evaluations

**Frontend page:**
- `frontend/src/app/(dashboard)/evaluations/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/evaluations.py`

**Service/worker files:**
- `backend/src/contextedge/services/evaluation_service.py`
- `backend/src/contextedge/workers/evaluation_tasks.py`

**Main database/model files:**
- `backend/src/contextedge/models/evaluation.py`

**Simple meaning:**
Evaluations backend tests quality of retrieval, recommendations, and generated results.

## 20. Policies

**Frontend page:**
- `frontend/src/app/(dashboard)/policies/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/policies.py`
- `backend/src/contextedge/api/v1/policy_assignments.py`

**Service files:**
- `backend/src/contextedge/services/policy_assignment.py`

**Main database/model files:**
- `backend/src/contextedge/models/policy.py`

**Simple meaning:**
Policies backend controls access, retention, classification, and approval rules.

## 21. Audit Log

**Frontend page:**
- `frontend/src/app/(dashboard)/audit/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/audit.py`

**Service/middleware files:**
- `backend/src/contextedge/middleware/audit.py`
- `backend/src/contextedge/middleware/request_audit.py`

**Main database/model files:**
- `backend/src/contextedge/models/audit.py`

**Simple meaning:**
Audit backend records who did what and when.

## 22. LLM Cost

**Frontend page:**
- `frontend/src/app/(dashboard)/admin/cost/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/admin_cost.py`

**Service files:**
- `backend/src/contextedge/services/admin_cost_service.py`
- `backend/src/contextedge/services/tenant_budget_service.py`

**Main database/model files:**
- `backend/src/contextedge/models/events.py`
- `backend/src/contextedge/models/tenant.py`

**Simple meaning:**
LLM Cost backend tracks AI token usage, cost, and budget limits.

## 23. Settings

**Frontend page:**
- `frontend/src/app/(dashboard)/settings/page.tsx`

**Backend files:**
- `backend/src/contextedge/api/v1/tenants.py`
- `backend/src/contextedge/api/v1/workspaces.py`
- `backend/src/contextedge/api/v1/domains.py`
- `backend/src/contextedge/api/v1/users.py`

**Main database/model files:**
- `backend/src/contextedge/models/tenant.py`

**Simple meaning:**
Settings backend manages tenant, workspace, domain, and user configuration.

## Demo Explanation

Use this short line:

Each tab has a frontend page for display and a backend API file for data. The API file calls service logic, and service logic reads or writes database model files.
