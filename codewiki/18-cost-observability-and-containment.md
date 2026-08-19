# LLM cost: observability and containment

## Summary

You will see how every LLM call is **measured** (tokens, thinking, cache hits, per-tenant spend), how the **admin dashboard** prices it, and the **ceilings** that bound spend which would otherwise be open-ended — the retry count, the global and per-task output-token caps, the embedding batch cap, and the default daily budget that closes the "unconfigured tenant is the only uncapped one" hole. You will also see the *avoidance* layer: the gates that skip or defer model calls entirely, which on the synthesis lane save more than any ceiling.

## Business picture

An operational-memory product spends money on every ingested ticket and every agent question, and the bill is invisible until it isn't. Two failure modes matter. First, **wrong numbers**: a dashboard pricing `vertex_ai/gemini-*` calls at a fallback rate shows a plausible figure that is simply false, and thinking tokens — routinely the *majority* of generated output on a reasoning model — were billed correctly but never shown, so cost growth looked inexplicable. Second, **unbounded spend**: retries multiply every call's worst case, a caller can request a 16k-token answer on every attempt, an embedding batch can be arbitrarily large and fail after it's paid for, and a tenant with no configured budget used to mean a tenant with *no limit*. Observability makes the bill legible; containment makes the worst case survivable. And a third lesson, learned live: a ceiling set too low is not savings — it silently truncated playbook and episode JSON and shipped confidently empty artifacts, which is why the output caps are now per-task.

## Technical walkthrough

### Observability

1. **Every call is recorded** — `ai/observability.record_llm_usage` writes three sinks per call (backend/src/contextedge/ai/observability.py:133-249): Prometheus counters (`contextedge_llm_tokens_total` by tenant/model/task/token-type, `contextedge_llm_requests_total` by outcome — observability.py:39-49), a structured `llm.usage` log line carrying `prompt_name`/`prompt_version` plus the request/correlation/causation ids pulled from the request-context ContextVar so one id greps across the HTTP → Celery → LLM boundary (observability.py:176-210; Celery propagates the ids via message headers, backend/src/contextedge/workers/celery_app.py:25-68), and — when a db session is passed — an `operational_events` row with `event_type="llm.usage"`, the dashboard's source of truth (observability.py:215-244). It runs in the `finally` of `llm_complete`, so errored calls are recorded too — they still consumed provider-side tokens (backend/src/contextedge/ai/provider.py:385-405). A failed event write logs `llm.usage_event_failed` and never breaks the call (observability.py:245-247). Since F5, a call *about* an existing row can anchor its event to that row via `subject_type`/`subject_id` — the episode AI reviewer passes `subject_type="episode"`, so "what did we spend reviewing this draft?" is a direct query (observability.py:223-228; wired call sites are still the minority — codewiki/KNOWN_GAPS.md:32).

2. **Reasoning tokens are a breakdown, not an addition** — `extract_usage` reads `completion_tokens_details.reasoning_tokens`. LiteLLM normalises every provider onto the OpenAI convention where thinking is already *inside* `completion_tokens` (verified against `vertex_ai/gemini-2.5-flash`: completion 362 = 110 text + 252 reasoning, total = prompt + completion) (observability.py:75-127). Cost therefore prices completion once; reasoning exists so operators can see how much of the output bill was thinking — measured at **72% of all output tokens** across recorded gemini-2.5-flash traffic (backend/src/contextedge/config.py:156-158). It gets its own Prometheus counter (`contextedge_llm_reasoning_tokens_total`) rather than another `token_type` label, because summing across labels would double-count (observability.py:51-59). Cache hits are normalised the same way: OpenAI's `prompt_tokens_details.cached_tokens` and Anthropic's `cache_read_input_tokens` both land in one `cached_tokens` field (observability.py:112-119).

3. **Pricing** — `services/admin_cost_service.MODEL_COST_USD_PER_M_TOKENS` is a substring-matched in-process rate table (OpenAI, Anthropic, and Gemini/Vertex families; `_FALLBACK_RATE` = 1.00/3.00/0.10 otherwise) (backend/src/contextedge/services/admin_cost_service.py:29-53). Notable entries: `gemini-2.5-flash` at 0.30/2.50/0.075 and `gemini-3.7-flash` at 0.75/3.75/0.19 — the latter an introductory rate through 2026-12-31 that doubles on 2027-01-01 (admin_cost_service.py:43-48). `_estimate_cost` prices non-cached prompt, cached prompt, and completion separately; thinking tokens bill at the output rate and are already inside completion, so no separate reasoning rate exists (admin_cost_service.py:41-42, 64-72). `get_llm_usage` aggregates the window's `llm.usage` events into totals and a model×task breakdown sorted by cost, deriving `cache_hit_rate` and `reasoning_share` (admin_cost_service.py:75-195). Estimates are for dashboard UX; the provider's billing dashboard is authoritative (admin_cost_service.py:7-10).

4. **Dashboard** — `frontend/src/app/(dashboard)/admin/cost/page.tsx` renders KPI cards (including a Thinking-tokens card), a stacked prompt/cached/answer/thinking bar per model×task (thinking is *carved out of* output, never stacked on top), and the budget panel. The budget panel never says "uncapped" for a tenant on the deployment defaults: `GET /api/v1/admin/tenant-budget/status` returns `effective_token_limit`, `effective_cost_cap_usd`, and `limit_source` (`tenant` / `default` / `none`), and the bars draw against what is actually enforced (backend/src/contextedge/api/v1/admin_cost.py:137-163). The other admin surfaces: `GET /admin/llm-usage` (admin_cost.py:33), `GET`/`PUT /admin/tenant-budget` (admin_cost.py:102, 113), `GET /admin/pipeline-health` (admin_cost.py:166).

### Containment

The ceilings live in `config.py`, all overridable via env (documented in `.env.example` at the repo root). Each is a backstop, not a target — ordinary work stays far below all of them.

| Knob | Default | What it stops |
| --- | --- | --- |
| `llm_num_retries` (config.py:91) | 2 (was hardcoded 5) | Each retry is a fully billed call; the count multiplies every request's worst case |
| `llm_max_output_tokens` (config.py:95) | 4096 | Clamped over the caller's `max_tokens` — one caller buying an oversized answer on every attempt |
| `llm_task_output_tokens` (config.py:132-138) | `{playbook: 16384, extraction: 16384, pattern: 16384}` | The global cap silently truncating tasks whose *correct* answer is long — see the post-mortem below |
| `embedding_max_batch_size` (config.py:142) | 64 | Oversized batches are split in `generate_embeddings_batch`; a giant request otherwise fails *after* the tokens are spent — and each sub-batch **re-checks the budget**, so a long ingest stops at the cap instead of finishing past it (provider.py:859-876) |
| `default_daily_token_limit` / `default_daily_cost_cap_usd` (config.py:194-198) | 2M / $25, action `block` | Tenants with **no** `tenant_llm_budgets` row. "No row" used to mean "no limit", making the unconfigured tenant — the normal state — the only uncapped one |

**The per-task ceiling post-mortem (why the map exists).** The 4,096 global cap silently overruled callers that had asked for more: playbook generation requested 16,384, got 4,096, ran out mid-steps-array, and the JSON-repair path salvaged the complete-looking prefix — a playbook persisted with **zero steps** while the task reported success. Episode reconstruction hit the identical failure: completion_tokens 4,082 against the 4,096 ceiling, of which reasoning_tokens 3,930 — ~150 tokens of actual answer, every attempt failing at the same offset (config.py:96-131). Reasoning counts against the same output budget, which is why these ceilings cannot be trimmed close to expected answer size. The clamp is applied in the provider as `min(caller's max_tokens, llm_task_output_tokens.get(task, llm_max_output_tokens))` (provider.py:290-293); `llm_complete_json` requests 16,384 for `extraction`/`playbook`/`pattern` and 8,192 otherwise, so classification effectively runs at 4,096 (provider.py:527). Any new long-output lane must get its own entry — `pattern` entered exactly because its lane flip would otherwise have silently dropped 16,384 → 4,096 (config.py:128-131). This is the measure-first precedent CLAUDE.md points at: a cap that changes model output structure is a quality change, not a cost change.

5. **Budget enforcement path** — `llm_complete` (and both embedding entry points) calls `tenant_budget_service.check_budget` before spending (provider.py:234-285, 755-774, 838-857). A tenant's own budget row wins when present; otherwise the deployment defaults flow through the *same* evaluation — `_DefaultBudget` carries exactly the attributes `_check_budget_locked` reads, deliberately not persisted so a row created on first use can't shadow later default changes (backend/src/contextedge/services/tenant_budget_service.py:107-120, 249-282). Ordering: token limit before cost cap — a tenant with only a token cap never sees `cost_cap_exceeded` (tenant_budget_service.py:237-243, 301-320). `block` raises `TenantBudgetExceeded` so callers can degrade; `warn` logs, emits an `llm.budget_warning` operational event, and lets the call through — roll out as `warn`, flip to `block` (provider.py:242-280). Usage comes from summing the current UTC day's `llm.usage` events — no second source of truth to drift — priced through the same `_estimate_cost` as the dashboard (tenant_budget_service.py:191-231). A 60 s per-tenant usage cache bounds DB reads (at most one over-cap call slips through per minute, tenant_budget_service.py:47-51); concurrent checks within one event loop serialise on a per-tenant `asyncio.Lock` keyed **per loop** in a `WeakKeyDictionary` — a plain module dict bound every lock to the first task's loop and killed a 499-task sweep under the `-P threads` pool (tenant_budget_service.py:53-90). Cross-worker overshoot is bounded by concurrency and documented; closing it needs a Redis counter (tenant_budget_service.py:60-63).

6. **Vision goes through the same gate** — `llm_complete(images=...)` is a parameter, not a separate client, precisely so the most expensive call type cannot bypass budget, recording, breaker, or clamp (provider.py:190-201).

**Degradation signature operators actually see** when a tenant hits a `block` cap mid-ingest: chunks stuck at `embedding IS NULL`, work that simply stops, and `/admin/tenant-budget/status` returning `allowed: false` with `reason` = `token_limit_exceeded` or `cost_cap_exceeded` (tenant_budget_service.py:100, 301-320).

Read the signature carefully, because the obvious guess is wrong. A blocked call raises `TenantBudgetExceeded` in the gate at the *top* of `llm_complete`, before the `try`/`finally` that records usage ever starts (provider.py:245 against the recorder at 385-405), so a blocked tenant writes **no `llm.usage` event at all** — its spend line goes flat rather than filling with error rows. `outcome` only ever takes the values `ok` and `error` (provider.py:324, 383); there is no `budget_exceeded` outcome anywhere in the backend. In `warn` mode you do get a row: an `llm.budget_warning` operational event per call (provider.py:256-275). Several docs used to tell operators to grep for `outcome = budget_exceeded`; that string will never match and the guidance was corrected across the corpus on 2026-08-19. Use the flat `llm.usage` line, the `chunk_embedding_failed` worker log naming `TenantBudgetExceeded` (`workers/chunk_tasks.py:172-181`), and `GET /api/v1/admin/tenant-budget/status` instead.

### Spend avoidance — the calls that never happen

Ceilings cap what a call may cost; these knobs and gates skip or defer calls entirely. On the synthesis lane they matter more than any ceiling — episode synthesis was ~73% of cold-start spend on the measured backfill (docs/RUNBOOK.md:293) and is 29% of all tokens on the message corpus with **71% of its output superseded** by dedup (codewiki/KNOWN_GAPS.md:39).

- **Per-task model & location routing**: `pattern_model` / `playbook_model` plus per-task `*_LOCATION` settings let each pipeline lane target its own model and Vertex region (`MODEL_ROUTING` / `LOCATION_ROUTING`, provider.py:47-61; config.py:56-75) — the lever for pointing high-volume lanes at cheaper models without touching quality lanes. The playbook lane runs `gemini-3.7-flash` on the measured verdict below; the pattern lane stays on `gemini-2.5-flash` because it has not been measured — the same measure-first gate applies before flipping it (config.py:59-65; KNOWN_GAPS.md:441-453).
- **Synthesis gates before the model call** (mechanics in [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md)): the 180 s debounce, the `MIN_AUTO_SYNTHESIS_CLUSTER = 3` fragment floor, the per-cluster advisory lock (8 concurrent tasks once minted 8 identical episodes), and the ≥1.5× growth gate — whose in-code rationale quantifies ~12,700 tokens per redundant synthesis (backend/src/contextedge/workers/extraction_tasks.py:746-774, 1059-1080).
- **Resolution gate** (`EPISODE_RESOLUTION_GATE=cluster`, default `off`, config.py:175): synthesis is deferred for clusters carrying no resolution signal anywhere, at zero LLM cost — the check reads the source's own `case_state` verdict first, then a bounded deterministic regex (backend/src/contextedge/services/resolution_signal_service.py:105-117, 40-64). Deferred, not dropped; design in [07-episodes-patterns-playbooks](./07-episodes-patterns-playbooks.md).
- **Facet skip**: a knowledge article whose source already states environment/version gets `applicability` directly from `source_facets` and skips `extract_applicability_llm` — ~7,200 tokens a call that someone who knew already typed (extraction_tasks.py:704-719).
- **Review-sweep thrift**: the AI episode reviewer selects only drafts with `ai_review IS NULL` (never pays twice for one draft), defers per tenant while bulk ingest is active (assessing drafts the next burst supersedes is spend with no beneficiary), commits per episode so one deadlock costs one review instead of a 50-call batch, aborts a tenant's batch after 5 consecutive provider transients, and offers hash-sharding so concurrent sweep workers never review the same drafts in lockstep (backend/src/contextedge/workers/evaluation_tasks.py:241-268, 278-310).

### Thinking budgets: why only `relevance` is capped

`llm_thinking_budgets` (config.py:188-190) can cap Gemini's reasoning tokens per **prompt name** — task is too coarse, since "classification" covers relevance, identity, and adjudication with very different tolerances (provider.py:117-149; the budget is resolved per attempt and sent only when the attempt's model supports reasoning, provider.py:342-351). It ships configured for exactly one prompt — `relevance: 0` — and live A/B runs (2026-08-06, real tickets from the dev graph) confirmed that stopping there is correct, not cautious:

- **`episode` at 1024**: thinking dropped 2,601 → 784 on multi-evidence reconstruct chunks, but the *segmentation changed* — the same 20-evidence group produced 1 episode dynamically and 2 episodes capped, and step counts doubled on another group. A cap that redraws episode boundaries is a quality change, not a cost change.
- **`identity` at 1024**: extracted entity sets were identical (good), but typical tickets only think ~590 tokens dynamically — the cap never binds where the volume is, and the heavy calls where it would bind are exactly the untested ones.
- **`identity_adjudication` / `identity_reconciliation` / `message_function`**: confidence-threshold-coupled (`AUTO_LINK_THRESHOLDS` 0.95, reconciliation `MIN_CONFIDENCE` 0.95, `CLASSIFIER_TRUST_FLOOR`). The measured run showed capping adjudication moved confidence 0.95 → 0.80 at unchanged verdicts — capping silently converts auto-links into review-queue items unless thresholds are re-tuned in the same change (config.py:156-167; pinned by `backend/tests/test_thinking_budget.py`).

So `relevance` ships at 0 (binary classifier, verdict unchanged, ~70% fewer output tokens) and everything else keeps provider-dynamic thinking until it has been A/B'd (config.py:165-167).

Also measured: Vertex per-call latency varies 3–4× at identical token counts, so thinking caps are a **cost** lever, not a latency lever. Per-ticket latency comes from worker concurrency (docs/RUNBOOK.md "Worker topology"), not from trimming reasoning.

### Playbook model: gemini-3.7-flash measured better (2026-08-17)

The lane-routing switch shipped with an explicit gate: no model upgrade without a measurement. This is that measurement, for the playbook lane. `backend/src/contextedge/evals/playbook_model_ab.py` ran the REAL generator — prompt, source-ref validation, structural grounding classification — on 6 patterns from the live Zoho corpus (spread: 3 multi-episode, 2 pairs, 1 singleton) with only the model swapped, so the comparison isolates the model. Grounding is the axis that decides: `grounding_status` comes from validated citations, so the model cannot claim it.

| | gemini-2.5-flash | gemini-3.7-flash |
| --- | --- | --- |
| grounded-step share | 0.70 | **0.81** (never worse on any of the 6) |
| steps per playbook | 10.7 | **5.8** |
| latency | 25.5s | **14.5s** |
| rollback notes | 6/6 | 6/6 |

**Why it shipped:** 3.7-flash is better-grounded, not merely shorter — across the 6 patterns, steps fell 64 → 35 while source refs fell only 66 → 56, so each remaining step carries more citation than before. (The harness docstring rounds that to "refs held"; the snapshot shows a real 15% dip, which is still a third of the step drop.) That reads as less invented best-practice padding, and it cost half the wall-clock. `playbook_model` defaults to `vertex_ai/gemini-3.7-flash` (config.py:59-67; `.env.example`); snapshot pinned at `backend/src/contextedge/evals/datasets/playbook_model_ab_2026-08-17.json`.

**Tradeoff / what this does not license:** the flip is a quality-and-latency call, not a saving — 3.7-flash is per-token pricier ($0.75/$3.75 per M vs 2.5-flash's $0.30/$2.50, introductory until 2026-12-31, doubling 2027-01-01), roughly a wash per playbook only because step counts halved. The rate is in `admin_cost_service.MODEL_COST_USD_PER_M_TOKENS` (admin_cost_service.py:46-48); re-check the math when the introductory window closes. And 6 patterns is a decision-grade sample, not a benchmark — the verdict was unanimous across the spread, which is what made it sufficient. It says nothing about the **pattern** lane (different prompt, different failure modes: over-merging clusters rather than padding steps); that lane keeps 2.5-flash until its own A/B (config.py:64-65). `has_verification` was 0/6 on *both* models — a prompt property, not a model differentiator, and a known open item.

### Playbook prompt: v6 measured better, but not for the reason it was written (2026-08-19)

The brief was a quality one, not a cost one: playbooks must be logically correct, meaningfully sequenced, and free of padding, in plain friendly English. v5 constrains what a step may *claim* — grounding, citations, verbatim commands — and says nothing about how the procedure reads as a whole, so v6 added three rules: sequence by causality, emit the minimal complete step set, write plain imperative prose. `backend/src/contextedge/evals/playbook_prompt_ab.py` is the model A/B's sibling with the other variable isolated — same model, same 6 live patterns, only the registry default swapped. Three axes structure cannot see (sequencing, redundancy, language) are scored by a **blind judge** that sees one playbook at a time and never which prompt wrote it.

| | playbook v5 | playbook v6 |
| --- | --- | --- |
| steps per playbook | 6.3 | **5.5** |
| source refs (total) | 62 | 61 |
| grounded-step share | 0.79 | **0.94** |
| language grade (blind judge, 1–5) | 4.67 | **5.0** |
| rollback notes / latency | 6/6, 16.6s | 6/6, 16.6s |

**Why it shipped:** fewer steps at the same citation count is the "tighter, not thinner" signature — the same evidence carried in less prose — and grounded share rose with it. `playbook` now defaults to v6 (`backend/src/contextedge/ai/prompts/playbook.py:415-422`; the version table in [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) lists it too); snapshot at `backend/src/contextedge/evals/datasets/playbook_prompt_ab_2026-08-19.json`. Grounded share here is the mean of the six per-pattern shares, not one pooled ratio — pooled, the same snapshot reads 0.82 → 0.94.

**Two negative results, recorded so they are not re-litigated:**

1. **The judge's `logic_flaws` count is too noisy to decide on.** It read 4 (v5) vs 6 (v6) — a gap concentrated entirely in one pattern. Re-running that same pattern reversed it to 3 vs 0. Both arms regenerate per run, so a single-pattern flaw delta is sampling noise. Judge prose is useful for *reading*; its counts are not a metric.
2. **Prompting does not fix branch validity, so code does.** A deterministic audit — dead branches (`if_true_goto == if_false_goto`), targets naming no step, self-loops, unreachable steps — found both versions clean on only 5 of 8 patterns, with v6 emitting *more* defect occurrences (6 vs 3). The same audit over the live corpus found roughly **a third of branching playbooks defective** (13 of the 36 still on v5 mid-sweep). A decision point that decides nothing survives review precisely because it looks like considered control flow. This is now enforced structurally by `sanitize_branching_logic`, which drops invalid points and counts them, exactly as `validate_source_refs` does for minted citations (both in backend/src/contextedge/ai/generators/playbook_generator.py — the sanitizer at :106, called at :93, alongside `validate_source_refs` at :259, called at :91). Playbooks regenerated on v6 audit clean on every class the guard covers. No future prompt version should claim credit for branch validity.

   *Methodology note, because the first measurement was wrong.* The auditor's initial reachability check computed skipped steps one decision point at a time (`range(after_step + 1, min(true, false))`). That misreads the **switch** shape — several decision points sharing one anchor, each routing to a different remediation — where a step one point appears to skip is reached by a sibling's branch. It inflated the "unreachable" count roughly fourfold and flagged correct playbooks as broken. Reachability has to be a graph traversal from the first step, following each anchor's union of targets and falling through where no decision sits; per-point arithmetic cannot express "some other branch gets there." The guard itself was never affected — it drops only dead branches, dangling targets, self-loops, and bad anchors, all of which are decidable per point.

**Tradeoff:** v6's rule 10 (sequencing) is kept for its ordering guidance despite failing to deliver valid branching, because ordering and branch-target consistency are different things and only the latter is mechanically checkable. And a 6-pattern judge sample plus an 8-pattern structural sample is decision-grade, not a benchmark.

### Local models: feasibility measured (2026-08-07)

Benchmarked the FinetuneModeledge GGUF models via llama-server (OpenAI-compatible) on the real relevance-v2 prompt with real tickets, on the dev workstation (4-core Ryzen, no CUDA GPU):

| model | warm latency | JSON contract | label quality |
| --- | --- | --- | --- |
| qwen2.5-0.5b Q4 (CPU) | ~6.3s | 6/6 valid | unusable — everything "operational" |
| gemma-4B capability-router Q4 (CPU) | ~17s | 3/3 valid | discriminates (2/3 on a 3-ticket sample, wrong-task fine-tune) |
| Vertex gemini-2.5-flash (production, 901 calls same day) | **2.4s avg / 2.6s p90** | — | baseline |

These are bench numbers from that day's run, kept here as a record; the short version also lives in [LESSONS_LEARNED.md](./LESSONS_LEARNED.md) §9. Conclusions: integration is trivial (LiteLLM routes `openai/<model>` at a local base_url per task via `MODEL_ROUTING`; the JSON contract held 9/9) — **feasibility is purely a hardware question**. On CPU, local is 3–7× slower than Vertex for equal-or-worse quality: not viable. On the RTX-5090 class hardware these models were trained on, a 4B Q4 serves at ~1–2s/call — competitive with Vertex at zero marginal cost and no data egress. The credible path if pursued: fine-tune a small model on the relevance task itself (the FinetuneModeledge pipeline + eval harness exists; the ae-router card shows the full loop at ~11 min training), using the 332 v2-labeled evidence rows as seed data, serve on GPU, and A/B against stored v2 labels before touching `MODEL_ROUTING["classification"]`. The 30s calls people notice are episode extraction / identity reconciliation (13.6s avg / ~29s p90) — those need large-model quality and are the wrong local-model target; relevance (the volume workload) is already 2.4s external.

### What batching is and is not for

Batch APIs (~50% discount, minutes-to-hours latency) fit the **ingestion pipeline** — classification, extraction, pattern synthesis, playbook generation, embeddings, eval runs — where nobody is waiting. They do not fit the serving path. Adopting them means restructuring Celery tasks around submit-then-poll and is scoped as its own piece of work.

## Example: Acme VPN data at this stage

**Input (what arrives)** — one `llm.usage` operational event, written when the relevance classifier reads Acme's INC0010427 ticket (this row is what the budget check sums and the dashboard prices):

```json
{
  "tenant_id": "<acme-tenant-uuid>",
  "entity_type": "llm_usage",
  "event_type": "llm.usage",
  "payload": {
    "model": "vertex_ai/gemini-2.5-flash",
    "task": "classification",
    "prompt_tokens": 1412, "completion_tokens": 388,
    "reasoning_tokens": 252, "cached_tokens": 0, "total_tokens": 1800,
    "outcome": "ok", "duration_ms": 2410,
    "prompt_name": "relevance", "prompt_version": "v2"
  },
  "correlation_id": "<same id as the sync run that ingested the ticket>"
}
```

**Output (what the system produces)** — the budget panel's live view for Acme, which has no `tenant_llm_budgets` row (`GET /api/v1/admin/tenant-budget/status`):

```json
{
  "budget": null,
  "current_tokens": 184230,
  "current_cost_usd": 0.31,
  "allowed": true,
  "reason": "ok",
  "effective_token_limit": 2000000,
  "effective_cost_cap_usd": 25.0,
  "limit_source": "default"
}
```

`limit_source: "default"` is the load-bearing field: the panel labels the caps **deployment default** instead of claiming the tenant is uncapped, and the next `llm_complete` for Acme is gated against exactly these numbers.

## Design decisions

- **Reasoning as a separate metric, not a token_type label** — *Why:* it is a subset of completion; a label would double-count any dashboard summing across types (observability.py:51-54). *Tradeoff:* two counters to keep in sync.

- **Defaults enforced through the tenant-budget path, not beside it** — *Why:* one limit evaluation, no drift; the dashboard can state the *source* of the cap (tenant_budget_service.py:107-120). *Tradeoff:* a stand-in dataclass rather than a persisted row — deliberately not written to the DB, since a row created on first use would shadow later changes to the defaults.

- **Per-task output ceilings instead of one bigger global cap** — *Why:* the tasks whose correct answers are long (playbook, extraction, pattern) are also low-volume, so raising only their ceilings costs little; raising the global cap would hand every classification call a 4× worst case for no benefit (config.py:96-138). *Tradeoff:* a new long-output lane that forgets its map entry silently re-inherits 4,096 and recreates the truncation failure — the map is a thing engineers must know exists.

- **Cached prompt tokens get their own rate, roughly a tenth of the input rate** — *Why:* a cache read is real spend, not free, and pricing it at zero would make a well-cached workload look cheaper than it is. Every entry in the table carries a `cached_input` value, and so does `_FALLBACK_RATE`, so `_estimate_cost` never has to guess: it prices non-cached prompt, cached prompt, and completion as three separate terms (admin_cost_service.py:25-28, 64-72). *Tradeoff:* the 10%-of-input figure is a rule of thumb across OpenAI 4o-family and Anthropic ephemeral reads rather than a per-provider quote, so any one model's cache line can be off; edit the entry when a provider publishes its own number.

- **In-process rate table** — *Why:* no new table, editable per deployment (admin_cost_service.py:7-11). *Tradeoff:* goes stale; the provider's billing dashboard is authoritative and the UI says so.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Usage recording | `backend/src/contextedge/ai/observability.py` | `record_llm_usage` (:133), `extract_usage` (:75), `LLM_REASONING_TOKENS_TOTAL` (:55) | Every LLM call, in `finally` |
| Rates + aggregation | `backend/src/contextedge/services/admin_cost_service.py` | `MODEL_COST_USD_PER_M_TOKENS` (:29), `get_llm_usage` (:75), `_estimate_cost` (:64) | Dashboard poll; budget check |
| Budget enforcement | `backend/src/contextedge/services/tenant_budget_service.py` | `check_budget` (:234), `_DefaultBudget` (:107), `TenantBudgetExceeded` (:123), `get_current_day_usage` (:191) | Before each call |
| Ceilings + gates config | `backend/src/contextedge/config.py` | `llm_num_retries` (:91), `llm_max_output_tokens` (:95), `llm_task_output_tokens` (:132), `embedding_max_batch_size` (:142), `llm_thinking_budgets` (:188), `default_daily_*` (:194-198), `episode_resolution_gate` (:175) | Startup / per call |
| Call path | `backend/src/contextedge/ai/provider.py` | `llm_complete` (:177), clamp (:290-293), `generate_embeddings_batch` (:814), `resolve_thinking_budget` (:117) | Every LLM call |
| Cost API | `backend/src/contextedge/api/v1/admin_cost.py` | `/admin/llm-usage` (:33), `/admin/tenant-budget` (:102, :113), `/admin/tenant-budget/status` (:137), `/admin/pipeline-health` (:166) | HTTP, `tenant_admin` |
| Playbook A/B harness | `backend/src/contextedge/evals/playbook_model_ab.py` | run + snapshot (`evals/datasets/playbook_model_ab_2026-08-17.json`) | On demand, measure-first gate |
| Dashboard | `frontend/src/app/(dashboard)/admin/cost/page.tsx` | KPI cards, breakdown bar, budget panel | UI |

## Acme VPN incident (this layer)

Walking the numbers through for Acme's VPN incident (illustrative shape, not a recorded run): a few dozen classification calls, a handful of extractions, and the embeddings behind them land as pennies of estimated spend, and the dashboard's reasoning share shows most of the generated output was thinking — consistent with the measured 72% on gemini-2.5-flash traffic (config.py:156-158). Acme has no `tenant_llm_budgets` row, so the deployment defaults (2M tokens / $25 per day, `block`) protect the tenant from a runaway loop, and the budget panel labels those caps **deployment default** rather than claiming the tenant is uncapped.

On the avoidance side, the same incident never paid for fragment narration. The INC0010427 cluster settles at three items — the ServiceNow ticket, the Teams working discussion, the engineer's email — so reconstruction waits out the 180 s debounce, clears the minimum-cluster floor (which needs 3, and would have skipped the ticket-plus-Teams pair on its own), and then narrates once. Had a fourth message landed a minute later, the growth gate would have refused a re-telling: 4 is not 1.5× of 3. The ~12,700-token figure in the growth gate's rationale is the cost of one redundant synthesis on a ten-evidence cluster (extraction_tasks.py:758-773) — a three-item story costs well under that, which is exactly why the floors are shaped around cluster size rather than a flat token budget.

## Further reading

- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — the funnel these controls guard, and the synthesis gates in mechanism detail
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — where the spend originates
- [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) — eval runs as a batchable workload
- `.env.example` (repo root) — every ceiling, with the reasoning inline
