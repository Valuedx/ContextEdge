# ContextEdge ↔ AEAIHub Integration Plan

> **Scope**: how ContextEdge and AEAIHubOrchestrator (plus AutomationEdge as the execution layer) compose into a single operational stack. Supersedes [`DEMO_ARCHITECTURE_PLAN.md`](DEMO_ARCHITECTURE_PLAN.md) Section 5's Option 2b treatment by going deeper now that AIHub is confirmed modifiable.
>
> **Date**: 2026-04-22
> **Status**: Architecture locked — Q1 execution plan ready
>
> **Related docs**:
> - [`ENTERPRISE_ARCHITECTURE_REVIEW.md`](ENTERPRISE_ARCHITECTURE_REVIEW.md) — ContextEdge-only architecture review
> - [`DEMO_ARCHITECTURE_PLAN.md`](DEMO_ARCHITECTURE_PLAN.md) — original demo-use-case planning, especially the CMDB / federation lens
> - [`CONTEXTEDGE_IMPLEMENTATION_PLAN.md`](CONTEXTEDGE_IMPLEMENTATION_PLAN.md) — ContextEdge roadmap
> - [`codewiki/16-decision-traces.md`](codewiki/16-decision-traces.md) — decision graph + review queue model
> - External: `D:/Projects/AEAIHubOrchestrator` (sibling repo)

## Contents

1. [Context — why this doc exists](#1-context--why-this-doc-exists)
2. [The three-system architecture, confirmed](#2-the-three-system-architecture-confirmed)
3. [Current-state scorecard](#3-current-state-scorecard)
4. [Design principle — modify, don't fuse](#4-design-principle--modify-dont-fuse)
5. [Modifications to AIHub that deliver leverage](#5-modifications-to-aihub-that-deliver-leverage)
6. [Traps to avoid](#6-traps-to-avoid)
7. [ContextEdge-side additions](#7-contextedge-side-additions)
8. [Shared-auth decision](#8-shared-auth-decision)
9. [Q1 roadmap](#9-q1-roadmap)
10. [Dependency ordering + recommended start](#10-dependency-ordering--recommended-start)
11. [Open questions and deferred items](#11-open-questions-and-deferred-items)
12. [Appendix A — canonical contract shapes](#appendix-a--canonical-contract-shapes)
13. [Appendix B — how to update this document](#appendix-b--how-to-update-this-document)

---

## 1. Context — why this doc exists

Earlier architecture reviews (both enterprise + demo) catalogued what a reasoning-over-execution IT-ops platform should look like — a reasoning engine, context graph, review console, playbook library, policy gates, intent router, and execution layer. Rendered as if one platform had to ship all of it, the gap list is long (18 items in [`ENTERPRISE_ARCHITECTURE_REVIEW.md`](ENTERPRISE_ARCHITECTURE_REVIEW.md)) and mostly concerns ContextEdge.

Rendering it against the three systems that **actually exist**:

- **ContextEdge** (this repo) — decision graph, reviewer console, learning loop, LLM observability
- **AEAIHubOrchestrator** (sibling repo at `D:/Projects/AEAIHubOrchestrator`) — agentic workflow engine with ReAct, HITL pause/resume, tight AE integration, MCP dispatch
- **AutomationEdge** — execution layer with 700+ connectors, bot farm, credentials vault

…the gap list shrinks dramatically, and the critical work relocates from "build missing components" to **"wire the two existing agentic services together correctly."**

This document records that architectural decision and the Q1 plan derived from it.

## 2. The three-system architecture, confirmed

```
┌─────────────────────────────────────────────────────────────┐
│  AEAIHubOrchestrator                                         │
│    • Reasoning Engine (ReAct loop)                          │
│    • Intent & Event Router (webhook triggers + HITL)         │
│    • Workflow DAG execution + pause/resume                   │
│    • MCP tool dispatch (tenant-aware)                        │
│    • AE integration (automationedge_client + node type)      │
│    • JWT-based service auth                                  │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼ (already built, tight)
┌─────────────────────────────────────────────────────────────┐
│  AutomationEdge                                              │
│    • 700+ connectors, bot farm, credentials vault            │
│    • Workflow execution, Process Studio authoring            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ContextEdge                                                 │
│    • Decision graph (3-tier capture)                         │
│    • Review console /review                                  │
│    • Playbook lifecycle + PlaybookStep metadata              │
│    • Learning loop (structured reject codes + effectiveness) │
│    • LLM cost observability                                  │
│    • Evidence + correlation + baselines                      │
└─────────────────────────────────────────────────────────────┘

               ? — NO WIRES TODAY BETWEEN AIHUB AND CONTEXTEDGE
```

Verified facts from a targeted scan of AIHub:

- **ReAct loop** — `backend/app/engine/react_loop.py:27-37`, 25-iteration cap, multi-provider
- **AE integration** — `backend/app/engine/automationedge_client.py` with async job tracking via `AsyncJob` table; "AutomationEdge" node type at `app/engine/node_handlers.py:117-118`
- **MCP client** — `backend/app/engine/mcp_client.py`, tenant-aware session pool, 300s tool cache
- **HITL pause/resume** — `backend/app/engine/dag_runner.py:120-137, 380+` with `_finalize_paused` / `resume_paused_graph`
- **Webhook callback** — `POST /{workflow_id}/instances/{instance_id}/callback` at `backend/app/api/workflows.py:624-646`
- **ContextEdge references in AIHub** — grep-confirmed **zero matches**

## 3. Current-state scorecard

Each row of the essay's six-component architecture mapped to one of the three systems:

| Component | Owner | Status |
|---|---|---|
| Intent & Event Router | AEAIHub | ✅ Webhook triggers, JWT auth, tenant scoping in place |
| Context Graph Service | ContextEdge | 🟢 ~65% — strong edge vocabulary, decision graph, identity resolution. Missing: first-class User/Device/Group taxonomy, staleness/provenance on edges |
| Reasoning Engine | AEAIHub | ✅ ReAct loop present. Does not yet pull from ContextEdge's graph |
| Playbook Library | **Split** | 🟡 AIHub: 2 frontend templates only. ContextEdge: lifecycle + step metadata exist. `tool_ref` not yet bound to AIHub workflow IDs |
| Confidence & Policy | ContextEdge | 🔴 ~20% — scattered Python conditionals, no declarative rule engine |
| Review Console | ContextEdge | 🟢 ~75% — zones 2/3/5/7 shipped. Approve/Modify/Reject **does not call AIHub's callback endpoint** — loop open |
| Ticket Ingest Miner | ContextEdge | 🟡 ~50% — per-evidence works; batch-at-scale playbook proposal doesn't |
| Knowledge Ingestion | ContextEdge | 🟡 ~30% — contradiction mechanism exists, Confluence/SharePoint connectors missing |
| Telemetry Collector | AIHub + ContextEdge | 🟡 AIHub can live-query via MCP; ContextEdge has schema waiting for data. Nothing flows between |

**Headline**: our reviewer console, decision graph, and learning-loop telemetry are genuinely strong. AIHub's ReAct + AE + MCP stack is mature. The critical missing piece is **the seams between them**.

## 4. Design principle — modify, don't fuse

With AIHub confirmed modifiable, the design space widens. Three hard lines worth holding even so:

1. **Separate databases.** ContextEdge owns Sessions/Decisions/graph_edges; AIHub owns Instances/workflows. Merging schemas creates coupling nightmares at scale.
2. **Separate deployments.** Each service deploys independently. Tight in-process coupling is brittle.
3. **Separate UIs.** ContextEdge's `/review` stays the reviewer surface. AIHub's workflow-author UI stays in AIHub. Don't mash them — operators see different surfaces for different jobs.

Inside those boundaries, **four deep AIHub modifications deliver outsized leverage** vs. a thin HTTP wrapper, plus **three ContextEdge-side additions**. The rest stays loosely coupled via HTTP.

## 5. Modifications to AIHub that deliver leverage

### M.1 — `awaiting_external_review` as a first-class Instance state

**Why**: AIHub's generic HITL pause (`_finalize_paused`, `resume_paused_graph`) assumes a human clicking in AIHub's UI on a minutes-timescale with free-form context patch. External reviewer pause is semantically different:

- Verdict comes from an external service (ContextEdge), not AIHub's UI
- Resume payload is **structured** (`ReviewVerdict = {approved|modified|rejected, code, modification_diff}`)
- Timeout window is hours/days, not minutes — escalation logic differs
- Dashboarding benefit: "37 instances awaiting external review, 3 exceeded SLA" is a real operator question

**Fix in AIHub**:

- Add `Instance.status = "awaiting_external_review"` value to the Instance state machine
- New fields: `external_review_system` (e.g., `"contextedge"`), `external_review_id` (the ContextEdge decision_id), `external_review_due_at`
- `callback` endpoint gets a typed path for external-review resumes that validates the body against a `ReviewVerdict` schema
- Dashboard query support: index on `(external_review_system, external_review_due_at)` so "overdue external reviews" is one index hit

**Effort**: ~1 week.

**Win**: a workflow node called `contextedge.wait_for_review` targets this state directly; operators gain external-review visibility without bolt-on dashboards; SLA timeouts fire escalation reliably.

### M.2 — Built-in ContextEdge tools in the ReAct loop

**Why**: `react_loop.py:27-37` assembles the tool list per workflow. Today, workflow authors have to wire context fetching explicitly into their DAGs. That means agents reason blind unless someone remembered to add context lookups upstream — which defeats the whole point of an agentic loop.

**Fix in AIHub**: when a workflow's tenant has ContextEdge configured (env var / tenant setting), the ReAct loop auto-registers these tools without the workflow author declaring them:

- `contextedge_find_similar(query, decision_type?)` — semantic similar-decision retrieval backed by `GET /api/v1/decisions/similar/aggregate`
- `contextedge_get_provenance(decision_id)` — evidence, episodes, patterns via `GET /api/v1/decisions/{id}/provenance`
- `contextedge_check_contradictions(playbook_id)` — flag if the playbook's approved steps contradict recent evidence
- `contextedge_fetch_graph_context(entity_id, depth=1)` — pull a subgraph slice via `GET /api/v1/graph/subgraph/{entity_type}/{entity_id}`

**Effort**: ~1 week, tools module + ReAct-loop registration hook.

**Win**: every workflow becomes context-aware by default. The practical difference between "agentic workflow" and "DAG with LLM nodes" is exactly whether the agent naturally reaches for historical context. This is that lever.

### M.3 — Shared contracts package (`aeops-contracts`)

**Why**: without this, I'd be writing `ReviewVerdict` as a Pydantic model in ContextEdge's `schemas/` and a drifting duplicate in AIHub's `app/api/schemas.py`. Guaranteed to diverge.

**Fix**: tiny shared package `aeops-contracts` (standalone repo or monorepo sub-package). Publishes Pydantic models for:

- `ReviewVerdict` — approve / modify / reject + reason code + `modification_diff`
- `TriggerPayload` — canonical shape from monitoring / ITSM webhooks
- `DecisionRecord` — what ContextEdge returns when asked about a decision
- `RejectionReasonCode` — the existing 6-code enum (already in ContextEdge at `models/decision.py::REJECTION_REASON_CODES`)
- `SafetyClass` — existing 4-level enum
- `AutomationMode` — `suggest_only` / `supervised` / `full_auto` / `shadow`
- `SessionBrief` — minimal session shape for cross-system references

Both projects `pip install aeops-contracts` and import from it. **CI gate**: if a PR bumps the contracts package, both consumers must rebuild against the new version before merging.

See [Appendix A](#appendix-a--canonical-contract-shapes) for initial field shapes.

**Effort**: ~3 days (package scaffolding + initial models + CI wiring).

**Win**: prevents an entire category of schema-drift bugs. Unblocks every subsequent piece. Should ship first.

### M.4 — Shared auth via a common signer

**Why**: service-to-service auth without a shared trust root means rotating shared secrets forever.

**Two options**:

- **Option A (production-grade)**: shared IdP — Keycloak, Entra, or Auth0. Both projects verify tokens against the same JWKS URL. Service-to-service auth is "mint a short-lived token with the right scope from the IdP, send it."
- **Option B (demo-grade, 1-day fix)**: ContextEdge and AIHub share a signing key for service-to-service tokens. One project mints, the other verifies. Still bearer JWTs; just a common secret/key pair.

**Recommendation**: ship B for the demo, plan A for production rollout. A without B means blocking on IdP procurement; B without A means rotating shared secrets forever.

**Effort**: B = ~1 day; A = 2-4 weeks depending on IdP procurement.

## 6. Traps to avoid

Tempting modifications that cost more than they earn:

- **Merging session ↔ instance models.** It sounds elegant ("one state machine for a ticket's lifecycle!") but it forces ContextEdge to ship AIHub's workflow-execution dependencies and vice versa. Keep them as separate concepts linked by ID. `ContextEdge.ResolutionSession.external_instance_id` points at `AIHub.Instance.id`; `AIHub.Instance.context["contextedge_session_id"]` points back. **Loose coupling via correlation, not fusion.**
- **Making AIHub emit ContextEdge-shaped events natively.** That creates a dependency from AIHub's core toward ContextEdge. Keep it the other way: AIHub's `callback` endpoint speaks a generic external-review protocol; ContextEdge-specific glue lives in the ContextEdge-side client.
- **Having ContextEdge's reviewer UI mutate AIHub instances directly.** The callback indirection (reviewer → ContextEdge → AIHub's callback endpoint → resume) is slightly more work but keeps data-ownership clean. A ContextEdge bug can never corrupt an AIHub instance.

## 7. ContextEdge-side additions

### I.1 — `contextedge_client.py` in AIHub

Typed HTTP wrapper, modelled on the existing `automationedge_client.py`:

```python
async def open_session(tenant_id, symptoms, entities, external_case_ids) → session_id
async def fetch_similar_aggregate(tenant_id, decision_type, context) → SimilarDecisionsAggregateResponse
async def fetch_provenance(tenant_id, decision_id) → DecisionProvenanceResponse
async def record_decision(tenant_id, session_id, options, confidence, rationale) → decision_id
async def record_outcome(tenant_id, decision_id, execution_result, ae_run_id) → outcome_id
```

Auth via shared-signer JWT (M.4 Option B). Config via AIHub's existing tenant-secrets vault (`{{ env.CONTEXTEDGE_SERVICE_TOKEN }}`).

**Effort**: ~4 days.

### I.3 — `aeaihub_client.py` in ContextEdge + callback wiring

Symmetric side. When the reviewer hits Approve / Modify / Reject in `/review`, ContextEdge's mutation path needs to POST to AIHub's callback endpoint:

- **Approve** → `POST /workflows/{id}/instances/{instance_id}/callback` with `{decision: "approved", modification_diff: null}`
- **Modify** → same endpoint with `{decision: "approved_with_modification", modification_diff: {...}}`
- **Reject** → same endpoint with `{decision: "rejected", reason_code: "wrong_diagnosis", comment: "..."}`

Wire-up points in ContextEdge:

- `services/decision_trace_service.reject_decision` — after recording outcome, call `aeaihub_client.callback_reject`
- `services/execution_service.decide_approval` — after the approval transitions to `approved`, call `aeaihub_client.callback_approve`
- `services/execution_service.modify_approval` — call `aeaihub_client.callback_modify` with the diff

All three calls are best-effort (swallow failures, log, queue for retry) — a transient AIHub unavailability must not block a reviewer decision.

**Effort**: ~1 week including the retry/queue pattern.

**Win**: closes the execution loop. Today when a reviewer approves, the workflow stays suspended forever because nobody tells it to resume.

### I.4 — `tool_ref` scheme bound to AIHub workflow IDs

`PlaybookStep.tool_ref` is a free-text field today. Formalize the scheme:

- `aeaihub:workflow:{workflow_id}` — resolves to an AIHub DAG
- `ae:workflow:{workflow_name}` — resolves to an AE workflow (via AIHub's AE integration, not direct)
- `mcp:{server}/{tool}` — resolves to an MCP tool call (usable on both sides)
- `native:context_lookup` — ContextEdge-internal reads

Playbook publication validates the `tool_ref` resolves by calling AIHub's workflow-list endpoint. Catch dangling references at publish time, not at runtime.

**Effort**: ~3 days.

## 8. Shared-auth decision

**Picked: Option B (shared signing key) for the demo, Option A (shared IdP) as the production successor.**

Rationale:

- Option A requires customer-side IdP procurement (Entra / Okta / Keycloak). Often a 2–4-week blocker not under our control.
- Option B is 1 day of config change across both projects, zero external dependencies.
- Migration path A ← B is clean: when IdP lands, tokens get validated against JWKS instead of the shared key; wire semantics unchanged.

**Implementation for B**:

- Single `AEOPS_SERVICE_JWT_KEY` env var shared across both deployments (managed in vault, not committed)
- ContextEdge mints service tokens for AIHub-callback calls with `scope=aiehub:callback`
- AIHub mints service tokens for ContextEdge reads with `scope=contextedge:read+write`
- Both verify tokens against the shared key, check scope, accept

## 9. Q1 roadmap

Replacing the earlier I.1–I.5 plan (which treated AIHub as immutable). Same total effort, better leverage.

| # | Work | Where | Effort | Depends on |
|---|---|---|---|---|
| **M.3** | `aeops-contracts` package + initial models + CI | New repo, consumed in both | ~3 days | — |
| **M.1** | `awaiting_external_review` state + typed callback | AIHub modification | ~1 week | M.3 |
| **I.1** | `contextedge_client.py` in AIHub | AIHub addition | ~4 days | M.3, M.4 |
| **M.2** | Built-in ContextEdge tools in ReAct | AIHub modification | ~1 week | I.1 |
| **I.3** | `aeaihub_client.py` in ContextEdge + callback wiring | ContextEdge addition | ~1 week | M.3, M.4 |
| **I.4** | `tool_ref` scheme + publication-time validation | ContextEdge modification | ~3 days | I.1 |
| **M.4 (B)** | Shared JWT signer for service-to-service | Both (config + docs) | ~1 day | — |
| **Seed** | 3 end-to-end playbooks exercising full loop | Content | ~2 weeks | All above |

**Total**: ~6–7 weeks with one engineer comfortable in both repos, or 4–5 weeks with one engineer in each repo working in parallel.

**Exit criterion**: user reports password expiry → AIHub workflow triggers → ReAct loop naturally reaches for `contextedge_find_similar` (per M.2) → opens session + records Decision via the typed client (per I.1) → suspends in `awaiting_external_review` state (per M.1) → reviewer sees it in ContextEdge `/review` → approve callback fires with typed `ReviewVerdict` (per M.3 contracts + I.3 wiring) → AIHub resumes → AE resets password via existing AE integration → outcome recorded → effectiveness updates.

## 10. Dependency ordering + recommended start

**Ship M.3 (contracts package) first.** Three days of work, unblocks everything else cleanly. Every subsequent piece — M.1's typed callback, I.1's client, I.3's reverse client, the built-in tools in M.2 — imports from it. Doing it last risks writing three drifting versions of `ReviewVerdict`; doing it first prevents that and makes every other task smaller.

Then in dependency order:

```
M.3 (contracts)          ←── M.4 (auth)
    │                          │
    ├─ M.1 (AIHub state)       │
    ├─ I.1 (client in AIHub) ──┤
    │     │                    │
    │     ├─ M.2 (ReAct tools)
    │     └─ I.4 (tool_ref validation)
    │                          │
    └─ I.3 (client in ContextEdge + callback) ←──┘
                │
                └─ Seed 3 playbooks
```

**Recommended first commit**: M.3 contracts package scaffold + initial `ReviewVerdict` + `RejectionReasonCode` models + CI wiring.

## 11. Open questions and deferred items

- **Monorepo vs two repos for `aeops-contracts`.** Leaning standalone repo for independence; monorepo simpler for CI. Decide at M.3 start.
- **Fallback behaviour when AIHub is down during a ContextEdge reviewer approval.** Current plan: queue the callback, retry with backoff, alert on sustained failure. Concrete queue mechanism (Celery? Redis list? outbox table?) TBD.
- **Multi-tenant isolation across the boundary.** Assuming both services trust the same `tenant_id` claim shape. Needs sign-off from both teams that tenant-id semantics align.
- **Audit trail across the seam.** Today, an AIHub instance's `audit_log` and a ContextEdge decision's `OperationalEvent` are separate trails with no common correlation id. Propose: both accept and echo an `X-Correlation-Id` header; ContextEdge writes it into the `OperationalEvent.correlation_id` column (already exists); AIHub writes it into `Instance.audit_metadata`. Wire in Q2.
- **Cost observability across the seam.** The admin cost dashboard shipped in ContextEdge tracks LLM spend per tenant. When the ReAct loop runs in AIHub, those tokens are AIHub's. Either AIHub reports spend back to ContextEdge via operational events, or we ship a cross-service aggregation dashboard. Defer until post-Q1.
- **Seed catalog ownership model.** Are the 3 starter playbooks maintained in the aeops-contracts repo as canonical seed, or in each customer deployment? Trade-off between reusability and per-customer customisation.

## Appendix A — canonical contract shapes

Initial Pydantic models for the `aeops-contracts` package. Subject to iteration — change via PR to that package + CI rebuild in both consumers.

```python
# aeops_contracts/review.py

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class RejectionReasonCode(str, Enum):
    WRONG_DIAGNOSIS = "wrong_diagnosis"
    PLAN_INCOMPLETE = "plan_incomplete"
    NEEDS_HUMAN_JUDGMENT = "needs_human_judgment"
    USER_CONTEXT_MISSING = "user_context_missing"
    POLICY_VIOLATION = "policy_violation"
    OTHER = "other"


class SafetyClass(str, Enum):
    READ_ONLY = "read_only"
    LOW_SIDE_EFFECT = "low_side_effect"
    HIGH_SIDE_EFFECT = "high_side_effect"
    DESTRUCTIVE = "destructive"


class AutomationMode(str, Enum):
    SHADOW = "shadow"
    SUGGEST_ONLY = "suggest_only"
    SUPERVISED = "supervised"
    FULL_AUTO = "full_auto"


class ReviewVerdictApprove(BaseModel):
    decision: Literal["approved"] = "approved"
    comment: str | None = None


class ReviewVerdictModify(BaseModel):
    decision: Literal["approved_with_modification"] = "approved_with_modification"
    modification_diff: dict = Field(..., description="Step-level diff the reviewer applied")
    reason_code: RejectionReasonCode
    comment: str | None = None


class ReviewVerdictReject(BaseModel):
    decision: Literal["rejected"] = "rejected"
    reason_code: RejectionReasonCode
    comment: str | None = None


ReviewVerdict = ReviewVerdictApprove | ReviewVerdictModify | ReviewVerdictReject
```

```python
# aeops_contracts/trigger.py

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TriggerPayload(BaseModel):
    """Canonical shape of anything that could open a reasoning session."""
    trigger_type: Literal["user_initiated", "event_driven", "scheduled"]
    source_system: str
    source_event_id: str
    tenant_id: UUID
    symptoms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    external_case_ids: list[str] = Field(default_factory=list)
    raw_payload: dict | None = None
    received_at: datetime
    correlation_id: UUID | None = None
```

See the ContextEdge repo's `models/decision.py::REJECTION_REASON_CODES` for the canonical source that these enums must stay in sync with.

## Appendix B — how to update this document

- When M.x or I.x items ship, add a `**Shipped ({commit}, YYYY-MM-DD):**` note inline rather than deleting the item. Keeps the doc interpretable as a historical artefact.
- When an integration gap is discovered in production, add it to Section 11 (Open questions) first; promote to Sections 5/7 only if it warrants roadmap reordering.
- Appendix A contract shapes are duplicated from the source-of-truth in the `aeops-contracts` package. **Do not diverge them here**; if a field changes in the package, update this appendix in the same PR.
- Re-do Section 3 (scorecard) roughly quarterly or after any major integration change.
