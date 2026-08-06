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

5. **Budget enforcement path** — `llm_complete` calls `tenant_budget_service.check_budget` before spending. A tenant's own budget row wins when present; otherwise the deployment defaults flow through the *same* evaluation (`_DefaultBudget` carries exactly the attributes `_check_budget_locked` reads — no second implementation of the limit logic to drift). `block` raises `TenantBudgetExceeded` so callers can degrade; `warn` logs, emits an `llm.budget_warning` event, and lets the call through — roll out as `warn`, flip to `block`.

6. **Vision goes through the same gate** — `llm_complete(images=...)` is a parameter, not a separate client, precisely so the most expensive call type cannot bypass budget, recording, breaker, or clamp.

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
