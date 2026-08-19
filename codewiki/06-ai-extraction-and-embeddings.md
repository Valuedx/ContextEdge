# AI extraction and embeddings

## Summary

You will understand how ContextEdge calls **language models** through one guarded funnel (LiteLLM under `llm_complete`), how **embeddings** power semantic search on two surfaces (parent evidence and chunks), how **episode reconstruction** turns a correlated evidence cluster into a reviewable draft — including the row of cost gates that runs *before* any model call — and how the optional **AI review** stage stamps or auto-approves those drafts. Nothing in the AI layer is a black box: every call is versioned (prompt registry), budgeted (tenant caps), recorded (`llm.usage`), and validated (schema gates) before its output touches the database.

## Business picture

AI reads your tickets and messages faster than any human — but everything it proposes starts as a draft. The platform uses AI to **sort incoming evidence by relevance** (so noise never pays for downstream processing), **find each item's place in the story** (embeddings and correlation), and **reconstruct what happened** — symptoms, steps tried, root cause, outcome — as an episode a reviewer can check line by line, with every step pointing at the evidence that supports it.

Because narration costs real money and most partial stories get retold when more evidence arrives, the system is deliberately reluctant to call the model: it waits for a cluster to settle, refuses to narrate fragments, and refuses to re-narrate a story that has barely grown. When a draft does exist, an optional AI reviewer can pre-screen it: in **advisory** mode it annotates drafts for the human queue; in **auto_approve** mode, drafts that clear both the model's verdict *and* hard deterministic floors are approved with no human signature — permanently distinguishable from a human approval, so trust can be audited later. Tenant isolation and budget caps hold at every step.

## Technical walkthrough

### 1. Provider abstraction — one funnel, per-task routing

- `MODEL_ROUTING` maps a **task** key to a model from settings (backend/src/contextedge/ai/provider.py:47-53). Current defaults (backend/src/contextedge/config.py:56-67): `classification`, `extraction`, and `pattern` → `vertex_ai/gemini-2.5-flash`; `playbook` → `vertex_ai/gemini-3.7-flash` (switched on the measured 2026-08-17 A/B — see [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md)); `embedding` → `text-embedding-3-small`. `get_model_for_task` falls back to the extraction model for unknown tasks (provider.py:64-65).
- `LOCATION_ROUTING` gives each task its own Vertex location, all defaulting to `"global"` (provider.py:55-61; config.py:70-75). Vertex calls pass `vertex_project`/`vertex_location` per request rather than trusting process env (`_vertex_litellm_kwargs`, provider.py:94-114). `_is_vertex_model` routes `vertex_ai/*` ids and bare `gemini-*` ids (when the default provider is Vertex and credentials exist) through Vertex; `gemini/gemini-*` stays on the API-key provider (provider.py:76-91).
- **Prompt caching is provider-gated.** `cache_control: {"type": "ephemeral"}` markers on the system block go only to providers in `_EXPLICIT_CACHE_PREFIXES` (`anthropic/`, `claude-`, `openai/`, `gpt-`, `azure/`) (provider.py:168-174). Vertex/Gemini is deliberately excluded: above ~3K chars of system prompt, LiteLLM turns the marker into a Vertex context-cache resource whose creation 404s — every call fails outright, discovered when an extraction prompt grew from 2.8K to 4.5K chars (comment at provider.py:152-167). Gemini caches repeated prefixes implicitly anyway.

### 2. `llm_complete` — what happens on every call, in order

Everything (JSON mode, vision images, all classifiers and extractors) routes through `llm_complete` (provider.py:177-405), so the budget gate, the clamp, the breaker, the timeout, the fallback, and usage recording cannot be bypassed:

1. **Tenant budget gate** — when `tenant_id` and `db` are both passed, `check_budget` runs *before* any tokens are spent; a `block` verdict raises `TenantBudgetExceeded`, a `warn` verdict logs `llm.budget_warning` and writes an operational event, then proceeds (provider.py:234-285). Details in [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md).
2. **Output-token clamp** — `effective_max_tokens = min(caller's max_tokens, settings.llm_task_output_tokens.get(task, settings.llm_max_output_tokens))` (provider.py:290-293). The per-task map grants `extraction`/`playbook`/`pattern` 16,384 tokens; everything else is capped at 4,096 (config.py:95, 132-138) — the ceilings exist because a silent clamp once truncated playbook and episode JSON mid-array (post-mortem in config.py:96-131).
3. **Attempt with resilience** — `_attempt` checks the per-model circuit breaker, resolves the **thinking budget per attempt** (the fallback model may not support reasoning and would 400 — comment at provider.py:317-321), and runs `litellm.acompletion` under a 120 s timeout (provider.py:338-363; `LLM_CALL_TIMEOUT_SECONDS` at backend/src/contextedge/ai/resilience.py:28). The breaker opens after 5 consecutive failures per model for 60 s with one half-open probe (resilience.py:29-30, 59-91). LiteLLM itself retries transients `llm_num_retries` = 2 times — each retry is a fully billed call (provider.py:41-45; config.py:91).
4. **Fallback** — if the primary attempt raises and `settings.llm_fallback_model` is set and different, the call logs `llm.falling_back` and retries once on the fallback; `model` is reassigned so usage records the model that actually served (provider.py:365-380; config.py:80-82).
5. **`finally`: `record_llm_usage`** — always, including on error, because an errored call still consumed provider-side tokens (provider.py:385-405). See §7.

**JSON mode.** `llm_complete_json` requests 16,384 max tokens for `extraction`/`playbook`/`pattern`, else 8,192 (provider.py:527 — then step 2 clamps; for `classification` the effective ceiling is 4,096), sets `response_format={"type": "json_object"}` (provider.py:535), and on unparseable output walks a repair ladder: strip markdown fences → slice first `{`/`[` to last `}`/`]` → `repair_truncated_json` (close open braces/quotes, provider.py:408-451) → `_salvage_truncated_entities_json` (recovers complete objects from a truncated `{"entities": [...]}`, provider.py:454-501) → `ValueError` with `llm_json_repair_failed`/`llm_json_parse_failed` logs (provider.py:582-597).

**Schema-validated retry.** `llm_complete_json_validated[T: BaseModel]` validates the parsed JSON against a Pydantic schema; on the first failure it builds a repair prompt embedding the JSON schema, the validation errors, and the invalid response, and re-sends **once** at temperature 0 — the retry budget is hard-capped at 1 because "two LLM calls per extraction is already a real cost line" (provider.py:651-736). A second failure raises `ValueError` naming the schema. The episode extractor does *not* use this wrapper — it uses `llm_complete_json` plus its own drop-don't-repair gate (§4).

### 3. Embeddings — two surfaces, one gate

- `generate_embedding` runs the same pre-call budget gate as completions (the agent seed resolver triggers one per turn, so a blocked tenant must block here too), sends `dimensions=3072` unless the model name contains `gemini-embedding`, hard-fails any model that returns a different dimensionality, and records usage with `task="embedding"` in a `finally` block (provider.py:739-811).
- `generate_embeddings_batch` (provider.py:814-916) splits requests into sub-batches of `embedding_max_batch_size` = 64 (config.py:142) and **re-checks the budget per sub-batch** — it recurses into itself with the tenant context, so a long ingest stops at the cap mid-request instead of finishing past it, and each sub-batch records its own attributed usage (provider.py:859-876). Threading tenant context here closed review P1-8: ingestion embeddings — the bulk of embedding spend — previously bypassed blocked tenants' caps and recorded as `tenant_id=unknown` (provider.py:829-834; codewiki/KNOWN_GAPS.md:47).
- Evidence-facing helpers `embed_evidence` / `embed_decision` / `embed_evidence_batch` accept and forward `tenant_id`/`db`; empty text returns a zero 3072-vector (backend/src/contextedge/ai/embeddings.py:19-100).

Two embedding surfaces coexist on evidence:

- **Parent embedding** — `EvidenceItem.embedding`, generated inline in `_normalize` via `_ensure_embedding` → `embed_evidence(title, body_text)` (backend/src/contextedge/workers/extraction_tasks.py:65-70). Drives contradiction scanning, similar-decision retrieval, and baseline matching. Caveat worth knowing: this call site (and the artifact re-embed at backend/src/contextedge/services/artifact_extraction_service.py:510) passes **no** `tenant_id`/`db`, so parent-evidence embedding spend is currently neither budget-gated nor attributed at those two sites — it appears in Prometheus/logs as `tenant_id=unknown` and writes no operational event.
- **Chunk embeddings** — `EvidenceChunk.embedding`, written by `embed_chunks_batch_task` (task `extraction.embed_chunks_batch`, backend/src/contextedge/workers/chunk_tasks.py:238) in groups of `EMBED_BATCH_SIZE` = 32 (chunk_tasks.py:51) via `generate_embeddings_batch` **with tenant context** (chunk_tasks.py:166-171). Chunks land first via `extraction.chunk_evidence` (chunk_tasks.py:210), which hands off embedding so `_normalize`'s critical path stays bounded; a chunk row exists with `embedding = NULL` until the batch task fires, and the `IS NULL` filter makes replays idempotent (chunk_tasks.py:157, 179-181). Both tasks route to the dedicated **embedding** queue (backend/src/contextedge/workers/celery_app.py:259-268) — carved out after a live backfill left 85% of chunks unembedded behind 10,000+ normalizations (the docstring at chunk_tasks.py:215 still says "extraction queue"; the routing table is the authority). After embeddings commit, the task fans out `evaluation.generate_correlation_suggestions` per evidence (chunk_tasks.py:257-263).

Every similarity query against these two surfaces — evidence and chunks — goes through `halfvec_cosine_distance`, matching the `0032` halfvec expression indexes (verified: the only raw `.cosine_distance(...)` call sites left in the backend are two episode-embedding filters, backend/src/contextedge/workers/pattern_tasks.py:243 and :308). See the HNSW design decision below.

### 4. Relevance classification — the cost gate at the front door

`classify_relevance` (backend/src/contextedge/ai/classifiers/relevance.py:32-81) resolves the versioned prompt (`relevance`, default **v2**; v3 is registered but not default — backend/src/contextedge/ai/prompts/relevance.py:76-83, 121-124), slices the body with `salient_slice(body, 2000)` — salience-aware, not head-first, because a fused thread's first 2,000 chars are the newest reply's greetings (relevance.py:55-58) — and calls `llm_complete_json` with `task="classification"`. It returns `{classification, confidence, reasoning, summary, claims}`. It runs inline in the normalize worker *before* embedding so `not_relevant` items skip the expensive fan-out (relevance.py:1-6); the standalone task `extraction.classify_relevance` (backend/src/contextedge/workers/extraction_tasks.py:1357-1384) re-classifies stale verdicts and is routed to the **default** queue so a ~2.5 s gate call never queues behind 20-60 s extraction tasks (celery_app.py:229-233).

### 5. Episode reconstruction — gates first, model second

**Dispatch.** `extraction.correlate_evidence` (queue `correlation`, backend/src/contextedge/workers/correlation_tasks.py:16) runs correlation for one evidence item; if it created correlations, it enqueues `extraction.reconstruct_episode` **with a 180 s countdown** (`RECONSTRUCT_DEBOUNCE_SECONDS`, extraction_tasks.py:746) seeded by that evidence id (correlation_tasks.py:48-52). The task (extraction_tasks.py:1387-1409, queue `correlation` per celery_app.py:257, `max_retries=3`) calls `_reconstruct(db, cluster_id, tenant_id, domain_id, settle=True)`; reviewers with the `domain_admin` role can force a fresh narration via `POST /api/v1/episodes/reconstruct`, which dispatches the same task with `settle=False` (backend/src/contextedge/api/v1/episodes.py:342-353, `settle=False` at 397-402). Known design gap: dispatch is evidence-keyed and should be case-keyed — the min-cluster floor below is a mitigation, not the fix (codewiki/KNOWN_GAPS.md:511-524).

**The gate sequence in `_reconstruct`** (extraction_tasks.py:995-1297), in order — every gate exists to *not* pay for narration that dedup would retire:

1. **Cluster resolution** — `resolve_episode_cluster` materializes the connected component over `case_links` + `correlation_edges` from the seeds (backend/src/contextedge/services/episode_cluster_service.py:108), bounded by `MAX_CLUSTER_SIZE` 50, `MAX_HOPS` 3, and a 30-day window measured to the *nearest* seed (episode_cluster_service.py:47-49, 96-105). Tenant mismatch, legal holds, and pending redactions never enter the cluster — they are filtered in SQL (episode_cluster_service.py:66-93). The cluster fingerprint is a sha256 of the sorted member ids (episode_cluster_service.py:61-63).
2. **Minimum-cluster floor** — clusters below `MIN_AUTO_SYNTHESIS_CLUSTER` = 3 return `skipped_below_min_cluster` (extraction_tasks.py:1016-1031; rationale at 748-756: 58% of one day's drafts were 1-2-evidence fragments retired by dedup). Caveat: a stable pair that never grows gets no retry and no episode (KNOWN_GAPS.md:502-509).
3. **Resolution gate** (only when `episode_resolution_gate == "cluster"`; default `"off"`, config.py:175) — defers clusters with no resolution signal anywhere, fail-open on error (extraction_tasks.py:1033-1057). The signal check is deterministic, no LLM: it reads `case_state == 'resolved'` first (the source system's own verdict; `cancelled` deliberately does not qualify — backend/src/contextedge/services/resolution_signal_service.py:102-117), then a precision-first regex over head *and* tail 4,000 chars of up to 200 items, newest first (resolution_signal_service.py:40-64, 119-145).
4. **Per-cluster advisory lock** — `pg_try_advisory_xact_lock(hashtext('episode_reconstruct:{tenant}:{fingerprint}'))`; losers return `skipped_locked` without spending a call. Exists because 8 concurrent tasks once minted 8 identical episodes in 46 seconds (extraction_tasks.py:1059-1080).
5. **Settlement re-check** — if the newest member was ingested inside the 180 s window the task defers (`deferred_unsettled`), *unless* the oldest member is already 1,800 s old (`MAX_SYNTHESIS_DELAY_SECONDS`, extraction_tasks.py:834) — the starvation guard that guarantees a never-quiet channel still gets its first synthesis within 30 minutes (extraction_tasks.py:1082-1117).
6. **Draft idempotency** — an existing `pending_review` draft with the same fingerprint returns `duplicate_cluster` (extraction_tasks.py:1119-1136).
7. **Growth gate** — `_largest_covered_episode` finds the biggest pending episode whose `evidence_ids` this cluster contains (raw SQL `evidence_ids <@ :ids`; fails **open** because a miss only costs one redundant synthesis — extraction_tasks.py:777-833); unless the cluster is ≥ 1.5× that prior (`MIN_RESYNTHESIS_GROWTH` = 0.5, extraction_tasks.py:774), return `skipped_insufficient_growth` (extraction_tasks.py:1155-1173). Without it, ten messages on a ten-evidence cluster paid ten full ~12,700-token syntheses of which dedup retired nine (extraction_tasks.py:758-773).
8. **Source roles** — each evidence joins to its `Source`; `resolve_synthesis_role` picks the authority label the episode v3 prompt keys on. Precedence: per-source `Source.config["synthesis_role"]` override → evidence-type map (`kb_article`/`sop`/`documentation` → `document`, `alert` → `monitoring`, `ticket` → `ticket`) → source-type map (ServiceNow / Jira SM / SapphireIMS / Zoho Desk → `ticket`, Teams → `working_discussion`, Gmail → `external_communication`, local files → `document`) → fallback `"evidence"` (extraction_tasks.py:852-897, applied at 1183-1203). Items are built as `{title, body, source_type, source_role, timestamp, evidence_id}` sorted by timestamp (extraction_tasks.py:1205-1223).
9. **Supersede-on-growth** — any pending draft whose evidence is a strict subset of this cluster is marked `reviewer_state = "superseded"` with an `episode.draft_superseded` operational event naming both fingerprints (extraction_tasks.py:1232-1269), so reviewers see one evolving draft, not four near-duplicates.
10. Only now: `create_episodes_from_evidence` — the actual model call (extraction_tasks.py:1271-1281).

**The extractor** (`reconstruct_episode`, backend/src/contextedge/ai/extractors/episode_extractor.py:167-211): clusters of ≤ `MAX_ITEMS_PER_CALL` = 20 items go in one LLM call; larger clusters split into chunks of 20 extracted sequentially, episode lists concatenated, logged as `episode_extractor.chunked` (episode_extractor.py:44, 196-211). There is deliberately no cross-chunk synthesis pass — dedup handles overlap downstream (module docstring, episode_extractor.py:7-16). **Open P1:** this chunked path stacks each chunk's steps into one episode, all numbered from #1 (worst case: 319 steps); 836 affected pending drafts carry a data-level `hold / timeline_corrupted_pending_repair` stamp in `ai_review`, which the review sweep skips (KNOWN_GAPS.md:464-478). Do not describe chunked extraction of big clusters as producing correct timelines until that merge is fixed.

Per chunk, `_extract_from_chunk` (episode_extractor.py:97-164):

1. Resolves the prompt — `get_prompt("episode", tenant_id)`, current default **v3** (backend/src/contextedge/ai/prompts/episode.py:252-259).
2. Formats each item as `[ev-N]` with source, role, time, title, and `Content:` truncated by `salient_slice(body, 2000)` (episode_extractor.py:48, 51-74). `salient_slice` (backend/src/contextedge/ai/text_salience.py:135) is deterministic: under-budget text passes byte-identical; over-budget text is segmented, boilerplate-stripped, scored by technical-token density, and re-emitted in original order, with 25% of budget reserved for the head unless the text is a fused thread (text_salience.py:65, 122-132).
3. Wraps the whole evidence block in `fence_untrusted` — a notice plus `<untrusted-evidence>` markers with embedded closing markers neutralized by a zero-width space, so ticket text can never break out and act as instructions (backend/src/contextedge/ai/fencing.py:24-28; applied at episode_extractor.py:110).
4. Calls `llm_complete_json(..., task="extraction", prompt_name=..., prompt_version=...)` (episode_extractor.py:111-119).
5. Translates model-emitted `[ev-N]` labels back to real evidence UUIDs via `_translate_refs`, dropping unknown labels — **the model can never mint evidence** (episode_extractor.py:77-89, 156-158); contradiction accounts get the same treatment (episode_extractor.py:142-152).
6. Runs the **schema gate**: `validate_episode` is strict about structure (a broken episode drops with `episode_draft_invalid`) and lenient about vocabulary (unknown `step_type` coerces to `"observation"`, unknown `result_state` to `"unknown"`, confidences clamp to [0,1], malformed steps and contradictions drop individually) (backend/src/contextedge/ai/extractors/episode_schema.py:46-130). Step vocabularies: `complaint, diagnostic, hypothesis, action, observation, failed_step, remediation, escalation, outcome`; result states: `success, failure, inconclusive, unknown` (episode_schema.py:22-33).
7. Stamps provenance **after** the gate so the model cannot supply its own: `ep["_generation"] = generation_provenance(prompt, task="extraction")` carries `prompt_name`, `prompt_version`, `task`, `model_requested` (the *routed* model — the fallback can substitute mid-call and only `llm.usage` sees it), and `correlation_id` as the join key to the usage events (episode_extractor.py:159-161; backend/src/contextedge/ai/provenance.py:28-51).

**Persistence** (`create_episodes_from_evidence`, backend/src/contextedge/services/episode_service.py:114-333): an LLM failure logs `episode_reconstruction_llm_failed` and returns `[]` — never crashes the task (episode_service.py:130-141); an empty-but-valid result logs `episode_reconstruction_zero_result` separately so ops can tell drift from outage (episode_service.py:146-152). Membership is per episode from the model's validated citations (`membership_source = "model_attribution"`); no valid citations → full-cluster fallback, logged, never silent (episode_service.py:164-182). The episode embedding threads `tenant_id`/`db` so the spend is attributed and gated; on failure the episode persists with `embedding = NULL` (episode_service.py:190-198). A same-occurrence dedup pre-check absorbs the membership into an existing active episode only on lowercase-title match **and** evidence overlap — title alone never merges (episode_service.py:200-259). New rows: `episodes` with `status="draft"`, `reviewer_state="pending_review"`, `cluster_fingerprint`, `evidence_ids`, `entity_refs`, `contradictions`, `generation_provenance` (episode_service.py:261-279; model columns at backend/src/contextedge/models/episode.py:244-261); one `episode_evidence_links` row per grounding evidence carrying *why* it is in the cluster (episode_service.py:283-297); `episode_steps` with order, type, flags, and `evidence_refs` (episode_service.py:299-312). A per-episode `episode.synthesis_quality` log records `steps_ungrounded` / `ungrounded_ratio` — the day-1 proxy for unsupported claims (episode_service.py:318-331).

### 6. Episode AI review — advisory verdicts and gated auto-approval

`settings.episode_ai_review` is `"off"` (default) / `"advisory"` / `"auto_approve"`, regex-enforced (config.py:185-187). Advisory stamps a verdict on `episodes.ai_review`; auto_approve additionally approves drafts that clear the model verdict *and* deterministic floors, with `reviewer_user_id` left NULL — permanently distinguishable from a human approval (config.py:176-184). Auto_approve was blocked until 2026-08-19; the two blocking findings (dispatch-before-commit, and write-ordering against concurrent humans/dedup) are fixed (KNOWN_GAPS.md:480-501).

**The sweep** — `evaluation.ai_review_episodes` (backend/src/contextedge/workers/evaluation_tasks.py:125-358), hourly from beat with `args=("all",)` — scheduled unconditionally, returning `{"status": "disabled"}` instantly while off so enabling the setting needs no beat restart (celery_app.py:379-383; evaluation_tasks.py:171-173). Also dispatchable on demand via `POST /api/v1/episodes/ai-review` (role `knowledge_manager`; episodes.py:556-579, dispatch and audit at 587-604). Per tenant, in order:

1. **Mode resolution, downgrade-only** — a `mode_override` can run advisory under auto_approve, never the reverse (evaluation_tasks.py:174-181).
2. **Bulk-ingest deferral** — `tenant_pipeline_active` defers tenants with > 50 fresh evidence rows *or* > 30 fresh episodes in the last 10 minutes (backend/src/contextedge/workers/pattern_tasks.py:693-742; evaluation_tasks.py:194-203), so the model reads consolidated drafts, not about-to-be-superseded fragments.
3. **Crash-recovery mop-up** — re-dispatches `evaluation.extract_issue_signature` (backend/src/contextedge/workers/signature_tasks.py:20-26) for up to 20 auto-approved episodes missing an issue-signature row — covers process death between commit and broker send (evaluation_tasks.py:205-239).
4. **Draft selection** — `reviewer_state == "pending_review"` AND `ai_review IS NULL` (the sweep never pays twice for a draft), ordered by `review_priority_expression()` — the same SQL priority score the human queue uses: +40 substantive outcome, +20 substantive root cause, +3 per evidence item capped at 10, +10 × extraction confidence (evaluation_tasks.py:241-250; backend/src/contextedge/services/episode_review_service.py:57-86). Optional `shard`/`shards` args hash-partition drafts across concurrent sweeps — a spend optimization, not a correctness one (evaluation_tasks.py:251-268).
5. **Per-episode loop** — `ai_review_episode(...)` then **commit per episode, before any dispatch**: a batch-end commit made every verdict hostage to the last one (one deadlock = 50 LLM calls re-paid) (evaluation_tasks.py:273-291). One bad draft rolls back and continues; five consecutive transient failures (provider down, budget blocked) abort the tenant's batch (evaluation_tasks.py:297-310).
6. **Post-approve dispatches** (auto_approve only, after the commit landed): signature extraction per approval, then one `pattern.cluster_episodes` dispatch **per domain** that had approvals — passing None clustered nothing, because the global mining pass sees only NULL-domain episodes (evaluation_tasks.py:319-351).

**One draft** (`ai_review_episode`, episode_review_service.py:174-308): steps are loaded by explicit query, never the lazy relationship (`MissingGreenlet` under async — episode_review_service.py:187-195); contradictions render as actual claims, not a count (episode_review_service.py:200-212); evidence excerpts are **citation-driven** — budget 10 items × 450 chars prioritizing what the steps cite, then the chronologically last item (the fix confirmation), then the first (the complaint) — replacing a blind head+tail sample after the first sweep held 100/100 drafts with "steps not supported by the provided evidence excerpts", which was structurally true (episode_review_service.py:46-54, 104-171). The LLM call (`review_episode_llm`, backend/src/contextedge/ai/classifiers/episode_review.py:53-98) uses prompt `episode_review` v1, `task="classification"`, and anchors its `llm.usage` event to the episode row via `subject_type`/`subject_id` (episode_review.py:84-85). Parsing **fails closed**: unknown verdict / boolean confidence / non-dict → `hold` at 0.0 (episode_review.py:27-50); provider exceptions become `{verdict: hold, transient_failure: True}` and the caller persists nothing so the draft stays retryable (episode_review.py:87-94). After the ~14 s call, the row is re-read `FOR UPDATE` with `populate_existing=True` (load-bearing — without it the identity map returns stale attributes and the check is vacuous); any state change during the review window — human decision, dedup supersede, twin sweep — wins, and the sweep skips (episode_review_service.py:238-265). **Auto-approve floors**, all deterministic and all required: ≥ 2 evidence items, ≥ 20-char outcome, verdict exactly `"approve"`, confidence ≥ 0.8 (episode_review_service.py:42-44, 89-101). The stamp itself — verdict, confidence, reasons, prompt_version, mode, auto_approved, failed_floors, reviewed_at — is written in both modes (episode_review_service.py:270-276; column at models/episode.py:261). Approval sets `status`/`reviewer_state` to `"approved"` with `reviewer_user_id` NULL and emits `episode.ai_approved`; there is deliberately **no task dispatch inside the function**, which runs in an open transaction (episode_review_service.py:278-300). The human path (`POST /api/v1/episodes/bulk-approve`, episodes.py:282-283) sets the same fields with a real `reviewer_user_id` and follows the same commit-before-dispatch rule.

### 7. Every call is recorded

`record_llm_usage` (backend/src/contextedge/ai/observability.py:133-249) writes Prometheus counters, a structured `llm.usage` log line carrying `prompt_name`/`prompt_version` and the request/correlation/causation ids across HTTP → Celery → LLM, and (when a db session is passed) an `operational_events` row — the cost dashboard's source of truth. Reasoning tokens are a *subset* of completion tokens and get their own counter rather than a `token_type` label (observability.py:51-59, 84-92). Full detail in [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md).

### 8. Versioned prompt registry

`ai/prompts/` registers each prompt as a frozen `Prompt(name, version, system, user_template)` — the system/user split exists so the system block is cacheable (backend/src/contextedge/ai/prompts/__init__.py:39-50). Registration happens at package import time via the submodule list; adding a family = adding a submodule there (prompts/__init__.py:186-201). `resolve_version` precedence: per-tenant override from `settings.tenant_prompt_variants_json` (malformed JSON logs `prompt_variants_config_invalid` and becomes `{}` — bad config can never crash ingest, prompts/__init__.py:88-114) → registered default → alphabetically-last version with a loud warning; unknown prompt *names* raise `KeyError`, fail loud (prompts/__init__.py:124-162). `get_prompt` returns the object whose `.version` callers must thread into `llm_complete*` so `llm.usage` records it (prompts/__init__.py:174-183); `get_prompt_version` pins exact versions for the eval harness (prompts/__init__.py:165-171). Every shipped version is immutable — changes ship as a new version and the default moves (repo convention in CLAUDE.md).

Thirteen prompt families are registered today (defaults verified in code):

| Prompt name | Versions | Default | Registered at |
| --- | --- | --- | --- |
| `episode` | v1-v3 | **v3** (source-authority rules + structured contradictions) | ai/prompts/episode.py:252-259 |
| `episode_review` | v1 | **v1** (verdicts `approve`/`hold`; "default to hold whenever uncertain") | ai/prompts/episode_review.py:53-60 |
| `relevance` | v1-v3 | **v2** (v3 registered, not default) | ai/prompts/relevance.py:76-83 |
| `identity` | v1-v4 | **v3** | ai/prompts/identity.py:231-238 |
| `identity_adjudication` | v1-v2 | **v2** | ai/prompts/identity.py:479-486 |
| `identity_reconciliation` | v1 | **v1** | ai/prompts/identity.py:540-547 |
| `decision` | v1-v2 | **v2** | ai/prompts/decision.py:61-68 |
| `pattern` | v1-v2 | **v2** | ai/prompts/pattern.py:97-104 |
| `playbook` | v1-v6 | **v6** (causal sequencing + minimal step set + plain imperative prose; shipped on the 2026-08-19 prompt A/B) | ai/prompts/playbook.py:415-422 |
| `knowledge_applicability` | v1 | **v1** | ai/prompts/applicability.py:74-81 |
| `contradiction` | v1 | **v1** | ai/prompts/contradiction.py:23-30 |
| `issue_signature` | v1 | **v1** | ai/prompts/issue_signature.py:53-60 |
| `message_function` | v1 | **v1** | ai/prompts/message_function.py:54-61 |

Consumers beyond this page: contradiction scanning compares playbook steps to KB evidence via `llm_complete_json` and may write `contradicts` graph edges (backend/src/contextedge/services/contradiction_service.py:134-136, 486); knowledge applicability skips its ~7,200-token LLM call entirely when the source already states environment/version in `source_facets` (extraction_tasks.py:704-719); issue signatures are minted per approved episode by `evaluation.extract_issue_signature` (signature_tasks.py:20-32).

## Example: Acme VPN data at this stage

**Input (what arrives)** — the fenced, labelled evidence block the episode extractor sends for Acme's VPN incident (ServiceNow INC0010427, the Teams working discussion, the engineer's email — one cluster after ticket-number bridging):

```text
--- Evidence [ev-1] ---
Source: servicenow (ticket)
Time: 2026-08-11T08:14:00Z
Title: INC0010427 — VPN tunnel flapping on vpn-gw-east-01
Content: Users report VPN disconnects since patch KB5032190.
Error in gateway log: AUTH_CERT_EXPIRED. Status: Resolved. ...

--- Evidence [ev-2] ---
Source: teams (working_discussion)
Time: 2026-08-11T09:02:00Z
Content: restarted the VPN service on vpn-gw-east-01 — no change.
cert chain shows the gateway cert invalidated by the new patch...

--- Evidence [ev-3] ---
Source: gmail (external_communication)
Time: 2026-08-11T11:40:00Z
Title: RE: INC0010427 root cause
Content: Root cause: gateway certificate invalidated by patch chain.
Renewed via internal CA; tunnels stable since 10:55. ...
```

**Output (what the system produces)** — one validated episode draft, after `[ev-N]` translation, the schema gate, and the provenance stamp:

```json
{
  "title": "Corporate VPN authentication failure after KB5032190",
  "overall_confidence": 0.87,
  "root_cause_summary": "Gateway certificate on vpn-gw-east-01 invalidated by the KB5032190 patch chain",
  "final_outcome": "Certificate renewed via internal CA; tunnels stable",
  "evidence_refs": ["<uuid ev-1>", "<uuid ev-2>", "<uuid ev-3>"],
  "steps": [
    { "step_order": 1, "step_type": "complaint",   "text": "Users report VPN drops after patch Tuesday", "result_state": "unknown", "evidence_refs": ["<uuid ev-1>"] },
    { "step_order": 2, "step_type": "diagnostic",  "text": "Gateway logs show AUTH_CERT_EXPIRED", "result_state": "unknown", "evidence_refs": ["<uuid ev-2>"] },
    { "step_order": 3, "step_type": "failed_step", "text": "Restarted VPN service — no improvement", "result_state": "failure", "failed_flag": true, "evidence_refs": ["<uuid ev-2>"] },
    { "step_order": 4, "step_type": "remediation", "text": "Renewed gateway certificate via internal CA", "result_state": "success", "successful_flag": true, "evidence_refs": ["<uuid ev-3>"] },
    { "step_order": 5, "step_type": "outcome",     "text": "VPN restored for all affected users", "result_state": "success", "evidence_refs": ["<uuid ev-3>"] }
  ],
  "_generation": { "prompt_name": "episode", "prompt_version": "v3", "task": "extraction", "model_requested": "vertex_ai/gemini-2.5-flash", "correlation_id": "..." }
}
```

Every step cites the evidence that supports it (labels the model emitted, translated to real UUIDs — unknown labels dropped), so reviewers verify the AI's interpretation before promoting the episode. If AI review runs in advisory mode, the draft additionally carries `ai_review: {"verdict": "approve", "confidence": 0.9, ...}` for the human queue.

## Design decisions

- **One funnel, LiteLLM underneath** — *Why:* budget gate, output clamp, circuit breaker, timeout, fallback, and usage recording live in `llm_complete`; a parallel client (vision included — `images` is a parameter, not a separate path, provider.py:190-201) would be the one call type that escaped the spend controls. Providers swap without rewriting call sites. *Tradeoff:* behavior varies subtly per provider; the cache-marker incident (provider.py:152-167) shows the funnel must encode provider quirks itself.

- **Gates before spend on the synthesis path** — *Why:* episode synthesis is the costliest lane (~73% of cold-start spend on the measured backfill, docs/RUNBOOK.md:293; 29% of all tokens on the message corpus with 71% of its output superseded, KNOWN_GAPS.md:39). The min-cluster floor, debounce, growth gate, advisory lock, and optional resolution gate all refuse to buy narration dedup would retire — dedup recovers the graph, never the money (extraction_tasks.py:1150-1154). *Tradeoff:* deferral is not free — stable two-evidence clusters are currently terminally skipped (KNOWN_GAPS.md:502-509), and every gate is another state a debugging engineer must know about.

- **Drop, don't repair, at the episode schema gate** — *Why:* the extractor validates with `validate_episode` (strict structure, lenient vocabulary) instead of the two-call `llm_complete_json_validated` repair loop; a malformed draft must not reach reviewers, and a second LLM call per episode doubles the priciest lane. *Tradeoff:* a structurally broken episode is lost for that run (logged as `episode_draft_invalid`) rather than recovered; the next dispatch re-tells the cluster.

- **Fail-closed AI review with deterministic floors** — *Why:* a wrong hold costs one human review that was going to happen anyway; a wrong approve feeds patterns and playbooks (episode_review.py:1-8). The model proposes; `passes_auto_approve_floors` disposes, and floors are code, not prompt text (episode_review_service.py:89-101). *Tradeoff:* real incidents with terse outcomes (< 20 chars) or single-evidence stories can never auto-approve, by design — they wait for a human.

- **HNSW via halfvec expression indexes (migration `0032`)** — *Why:* pgvector's HNSW on the plain `vector` type caps at 2,000 dimensions and the app stores 3,072 — the earlier `0021`/`0030` indexes **never existed** as usable indexes, so every similarity query was a sequential scan. `0032` (backend/alembic/versions/0032_halfvec_hnsw_indexes.py) builds HNSW expression indexes over `(embedding::halfvec(3072))`, and all cosine ordering must route through `halfvec_cosine_distance` to match them — a raw `column.cosine_distance(...)` is a guaranteed sequential scan (backend/src/contextedge/search/vector_ops.py:1-15, 40-45). Callers raise recall with `SET LOCAL hnsw.ef_search = 200` because the indexes are global while queries post-filter by tenant (vector_ops.py:24-37). *Tradeoff:* half-precision and approximate neighbours (negligible recall cost); requires pgvector ≥ 0.7 — `0032` fails loud below it, but an environment stamped at an older revision of that file silently stays on sequential scans (KNOWN_GAPS.md:40). One caveat the migration's own docstring no longer covers: it claims "all call sites now route through" the helper, and two do not — the episode-similarity filters in pattern clustering still call `Episode.embedding.cosine_distance(...)` directly (pattern_tasks.py:243, :308), so those two scans are sequential. Evidence and chunk search are unaffected.

- **Classify-before-embed** — *Why:* at typical enterprise inbox noise rates, embedding irrelevant items before classifying wasted the embedding plus identity/decision extraction; `classify_relevance` runs inline in `_normalize` before the expensive fan-out (relevance.py:1-6). *Tradeoff:* one LLM call on the normalize critical path; mitigated by routing the standalone re-classify task to the fast `default` lane (celery_app.py:229-233) and by disabling thinking for the relevance prompt (`llm_thinking_budgets = {"relevance": 0}`, config.py:188-190 — ~70% fewer output tokens, verdict unchanged).

- **Chunk embeddings batched, not inline** — *Why:* embedding 50 chunks inline would blow `_normalize`'s p95 and re-fire the budget gate per chunk; the batch task covers 32 chunks per call and the gate runs per sub-batch (chunk_tasks.py:51, 162-171). *Tradeoff:* a window where chunks exist with `embedding IS NULL` — invisible to vector search until the batch lands; the `IS NULL` filter makes retries idempotent, and the dedicated `embedding` queue keeps the window short (celery_app.py:259-268).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| LLM funnel + embeddings | `backend/src/contextedge/ai/provider.py` | `llm_complete` (:177), `llm_complete_json` (:504), `llm_complete_json_validated` (:651), `generate_embedding` (:739), `generate_embeddings_batch` (:814), `MODEL_ROUTING` (:47), `resolve_thinking_budget` (:117) | Every AI call |
| Resilience | `backend/src/contextedge/ai/resilience.py` | `CircuitBreaker`, `LLM_CALL_TIMEOUT_SECONDS` (:28), `breaker` (:95) | Inside each attempt |
| Usage recording | `backend/src/contextedge/ai/observability.py` | `record_llm_usage` (:133), `extract_usage` (:75), `build_messages` | `finally` of every call |
| Prompt registry | `backend/src/contextedge/ai/prompts/__init__.py` | `Prompt` (:39), `register_prompt` (:62), `resolve_version` (:124), `get_prompt` (:174) | Import-time registration; per-call resolution |
| Prompt fencing / salience | `backend/src/contextedge/ai/fencing.py`, `ai/text_salience.py` | `fence_untrusted` (fencing.py:24), `salient_slice` (text_salience.py:135) | Prompt assembly |
| Provenance | `backend/src/contextedge/ai/provenance.py` | `generation_provenance` (:28), `GENERATION_PROVENANCE_KEY` (:25) | After each schema gate |
| Evidence vectors | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence` (:19), `embed_decision` (:38) | Normalize, episode persist |
| Chunk embed pipeline | `backend/src/contextedge/workers/chunk_tasks.py` | `chunk_evidence_task` (:206), `embed_chunks_batch_task` (:234), `EMBED_BATCH_SIZE` (:51) | Celery **embedding** queue |
| ANN expressions | `backend/src/contextedge/search/vector_ops.py` | `halfvec_cosine_distance` (:40), `tune_ann_recall` (:34) | Every similarity query |
| Relevance | `backend/src/contextedge/ai/classifiers/relevance.py` | `classify_relevance` (:32) | Inline in `_normalize`; task on `default` queue |
| Episode extractor | `backend/src/contextedge/ai/extractors/episode_extractor.py` | `reconstruct_episode` (:167), `MAX_ITEMS_PER_CALL` (:44), `_translate_refs` (:77) | From `create_episodes_from_evidence` |
| Episode schema gate | `backend/src/contextedge/ai/extractors/episode_schema.py` | `validate_episode` (:118), `STEP_TYPES` (:22) | Per extracted episode |
| Reconstruction gates | `backend/src/contextedge/workers/extraction_tasks.py` | `_reconstruct` (:995), `RECONSTRUCT_DEBOUNCE_SECONDS` (:746), `MIN_AUTO_SYNTHESIS_CLUSTER` (:756), `MIN_RESYNTHESIS_GROWTH` (:774) | Celery **correlation** queue |
| Cluster resolution | `backend/src/contextedge/services/episode_cluster_service.py` | `resolve_episode_cluster` (:108), `MAX_CLUSTER_SIZE` (:47) | Inside `_reconstruct` |
| Resolution gate | `backend/src/contextedge/services/resolution_signal_service.py` | `cluster_has_resolution_signal`, `SCAN_CHARS` (:63) | When `EPISODE_RESOLUTION_GATE=cluster` |
| Episode persist + dedup | `backend/src/contextedge/services/episode_service.py` | `create_episodes_from_evidence` (:114), `deduplicate_episodes`, `supersede_contained_episodes` | Reconstruction; hourly dedup sweep |
| AI review service | `backend/src/contextedge/services/episode_review_service.py` | `ai_review_episode` (:174), `passes_auto_approve_floors` (:89), `review_priority_expression` (:57) | Hourly sweep / on demand |
| AI review classifier | `backend/src/contextedge/ai/classifiers/episode_review.py` | `review_episode_llm` (:53), `_parse` (:27) | Per reviewed draft |
| Review sweep task | `backend/src/contextedge/workers/evaluation_tasks.py` | `ai_review_episodes` (:125-131) | Beat hourly, **evaluation** queue |
| Tenant budget | `backend/src/contextedge/services/tenant_budget_service.py` | `check_budget` (:234), `TenantBudgetExceeded` (:123) | Pre-call gate |
| Cost aggregation | `backend/src/contextedge/services/admin_cost_service.py` | `get_llm_usage` (:75), `MODEL_COST_USD_PER_M_TOKENS` (:29) | `GET /admin/llm-usage` |

## Acme VPN incident (this layer)

When Acme's duplicate VPN tickets arrive, the relevance classifier marks them **operational** and the fan-out proceeds; correlation and the ticket-number bridge pull INC0010427, the Teams thread, and the engineer's email quoting "INC0010427" into one cluster. The debounce and min-cluster gates hold reconstruction until the cluster settles at three items, the advisory lock elects one worker, and a single episode v3 call — evidence fenced, roles labelled `ticket` / `working_discussion` / `external_communication` — proposes **"Corporate VPN authentication failure after KB5032190"** with five grounded steps, leaving `reviewer_state = "pending_review"`. If `EPISODE_AI_REVIEW=advisory` is on, the hourly sweep stamps `ai_review: approve @ 0.9` onto the draft for Acme's knowledge manager; under `auto_approve` the same draft — three evidence items, a substantive outcome, confidence above 0.8 — would be approved with `reviewer_user_id` NULL and dispatched onward to issue-signature extraction and pattern clustering.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — where embeddings are queried
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — lifecycle around AI outputs
- [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) — budgets, ceilings, and the cost dashboard
- [CHUNKING_DESIGN.md](./CHUNKING_DESIGN.md) — where chunking slots into `_normalize`
- [`docs/SETUP_GUIDE.md`](../docs/SETUP_GUIDE.md) — provider keys and env
