# LLM cost: observability and containment

## Summary

You will see how every LLM call is **measured** (tokens, thinking, cache hits, per-tenant spend), how the **admin dashboard** prices it, and the four **ceilings** that bound spend which would otherwise be open-ended — including the default daily budget that closes the "unconfigured tenant is the only uncapped one" hole.

## Business picture

An operational-memory product spends money on every ingested ticket and every agent question, and the bill is invisible until it isn't. Two failure modes matter. First, **wrong numbers**: a dashboard pricing `vertex_ai/gemini-*` calls at a fallback rate shows a plausible figure that is simply false, and thinking tokens — routinely the *majority* of generated output on a reasoning model — were billed correctly but never shown, so cost growth looked inexplicable. Second, **unbounded spend**: retries multiply every call's worst case, a caller can request an 8k-token answer on every attempt, an embedding batch can be arbitrarily large and fail after it's paid for, and a tenant with no configured budget used to mean a tenant with *no limit*. Observability makes the bill legible; containment makes the worst case survivable.

## Technical walkthrough

### Observability

1. **Every call is recorded** — `ai/observability.record_llm_usage` writes three sinks per call: Prometheus counters (`contextedge_llm_tokens_total` by tenant/model/task/token-type, `contextedge_llm_requests_total` by outcome), a structured log line carrying request/correlation/causation ids across the HTTP → Celery → LLM boundary, and (when a db session is passed) an `operational_events` row with `event_type="llm.usage"` — the dashboard's source of truth.

2. **Reasoning tokens are a breakdown, not an addition** — `extract_usage` reads `completion_tokens_details.reasoning_tokens`. LiteLLM normalises every provider onto the OpenAI convention where thinking is already *inside* `completion_tokens` (verified against `vertex_ai/gemini-2.5-flash`: completion 362 = 110 text + 252 reasoning, total = prompt + completion). Cost therefore prices completion once; reasoning exists so operators can see how much of the output bill was thinking — on the pipeline run that motivated this, **73%**. It gets its own Prometheus counter (`contextedge_llm_reasoning_tokens_total`) rather than another `token_type` label, because summing across labels would double-count.

3. **Pricing** — `services/admin_cost_service.MODEL_COST_USD_PER_M_TOKENS` is a substring-matched in-process rate table (OpenAI, Anthropic, and Gemini/Vertex families; `_FALLBACK_RATE` otherwise). `get_llm_usage` aggregates the window's `llm.usage` events into totals and a model×task breakdown, including `reasoning_tokens` and a `reasoning_share`.

4. **Dashboard** — `frontend/.../admin/cost/page.tsx` renders KPI cards (including a Thinking-tokens card), a stacked prompt/cached/answer/thinking bar per model×task (thinking is *carved out of* output, never stacked on top), and the budget panel. The budget panel never says "uncapped" for a tenant on the deployment defaults: `GET /admin/tenant-budget/status` returns `effective_token_limit`, `effective_cost_cap_usd`, and `limit_source` (`tenant` / `default` / `none`), and the bars draw against what is actually enforced.

### Containment

Four ceilings, all in `config.py`, all overridable via env (documented in `.env.example`). Each is a backstop, not a target — ordinary work stays far below all of them.

| Knob | Default | What it stops |
| --- | --- | --- |
| `llm_num_retries` | 2 (was hardcoded 5) | Each retry is a fully billed call; the count multiplies every request's worst case |
| `llm_max_output_tokens` | 4096 | Clamped over the caller's `max_tokens` — one caller buying an 8k answer per attempt |
| `embedding_max_batch_size` | 64 | Oversized batches are split in `generate_embeddings_batch`; a giant request otherwise fails *after* the tokens are spent |
| `default_daily_token_limit` / `default_daily_cost_cap_usd` | 2M / $25, action `block` | Tenants with **no** `tenant_llm_budgets` row. "No row" used to mean "no limit", making the unconfigured tenant — the normal state — the only uncapped one |

Beyond the ceilings, two **spend-avoidance** knobs route or skip work instead of capping it (both default to changing nothing):

- **Per-task model & location routing** (`3f6d3c3`): `pattern_model` / `playbook_model` plus per-task `*_LOCATION` settings let each pipeline lane target its own model and Vertex region (`ai/provider.MODEL_ROUTING` / `LOCATION_ROUTING`) — the lever for pointing high-volume lanes at cheaper models without touching quality lanes. The playbook lane runs `gemini-3.7-flash` on the measured verdict below (2026-08-17); the pattern lane stays on `gemini-2.5-flash` because it has not been measured — the same measure-first gate applies before flipping it ([KNOWN_GAPS](./KNOWN_GAPS.md)).
- **Resolution gate** (`EPISODE_RESOLUTION_GATE=cluster`, default `off`): episode synthesis — the costliest lane, 73% of e2e spend — is deferred for clusters carrying no resolution signal anywhere, at zero LLM cost (deterministic scan). Deferred, not dropped; design in [07-episodes-patterns-playbooks](./07-episodes-patterns-playbooks.md).

5. **Budget enforcement path** — `llm_complete` calls `tenant_budget_service.check_budget` before spending. A tenant's own budget row wins when present; otherwise the deployment defaults flow through the *same* evaluation (`_DefaultBudget` carries exactly the attributes `_check_budget_locked` reads — no second implementation of the limit logic to drift). `block` raises `TenantBudgetExceeded` so callers can degrade; `warn` logs, emits an `llm.budget_warning` event, and lets the call through — roll out as `warn`, flip to `block`.

6. **Vision goes through the same gate** — `llm_complete(images=...)` is a parameter, not a separate client, precisely so the most expensive call type cannot bypass budget, recording, breaker, or clamp.

### Thinking budgets: why only `relevance` is capped

`llm_thinking_budgets` (config.py, keyed by prompt name) can cap Gemini's reasoning tokens per prompt. It ships configured for exactly one prompt — `relevance: 0` — and live A/B runs (2026-08-06, real tickets from the dev graph) confirmed that stopping there is correct, not cautious:

- **`episode` at 1024**: thinking dropped 2,601 → 784 on multi-evidence reconstruct chunks, but the *segmentation changed* — the same 20-evidence group produced 1 episode dynamically and 2 episodes capped, and step counts doubled on another group. A cap that redraws episode boundaries is a quality change, not a cost change.
- **`identity` at 1024**: extracted entity sets were identical (good), but typical tickets only think ~590 tokens dynamically — the cap never binds where the volume is, and the heavy calls where it would bind are exactly the untested ones.
- **`identity_adjudication` / `identity_reconciliation` / `message_function`**: confidence-threshold-coupled (`AUTO_LINK_THRESHOLDS` 0.95, reconciliation `MIN_CONFIDENCE` 0.95, `CLASSIFIER_TRUST_FLOOR`). A measured run showed capping adjudication moved confidence 0.95 → 0.80 at unchanged verdicts — capping silently converts auto-links into review-queue items unless thresholds are re-tuned in the same change (see `test_thinking_budget.py`).

Also measured: Vertex per-call latency varies 3–4× at identical token counts, so thinking caps are a **cost** lever, not a latency lever. Per-ticket latency comes from worker concurrency (RUNBOOK), not from trimming reasoning.

### Playbook model: gemini-3.7-flash measured better (2026-08-17)

The lane-routing switch shipped with an explicit gate: no model upgrade without a measurement. This is that measurement, for the playbook lane. `evals/playbook_model_ab.py` ran the REAL generator — prompt, source-ref validation, structural grounding classification — on 6 patterns from the live Zoho corpus (spread: 3 multi-episode, 2 pairs, 1 singleton) with only the model swapped, so the comparison isolates the model. Grounding is the axis that decides: `grounding_status` comes from validated citations, so the model cannot claim it.

| | gemini-2.5-flash | gemini-3.7-flash |
| --- | --- | --- |
| grounded-step share | 0.70 | **0.81** (never worse on any of the 6) |
| steps per playbook | 10.7 | **5.8** |
| latency | 25.5s | **14.5s** |
| rollback notes | 6/6 | 6/6 |

**Why it shipped:** 3.7-flash is better-grounded, not merely shorter — source-ref counts held while step counts halved, which reads as less invented best-practice padding, and it cost half the wall-clock. `playbook_model` defaults to `vertex_ai/gemini-3.7-flash` (config.py, `.env.example`); snapshot pinned at `evals/datasets/playbook_model_ab_2026-08-17.json`.

**Tradeoff / what this does not license:** the flip is a quality-and-latency call, not a saving — 3.7-flash is per-token pricier ($0.75/$3.75 per M vs 2.5-flash's $0.30/$2.50, introductory until 2026-12-31, doubling 2027-01-01), roughly a wash per playbook only because step counts halved. The rate is in `admin_cost_service.MODEL_COST_USD_PER_M_TOKENS`; re-check the math when the introductory window closes. And 6 patterns is a decision-grade sample, not a benchmark — the verdict was unanimous across the spread, which is what made it sufficient. It says nothing about the **pattern** lane (different prompt, different failure modes: over-merging clusters rather than padding steps); that lane keeps 2.5-flash until its own A/B. `has_verification` was 0/6 on *both* models — a prompt property, not a model differentiator, and a known open item.

### Local models: feasibility measured (2026-08-07)

Benchmarked the FinetuneModeledge GGUF models via llama-server (OpenAI-compatible) on the real relevance-v2 prompt with real tickets, on the dev workstation (4-core Ryzen, no CUDA GPU):

| model | warm latency | JSON contract | label quality |
| --- | --- | --- | --- |
| qwen2.5-0.5b Q4 (CPU) | ~6.3s | 6/6 valid | unusable — everything "operational" |
| gemma-4B capability-router Q4 (CPU) | ~17s | 3/3 valid | discriminates (2/3 on a 3-ticket sample, wrong-task fine-tune) |
| Vertex gemini-2.5-flash (production, 901 calls same day) | **2.4s avg / 2.6s p90** | — | baseline |

Conclusions: integration is trivial (LiteLLM routes `openai/<model>` at a local base_url per task via `MODEL_ROUTING`; the JSON contract held 9/9) — **feasibility is purely a hardware question**. On CPU, local is 3–7× slower than Vertex for equal-or-worse quality: not viable. On the RTX-5090 class hardware these models were trained on, a 4B Q4 serves at ~1–2s/call — competitive with Vertex at zero marginal cost and no data egress. The credible path if pursued: fine-tune a small model on the relevance task itself (the FinetuneModeledge pipeline + eval harness exists; the ae-router card shows the full loop at ~11 min training), using the 332 v2-labeled evidence rows as seed data, serve on GPU, and A/B against stored v2 labels before touching `MODEL_ROUTING["classification"]`. The 30s calls people notice are episode extraction / identity reconciliation (13.6s avg / ~29s p90) — those need large-model quality and are the wrong local-model target; relevance (the volume workload) is already 2.4s external.

### What batching is and is not for

Batch APIs (~50% discount, minutes-to-hours latency) fit the **ingestion pipeline** — classification, extraction, pattern synthesis, playbook generation, embeddings, eval runs — where nobody is waiting. They do not fit the serving path. Adopting them means restructuring Celery tasks around submit-then-poll and is scoped as its own piece of work.

## Design decisions

- **Reasoning as a separate metric, not a token_type label** — *Why:* it is a subset of completion; a label would double-count any dashboard summing across types. *Tradeoff:* two counters to keep in sync.

- **Defaults enforced through the tenant-budget path, not beside it** — *Why:* one limit evaluation, no drift; the dashboard can state the *source* of the cap. *Tradeoff:* a stand-in dataclass rather than a persisted row — deliberately not written to the DB, since a row created on first use would shadow later changes to the defaults.

- **Cached tokens priced at full input rate unless a `cached` rate is set** — *Why:* understating cost is the worse failure for a number people plan against. *Tradeoff:* the estimate overstates when caching works; set the provider's cache rate to sharpen it.

- **In-process rate table** — *Why:* no new table, editable per deployment. *Tradeoff:* goes stale; the provider's billing dashboard is authoritative and the UI says so.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Usage recording | `backend/src/contextedge/ai/observability.py` | `record_llm_usage`, `extract_usage`, `LLM_REASONING_TOKENS_TOTAL` | Every LLM call |
| Rates + aggregation | `backend/src/contextedge/services/admin_cost_service.py` | `MODEL_COST_USD_PER_M_TOKENS`, `get_llm_usage`, `_estimate_cost` | Dashboard poll |
| Budget enforcement | `backend/src/contextedge/services/tenant_budget_service.py` | `check_budget`, `_DefaultBudget`, `TenantBudgetExceeded` | Before each call |
| Ceilings | `backend/src/contextedge/config.py` | `llm_num_retries`, `llm_max_output_tokens`, `embedding_max_batch_size`, `default_daily_*` | Startup / per call |
| Call path | `backend/src/contextedge/ai/provider.py` | `llm_complete`, `generate_embeddings_batch` | Every LLM call |
| Cost API | `backend/src/contextedge/api/v1/admin_cost.py` | `/admin/llm-usage`, `/admin/tenant-budget/status` | HTTP |
| Dashboard | `frontend/src/app/(dashboard)/admin/cost/page.tsx` | KPI cards, breakdown bar, budget panel | UI |

## Acme VPN incident (this layer)

Acme's nine VPN tickets cost $0.30 to ingest — 62 classification calls, 15 extractions, 29 embeddings — and the dashboard shows 73% of the generated output was thinking. Acme has no budget row, so the deployment defaults (2M tokens / $25 per day, `block`) protected the tenant from a runaway loop, and the budget panel labels those caps **deployment default** rather than claiming the tenant is uncapped.

## Further reading

- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — where the spend originates
- [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) — eval runs as a batchable workload
- `.env.example` — every ceiling, with the reasoning inline
