# 16 — First-Class Decision Traces

## Summary

You will learn how ContextEdge records every consequential choice — a playbook selection, an approval, an agent's diagnosis — as a first-class `Decision` row with options, outcomes, and typed graph edges, and how those records are retrieved later: semantically similar decisions, effectiveness aggregates, provenance drill-in, decision chains, and the cached reviewer-console bundle. After reading you should know the exact order of writes inside `create_decision`, which functions serve each endpoint, and where the caveats are.

## Business picture

Decision traces are the **institutional reasoning memory** of ContextEdge. Every time the system or a human makes a choice — selecting a playbook, approving a step, escalating, deferring — the record captures what evidence was considered, what options were weighed, why one was chosen, and what actually happened afterwards.

That memory pays off three ways. Reviewers get a console built to a sub-2-second first-render budget — the whole bundle is pre-warmed in Redis so the opening click hits the cache, not Postgres ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Review-queue bundle prefetched to Redis on session creation") — showing the decision, its confidence badge, and a provenance line of the form "based on N similar tickets, X% succeeded", with every citation clickable back to the source ticket. Analysts get the raw material for honest analytics: rejections carry structured reason codes, so "wrong diagnosis" and "policy violation" are stored as countable categories rather than free text — recorded and indexed today, though no report groups by them yet. And the AI gets safer: an agent's diagnosis is written through the exact same path a human's decision takes, flagged for review, and **invisible to the next agent run until a human has looked at it** — agent output can never quietly launder itself into agent input.

Flat event logs (`DecisionTraceEvent`) are preserved for lightweight session audit trails; a `Decision` created inside a session also appends one, so the two views never diverge.

## Technical walkthrough

### The write path — `create_decision`

`create_decision(db, *, tenant_id, decision_type, agent_step, rationale_summary, ...)` is the single mint for decision records (backend/src/contextedge/services/decision_trace_service.py:51-243). In order:

1. **Derive governance fields.** `decision_intent` derives from `decision_type` via `INTENT_BY_DECISION_TYPE` (an explicit argument wins; unknown explicit values raise), and `risk_level` comes from the **selected** option only — never the riskiest one considered (decision_trace_service.py:82-103; the mapping and enums live at backend/src/contextedge/models/decision.py:34-59).
2. **Insert the `decisions` row** with `context_snapshot`, a compacted `evidence_summary`, `rationale_summary`, `confidence`, `compact_trace`, `approval_required`, `policy_refs`, `status` (default `"pending"`), and `policy_result` — where NULL means "no rule existed", deliberately distinct from `allowed_auto` (decision_trace_service.py:105-143). Then one `decision_options` row per option (145-161).
3. **Graph edges**, all through the idempotent builder linkers: `based_on` → evidence/episode/pattern per typed ref (`_REF_TYPE_TO_LINKER`, decision_trace_service.py:44-48, 163-170; backend/src/contextedge/graph/builder.py:328-376), `considered` for every option plus `chose` for the selected one (172-175; builder.py:379-405), `applied_policy` per policy ref (177-182), and `followed_by` parent → child when chained (184-185).
4. **Session trail.** With a `session_id`, `append_trace_event(event_type=f"decision.{decision_type}")` writes a `decision_trace_events` row plus an operational event (decision_trace_service.py:187-197; backend/src/contextedge/services/session_service.py:139-181). An operational event `decision.created` follows with `memory_class="reasoning"` (199-215).
5. **Inline embedding.** `embed_decision(decision_type, rationale_summary, compact_trace)` populates `decisions.embedding` (Vector(3072)); failure logs `decision.embed_failed` and the decision lands with a NULL embedding — it participates in structural retrieval until re-embedded, and **no backfill task exists** (decision_trace_service.py:217-237; backend/src/contextedge/ai/embeddings.py:38-53; [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Semantic similar-decision retrieval", the 2026-07-29 update).
6. **Cache invalidation.** `invalidate_review_context(tenant_id, session_id)` drops the reviewer-bundle cache; it fires post-flush pre-commit, so a narrow re-population race exists that the 300s TTL backstops (decision_trace_service.py:239-240; [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Cache invalidation on downstream mutations", *Known caveat*).

### Outcomes and rejection

- `record_outcome` inserts a `decision_outcomes` row, adds the `resulted_in` edge, optionally chains `followed_by` to a follow-up decision, moves `status` from `pending` to `completed`, emits `decision.outcome_recorded`, and invalidates the cache (decision_trace_service.py:246-311).
- `reject_decision` validates the code against `REJECTION_REASON_CODES` (`wrong_diagnosis`, `plan_incomplete`, `needs_human_judgment`, `user_context_missing`, `policy_violation`, `other` — models/decision.py:65-72), un-selects the chosen option stamping `rejection_code` (+ the comment as `rejection_reason`), flips `status="superseded"` and `human_override=True`, writes an outcome with `execution_result="rejected"` and the same code as `feedback_code`, and emits `decision.rejected` tagged with the code for analytics (decision_trace_service.py:586-664). Rejected outcomes are counted separately from executed-and-failed ones in effectiveness math.

### Similar-decision retrieval

`find_similar_decisions(db, *, tenant_id, decision_type, context_snapshot, query_decision_id, query_text, limit=10)` (decision_trace_service.py:517-583):

1. **Embedding resolution priority** (`_resolve_query_embedding`, 478-514): an explicit `query_decision_id` uses that decision's stored embedding (a NULL there means no semantic ordering, deliberately — no silent fallback to text); otherwise `query_text` is embedded on the fly with failures swallowed and logged `decision.query_embed_failed`; otherwise None.
2. **With an embedding:** constrain to `embedding IS NOT NULL`, exclude the query decision itself, run `tune_ann_recall`, and order by `halfvec_cosine_distance` so the `0032` HNSW expression index is used (558-565; backend/src/contextedge/search/vector_ops.py:34-45). **Without:** `created_at DESC` — the pre-semantic behavior, so no caller regressed when semantics shipped (566-567).
3. Both paths apply a JSONB containment pre-filter on `context_snapshot` for whichever of `workflow` / `environment` / `impacted_dependency` the caller provided (569-580).

Companions sharing the same filter contract: `count_similar_decisions` (443-475), `get_decision_effectiveness` — grouped counts of `decision_outcomes.execution_result` (863-915) — and `find_similar_decisions_aggregate`, which composes all three in one call. Its `success_rate = success / Σ(counted)` counts only `{success, failure, partial, timeout, rejected}`, so a rogue label cannot skew the denominator; count and effectiveness stay **structural** (type + context filters) even when the top-K list reorders semantically, keeping "N similar tickets" a stable denominator (667-730). `get_decision_provenance` hydrates the `based_on` edges into evidence cards — title, summary, source, `delta_signal`, and a deep link built by `build_source_deep_link` (admin-configurable `source.config.deep_link_template` with built-in defaults) — plus episode and pattern cards (733-860). `get_decision_chain` walks parents up and children down, `max_depth=20`, cycle-guarded (387-440).

### HTTP surface

All under `/api/v1/decisions` (backend/src/contextedge/api/v1/decisions.py):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/decisions/similar` | Similar decisions; `query_decision_id` or `query_text` opts into semantic ordering (:50-90) |
| GET | `/decisions/similar/aggregate` | Top-N + total count + outcome aggregate + success rate in one call (:93-132) |
| GET | `/decisions/effectiveness` | Aggregate outcome stats per decision type + context (:135-156) |
| GET | `/decisions` | List with filters, confidence thresholds, and sort (`created_desc` / `confidence_desc` / `confidence_asc`) (:159-193) |
| POST | `/decisions` | Create (:196-225) |
| GET | `/decisions/{id}` | Decision with options and outcomes (:228-237) |
| POST | `/decisions/{id}/outcome` | Record an outcome (:240-265) |
| GET | `/decisions/{id}/chain` | Full decision chain (:268-275) |
| GET | `/decisions/{id}/provenance` | Hydrated `based_on` refs with deep links (:278-301) |
| POST | `/decisions/{id}/reject` | Structured rejection (:304-329) |

Confidence filter/sort powers the reviewer queue: `sort=confidence_desc&min_confidence=0.85` surfaces the bulk-approvable set, while `confidence_asc` focuses human attention on the uncertain cases (decision_trace_service.py:331-384).

Caveat, stated plainly: `DecisionOutcomeCreate` accepts and validates a `feedback_code` field (backend/src/contextedge/schemas/decision.py:44-63), but the outcome route does **not** forward it to the service (decisions.py:251-262) — today only the reject flow persists a structured code. Treat outcome-route `feedback_code` as accepted-but-dropped until that call site passes it through.

### The reviewer-console bundle and its cache

`GET /api/v1/review-queue/{session_id}/context` returns everything the reviewer console needs in one round trip: session header, top pending decision with a confidence badge, similar-decision aggregate, and scoped decisions / runs / events (backend/src/contextedge/api/v1/review_queue.py:30-80). `build_review_context` composes the pieces; the badge level is server-derived — `green ≥ 0.8`, `amber 0.5–0.8`, `red < 0.5` — so every consumer agrees on thresholds (backend/src/contextedge/services/review_queue_service.py:132-140, 174-249).

The bundle is read-through cached on Redis under `review_queue:{tenant_id}:{session_id}` with `REVIEW_CONTEXT_CACHE_TTL_SEC = 300` (review_queue_service.py:35-58). The Celery task `review_queue.prefetch_review_context` is enqueued from `create_resolution_session`, so the cache is warm before the reviewer opens the ticket; the enqueue is wrapped so a broken broker logs and is swallowed rather than failing session creation (backend/src/contextedge/workers/review_queue_tasks.py:33-35; backend/src/contextedge/services/session_service.py:18-36, 89). It routes to the `default` queue explicitly (backend/src/contextedge/workers/celery_app.py:273-276). Only default limits are cached — custom limits bypass the cache to avoid payload-shape poisoning — and `?no_cache=true` forces a live read (review_queue.py:39-59). Corrupt or unreachable cache falls back to live compute (review_queue.py:83-105).

Every mutation that changes review state invalidates the key: `create_decision`, `record_outcome`, `reject_decision`, and `close_resolution_session`; `decide_approval` and `modify_approval` invalidate transitively through their embedded `create_decision` (decision_trace_service.py:239-240, 307-308, 660-661; session_service.py:251-252; [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Cache invalidation on downstream mutations"). Those four call sites are the only callers of the helper in the backend. The helper swallows Redis transport errors so a degraded cache never breaks a mutation (review_queue_service.py:88+).

### Integration points

- **Execution service.** `start_execution` creates a `Decision(decision_type="execute_playbook")` (backend/src/contextedge/services/execution_service.py:978-981); `decide_approval` creates one per approval/denial (1441-1444); `modify_approval` creates a `Decision(decision_type="modify")` with two options — the original un-selected with `rejection_code=<reason>`, the modified one selected (1589-1592; docstring at 1482). The Modify flow shares the same code enum via `ApprovalRequest.modification_reason_code`, so rejections and modifications produce comparable learning signals. `DECISION_TYPES` includes `"modify"` alongside `"approve"` and `"deny"` (models/decision.py:11-25).
- **MAF write-back.** After an agent run that a graph projection informed, `after_run` turns the answer into a decision through the same `create_decision` path humans use: `decision_type="agent_diagnosis"`, `agent_step="maf_run"`, `actor_type="ai"`, `approval_required=True`, the answer as `rationale_summary` (2,000 chars), the projection identity as `context_snapshot`, and one typed `evidence_refs` entry per cited node (≤40) (backend/src/contextedge/integrations/maf/provider.py:114-174). Fail-soft: `maf_decision_writeback_failed` is logged and the answer stands (175-179).
- **Agent projection visibility.** In the agent graph's node-visibility gate, superseded/reverted decisions are invisible, and **a pending AI-authored decision is invisible** — the loop closes only after human review or a recorded outcome moves it past `pending` (backend/src/contextedge/graph/agent/hydrators.py:172-180). *(Line reference corrected 2026-08-20: this said 152-160, which now holds the episode and evidence branches of the same `node_is_visible` chain — the decision branch moved down as the gate grew.)*
- **The same rule, restated in a second schema.** The `OperationalSituation` family added by migration `0074` writes this constraint into its own DDL commentary: a `situation_change_candidate` may reach `confirmed` only from *governed* evidence — an ITSM caused-by relation, an approved RCA, a human decision — "never a score, and never an agent's opinion — that would let agent output launder itself into agent input, which the decision projection already refuses to allow" (backend/src/contextedge/models/situation.py:420-424). Nothing writes those rows yet; the rule is recorded where the first writer will find it. See [09-graph-and-correlation.md](./09-graph-and-correlation.md).
- **Memory service.** `build_runtime_memory_context` includes the session's last 5 decisions in the `reasoning` memory bucket, so runtime matching sees recent decision history (backend/src/contextedge/services/memory_service.py:82, 149-160).
- **Session service.** `get_resolution_session(include_decisions=True)` eager-loads a session's decisions (session_service.py:98-101).
- **Analytics workers.** `evaluation.calibrate_decision_confidence` (predicted vs observed) and `evaluation.mine_decision_patterns` run daily on Beat with the `"all"` tenant fan-out (backend/src/contextedge/workers/decision_tasks.py; backend/src/contextedge/workers/celery_app.py:309-318).

### Data model reference

**`decisions`** (backend/src/contextedge/models/decision.py:75-172) — key fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `decision_type` | string | `execute_playbook`, `approve`, `deny`, `modify`, `escalate_to_human`, `agent_diagnosis`, … (registered types at :11-25; the column accepts service-supplied values beyond the tuple) |
| `agent_step` | string | `AGENT_STEPS` lists `diagnostics`, `remediation`, `evaluation`, `triage` (:27), but the column is plain `String(30)` with no constraint — the MAF write-back stores `maf_run` (:101; provider.py:163) |
| `actor_type` | string | `ai`, `human`, `hybrid` (:60) |
| `decision_intent` | string? | Governance axis (`diagnosis`, `remediation`, `approval_decision`, …), derived from `decision_type` (:34-59, :141-143) |
| `risk_level` | string? | Risk of the path actually taken — from the selected option (:146) |
| `policy_result` | string? | The action-policy verdict applied; NULL = "no rule existed" (:149-151) |
| `context_snapshot` | JSONB | Workflow / environment / failure signature at decision time (:107-109) |
| `evidence_summary` | JSONB | Compact `{ref_type, ref_id, description}` list (:110-112) |
| `confidence` | float? | Predicted confidence 0–1 (:114) |
| `compact_trace` | text? | One-liner for Teams/ticket display (:117) |
| `status` | string | `pending`, `completed`, `superseded`, `reverted` (:61, :129-131) |
| `parent_decision_id` | FK | Self-reference for chains (:93-98) |
| `embedding` | Vector(3072) | Semantic retrieval; nullable, written inline (:133) |

**`decision_options`** (:175-205): `action`, `suitability`, `risk_level`, `preconditions`, `rejection_reason` (free text), `rejection_code` (structured), `selected`.

**`decision_outcomes`** (:208-250): `action_executed`, `execution_result` (`success` / `failure` / `partial` / `timeout` / `rejected`, :63), `result_details`, `follow_up_needed`, `follow_up_decision_id`, `feedback_received`, `feedback_code`, `feedback_by`.

**Graph edge types** (all in the registered vocabulary, written via `graph/builder.py`): `based_on`, `considered`, `chose`, `applied_policy`, `resulted_in`, `followed_by` (builder.py:328-469). One linker in that block is dormant: `link_decision_approval` would write `decision -[required_approval]-> approval_request` (builder.py:424-438) but has no caller outside the builder module, so that edge is registered and projectable yet never actually written. The approval link that does exist in the graph is `execution_run -[requires_approval]-> approval_request`, materialized on the 6h sweep (see [09](./09-graph-and-correlation.md)).

The registry those types live in grew when migration `0074`'s `_SITUATION` group landed: `graph/edge_types.py` now declares **76** types in **six** groups (a `_SITUATION` group joined the five), **19** of them excluded from the agent projection with a recorded reason, leaving **57** projected. The `_DECISION` group is unchanged by that addition — nothing on this page moved — but any count quoted from this file or from KNOWN_GAPS.md before the 2026-08-20 sweep (69 / 16 / 53) is now stale. Details in [09-graph-and-correlation.md](./09-graph-and-correlation.md).

## Example: Acme VPN data at this stage

**Input** (what arrives) — the MAF agent finished diagnosing Acme's VPN outage with graph context, and the write-back path posts:

```json
{
  "decision_type": "agent_diagnosis",
  "agent_step": "maf_run",
  "actor_type": "ai",
  "approval_required": true,
  "rationale_summary": "AUTH_CERT_EXPIRED on vpn-gw-east-01; the gateway TLS certificate expired. Recommend certificate renewal per the approved playbook, then RADIUS restart.",
  "context_snapshot": {"projection_id": "proj-8c1d...", "cited_nodes": ["evidence:ev-a1b2c3", "episode:ep-vpn-outage"]},
  "evidence_refs": [
    {"ref_type": "evidence", "ref_id": "ev-a1b2c3", "description": "cited in the projection that informed this run"},
    {"ref_type": "episode", "ref_id": "ep-vpn-outage", "description": "cited in the projection that informed this run"}
  ]
}
```

**Output** (what the system produces) — a pending decision with edges and an embedding, and later a reviewed outcome:

```json
{
  "id": "dec-77f0...",
  "decision_type": "agent_diagnosis",
  "decision_intent": null,
  "status": "pending",
  "actor_type": "ai",
  "approval_required": true,
  "confidence": null,
  "graph_edges": ["based_on -> evidence:ev-a1b2c3", "based_on -> episode:ep-vpn-outage"],
  "embedding": "3072-dim vector (inline)"
}
```

While `status` is `pending` and `actor_type` is `ai`, this decision is hidden from the agent's own graph projection. A reviewer opens `/review-queue/{session}/context` (served from the pre-warmed Redis bundle), sees the amber badge, and approves the plan; when the renewal succeeds, `record_outcome` flips the decision to `completed` with `execution_result="success"`. The next VPN certificate incident asks `GET /decisions/similar/aggregate?decision_type=agent_diagnosis&query_text=VPN certificate expired` and gets this decision back semantically, inside a stable "N similar, X% succeeded" denominator.

## Design decisions

- **Decisions are nodes, not logs** — *Why:* typed edges to evidence, options, policies, and outcomes make "why was this recommended" a graph query, and let the agent projection traverse decision history like any other topology. *Tradeoff:* every decision costs several `graph_edges` writes on top of its rows; `DecisionTraceEvent` is kept as the cheap session-scoped audit view, written alongside (decision_trace_service.py:187-197).
- **Inline best-effort embedding, structural fallback** — *Why:* embedding at create time means similar-decision retrieval works immediately, and a provider hiccup must never fail decision creation (decision_trace_service.py:217-237). *Tradeoff:* embed-failed and pre-C3 decisions sit at NULL with **no backfill task**, invisible to semantic ordering until rewritten ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Semantic similar-decision retrieval").
- **Structured rejection codes over free text** — *Why:* "wrong diagnosis" and "policy violation" are only countable if they are enum values, so the codes are validated on the way in, stored on both the option and the outcome, indexed by migration `0017`, carried on the `decision.rejected` event, and projected into the agent's decision facts (models/decision.py:65-72; decision_trace_service.py:617, 633, 650-657; hydrators.py:366, 376). The Modify flow reuses the same enum, so rejections and modifications are comparable learning signals (execution_service.py:1482, 1489-1492). *Tradeoff:* nuance lands in the `other` + comment bucket, the enum needs curation as new failure modes appear, and — worth saying plainly — **nothing aggregates by these codes yet**: `get_decision_effectiveness` groups by `execution_result` only (decision_trace_service.py:875-886), so today the codes are recorded, not reported.
- **Pending AI decisions are invisible to the agent surface** — *Why:* an unreviewed diagnosis must not steer the next run — otherwise agent output launders itself into agent input (hydrators.py:172-180; provider.py:168-171). The rule has since been carried into the situation schema, where a change candidate is to reach `confirmed` only from governed evidence and never from a score or an agent's opinion (models/situation.py:420-424) — the same principle, written down one layer earlier. Written down, not enforced: that one lives in a column comment, no CHECK constraint backs it, and no code writes a change candidate at all yet. *Tradeoff:* the learning flywheel only turns as fast as human review or outcome recording does, and the principle now lives in two places that must be kept in agreement by hand.
- **Derived governance fields, never guessed** — *Why:* `decision_intent` derives from `decision_type` so the governance axis cannot drift from the action axis, and `risk_level` reads the selected option so the trace records the risk actually taken (decision_trace_service.py:82-103). *Tradeoff:* unknown decision types and selection-less option sets leave NULLs — honest gaps rather than fabricated values.
- **Read-through cache with post-flush invalidation** — *Why:* the pre-warmed Redis bundle is what the sub-2s first-render budget rests on; invalidation from every mutating path keeps it honest (review_queue_service.py:35-58; session_service.py:18-36, 89; [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Review-queue bundle prefetched to Redis on session creation"). *Tradeoff:* invalidation fires pre-commit, so a concurrent read can re-cache the pre-commit snapshot for up to the 300s TTL; an `after_commit` hook is the recorded fix if that ever matters (same file, "Resolved: Cache invalidation on downstream mutations").

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Core service | `backend/src/contextedge/services/decision_trace_service.py` | `create_decision` (:51), `record_outcome` (:246), `reject_decision` (:586), `find_similar_decisions` (:517), `find_similar_decisions_aggregate` (:667), `get_decision_provenance` (:733), `get_decision_chain` (:387), `get_decision_effectiveness` (:863) | API / execution / MAF write-back |
| ORM models | `backend/src/contextedge/models/decision.py` | `Decision` (:75), `DecisionOption` (:175), `DecisionOutcome` (:208), `REJECTION_REASON_CODES` (:65), `INTENT_BY_DECISION_TYPE` (:45) | ORM |
| Embedding | `backend/src/contextedge/ai/embeddings.py` | `embed_decision` (:38) — zero vector on empty input so `IS NOT NULL` gates still work | inside `create_decision` |
| Graph linkers | `backend/src/contextedge/graph/builder.py` | `link_decision_evidence`/`_episode`/`_pattern`/`_option`/`_policy`/`_chain` (:328-421, :456) and `_outcome` (:440); `_approval` (:424) is uncalled | inside `create_decision` / outcomes |
| HTTP routes | `backend/src/contextedge/api/v1/decisions.py` | routes table above (:50-329) | HTTP |
| Reviewer bundle | `backend/src/contextedge/api/v1/review_queue.py` | `get_review_context` (:30) | HTTP |
| Bundle service | `backend/src/contextedge/services/review_queue_service.py` | `build_review_context` (:174), `derive_badge_level` (:132), `invalidate_review_context` (:88), `build_cache_key` (:39), `REVIEW_CONTEXT_CACHE_TTL_SEC` (:35) | HTTP / mutations |
| Cache warmer | `backend/src/contextedge/workers/review_queue_tasks.py` | `review_queue.prefetch_review_context` (:33) | on session creation |
| Deep links | `backend/src/contextedge/services/source_deep_link_service.py` | `build_source_deep_link` (:130) — `deep_link_template` config wins (:143), built-in defaults per source type | provenance reads |
| Execution integration | `backend/src/contextedge/services/execution_service.py` | `create_decision` embeds (:978, :1441, :1589) | execution API |
| MAF write-back | `backend/src/contextedge/integrations/maf/provider.py` | `after_run` (:114-179) | after agent runs |
| Visibility gate | `backend/src/contextedge/graph/agent/hydrators.py` | decision rules in `node_is_visible` (:172-180); rejection/feedback codes in the decision facts (:366, :376) | agent projections |
| Analytics workers | `backend/src/contextedge/workers/decision_tasks.py` | `evaluation.calibrate_decision_confidence`, `evaluation.mine_decision_patterns` | Beat, daily (celery_app.py:309-318) |
| Schemas | `backend/src/contextedge/schemas/decision.py`, `backend/src/contextedge/schemas/review_queue.py` | `DecisionRejectRequest`, `DecisionOutcomeCreate` (:44), `ReviewQueueContext` | request/response validation |
| Migration | `backend/alembic/versions/0017_rejection_modification_codes.py` | adds `rejection_code` / `feedback_code` / `modification_*` | upgrade |
| Frontend | `frontend/src/app/(dashboard)/decisions/page.tsx`, `frontend/src/app/(dashboard)/review/page.tsx`, `frontend/src/components/decisions/*` | decisions list/detail/chain; reviewer console consuming the bundle + `/similar/aggregate` | UI |

## Acme VPN incident (this layer)

During Acme's VPN outage, the resolution session accumulates the full trail: a `retrieve` trace event when `/runtime/match` recommends the certificate-renewal playbook, an `execute_playbook` decision when the run starts, an `approve` decision when the manager clears the medium-risk step, and — because the MAF agent's diagnosis ran with graph context citing INC0010427's evidence — an `agent_diagnosis` decision held at `pending` until review. Each carries `based_on` edges to the evidence that informed it, so six months later, when the certificate expires again, the reviewer console can say "a semantically similar diagnosis succeeded here last time" and link straight back to the original ServiceNow ticket.

## Further reading

- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — the edge vocabulary and builder these decisions write through
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — the shared halfvec ANN plumbing behind similar-decision retrieval
- [17-ae-ops-context-graph-alignment.md](./17-ae-ops-context-graph-alignment.md) — the `0029` governance columns (`decision_intent`, `risk_level`, `policy_result`) and their Epic F population history; see also the Epic F entries in [KNOWN_GAPS.md](./KNOWN_GAPS.md)
- [`docs/API.md`](../docs/API.md) — full request/response shapes for `/decisions` and `/review-queue`
