# AI extraction and embeddings

## Summary

You will understand how ContextEdge calls **language models** through **LiteLLM**, how **embeddings** power semantic search, and how **classification** and **JSON extraction** tasks (episodes, patterns, contradictions) are structured—without treating the AI layer as a black box unrelated to governance.

## Business picture

AI reads your tickets and messages faster than any human—but everything it proposes goes through human review before it becomes trusted knowledge. The platform uses AI to **summarize** incoming evidence, **sort it by relevance**, **group similar items together**, and **reconstruct the story** of what happened (symptoms, steps tried, root cause, outcome). Lighter models handle quick triage; more capable models reconstruct full incident narratives. Every AI output starts as a draft: reviewers see exactly what the AI suggested, can correct it, and only then promote it into the organization's knowledge base. Tenant isolation and governance controls stay in place at every step—AI suggests, humans approve.

## Technical walkthrough

1. **Provider abstraction** — `ai/provider.py` configures LiteLLM (API keys, Vertex credentials, retries). `MODEL_ROUTING` picks defaults per **task** key: `classification`, `extraction`, `embedding`. `get_model_for_task` resolves the model id.

2. **Completion** — `llm_complete` sends a chat-style `acompletion`; `llm_complete_json` requests `response_format` JSON and parses with error logging on failure.

3. **Embeddings** — `generate_embedding` calls `litellm.aembedding` with fixed **3072** dimensions (aligned with Vertex / model expectations). Used by `embed_evidence` in `ai/embeddings.py` (title + body, truncated body) and by search (`vector_search`, `hybrid_ranker` query embedding). `embed_evidence_batch` fills sparse texts with zero vectors to keep array shapes consistent.

4. **Relevance classification** — `ai/classifiers/relevance.py` `classify_relevance` builds a short prompt and returns `classification`, `confidence`, `reasoning` via `llm_complete_json`. The Celery task `classify_relevance_task` in `extraction_tasks.py` invokes this to move evidence out of `unclassified`.

5. **Episode reconstruction** — `ai/extractors/episode_extractor.py` `reconstruct_episode` sends ordered evidence text through a large structured prompt; output is a list of episodes with steps and confidence. `episode_service.create_episodes_from_evidence` persists `Episode` and `EpisodeStep` rows. **Large clusters are chunked**: clusters of more than `MAX_ITEMS_PER_CALL` (default 20) evidence items are split into per-chunk LLM calls and the resulting episode lists concatenated. Each evidence body is also truncated to `PER_ITEM_CHAR_LIMIT` (default 2000 chars) before prompt assembly, so a single pathological item can't blow the per-call token budget. Cross-chunk episode dedup is deferred to downstream pattern-mining and correlation services. `chunk_count` is emitted via the `episode_extractor.chunked` structured log for observability.

6. **Other extractors** — `ai/extractors/pattern_extractor.py` and `identity_extractor.py` (pattern and identity flows) follow the same "prompt + JSON" style where used by services.

7. **Contradiction scanning** — `contradiction_service.py` uses `llm_complete_json` to compare playbook steps to KB-style evidence and may add graph edges via `add_contradicts_edge`.

8. **Configuration** — Model names and keys come from `config.py` / environment (documented in setup and runbook); changing models is an ops concern, not a code change.

9. **Versioned prompt registry** — `ai/prompts/` registers each prompt as a `Prompt(name, version, system, user_template)` object. Callers resolve via `get_prompt("relevance", tenant_id)`, which returns the tenant's configured variant (from `settings.tenant_prompt_variants_json`) or the registered default. `prompt_name` + `prompt_version` are threaded through `llm_complete` / `llm_complete_json` into the `llm.usage` structlog line and operational event, so the admin cost dashboard can break down cost + quality per version. The relevance classifier is the first migrated family (`v1` default); migrate others by adding a submodule that calls `register_prompt`. Treat versions as immutable: edit = ship a new version, never mutate a released one, so historical events stay accurate.

10. **Schema-validated retry** — `llm_complete_json_validated(prompt, schema, ...)` takes a Pydantic model, validates the parsed JSON against it, and on failure sends exactly one repair call that includes the raw invalid response + the Pydantic validation errors + the JSON Schema of the target model. Retry budget is hard-capped at 1 (two LLM calls per extraction is already a real cost line). Raises `ValueError` naming the schema when the retry also fails — cheaper for callers to catch than chasing `ValidationError` up the stack.

## Example: Acme VPN data at this stage

**Input — evidence text sent to the relevance classifier**

```json
{
  "evidence_id": "ev-a1b2c3",
  "title": "VPN connection drops after Windows update KB5032190",
  "body": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED."
}
```

**Output — relevance classification**

```json
{
  "classification": "operational",
  "confidence": 0.94,
  "reasoning": "Describes a specific infrastructure issue with affected systems, error codes, and user impact."
}
```

**Input — evidence batch sent to the episode reconstructor**

Multiple evidence items from the same incident are assembled in time order and sent to a language model:

```json
{
  "evidence_items": [
    { "evidence_id": "ev-a1b2c3", "source": "jira", "text": "VPN connection drops after Windows update KB5032190..." },
    { "evidence_id": "ev-d4e5f6", "source": "teams", "text": "Thread: engineers discuss AUTH_CERT_EXPIRED on vpn-gw-east-01..." },
    { "evidence_id": "ev-g7h8i9", "source": "email", "text": "Root cause: gateway certificate invalidated by new patch chain..." }
  ]
}
```

**Output — proposed episode (draft, pending human review)**

```json
{
  "title": "Corporate VPN authentication failure after KB5032190",
  "confidence": 0.87,
  "steps": [
    { "order": 1, "type": "complaint", "text": "Users report VPN drops post-patch Tuesday", "evidence_ref": "ev-a1b2c3" },
    { "order": 2, "type": "diagnostic", "text": "Checked gateway logs — AUTH_CERT_EXPIRED errors", "evidence_ref": "ev-d4e5f6" },
    { "order": 3, "type": "failed_attempt", "text": "Restarted VPN service — no improvement", "evidence_ref": "ev-d4e5f6" },
    { "order": 4, "type": "remediation", "text": "Renewed gateway certificate via internal CA", "evidence_ref": "ev-g7h8i9" },
    { "order": 5, "type": "outcome", "text": "VPN restored for all affected users", "evidence_ref": "ev-g7h8i9" }
  ]
}
```

Every proposed step links back to the evidence that supports it, so reviewers can verify the AI's interpretation before promoting the episode.

## Design decisions

- **LiteLLM as a single integration point** — *Why:* swap OpenAI, Anthropic, Vertex without rewriting call sites. *Tradeoff:* behavior varies slightly by provider; testing must cover the configured backend.

- **Fixed embedding dimension** — *Why:* consistent pgvector column shape and distance math. *Tradeoff:* changing embedding models may require re-embedding and migration.

- **Task-based model routing** — *Why:* use a smaller/faster model for classification, a stronger one for extraction. *Tradeoff:* misconfiguration affects quality more than code bugs.

- **JSON-mode extraction** — *Why:* structured downstream ORM mapping. *Tradeoff:* models occasionally violate schema; code must validate and log (`llm_json_parse_failed`).

- **Prompt caching** (since 2026-04-22) — *Why:* classification and extraction prompts have large, stable system blocks (instructions + schema) and small, dynamic user blocks (the evidence). Splitting them lets OpenAI's automatic prefix cache and Anthropic's `cache_control: {"type": "ephemeral"}` both hit, cutting cached-prompt tokens to 10–25% of normal pricing. `ai/provider.py::llm_complete` takes `system_prompt` as a kwarg and builds messages via `ai/observability.build_messages` which emits the content-block shape LiteLLM routes to Anthropic's native caching. Classifier's `SYSTEM_PROMPT` is now a module constant — stable across all calls. *Tradeoff:* requires callers to split their prompts; legacy call sites that pass only `prompt` still work but lose the cache benefit.

- **Classify-before-embed** (since 2026-04-22) — *Why:* at typical enterprise IT inbox noise rates (~60–70% non-operational), embedding irrelevant items before classifying them wasted both the embedding and downstream identity / decision extraction cost. `_normalize` now runs `classify_relevance` inline immediately after thread + attachment setup. Items scoring `not_relevant` with confidence ≥ 0.75 skip the expensive fan-out. *Tradeoff:* normalize is slightly slower per item (one LLM call added to critical path), but the aggregate LLM cost per ingested batch drops by ~65% on noisy tenants.

- **Per-call token + cost instrumentation** (since 2026-04-22) — *Why:* LLM spend is the single largest variable cost; without per-tenant observability the first production incident will be a cost incident, not an availability incident. `ai/observability.record_llm_usage` is called from every `llm_complete` / `generate_embedding` code path, emitting Prometheus counters + structured logs + `OperationalEvent(event_type="llm.usage")`. *Tradeoff:* one extra DB write per LLM call when a db session is provided; the write is best-effort and swallows errors so observability failure never breaks the actual work. See the `/admin/cost` dashboard and `GET /api/v1/admin/llm-usage` for the aggregated view.

- **HNSW indexes on embedding columns** (since 2026-04-22, migration `0021_hnsw_vector_indexes`) — *Why:* unindexed cosine-distance queries scan the full 3072-dim column linearly; at several million rows this dominates runtime similarity queries. HNSW gives ~95% recall at roughly 100× the throughput. *Tradeoff:* approximate rather than exact neighbours; index maintenance cost on inserts (bounded — pgvector's HNSW implementation is designed for this). Runtime recall is tunable per session via `SET LOCAL hnsw.ef_search = <n>`.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| LLM + embeddings | `backend/src/contextedge/ai/provider.py` | `llm_complete`, `llm_complete_json`, `llm_complete_json_validated`, `generate_embedding`, `get_model_for_task` | Many services |
| Observability | `backend/src/contextedge/ai/observability.py` | `record_llm_usage`, `build_messages`, `extract_usage`, `LLM_TOKENS_TOTAL` | Every LLM + embedding call |
| Prompt registry | `backend/src/contextedge/ai/prompts/__init__.py`, submodules per family | `Prompt`, `register_prompt`, `get_prompt`, `resolve_version` | Import-time registration; per-call resolution |
| Evidence vectors | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence`, `embed_evidence_batch` | Normalize, batch jobs |
| Relevance | `backend/src/contextedge/ai/classifiers/relevance.py` | `classify_relevance` (resolves versioned prompt per call) | Inline in `_normalize` before embed + identity/decision extraction |
| Episodes | `backend/src/contextedge/ai/extractors/episode_extractor.py` | `reconstruct_episode`, `MAX_ITEMS_PER_CALL`, `PER_ITEM_CHAR_LIMIT` | Episode creation |
| Episode persist | `backend/src/contextedge/services/episode_service.py` | `create_episodes_from_evidence` | API / tasks |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | `scan_contradictions` (HNSW top-K + cursor + budget) | Evaluation tasks |
| Celery hooks | `backend/src/contextedge/workers/extraction_tasks.py` | `_normalize` (redacts → classifies → embeds), `classify_relevance_task`, `reconstruct_episode_task` | Queues |
| Tenant budget | `backend/src/contextedge/services/tenant_budget_service.py` | `check_budget`, `TenantBudgetExceeded`, `get_current_day_usage` | Pre-call gate in `llm_complete` |
| Admin cost aggregation | `backend/src/contextedge/services/admin_cost_service.py` | `get_llm_usage`, `MODEL_COST_USD_PER_M_TOKENS` | `GET /admin/llm-usage` |

## Acme VPN incident (this layer)

The classifier marks VPN tickets as **operational**; embedding groups them near past "certificate expiry" evidence; `reconstruct_episode` proposes one episode titled **Corporate VPN authentication failure** with ordered steps from Jira + Teams + mail, leaving `reviewer_state` pending human approval.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — where embeddings are queried  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — lifecycle around AI outputs  
- [`docs/SETUP_GUIDE.md`](../docs/SETUP_GUIDE.md) — provider keys and env  
