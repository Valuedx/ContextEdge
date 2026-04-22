# ContextEdge — Enterprise Architecture Review

> **Reviewer context**: Review framed from the perspective of a technical architect supporting Claude-for-Enterprise deployments. Scope: the backend engine plus the frontend reviewer surface as of the `integration/demo-aeops` branch (commit `4f77e32`, merging ForAEOpsSupport + Demo). Version3's episode-extractor and Gmail-filter improvements are considered but not yet on the reviewed branch. This document is a reference artefact — update it alongside major architectural changes rather than replacing it.
>
> **Date**: 2026-04-22
> **Status**: Working review — roadmap items tracked separately
>
> **Related docs**: [TECHNICAL_BLUEPRINT.md](docs/TECHNICAL_BLUEPRINT.md), [AEAIHUB_INTEGRATION_PLAN.md](AEAIHUB_INTEGRATION_PLAN.md), [DEMO_ARCHITECTURE_PLAN.md](DEMO_ARCHITECTURE_PLAN.md), [CONTEXTEDGE_IMPLEMENTATION_PLAN.md](CONTEXTEDGE_IMPLEMENTATION_PLAN.md), [codewiki/KNOWN_GAPS.md](codewiki/KNOWN_GAPS.md), [docs/MIGRATIONS.md](docs/MIGRATIONS.md)
>
> **Scope note**: this review treats ContextEdge standalone. For the cross-system architecture (ContextEdge + AEAIHubOrchestrator + AutomationEdge), see [AEAIHUB_INTEGRATION_PLAN.md](AEAIHUB_INTEGRATION_PLAN.md) — the integration plan supersedes this review's implicit "build everything in ContextEdge" framing where the three-system split applies.

## Contents

1. [Executive summary](#1-executive-summary)
2. [What's genuinely good — keep it](#2-whats-genuinely-good--keep-it)
3. [Critical issues, ranked by ROI](#3-critical-issues-ranked-by-roi)
4. [Enterprise-readiness gaps (non-cost)](#4-enterprise-readiness-gaps-non-cost)
5. [Architectural concerns — watch list](#5-architectural-concerns--watch-list)
6. [90-day prioritised roadmap](#6-90-day-prioritised-roadmap)
7. [Strategic framing](#7-strategic-framing)
8. [Red flags to escalate to the customer](#8-red-flags-to-escalate-to-the-customer)

---

## 1. Executive summary

The engine is **architecturally well-structured** (clean layer separation, federated-edge graph, three-tier decision capture, reviewer-console bundle endpoint with prefetch cache) and **economically under-engineered**. Three issues dominate the cost and scale picture and none of them are hard to fix:

1. **Four LLM calls fire on every single ingested evidence item, with zero prompt caching.** Anyone running this at real volume is burning ~70% more inference cost than necessary.
2. **The contradiction scanner is quadratic** (`playbooks × KB items × steps`). Workable at 10 playbooks × 100 KB items; catastrophic at 500 × 10k.
3. **No vector index** on `evidence_items.embedding` or `decisions.embedding`. Every similarity query is a full Postgres scan of a 3072-dim vector space. Works today at demo scale; breaks at 3M rows.

Everything else downstream of those three is solvable with standard enterprise-readiness work (SSO, redaction, budgets, cost observability, partitioning). None of it is architecturally hard.

---

## 2. What's genuinely good — keep it

- **Edge-first graph model** with `(node_type, node_id)` adjacency (`backend/src/contextedge/models/pattern.py::GraphEdge`, `backend/src/contextedge/graph/builder.py`). Cheap, transactional, federated-friendly. Good call not using a dedicated graph DB.
- **Three-tier decision capture** — governed execution edges, AI-extracted decisions, and first-class `Decision` nodes with typed edges (`considered`/`chose`/`based_on`/`resulted_in`/`followed_by`). Rare to see this level of structured capture. The `considered`/`chose` edge pair is the right encoding for "ranked hypotheses with ruled-out alternatives." See [codewiki/16-decision-traces.md](codewiki/16-decision-traces.md).
- **Playbook lifecycle state machine** with `suggest_only` / `supervised` / `full_auto` automation modes and safety classes (`read_only`, `low_side_effect`, `high_side_effect`, `destructive`). Right primitive for enterprise trust ramp.
- **Read-through Redis cache with warm-on-creation + invalidation** on mutation paths. `backend/src/contextedge/services/review_queue_service.py` is textbook — short-lived client, schema-round-trip payload, tenant-scoped keys, graceful-fail, explicit invalidation from `create_decision`, `record_outcome`, `reject_decision`, `close_resolution_session`.
- **Structured reject/modify reason codes** (`REJECTION_REASON_CODES` in `backend/src/contextedge/models/decision.py`) feeding `get_decision_effectiveness` is the right foundation for the learning loop. Most competitors use free text and can't answer "why did engineers reject this?" at any granularity.
- **Bundle endpoint pattern** (single round trip for 7-zone reviewer console, see `backend/src/contextedge/api/v1/review_queue.py`). This avoids the N+1 that kills most operational UIs.

Don't redesign any of these. Build on them.

---

## 3. Critical issues, ranked by ROI

### 3.1. LLM fan-out with no prompt caching — single biggest cost lever

Every ingested evidence item fires four LLM calls (verified in `workers/extraction_tasks.py`, `services/identity_service.py`, `services/decision_service.py`, `ai/classifiers/relevance.py`): **embed → classify → extract identities → extract decisions**. Zero caching is in use (no `cache_control`, no `ephemeral` blocks, no OpenAI cache keys anywhere in the codebase — grep-verified).

Two compounding issues:

**Order is wrong.** Evidence is embedded (`_ensure_embedding` at `workers/extraction_tasks.py:33-38`) *before* being classified for relevance. This means irrelevant tickets — which the classifier will mark `unclassified`/`noise` — still pay the embedding cost. The classifier itself is two-stage in the Gmail connector (Version3 adds `_is_operationally_relevant_stage1`), but that's connector-specific. **Flip the order for the generic path**: classify first, embed only for relevant items. At typical enterprise IT inbox noise rates (~60–70% of mail is non-operational), this alone cuts embedding spend by ~65%.

**No prompt caching.** Classification/extraction prompts are heavy system prompts plus a small per-item user message. Anthropic prompt caching (`cache_control: {"type": "ephemeral"}` on the system block) cuts cached tokens to 10% of normal pricing with a 5-min TTL, or 25% for the 1-hour beta. Since these workers process batches of evidence with identical system prompts, cache hit rates should run 90%+ after the first call per worker warm-up. **Expected cost reduction: 40–60% of classification/extraction LLM spend.**

**Concrete fix**: wrap `llm_complete_json` in `backend/src/contextedge/ai/provider.py:115-132` with cache headers when the task is `classification` or `extraction` and the prompt has a static prefix. Split prompts into `[system_static, system_dynamic_few_shot, user_query]` and cache the first two blocks.

### 3.2. Contradiction scanner is a quadratic LLM bomb

`backend/src/contextedge/services/contradiction_service.py::scan_contradictions:140-253` iterates `approved_playbooks × kb_evidence × extracted_steps`. With a token-overlap gate (`should_compare_contradiction`) that trims to ~10–20% LLM calls, the worst case is **100–200K LLM calls per 12-hour beat** for a moderate-sized tenant (100 playbooks × 1000 KB items × 10 steps each).

At $0.50 per million input tokens on a cheap model, that's $200–400/day per tenant, 24/7, on a task that produces maybe 5 actionable contradictions per week.

**Redesign, not tune**:

- **Embedding-first gating**: use cosine distance between playbook-step embeddings and KB embeddings as the pre-filter. Only pairs with distance < threshold proceed to LLM. This drops 90%+ of pairs before an LLM call.
- **Incremental scan**: only compare KB items ingested or playbooks updated since the last scan. `scan_contradictions` currently always processes everything. Track `last_scanned_at` per `(playbook_id, kb_evidence_id)` pair.
- **Budget-bounded**: cap at N LLM calls per scan per tenant; log the queue depth for what got skipped. Operators can raise the cap if they care.

Version3 didn't touch this. It's a latent bomb. **Expected cost reduction: 80–95% of contradiction-scan spend.**

### 3.3. No vector index — everything is a full scan

`backend/src/contextedge/search/vector_search.py:25-46` does `ORDER BY embedding.cosine_distance(emb)` with no index support. At 3.65M evidence rows × 3072 dims × 4 bytes = **45 GB of embedding data** scanned linearly per similarity query. That's fine at 100k rows; it's a Postgres CPU fire at 10M.

**HNSW index on both `evidence_items.embedding` and `decisions.embedding`**:

```sql
CREATE INDEX CONCURRENTLY idx_evidence_embedding_hnsw
  ON evidence_items USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

One migration. Approximate nearest neighbor, ~95% recall at 100× speedup. [codewiki/KNOWN_GAPS.md](codewiki/KNOWN_GAPS.md) already flags this; it needs promotion from "follow-up" to "must-do before first pilot of any scale."

Pair with a cheaper embedding: **text-embedding-3-small at 1536 dims** (or Matryoshka-truncated from 3072 → 512 for first-pass retrieval, full-dim only for re-rank). The current `Vector(3072)` via `text-embedding-3-small` sets dims=3072 (`backend/src/contextedge/ai/provider.py:98`), which is the max Matryoshka allows but not optimal. **Expected storage reduction: 50%. Expected query latency: 10–30ms at 10M rows with HNSW + 1536 dims.**

### 3.4. Model tiering is two-tier, should be three

Current routing (`backend/src/contextedge/ai/provider.py:35-43`, `backend/src/contextedge/config.py:55-57`): classification → `gpt-4o-mini`, extraction → `gpt-4o`, embeddings → `text-embedding-3-small`. Reasonable default but leaves money on the table.

For a Claude-for-Enterprise deployment (recommended for the pricing + context window + prompt caching maturity):

- **Classification → Haiku 4.5** (`claude-haiku-4-5`). Faster, cheaper, matches gpt-4o-mini on classification tasks, significantly better on structured output.
- **Extraction (identity, decision, episode) → Sonnet 4.6** (`claude-sonnet-4-6`). Extractions like episode reconstruction benefit from better reasoning; gpt-4o is middle-of-the-road there.
- **Episode reconstruction with extended thinking → Sonnet with thinking enabled** or **Opus 4.7**. The Version3 prompt is asking the model to do item-by-item mapping with analysis — exactly what extended thinking is for. Current `task="extraction"` path at `max_tokens=16384` without thinking leaves accuracy on the table.
- **Keep embeddings on OpenAI or use Voyage/Cohere** (Anthropic doesn't offer embeddings; that's fine). But switch to a 1536-dim or Matryoshka model.

Cost shift: roughly neutral (Haiku cheaper than 4o-mini, Sonnet slightly pricier than 4o), but **accuracy improvement on extraction feeds directly into fewer reruns and higher agent confidence**, which means fewer reviewer-rejects and less LLM retry overhead downstream.

### 3.5. Episode extractor has no input-size cap

`backend/src/contextedge/ai/extractors/episode_extractor.py:75` truncates each item to 2000 chars but doesn't cap `N` items. For a VPN outage thread with 50 replies, prompt input = 50 × 2000 = 100k chars ≈ 25k tokens before template overhead. `max_tokens=16384` output. Single call cost at Sonnet pricing ≈ $0.08; at current extraction model volume ≈ manageable; at 10× scale ≈ not.

**Fix**: if `N > 20`, chunk into subgroups by thread contiguity or time window, extract per-chunk, then run a synthesis pass on the chunk-level outputs. Same pattern as map-reduce summarisation.

### 3.6. Retention is soft-delete only — unbounded table growth

`backend/src/contextedge/services/retention_service.py:25-73` sets `relevance_state = "archived"` but never deletes. At 10k/day × 365 = 3.65M rows/year that stay in the hot `evidence_items` table forever, dragging every query, bloating indexes, and holding 45GB of embeddings.

**Fix pattern**: nightly cron that moves archived-past-grace rows to a cold table (`evidence_items_archive`) and their embeddings to cheaper storage (or just drops the embedding column value to NULL on archival — the text stays queryable, similarity retrieval returns no match). Partition `evidence_items` by `tenant_id, ingested_at` month while you're in there.

---

## 4. Enterprise-readiness gaps (non-cost)

Tier-1 must-haves before any serious pilot, not yet in the codebase:

- **SSO / OIDC federation** — the seeded-JWT fallback won't pass procurement. `backend/src/contextedge/middleware/auth.py` has OIDC helpers; wire them to Entra/Okta with JIT user provisioning. **Deferred pending customer IdP choice** (§6 item 8).
- ~~**PII/secret redaction at ingest**~~ — **shipped 2026-04-23** via `services/redaction_service.py` regex MVP (EMAIL, PHONE, SSN, CREDIT_CARD, AWS_ACCESS_KEY, AWS_SECRET_KEY, PRIVATE_KEY) wired into `_normalize` before embed + extraction. Presidio upgrade still worth scheduling.
- ~~**Correlation ID propagation**~~ — **shipped 2026-04-23** via Celery `before_task_publish` / `task_prerun` / `task_postrun` signal handlers in `workers/celery_app.py` plus `llm.usage` structlog enrichment. One ID now flows HTTP → Celery → LLM.
- **Per-tenant cost observability** — nobody's tracking which tenant/decision-type burns the LLM budget. Minimum viable: log token counts + model from every `llm_complete_json` call with tenant ID tag, Grafana panel. One afternoon of work, saves you from the first "why is our bill $X" customer call. **Partially mitigated** by the Weeks 1-2 `llm.usage` instrumentation + `/admin/cost` dashboard; Grafana alerting still pending.
- **Per-tenant budget guardrails** — hard cap at N tokens/day/tenant; graceful degradation to cheaper models or skip-and-queue.
- **Output schema validation + retry on every LLM call** — `llm_complete_json` returns unstructured sometimes; retry with a reminder of the schema. Prompt-injection hardening also needs this.
- **Per-tenant model pinning** — EU customers won't accept US-hosted inference. `litellm` supports this; needs a `tenant_model_config` table.
- ~~**Shadow mode as first-class state**~~ — **shipped 2026-04-23** via `AUTOMATION_MODES` enum (§6 item 11). `"shadow"` is now a validated, observable state with tool-invocation dry-run tagging.

---

## 5. Architectural concerns — watch list

- ~~**`graph_edges` will need a GIN index on `metadata_extra`**~~ — **mitigated 2026-04-23** by migration `0025_jsonb_gin_indexes` (jsonb_path_ops GIN, built `CONCURRENTLY`). Richer edge-metadata traversals now hit an index instead of full-scanning.
- **JSONB-heavy schemas** (`context_snapshot`, `evidence_summary`, `result_details`, `baseline_ref`, `modification_diff`) are flexible but unindexed. Any tenant-facing filter over these pays full-scan cost. Add targeted GIN indexes as specific filter paths emerge; don't index everything — `0025` deliberately kept out these columns until a real query path justifies the write-amplification cost.
- ~~**`canonical_entity_refs` on `EvidenceItem`**~~ — **mitigated 2026-04-23** by the same migration (`ix_evidence_items_canonical_entity_refs_gin`, jsonb_path_ops). Whole-column rather than `->'identities'` specifically, so any filter against `decisions` or future sub-keys benefits too.
- **Celery task queue design** is reasonable but has no priority ordering. A customer's P1 incident ingestion competes with back-fill jobs. Add priority queues (`extraction-priority`, `extraction-bulk`) and route P1 session evidence to the priority lane.
- **Decision provenance endpoint** (`get_decision_provenance`) joins `Source` + `SourceObject` per evidence — single query today, but N=20 evidence × 2-table join doesn't scale past 100 evidence. Bundle the source info onto `EvidenceItem` as denormalised `source_cache_json` if this becomes a hot path.
- **Celery workers lack fair-share per tenant**. One noisy tenant can starve others. Add concurrency limits via `worker_max_tasks_per_child` tagged per tenant.

---

## 6. 90-day prioritised roadmap

### Weeks 1–2: cost quick wins

Can be one engineer in two weeks, ~50% cost reduction expected.

1. **Shipped 2026-04-22** — Prompt caching on classification + extraction system prompts. `ai/provider.py::llm_complete` now splits system/user messages and marks the system block with `cache_control: {"type": "ephemeral"}` via `ai/observability.build_messages`. Classifier prompt rewritten with stable `SYSTEM_PROMPT` for cache-prefix hits.
2. **Shipped 2026-04-22** — Normalize order flipped. `workers/extraction_tasks._normalize` now classifies inline *before* embedding and identity/decision extraction. Items confidently classified `not_relevant` (confidence ≥ 0.75) skip the expensive fan-out entirely. `classify_relevance_task` removed from post-normalize dispatch (retained for manual re-classification).
3. **Shipped 2026-04-22** — Token-count + model-name logging per LLM call. New `ai/observability.py` module with Prometheus counters (`contextedge_llm_tokens_total`, `contextedge_llm_requests_total` labelled by tenant/model/task/type), structured `llm.usage` logs, and operational-event persistence for historical dashboard queries. Embedding calls + completion calls both instrumented. `extract_usage` normalises OpenAI `prompt_tokens_details.cached_tokens` and Anthropic `cache_read_input_tokens` into one `cached_tokens` metric.
4. **Shipped 2026-04-22** — HNSW migration. `alembic/versions/0021_hnsw_vector_indexes.py` creates cosine-ops HNSW indexes on both `evidence_items.embedding` and `decisions.embedding` using `CONCURRENTLY` (no table lock). `pyproject.toml` bumped to `pgvector>=0.5`.

**Bonus ship 2026-04-22**: admin observability surface. New `GET /api/v1/admin/llm-usage` endpoint (`api/v1/admin_cost.py`) aggregates the `llm.usage` operational events into headline KPIs (total cost estimate, token split, cache-hit rate, avg cost/request) plus a top-N model×task breakdown. Frontend `/admin/cost` route (`tenant_admin` gated) renders the dashboard with KPI cards, a cache-health tone indicator (green/amber/red using the same thresholds as the reviewer-console confidence badge), and a CSS-only stacked-bar breakdown of prompt-non-cached / cached / completion tokens per row. 60-second auto-refresh so effects of prompt-caching or model swaps are visible in near-real-time.

### Weeks 3–4: the quadratic scanner

5. **Shipped 2026-04-19** — Contradiction scan redesign. `services/contradiction_service.py` rewritten to the three-gate pattern: (a) HNSW top-K KB candidate pull (default `top_k=20`) replaces the full KB sweep, (b) new `contradiction_scan_state` table (migration `0022_contradiction_scan_state`) records each `(playbook_version, evidence)` pair so already-scanned pairs short-circuit on subsequent ticks, (c) explicit `max_llm_calls` budget (default 1000) bounds worst-case spend per invocation. Result dict now reports `llm_calls_used`, `token_skips`, `cursor_skips`, `budget_skips`, `budget_exhausted`. System/user prompt split so the schema + instruction block is prompt-cacheable. Expected LLM call reduction on warm tenants: 80–95%.
6. **Shipped 2026-04-19** — Retention hard-delete / soft-purge cron. New `purge_archived_evidence(db, *, tenant_id, archive_grace_days=30, mode, dry_run, limit)` in `services/retention_service.py`. `hard_delete` mode issues real `DELETE`s against rows archived past the grace window (30-day default, GDPR-friendly), cascading via existing FKs to `attachment_artifacts` / `correlation_edges` / `contradiction_scan_state`. `soft_purge` mode NULLs `embedding` + `body_text` + `body_summary` and replaces `title` with `"[purged]"` — preserves IDs for reference linking while making content unrecoverable and search-invisible. Legal-hold rows always excluded in the SQL `WHERE` clause (not post-filtered). `dry_run=True` reports candidate count without mutation; `limit` + `limit_reached` flag let the caller drain backlogs across ticks.
7. **Shipped 2026-04-19** — Episode-extractor chunking + token-budget guard. `ai/extractors/episode_extractor.py` now splits clusters larger than `MAX_ITEMS_PER_CALL=20` into per-chunk LLM calls (single-call behaviour preserved for smaller clusters). `PER_ITEM_CHAR_LIMIT=2000` per-body truncation formalised as a module constant — a single pathological evidence item can no longer blow the per-call token budget. `chunk_count` logged via `episode_extractor.chunked` so spikes from oversized clusters are observable. Cross-chunk episode dedup deliberately deferred — downstream pattern-mining and correlation services already merge overlapping incidents, and a synthesis LLM pass would roughly double cost for marginal gain on current workloads.

### Weeks 5–6: enterprise gates

8. **Deferred** — SSO/OIDC wire-up with one demo IdP (Entra). Parked until the customer confirms an IdP choice. Existing JWT bearer auth (`jose.jwt` + `TenantContextMiddleware`) covers local-credential login, and the service-token path covers integrations — both will remain as the fallback once OIDC is the default.
9. **Shipped 2026-04-23** — Redaction pass at ingest (regex MVP). `services/redaction_service.py` introduces `redact(text)` and `redact_evidence_fields(title, body)` covering emails, phone numbers, SSN, credit-card-ish digit runs, AWS access keys, AWS secret keys, and `-----BEGIN … PRIVATE KEY-----` blocks. Wired into `_normalize` so title + body + the identity/decision-extractor prompt blob all run through redaction before any LLM / embed call — PII never leaves the tenant boundary on the ingest path. Per-kind counts are emitted as an `evidence.redacted` structlog event for audit. Gated by `settings.redaction_enabled` (on by default; flip to false only for local debugging). Deduplication hash is computed over the pre-redaction payload so tuning the regex rules doesn't break dedup.
10. **Shipped 2026-04-23** — Correlation ID propagation through Celery + LLM calls. `workers/celery_app.py` now wires three signal handlers: `before_task_publish` reads the current HTTP request's `request_id` / `correlation_id` / `causation_id` from the ContextVar and injects them into the outgoing Celery message headers; `task_prerun` rebinds them into the worker's ContextVar so any service code running inside the task automatically inherits the IDs; `task_postrun` resets the token. `append_operational_event` already pulled from the ContextVar, so worker-emitted events now carry correlation IDs without code changes. `ai/observability.record_llm_usage` also enriches the `llm.usage` structlog line with `request_id` / `correlation_id` / `causation_id`, so a single reviewer action can be grepped across HTTP → Celery → LLM log lines.
11. **Shipped 2026-04-23** — Shadow mode as first-class `automation_mode` value. `models/playbook.AUTOMATION_MODES` introduces an ordered enum (`suggest_only`, `shadow`, `human_confirmed`, `supervised`, `full_auto`) with Pydantic-level validation on `PlaybookCreate` / `PlaybookUpdate`. `execution_service._caller_max_safety_class` now permits admins to attempt destructive actions under shadow (since nothing real executes) and caps non-admins at `high_side_effect`. `record_tool_invocation` detects the shadow mode of the parent run via `is_shadow_mode(run.automation_mode)` and tags tool outputs with `shadow: True`, forces status to `shadow_executed`, and fires a `tool.shadow_executed` operational event — so analytics can separate real outcomes from dry-run traces when computing success rates.

### Weeks 7–9: scale foundations

12. **Shipped scoped MVP 2026-04-23** — Evidence-table scale readiness. Migration `0024_evidence_scale_indexes` adds three hot-path indexes built with `CREATE INDEX CONCURRENTLY`: a BRIN on `(tenant_id, ingested_at)` for time-range admin / drift / retention queries, a partial B-tree on `(tenant_id, relevance_state) WHERE relevance_state IN ('relevant', 'unclassified')` for the reviewer queue, and a partial B-tree on `(tenant_id, updated_at) WHERE relevance_state = 'archived'` for the retention purge sweep. The full partition-conversion (per-tenant list partitioning with optional per-month range sub-partitioning) is **deferred** with a detailed runbook at `codewiki/04-evidence-normalization-and-storage.md` → "Partitioning plan (deferred)" — because the partition-key choice (LIST vs HASH at 100+ tenants, sub-partition granularity) depends on real customer volume that doesn't exist yet. Shipping indexes now gives 80% of the scale benefit with 0% of the conversion risk.
13. **Deferred** — Read replica for reviewer-queue hot path. Requires live infra to validate; no useful shippable code without a replica to connect to.
14. **Shipped 2026-04-23** — Per-tenant LLM budget enforcement. New `tenant_llm_budgets` table (migration `0023`) with `daily_token_limit` + `daily_cost_cap_usd` + `action_on_exceed` (`block` / `warn`). `services/tenant_budget_service.check_budget` sums the current UTC day's usage from the existing `llm.usage` operational events (no second source of truth), cached with a 60s TTL so enforcement costs one aggregation per minute per tenant, not per LLM call. Pre-call gate in `ai/provider.llm_complete` raises `TenantBudgetExceeded` on `block` or logs + emits `llm.budget_warning` on `warn`. Admin API `GET/PUT /admin/tenant-budget` + `GET /admin/tenant-budget/status` (tenant_admin gated) lets operators configure and monitor live. Token-limit check precedes cost-cap check when both are set.
15. **Shipped 2026-04-23** — Output schema validation + retry. New `ai/provider.llm_complete_json_validated(prompt, schema, ...)` takes a Pydantic model, validates the response, and on failure sends one repair call that includes the raw prior response + the Pydantic validation errors + the JSON Schema of the target model. Retry budget is hard-capped at 1 — two LLM calls per extraction is already a real cost line. Raises `ValueError` naming the schema when the retry also fails, which is cheaper for callers to catch than chasing `ValidationError` up the stack.

### Weeks 10–12: agent quality

16. **Shipped 2026-04-23** — Prompt versioning + A/B infrastructure. New `ai/prompts/` package with a `Prompt` dataclass (`name` + `version` + `system` + `user_template`) and a registry (`register_prompt`, `get_prompt`, `resolve_version`). **All seven LLM prompt families now register a `v1` default** (`relevance`, `episode`, `decision`, `identity`, `pattern`, `playbook`, `contradiction`), each resolved per-call via `get_prompt(name, tenant_id)`. Where the inline prompt was a single blob, the migration splits it into a stable `system` block (cacheable — OpenAI automatic prefix cache + Anthropic ephemeral cache both hit) and a dynamic `user_template`. Per-tenant variants are config-driven via `settings.tenant_prompt_variants_json`. `prompt_name` + `prompt_version` are threaded all the way through `llm_complete`/`llm_complete_json` into the `llm.usage` structlog line and operational event, so the admin dashboard can break down cost + quality per version. Malformed variants config falls back cleanly to the default (with a log). Adding a new prompt family = a new submodule that calls `register_prompt` and a single import line in `ai/prompts/__init__.py`.
17. **Shipped 2026-04-23** — Confidence calibration + pattern-mining scheduling. `workers/decision_tasks.py` already defined `calibrate_decision_confidence` and `mine_decision_patterns`; they were never registered with Celery and never scheduled. Now they're named `evaluation.calibrate_decision_confidence` / `evaluation.mine_decision_patterns` (routes to the existing `evaluation` queue), accept the `"all"` sentinel for per-tenant fan-out (matching `detect_drift` / `scan_contradictions_task`), isolate per-tenant exceptions so one bad tenant doesn't kill the beat for the rest, and ship with daily beat entries. Output persists to `decision_analytics` operational events for dashboard queries.
18. **Shipped scaffold 2026-04-23** — Eval dataset + regression runner. New `backend/evals/` directory with per-extractor `golden.jsonl` files (relevance ships with 5 hand-labelled cases covering clear-operational, clear-noise, ambiguous edge), a CLI runner (`python -m evals.run_regression relevance`) that reports per-case pass/fail + confusion matrix + accuracy and exits non-zero on any failure, and a README documenting the case format + redaction rule + expansion guidelines. Weekly Celery Beat wiring is **deliberately deferred** until the customer signs off on what "regression" means for them (accuracy threshold vs week-over-week delta vs specific-decision-type criticality); the scaffolding makes that wiring a one-line beat addition once the pass bar exists.

---

## 7. Strategic framing

Two architectural commitments worth pushing the customer toward:

1. **Treat prompts as versioned artifacts with SLAs.** A model-version bump or prompt change that drops extraction accuracy by 3% is currently undetectable until a reviewer notices weeks later. Make it a tracked metric with a pre-deploy eval gate.

2. **Make the learning loop visible in the product.** `get_decision_effectiveness` analytics are backend-only right now. Surface "your rejection rate on `execute_playbook` dropped from 18% → 6% over the last month" in the reviewer console. This is the single clearest artifact that justifies the LLM spend — show it to the CIO, not just the engineers.

---

## 8. Red flags to escalate to the customer

- ~~**Contradiction scan at scale will surprise them**~~ — **mitigated 2026-04-19** by embedding-first gating + incremental cursor + budget cap (§6 item 5). Per-tenant budget enforcement (§6 item 14) still needed to cap cross-tenant blast radius.
- **No cost observability means the first production incident will be a cost incident, not an availability incident.** Partially mitigated by `/admin/cost` dashboard (Weeks 1-2 bonus ship); alerting still required.
- ~~**Retention is a compliance time bomb.**~~ — **mitigated 2026-04-19** via `purge_archived_evidence` hard-delete + soft-purge modes with legal-hold safety (§6 item 6). Still needs a scheduled cron beat wiring to actually run on a cadence.
- ~~**Episode extractor input is unbounded**~~ — **mitigated 2026-04-19** via `MAX_ITEMS_PER_CALL` chunking + `PER_ITEM_CHAR_LIMIT` truncation (§6 item 7). Per-tenant budget cap still desirable for multi-tenant blast-radius control.

---

## Appendix A — data points grounding the review

| Finding | Source |
|---|---|
| 4 LLM calls per ingested evidence (embed + classify + identity-extract + decision-extract) | `workers/extraction_tasks.py:36`, `services/identity_service.py:128,141`, `services/decision_service.py:39,62-77`, `ai/classifiers/relevance.py:38` |
| No prompt caching anywhere | grep: zero `cache_control`, `ephemeral`, `prompt_caching`, `cache_key` |
| Two-tier static routing | `ai/provider.py:35-43`; `config.py:55-57` |
| Hybrid ranker weights hard-coded, no tenant override | `search/hybrid_ranker.py:22-30` |
| No HNSW/IVFFlat index on embedding columns | `search/vector_search.py:25-46`; alembic migrations 0006–0020 |
| Retention marks archived, never deletes | `services/retention_service.py:25-73,76-88` |
| Contradiction scan O(playbooks × KB × steps) with token-overlap gate | `services/contradiction_service.py:47-101, 140-253` |
| Episode extractor: per-item 2000-char cap, no `N`-items cap | `ai/extractors/episode_extractor.py:75` |
| `max_tokens=16384` on extraction calls | `ai/provider.py:123` |

## Appendix B — how to update this document

- When a roadmap item ships, add a `**Shipped (commit hash, YYYY-MM-DD):**` note inline rather than deleting the item. Keeps the review interpretable as a historical artefact.
- When a new cost/scale issue is discovered in production, add a dated section to [codewiki/KNOWN_GAPS.md](codewiki/KNOWN_GAPS.md) first; promote to this doc only if it warrants roadmap reordering.
- Keep appendix A's data-point citations current — if the underlying code changes and a cited line moves or the behaviour shifts, update or retract the finding.
- Review should be re-done top-to-bottom roughly quarterly or after any major architectural change (connector framework rewrite, new execution tier, switch of LLM provider).
