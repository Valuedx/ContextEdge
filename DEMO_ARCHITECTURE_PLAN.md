# ContextEdge Demo Architecture & Use-Case Plan

Planning doc for end-to-end demos of ContextEdge as the operational-memory layer inside a real IT-ops stack: chat UI for human personas, AEAIHubOrchestrator for agentic workflows, AutomationEdge for deterministic execution, MCP-exposed enterprise systems for reads and lightweight writes, and ContextEdge for the reasoning / memory layer.

This document consolidates an architectural thread covering: integration options, the CMDB-replication anti-pattern, enterprise production gaps, the role of deterministic execution (AutomationEdge), and MCP-tool integration. Treat it as the current working plan, not a final design. Open questions are listed at the end.

---

## 1. Problem statement

Build a demo that showcases the original use-case list (password reset, DL membership, disk cleanup, SaaS access, laptop slowness, TLS cert renewal, P1 incidents, etc.) with realistic edge cases (low confidence, conflicting evidence, rejected recommendations, modified approvals, failures mid-execution, missing data), running against mock enterprise systems, with ContextEdge wired in as the reasoning / memory layer.

Stack assumptions going in:

- **Chat UI** already exists as a separate running service; modifiable. Two login modes needed (end user, IT support).
- **AEAIHubOrchestrator** at `D:/Projects/AEAIHubOrchestrator` — no-code agentic workflow engine with LLM-in-the-loop, HITL suspension, MCP tool node type, and an encrypted secrets vault.
- **AutomationEdge (AE)** — deterministic RPA / runbook execution platform. Used for destructive / audited / high-safety-class actions.
- **ContextEdge** — this repository. Operational memory + decision graph + reviewer APIs.

The demo must land an enterprise-grade narrative without over-committing to production scale concerns up front.

---

## 2. System roles

Four discrete surfaces, each with a single responsibility. Mixing responsibilities across them is the architectural mistake to avoid.

```
┌──────────────┐   ┌──────────────┐
│ Chat: user   │   │ Chat: IT     │   (same app, two logins)
└──────┬───────┘   └──────┬───────┘
       │                  │
       ▼                  ▼
┌──────────────────────────────────────────────┐
│  AEAIHubOrchestrator — REASONING + ROUTING   │
│  - LLM in the loop, ReAct, HITL              │
│  - Owns workflow + human-approval state      │
│  - Calls ContextEdge for context             │
│  - Calls MCP tools for live reads +          │
│    lightweight writes                        │
│  - Dispatches approved destructive plans     │
│    to AE                                     │
└────┬─────────────────┬─────────────┬─────────┘
     │ context         │ mcp         │ dispatch
     ▼                 ▼             ▼
┌──────────────┐  ┌──────────┐  ┌──────────────┐
│ ContextEdge  │  │ MCP      │  │ AutomationE  │
│ (memory /    │  │ servers  │  │ (execution)  │
│  reasoning)  │  │ (SNOW,   │  │              │
└──────────────┘  │  AD, …)  │  └──────┬───────┘
                  └──────────┘         │
                                       ▼
                           ┌──────────────────────┐
                           │  Real/mock systems   │
                           │  (AD, Intune, EDR…)  │
                           └──────────────────────┘
```

| Surface | Role | Example calls |
|---|---|---|
| Chat UI (user) | File tickets, receive resolution updates | "I forgot my password" |
| Chat UI (IT support) | Receive approval requests with context, Approve / Modify / Reject via structured UI | Approval inbox with context summary + three action buttons |
| AEAIHubOrchestrator | Agentic reasoning, workflow state, HITL orchestration, routing to execution tier | Classify intent, build context, propose options, suspend for approval, dispatch to AE or MCP |
| AutomationEdge | Deterministic RPA execution of destructive/audited actions | `reset_password_ad`, `renew_tls_cert`, `disk_cleanup_windows` |
| MCP servers | Standards-based read + light-write tools for live source data | `mcp:servicenow/search_incidents`, `mcp:ad/lookup_user` |
| ContextEdge | Memory: similar decisions, provenance, patterns, contradictions, effectiveness, playbooks, evidence history | Read-mostly; writes only decision + outcome records at workflow completion |
| Real/mock systems | AD, Intune, EDR, ServiceNow, Okta, etc. | Accessed through AE or MCP, never directly |

The mental model for enterprise buyers: **the agent thinks, the human approves, the robot executes.**

---

## 3. Design principle — federation, not replication

The single most important architectural principle. Applies to the graph, to source data, and to execution.

### The CMDB anti-pattern

The temptation is to replicate CMDB (or any system of record) data into ContextEdge's graph so the graph looks "complete." This is wrong. CMDBs are **systems of record** — optimized for governance, change control, compliance, ITIL. The graph is a **system of reasoning** — optimized for hypothesis ranking, blast-radius traversal, evidence-to-hypothesis mapping, historical outcome patterns.

Replicating CMDB data into the graph means:

- Inheriting CMDB staleness (industry-typical accuracy is south of 70%)
- Adding a second source of truth that immediately drifts from the first
- Spending 40% of engineering time on reconciliation jobs
- Confidence scores built on sand

### What the graph should actually be

1. **Federated, not mastered.** The CMDB owns "what devices exist." AD owns "what users exist." Okta owns "what apps are entitled." The graph owns **edges between them** and **resolved identities** that let you traverse across. Node attributes live in the source systems and are fetched or cached with explicit TTLs. A deep-link from an evidence card to Intune is the architecture working as designed.
2. **Reasoning-shaped, not inventory-shaped.** Model peer groups, blast-radius paths, historical ticket outcomes, evidence-pattern-to-hypothesis mappings, constraint edges. A lot of this has no CMDB equivalent. Conversely, CMDB procurement / depreciation / warranty metadata has no business in the graph.
3. **Temporal and evidential.** "CPU was 74% a week ago, 92% now"; "this hypothesis was rejected 12 times under this evidence pattern". CMDBs are point-in-time with change-history bolted on as audit.

### The test

For each node or edge you're considering putting in the graph, ask: **"does any source system own this, and if so, am I copying or pointing?"** If you're copying, you need a reconciliation story, a staleness budget, and a conflict-resolution policy. Nine times out of ten, the right answer is to point instead.

The stuff ContextEdge genuinely owns is the stuff nobody else produces: resolved identities across systems, derived peer groups, historical outcome patterns, constraint edges, evidence-hypothesis mappings.

### Where ContextEdge already implements this

- Edge-first `graph_edges` table — nodes are typed id references, most of which can be external
- A6 deep-link helper — `source_config.deep_link_template` + `external_id` → URL is the federation primitive
- Identity resolution + `canonical_entity_refs` — resolving jsmith/j.smith/John Smith across systems
- Decision edges (`considered`, `chose`, `based_on`, `resulted_in`, `followed_by`) — evidential and temporal; no CMDB produces these
- Pattern enrichment, contradictions, decision effectiveness — historical outcome patterns
- Case links + correlation edges — cross-system relationships

### Where ContextEdge is at risk of drifting

- `EvidenceItem.body_text` + `embedding` treats all sources uniformly. Tickets (mutable, deletable) legitimately need copies. Telemetry snapshots (Intune device state) don't — but they're stored the same way today.
- `baseline_ref` is schema-less by design. A feature (lets connectors populate numeric deltas) and a hazard (tempting to cache "disk was 74% a week ago" as a snapshot → shadow time-series DB).
- No "pointer vs copy" policy per source type.
- No staleness budget (`ingested_at` exists, `cached_until` doesn't).
- Peer groups, dependency chains, constraint edges aren't first-class yet.
- No claim-arbitration policy when sources disagree on an attribute.

---

## 4. Execution model — three tiers routed by safety class

```
                        safety_class routes to →
┌─────────────────┬──────────────────────────────────────────┐
│  read_only      │ MCP (preferred) • ContextEdge memory     │
│  low_side_effect│ MCP or AE (tenant policy decides)        │
│  high_side_effect│ AE required (policy-enforced)           │
│  destructive    │ AE required + multi-approval             │
└─────────────────┴──────────────────────────────────────────┘
```

AEAIHubOrchestrator becomes the router. `PlaybookStep.safety_class` + `tool_ref` determines the execution path.

### `tool_ref` scheme

| Prefix | Target | Typical use |
|---|---|---|
| `native:` | Orchestrator internal | Context lookups, decision records, audit writes |
| `mcp:{server}/{tool}` | MCP server | Reads, lightweight writes at `low_side_effect` or below |
| `ae:{workflow_key}` | AutomationEdge | Anything `high_side_effect` or above |
| `live:{source}.{attr}` | Legacy HTTP wrapper | Sources that don't speak MCP yet — shrinks over time |

### The architectural guarantee

Playbook validation rejects a published step where `safety_class >= high_side_effect` and `tool_ref` isn't `ae:`. Destructive actions **cannot** flow through MCP, even if an LLM is tempted to use an MCP tool that would work. This is enforced at publication time, not at runtime.

### Why MCP + AE, not either alone

- **MCP is good for**: lightweight reads, search / lookup, agent-in-the-loop exploration, tools a customer already exposes, standards-based integration.
- **AE is good for**: destructive / side-effect-heavy operations, multi-step workflows with rollback, credential vaulting, deterministic execution paths that enterprises already trust.
- **Overlap**: both can "create a ServiceNow ticket." The routing is a **governance choice**, not a technical one — safety class decides.

### Audit — one surface, three writers

The existing `ToolInvocation` model has the right shape (`tool_name`, `tool_version`, `safety_class`, `inputs`, `outputs`, `status`, `duration_ms`). No new tables needed:

- MCP call → one `ToolInvocation` row per call, full inputs/outputs stored (subject to redaction policy)
- AE dispatch → one lightweight `ToolInvocation` with `ae_run_id` in outputs; AE owns the detailed log
- Native call → operational event only, not tool-level

`DecisionOutcome.result_details` carries a discriminator: `{source: "ae", ae_run_id: "..."}` or `{source: "mcp", invocation_id: "..."}`. Same deep-link pattern as A6.

---

## 5. Integration options considered

### Option 1 — Full integration including reviewer UI

Chat UI for end users → AEAIHub workflow → ContextEdge session → evidence flows → decisions with options → ContextEdge `/review` route as the reviewer console → approve/modify/reject → workflow completes → outcome recorded.

Pros: deepest integration, ContextEdge is the reviewer surface, maximum storytelling.

Cons: builds and maintains two human surfaces (chat UI for users, `/review` for IT); two-way state sync between orchestrator sessions and ContextEdge sessions; heaviest implementation.

### Option 2a — ContextEdge as pure read-only context

Orchestrator queries ContextEdge for context (similar decisions, provenance, patterns). No writes. Chat UI is the only human surface. Fastest to demo.

Cons: loses the `get_decision_effectiveness` learning loop. "87% of similar tickets succeeded" shows historical data only.

### Option 2b (recommended) — ContextEdge as context + learning-loop sink

Orchestrator reads ContextEdge for context. Writes minimal decision records at workflow completion (`POST /decisions` + `POST /decisions/{id}/outcome`). No session creation, no HITL linkage — flat decision capture. One extra call per workflow at completion.

Pros: preserves learning-loop analytics for the "it gets smarter" pitch without building any ContextEdge UI. Matches the PRD framing of ContextEdge as an operational memory platform.

**Selected approach: Option 2b + three-tier execution (MCP + AE + ContextEdge) + federation lens.**

---

## 6. Enterprise complexity — what the demo plan misses

A deliberately thorough list of gaps between "demo works" and "first customer deploys this". Grouped by whether the demo needs to address it now or can acknowledge and plan for later.

### Must-have for any plausible enterprise demo

1. **SSO login story** (even OIDC against a demo Okta) — "we integrate with your IdP" is table stakes.
2. **Prompt-injection guardrail** — output schema validation + refusal-on-mismatch on every LLM tool-choice loop. Non-negotiable once MCP tool choice is in play.
3. **Shadow mode as first-class state** — "agent is watching but not acting." Under shadow: AE dispatch is a no-op that records intent; MCP writes also no-op; reads flow normally. Single most important trust-building posture.
4. **Per-tenant model pin** + "reset to known-good" switch — silent provider model bumps break calibrated confidence thresholds.
5. **Kill switch** — operator-only endpoint halting all AE dispatches and MCP writes globally (reads still flow).
6. **One redaction pass at ingest** — mask emails, phone numbers, common secret patterns. Cheap, massive risk reduction.
7. **Correlation id propagation** — single trace id from chat message → orchestrator run → ContextEdge session → mock call. Foundational for debuggability and observability.

### Identity, auth, session boundaries

- **SSO/IdP federation** — OIDC/SAML with Entra, Okta, Ping; JIT provisioning; claim-based role mapping. `middleware/auth.py` has helpers, demo-tenant flow uses seeded-user fallback.
- **On-behalf-of flows** — when the orchestrator clicks "Reset password" via AE, does AE use its own service principal or impersonate the approving IT engineer? Enterprise audits demand the latter. No OBO pattern today.
- **Service token rotation** — `X-Service-Token` has no rotation / revocation story. Needs short-lived tokens with refresh, or mTLS.
- **Break-glass accounts** — emergency path with maker-checker logging.
- **Segregation of duties** — today the same user who proposes a decision can approve its execution. Some customers require two-person rule for destructive actions.

### Data boundaries

- **Multi-tenancy at domain level** — 50-domain MSP customer tenants need per-domain isolation. `allowed_domain_ids` exists but isn't enforced uniformly (KNOWN_GAPS already calls this out).
- **Data residency** — EU customers won't accept US-hosted LLM calls or Redis in `us-east-1`. Per-tenant regional routing needed.
- **Cross-tenant LLM cache leakage** — prompt caching at provider level risks one tenant's embedding influencing another's retrieval. Needs per-tenant cache namespaces.

### PII, classification, retention

- **No redaction at ingest** — `body_text` stores tickets raw. Need pre-normalize DLP pass.
- **Right-to-erasure (GDPR)** — "delete everything about user X" must cascade across evidence, identity links, decisions, graph edges, embeddings, Redis cache. No cascade exists.
- **Legal hold** — preservation orders override retention. Not modeled.
- **Prompt injection via evidence** — malicious ticket body steers the LLM. Need output-schema validation + adversarial testing.

### LLM governance

- **Model version pinning** — silent provider model bumps change confidence distributions and break calibrated thresholds.
- **Fallback chains** — when OpenAI is down, Anthropic picks up? Confidence recalibration?
- **Token budget per tenant / per workflow / per ticket** — no budget enforcement today.
- **Hallucination guardrails on summaries** — `body_summary` from LLM without grounding.
- **Explainability for compliance** — `rationale_summary` + `evidence_summary` must hold up under auditor scrutiny. Needs red-teaming.

### Integration depth (mocks hide this)

- **Connector rate limiting** — Intune / Graph throttles at 15–60 req/s per tenant. Ingestion retries but no circuit breaker. Under the MCP+AE model, this is largely AE's problem; under direct connectors it's ContextEdge's.
- **Webhook / event-driven ingest** — polling misses real-time signals. No webhook receivers.
- **Per-customer field mapping** — no two ServiceNow instances are the same. No mapping layer.

### Scale

- **Embedding cost + no index** — 3072-dim on every decision and every evidence. HNSW/IVFFlat not added (KNOWN_GAPS). At 10K tickets/day: embedding cost runs into $10–50/day per tenant after re-embeds and query-time embeddings.
- **Review-queue cache memory** — 300s TTL fine at demo scale; at 100 concurrent reviewers × 50 sessions = 5000 keys × ~50KB = 250MB just for this.
- **Celery queue starvation** — `extraction` queue fans out classify + correlate + baseline + artifact per ingest. Needs per-queue priority and autoscaling.
- **Postgres partitioning** — `evidence_items` at 10M rows/tenant needs tenant+time partitioning and read replicas.

### Observability

- **No distributed tracing** — `X-Request-ID` set at API client but not propagated to Celery or downstream.
- **No cost observability** — unclear which tenant / workflow / ticket type burns LLM budget.
- **No agent-quality drift dashboards** — calibration worker exists; alerting doesn't.
- **No SLO on reviewer experience** — sub-2s was measured; no production SLI collection.

### Rollout and change management

- **Shadow mode** — `automation_mode` exists; shadow-only state (run workflow, record decisions, never execute) isn't first-class.
- **Canary per tenant / per decision_type** — not built.
- **Kill switch** — not built.
- **Playbook versioning with peer review** — M2 shipped the schema; lifecycle tooling is thin. No pull-request-style review, CI validation, dry-run against historical tickets.

### UX for real volume

- **Bulk approve** — deferred in Phase 5. The 10× win from "40 tickets/day → 10" depends on this.
- **Saved queues / personalization** — not built.
- **Keyboard-first** — deferred.
- **Mobile on-call** — not considered.
- **Accessibility (WCAG 2.1 AA)** — not addressed.

### Business / process

- **SLAs and escalation ladders** — not modeled.
- **VIP handling** — CEO's ticket has different SLA; not modeled.
- **Mass-incident clustering** — correlation service exists; no "mass resolve this cluster" flow.
- **Reopened tickets** — `superseded`/`reverted` exist; no reopen workflow.
- **Partial resolutions** — `execution_result="partial"` exists; no UI differentiation.
- **Change Advisory Board** — destructive playbooks route through CAB in real enterprises; not in the approval model.

### Accept-for-demo, plan-for-production

- Multi-region residency
- GDPR erasure cascade
- Real connector throttling + circuit breakers
- Embedding index + partitioning
- Canary per decision_type
- Mobile + accessibility
- CAB / four-eyes / SoD

### How AE + MCP reduce the enterprise-gaps list

Items AE handling makes **less urgent** (AE owns them):

- Per-connector throttling, retries, circuit breakers against real systems
- Source credential vaulting for execution paths
- Change control for execution workflows (AE workflows are version-controlled and reviewed by the customer's automation team)
- Rollback + idempotency (per-AE-workflow semantics)
- Execution audit detail (AE logs everything; ContextEdge references run ids)

Items that become **new and more acute** with AE + MCP in the path:

- Mapping between ContextEdge's `safety_class` and AE's risk taxonomy
- AE workflow catalog sync — deprecated workflow keys break `tool_ref: ae:{old_key}`
- Latency budget with AE in the path — dispatch adds network hops; use async-by-default
- Failure semantics when AE itself is unreachable (distinct from "AD said no")
- AE dispatch auth rotation
- **MCP prompt-injection risk** — LLM chooses tools at runtime; need allowlists + per-tool safety class + output schema validation

---

## 7. Demo use cases

Pick 3–5 from the original 10, not all. Each covers a specific product capability.

| # | Use case | Triage (AEAIHub + ContextEdge) | Execution |
|---|---|---|---|
| 1 | **Password reset** | Identity check, MFA status, device trust (MCP reads) | `ae:reset_password_ad` |
| 3 | **Disk cleanup** | Telemetry read, pattern match, policy lookup | `ae:disk_cleanup_windows_v3` (with rollback) |
| 5 | **Laptop slowness** | Multi-source triage (MCP: Intune + EDR + AD), hypothesis ranking | `ae:performance_baseline_reset` or `ae:service_restart` |
| 6 | **SSL/TLS cert renewal** | Policy check, CA availability | `ae:renew_tls_cert` with **modified** params from reviewer |
| 9 | **P1 incident** | Blast-radius + correlation + gated approvals | **Per-step** AE dispatches, each individually gated |

Coverage:

- Fast path: password reset
- Rollback / reversibility: disk cleanup
- Hypothesis ranking with ruled-out alternatives: laptop slowness
- Modify verb (cert TTL 90 → 30): cert renewal
- Per-step gated high-stakes: P1 incident

Cert renewal is the **strongest demo** for the "agent prepares, human refines, robot executes" narrative — reviewer hits **Modify** in chat, changes TTL from 90 → 30 in the structured form, modification flows as `params` into `ae:renew_tls_cert`.

---

## 8. Edge cases per use case

2–3 variants per demo that showcase defensibility:

- **Low-confidence path** — agent surfaces uncertainty, reviewer deep-dives (laptop slowness with ambiguous symptoms)
- **Conflicting evidence** — contradiction scan fires, playbook flagged (cert renewal where KB contradicts approved playbook)
- **Reject with structured code** — reviewer picks `policy_violation`; after 2–3 rejections on the same pattern, show `get_decision_effectiveness` analytics
- **Modify with diff** — reviewer shortens TTL on cert renewal; backend records original + modified options on the Decision
- **External system failure** — mock source returns 500 mid-workflow, graceful degradation + escalation to human
- **Policy block** — safety class exceeds caller's cap, approval escalates to domain admin
- **AE unreachable** — different UX from "source said no"; circuit breaker in action
- **Auto-close on recheck success** — verification step confirms resolved (ties to M2 `verification_policy`; **scope carefully — recheck worker not yet built**, flagged in KNOWN_GAPS)

---

## 9. Mock strategy

Collapses with AE + MCP adoption. Minimum viable mock set:

- **Mock AutomationEdge** (single service): `POST /workflows/{key}/run` → run_id, `GET /runs/{run_id}` → status, optional webhook callback, 5–8 scripted workflows covering the demo use cases. Failure modes: success, target-system-error, timeout, credential-error, AE-itself-unreachable.
- **Mock MCP servers**: ServiceNow MCP + device-management MCP + directory MCP. Each exposes `tools/list` + `tools/call` standard endpoints.
- **Drop custom `live:` HTTP wrappers** for the demo; if something isn't MCP-accessible, put it behind AE.

### Fidelity: fixtures vs state machines

- **Fixtures** (recommended): deterministic responses keyed by user / device / ticket id. Obviously mock, demo-stable.
- **State machines**: remember prior calls so modifications take effect. More realistic, more rehearsal risk.

Default to fixtures unless a specific use case needs state.

---

## 10. Phased plan

### Phase A — Foundation + first vertical slice (~1 week)

1. Mock AutomationEdge + 5–8 scripted workflows
2. Mock MCP servers (ServiceNow + device-management + directory)
3. `contextedge_client.py` + `automationedge_client.py` in AEAIHub; wire into step runner keyed on `tool_ref` prefix
4. AEAIHub node types: `contextedge.context_lookup`, `contextedge.record_decision`, `contextedge.record_outcome`, `automationedge.dispatch`, `live_query` (generic light federation for non-MCP sources)
5. MCP tool allowlist per workflow (config, not code; workflow YAML declares exposed tools)
6. Safety-class validator at playbook publish (refuses `high_side_effect+` steps with non-`ae:` tool_refs)
7. `ToolInvocation` writes from the MCP path — one row per call, routed through orchestrator audit
8. Chat UI two-role split + approval message rendering + AE/MCP progress streaming
9. Three enterprise must-haves baked in: shadow mode, kill switch, LLM output schema validation
10. First workflow end-to-end: **password reset** (simplest vertical slice)

Exit criterion: user types "I forgot my password" in chat → IT support sees ticket in approval inbox with evidence + hypotheses + confidence + deep-links → approves → AE mock returns success → user gets confirmation in chat → ContextEdge records decision + outcome for effectiveness analytics.

### Phase B — Use case library (~3 days per additional use case)

Each subsequent use case is a workflow + mock fixtures + seed data. No new architectural work after Phase A lands.

Order of additions:

1. Laptop slowness — demonstrates hypothesis ranking and multi-source MCP reads
2. Cert renewal — demonstrates Modify verb
3. Disk cleanup — demonstrates rollback + reversibility
4. P1 incident — demonstrates per-step gating

### Phase C — Edge cases (~1 day per variant)

Mostly fixture + workflow-branch tweaks. 2–3 variants per demo.

### Phase D — Demo script + rehearsal

- Narrative arc per use case (who's watching, which feature this proves, what the "aha" moment is)
- Run each demo twice end-to-end with realistic timing
- Fallback plan per demo (pre-recorded video, fixture-only mode)

---

## 11. Federation-specific Phase A deltas

Concrete additions to Phase A driven by the federation / CMDB anti-pattern lens:

1. **Pointer-vs-copy matrix** — one markdown table per mock source: what ContextEdge ingests (event history), what stays in the source and is live-queried at decision time (current state). Checked in, reviewed like an API contract.
2. **Live-query node type** (via `mcp:` or `live:`) — hits the source, caches briefly (15–60s), returns with freshness timestamp.
3. **`staleness_budget_sec` on evidence** — additive column; defaulted to NULL for event evidence; set by connectors for federated-cached entries. Ranker skips rows past their budget.
4. **One derived-edge job** — background task that computes peer-group edges from identity resolution + entity refs. Seeds 5–6 users across 2 peer groups for a visible demo.
5. **Reviewer UI delta** — evidence cards render live-queried values with a small clock icon + "live" pill alongside ContextEdge-sourced history. Same card, different provenance badge.
6. **Latency budget test** — Zone 4 render under 2s with two live MCP fetches in parallel.
7. **Per-source circuit breaker** — demo this by having one mock return 500s for 30 seconds mid-demo, show UI degrading gracefully.

---

## 12. Chat UI design — two-role split

Same chat UI, role-scoped behavior:

- **End user login** — can file tickets, see their own ticket status. Orchestrator posts status updates back to their thread.
- **IT support login** — sees dedicated "Pending approvals" inbox channel. Each HITL suspension posts a structured message with:
  - Session id and ticket header
  - Top hypothesis + confidence badge
  - Similar-ticket stats from ContextEdge ("based on 143 similar, 87% succeeded")
  - Evidence deep-links
  - Three action buttons (Approve / Modify / Reject)
  - Execution progress stream when AE is running

### Approval input design

**Structured buttons + inline forms**, not slash commands, not natural language. Deterministic input → deterministic demo. Reject opens a dialog with the 6-code enum (`wrong_diagnosis`, `plan_incomplete`, `needs_human_judgment`, `user_context_missing`, `policy_violation`, `other`). Modify opens a field-level editor pre-filled with the pending approval's step inputs (same UX as ContextEdge `/review` ships today).

---

## 13. Open questions (blocking)

Answers drive the Phase A task list.

1. **Demo audience** — CIO buyer / CISO buyer / IT director / technical evaluator? Each wants different proof points. Drives edge-case priority.
2. **Timeline** — one week for Phase A + password reset? Two weeks + 2 use cases? Month for all five + edge cases?
3. **Use case priority** — after password reset, top 2 to build next?
4. **Mock-app fidelity** — fixtures (recommended) or state machines?
5. **Chat UI modifications** — minimum acceptable changes? Need at minimum: send-ticket-intent outgoing + receive-status-updates incoming + structured-approval-message rendering.
6. **AE integration**: sync vs async default? (recommend async-by-default with optimistic UI)
7. **AE workflow catalog** — mirror in ContextEdge for playbook authoring, or always query AE at authoring time? (recommend always query, cache briefly)
8. **AE credential model** — per-tenant API key or shared service account with tenant-in-payload?
9. **AE run deep-linking** — does the AE deployment expose `{ae_base}/runs/{run_id}`? If yes, reviewer chat deep-links into AE's console for audit.
10. **MCP authentication** — OAuth / mTLS / bearer per MCP server?
11. **MCP tool catalog** — ContextEdge caches or always queries at authoring time? (recommend always query, cache 60s)
12. **Tenant-level default for `low_side_effect` routing** — MCP or AE? (recommend MCP for agility with per-tenant override to AE for governance-heavy customers)
13. **Pointer-vs-copy strictness for demo** — strict (live-query only for current state) vs pragmatic (live-query marquee attributes, ingest rest)? Recommend strict for technical/CTO demos, pragmatic for CIO demos.
14. **Option 2a vs 2b** — pure read-only ContextEdge, or capture minimal decision records for learning-loop analytics? (recommend 2b).

---

## 14. What to avoid

- **Don't demo all 10 use cases.** Five is already a lot; each needs mocks, fixtures, narrative, rehearsal.
- **Don't build real Intune/AD/EDR connectors for the demo.** Phase 4 production connectors are a separate track. Mocks are honest and faster.
- **Don't demo `verification_policy` auto-close.** Recheck worker isn't built (KNOWN_GAPS). Show the UI surface; call out the promise as phase-next.
- **Don't conflate reviewer console and chat UI.** Chat UI is the only human surface in Option 2b; ContextEdge `/review` route is not on the demo path.
- **Don't replicate CMDB into the graph.** Federate. Apply the pointer-vs-copy test to every node and edge.
- **Don't route destructive actions through MCP.** Architectural guarantee: `safety_class >= high_side_effect` must use `ae:` tool_refs, enforced at publish time.
- **Don't let the LLM choose tools above `low_side_effect`.** Safety-class-per-tool in the MCP registry; tool-choice is restricted to the safe band.

---

## 15. Out-of-scope for this plan

- Production-grade tenant provisioning
- Real connector rate limits and circuit breakers at scale
- Embedding index (HNSW / IVFFlat) — flagged follow-up
- `after_commit` SQLAlchemy hook for C1 cache invalidation race — flagged follow-up
- Recheck worker for M2 `verification_policy` — flagged follow-up
- Phase 4 real connectors (Intune, Entra, CrowdStrike, Okta) — separate track
- Phase 5 next slices (Zone 4 evidence cards, Zone 6 plan steps, bulk approve, keyboard shortcuts, typed Modify forms, frontend test runner)

---

## 16. References

- [CONTEXTEDGE_IMPLEMENTATION_PLAN.md](./CONTEXTEDGE_IMPLEMENTATION_PLAN.md) — phased checklist
- [STANDALONE_OPERATIONAL_MEMORY_PRD.md](./STANDALONE_OPERATIONAL_MEMORY_PRD.md) — product scope
- [docs/API.md](./docs/API.md) — endpoint catalog (Decisions, Review Queue, Execution Approvals, Evidence, Playbooks)
- [codewiki/16-decision-traces.md](./codewiki/16-decision-traces.md) — decision model, structured codes, review-queue bundle, semantic retrieval
- [codewiki/KNOWN_GAPS.md](./codewiki/KNOWN_GAPS.md) — implementation gaps and operational caveats
- [codewiki/09-graph-and-correlation.md](./codewiki/09-graph-and-correlation.md) — graph structure and decision-graph edges
- [codewiki/10-governance-sessions-execution-audit.md](./codewiki/10-governance-sessions-execution-audit.md) — sessions, execution safety, audit
- [codewiki/15-dashboard-and-operator-workflows.md](./codewiki/15-dashboard-and-operator-workflows.md) — dashboard navigation and Review Queue UI
- AEAIHubOrchestrator — `D:/Projects/AEAIHubOrchestrator` (external repo)
