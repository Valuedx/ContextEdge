# Search: hybrid ranking and access

## Summary

You will see how **evidence search** works on three surfaces — PostgreSQL **full-text search (FTS)**, **chunk-aware semantic search** over pgvector, and the **hybrid playbook ranker** behind `POST /runtime/match` — and what each one filters out before it answers. All three exclude evidence under a restricted access policy the caller's role does not cover; the semantic surface also fences legal hold and pending redaction, and the runtime ranker adds a role-based **risk cap** on playbooks. After reading you should know which function runs at each stage, in what order, and where each threshold lives.

## Business picture

Analysts type plain questions — "VPN certificate expired" — and expect back the most relevant tickets, messages, and documents. The platform delivers **ranked, access-controlled results**: not just keyword matches, but conceptually related evidence from across every connected tool, even when the analyst uses different words than the original source did.

Long documents no longer drown out short ones. Search matches at the level of **chunks** — one Teams message, one runbook section, one Jira description — so a 40-message thread is found because of the one message that matters, and the result still comes back as the parent ticket or thread the analyst can open, with the matching passage attached as a snippet.

At the same time, **access policies ensure analysts only see evidence they are authorized to view** — restricted items are silently excluded, never leaked as placeholders or counts. The semantic path adds two more fences on top of that: nothing under legal hold and nothing awaiting redaction can be returned. The keyword (full-text) path filters on access policy only — worth knowing, because the two surfaces are not identical.

The same ranking engine powers **playbook matching** for automation and assistants. When the system needs to recommend a runbook, it first drops every procedure above the caller's authorized risk tier, then weighs keyword relevance, conceptual similarity, graph connectivity to known infrastructure, evidence quality, and how recently the playbook was validated. When nothing scores well enough, it **abstains** and says so, rather than surfacing a low-signal guess. Every result carries a scoring breakdown so a reviewer can see exactly why it ranked where it did.

## Technical walkthrough

### The shared vector plumbing (read this first)

Every semantic path shares three pieces:

1. **One embedding function.** `generate_embedding(text, model=None, *, tenant_id=None, db=None)` produces the 3,072-dimension query vector and hard-fails any model that returns a different dimensionality (backend/src/contextedge/ai/provider.py:739-794). When the caller passes `tenant_id` and `db`, the per-tenant LLM budget gate runs first — a `block` verdict raises `TenantBudgetExceeded` (provider.py:755-772) — and usage is recorded even on error (provider.py:798-811). Which model runs is configuration, not code: `MODEL_ROUTING["embedding"]` reads `settings.default_embedding_model` (provider.py:47-53; config.py:58) and deployments override it by environment variable, so this chapter says "the configured 3,072-dim embedding model" rather than naming one. The dimension count is the only part the code enforces.
2. **One distance expression.** All cosine ordering goes through `halfvec_cosine_distance(column, embedding)`, which casts both sides to `halfvec(3072)` (backend/src/contextedge/search/vector_ops.py:40-45). This matters because pgvector's HNSW (an approximate-nearest-neighbor index) caps the plain `vector` type at 2,000 dimensions while the app stores 3,072 — migration `0032` therefore built HNSW **expression** indexes over `(embedding::halfvec(3072))` on `evidence_items`, `evidence_chunks`, `decisions`, and `episodes` (backend/alembic/versions/0032_halfvec_hnsw_indexes.py:58-61, 102-110). A raw `column.cosine_distance(...)` bypasses those indexes and is a guaranteed sequential scan (vector_ops.py:1-15). History note: migration `0021`'s indexes could never build at all for this reason (codewiki/KNOWN_GAPS.md:275). On pgvector below 0.7 the migration does **not** fail quietly — it refuses to run and says why (0032_halfvec_hnsw_indexes.py:90-95). The quiet failure mode is different: an environment already stamped at an earlier revision of that file never re-executes it, so it keeps sequential-scanning with no error anywhere (KNOWN_GAPS.md:40).
3. **One recall knob.** Before any tenant-filtered ANN query, callers run `await tune_ann_recall(db)`, which executes `SET LOCAL hnsw.ef_search = 200` (`ANN_EF_SEARCH = 200`, vector_ops.py:31-37). The 0032 indexes are global across tenants while every query post-filters by `tenant_id`; at the default `ef_search = 40` a small tenant's rows can be entirely absent from the candidate set (vector_ops.py:24-31).

### Evidence list/search (`GET /api/v1/evidence`)

- `search_evidence` in the API layer first resolves the caller's excluded access policies, then routes: a non-empty `query` goes to FTS, otherwise it is a filtered list ordered by `created_at_source DESC NULLS LAST, ingested_at DESC` (backend/src/contextedge/api/v1/evidence.py:29-95).
- `search_evidence_fts` builds `plainto_tsquery('english', query)` against `EvidenceItem.search_tsvector` and orders by `ts_rank` DESC (backend/src/contextedge/search/pg_fts.py:29-30, 64-72). Two fallbacks are OR-ed into the same statement: a **ticket-number** match against `raw_evidence_objects.raw_payload` (`ticket_number` / `ticketNumber` / `number`) and `external_id` — so a reviewer can find INC0010427 by its number without knowing its title (pg_fts.py:50-59) — and a `title ILIKE %query%` partial match (pg_fts.py:62). By default `evidence_type != 'thread_message'` so hydrated thread replies stay under their parent's thread view unless explicitly requested (pg_fts.py:35-38).
- Access exclusion is applied in SQL: rows whose `access_policy_id` is in the caller's excluded set never leave the database (pg_fts.py:73-79; evidence.py:62-68 for the list path).
- `get_evidence` returns 404 for a policy-excluded item — indistinguishable from "does not exist" (evidence.py:98-111).

### Who is excluded from what

`resolve_excluded_access_policy_ids(db, tenant_id, caller_roles)` returns `None` (no exclusions) when the caller holds any of `ADMIN_ROLES = {platform_super_admin, tenant_admin, domain_admin}`; otherwise it collects active `tenant_policies` rows with `policy_type = "access"` whose `config.restricted` is `True` (backend/src/contextedge/search/access_control.py:12-39).

Semantic search additionally applies `_visibility_predicates`: no `sensitivity_label = 'legal_hold'`, no `redaction_status` in `('pending', 'pending_redaction')`, and no excluded `access_policy_id` — enforced on **both** the chunk pass and the parent pass (backend/src/contextedge/search/vector_search.py:49-70).

The FTS path and the plain list path carry only the access-policy exclusion; neither adds the legal-hold or redaction predicates (pg_fts.py:73-79; evidence.py:62-68). So the same item can be hidden from semantic results and still reachable by keyword. Treat that as a real difference between the two surfaces, not a rounding error.

### Chunk-aware semantic search

`search_evidence_semantic(db, tenant_id, query_text, limit=20, *, query_embedding=None, exclude_policy_ids=None)` returns 3-tuples `(EvidenceItem, distance, best_chunk | None)`, closest first (backend/src/contextedge/search/vector_search.py:204-243). This shipped 2026-08-01 (codewiki/KNOWN_GAPS.md:115); the earlier parent-embedding-only behavior is gone. Exact order of operations:

1. **Embed** the query, unless the caller passed a precomputed embedding (vector_search.py:218). That internal call passes no `tenant_id`/`db`, so on its own it is neither budget-gated nor attributed to a tenant. The hybrid ranker sidesteps that by pre-computing one attributed, budget-gated embedding and passing it down, which also stops per-candidate searches re-spending on the same text (hybrid_ranker.py:271-281, 306).
2. **`tune_ann_recall(db)`** (vector_search.py:220).
3. **Chunk pass** — `_chunk_candidates` runs one ANN query over `evidence_chunks` joined to `evidence_items`, ordered by halfvec cosine distance, with `LIMIT _oversample_for(limit)` where oversample = `min(max(80, limit*3), 240)` (`CHUNK_OVERSAMPLE_MIN`/`MAX`, vector_search.py:40-46; the function is vector_search.py:73-158). It selects the chunk id, evidence id, distance, the raw embedding (needed for the diversity step), `parent_section`, `chunk_kind`, and a 240-char snippet (`SNIPPET_CHARS`, vector_search.py:42, 84-93). Visibility predicates apply here (vector_search.py:95-100).
4. **MMR diversification** — `mmr_order(candidates, select_n=max(limit*4, limit))` runs greedy maximal-marginal-relevance: score = `λ·relevance − (1−λ)·max_similarity_to_already_selected`, with `MMR_LAMBDA = 0.7` and relevance = `1 − clamp(distance, 0, 2)/2` (backend/src/contextedge/search/chunk_rollup.py:31, 44-47, 79-108). This stops five near-identical messages from one thread crowding out four distinct threads. MMR decides **which** candidates survive; the rollup's re-sort by distance decides final rank (vector_search.py:224-228).
5. **Rollup** — `rollup_best_chunk_per_evidence` keeps one candidate per `evidence_id` (its closest chunk), sorted `(distance, chunk_id)` for determinism, then truncated to `limit` (chunk_rollup.py:111-121; vector_search.py:228).
6. **Parent-pass merge** — `_merge_with_parent_pass` fetches the rolled chunks' parent `EvidenceItem` rows, then runs a second ANN query over `evidence_items.embedding` (same distance space, same visibility predicates, `LIMIT limit`), appends parents not already seen, sorts everything by distance, and truncates (vector_search.py:161-201, 230-243). This is why evidence with no chunks — pre-chunking rows, chunker failures — still surfaces (vector_search.py:4-8).

**Degradation:** a missing or malformed chunk embedding makes the MMR similarity matrix unbuildable and MMR falls back to pure distance ordering — a corrupt chunk never fails the request (chunk_rollup.py:59-76, 90-92).

**Callers:** there is no direct HTTP endpoint for unscoped semantic evidence search. In production code it has exactly one direct caller — knowledge retrieval for playbook generation (backend/src/contextedge/services/knowledge_retrieval_service.py:299-307) — plus the hybrid ranker via the playbook-scoped variant below. The agent graph's seed resolver does not call it; it runs its own ANN queries against the same halfvec indexes (backend/src/contextedge/graph/agent/repository.py:299-428). No codewiki chapter covers that projection yet; the workplan behind it is [INCIDENT_DIAGNOSIS_ROADMAP.md](./INCIDENT_DIAGNOSIS_ROADMAP.md).

**Playbook-scoped variant:** `search_evidence_semantic_for_playbook(db, tenant_id, playbook_id, playbook_version_id, ...)` is the same shape, but both passes INNER-join through `playbook_evidence_links` → `playbook_versions` restricted to one **published** version (vector_search.py:246-297). If the chunk pass returns nothing, it distinguishes "no chunk matched" from "this version has no provenance rows at all" and logs `search.playbook_scope_has_no_evidence_links` in the second case (vector_search.py:120-145).

### Playbook hybrid ranking (`rank_playbooks`)

`rank_playbooks(db, tenant_id, query_text, entities, top_k=5, ..., domain_id, max_risk_tier, allowed_domain_ids, caller_roles, min_score)` is the one scorer behind runtime matching and the eval harness (backend/src/contextedge/search/hybrid_ranker.py:213-379). Current weights (`RankingWeights`, hybrid_ranker.py:22-31):

| signal | weight |
| --- | --- |
| keyword | 0.25 |
| semantic | 0.30 |
| graph_distance | 0.15 |
| evidence_quality | 0.10 |
| identity | 0.05 |
| recency | 0.10 |
| freshness | 0.05 |
| negative_penalty | 0.05 (subtracted) |

Note `recency_score = freshness` (hybrid_ranker.py:334), so freshness effectively carries 0.15 today.

Mechanism, in order:

1. Resolve the caller's excluded access policies (hybrid_ranker.py:235).
2. Load all `lifecycle_state='approved'` playbooks for the tenant, then filter in Python: keep domainless or `domain_id`-matching (244-249), keep only the service token's `allowed_domain_ids` when set (250-254), and drop tiers above `max_risk_tier` via `risk_within_cap` — rank order `minimal(0) < low(1) < medium(2) < high(3) < critical(4)`, unknown tiers default to `medium` (hybrid_ranker.py:255-258; backend/src/contextedge/search/risk_policy.py:3-20).
3. Resolve the query's entity terms to canonical identity UUIDs (hybrid_ranker.py:262).
4. **Keyword pass:** `search_playbooks_fts(limit=50)` over `playbooks.search_tsvector` (approved only), each rank normalized by the max rank into [0, 1] (hybrid_ranker.py:264-269; pg_fts.py:84-105).
5. One **attributed, budget-gated** query embedding — `generate_embedding(query_text, tenant_id=tenant_id, db=db)`; any exception zeroes the semantic signal rather than failing the request (hybrid_ranker.py:271-281).
6. One batched query loads every candidate's newest **published** version (`_latest_published_versions`, hybrid_ranker.py:183-210); a playbook with no published version is skipped entirely (289-291).
7. Per candidate:
   - **Semantic:** `search_evidence_semantic_for_playbook(limit=10, query_embedding=...)` over that version's linked evidence (hybrid_ranker.py:297-311). `_semantic_corpus_score` maps the best distance to `max(0, 1 − best/2)` (45-54), and the final semantic score is gated by keyword: `min(1.0, sem × (0.6 + 0.4·keyword))` (330) — pure vector similarity with zero lexical support gets discounted.
   - **Graph:** `_graph_score_for_playbook` = `min(1, edges_touching_playbook/5) × 0.7 + min(1, correlation_edges_between_playbook_evidence_and_this_query's_semantic_hits/3) × 0.3`, domain-scoped when `domain_id` is set (57-112).
   - **Identity:** distinct `references_identity` edges from the playbook to the query's resolved identities, divided by the identity count, capped at 1 (115-137).
   - **Negative penalty:** `min(1, contradicts_edges × 0.3 + negative_knowledge_items_in_domain × 0.1)` (140-163).
   - **Quality:** `clamp(0.6 × published_version_confidence + 0.4 × min(evidence_hits/5, 1))` — reviewed confidence plus query-specific evidence support (174-180).
   - **Freshness** (`_compute_freshness`): 0.0 if `expiry_at` has passed; else `max(0, 1 − days_since_last_validated/180)`; 0.5 when never validated (382-389). Label: `fresh > 0.7`, `aging > 0.3`, else `stale` (347).
   - Total = weighted sum minus penalty; the full `breakdown` dict rides on each `RankedPlaybook` (336-366).
8. **Abstention:** sort descending, drop everything below `MIN_RECOMMENDATION_SCORE = 0.35` (or the caller's `min_score`). When candidates existed but all fell below, log `ranking.abstained` — an empty list means "no recommendation", by contract (hybrid_ranker.py:168-171, 368-379).

### `POST /api/v1/runtime/match` lifecycle

(backend/src/contextedge/api/v1/runtime.py:89-246)

1. Foreign `domain_id` → 400; a service token asking for a domain outside its allowlist → 403 (runtime.py:76-86, 97-105).
2. `build_runtime_memory_context` assembles short-term (session + last 5 trace events + recent evidence), long-term (identities, playbook/pattern counts), and reasoning (recent runs and decisions) memory plus the composite `query_text` (backend/src/contextedge/services/memory_service.py:82-288; called at runtime.py:107-117).
3. Risk cap by role — `_effective_max_risk_tier`: admins uncapped, `knowledge_manager` and service accounts capped at `high`, everyone else at `medium` (runtime.py:42-52).
4. `rank_playbooks(...)` with all caps threaded through (runtime.py:130-140).
5. `fallback_guidance` is set when there are no results or top confidence < 0.3 (runtime.py:162-164).
6. With a `session_id`: `append_trace_event(event_type="retrieve", ...)` writes a `decision_trace_events` row plus an operational event; a missing session → 404 (runtime.py:166-194; backend/src/contextedge/services/session_service.py:139-181).
7. Operational event `runtime.match_completed` with the full filters and top result (runtime.py:196-215).
8. The full explain payload is cached in Redis under `runtime:match:{match_id}` with TTL `MATCH_CACHE_TTL_SEC = 3600`; cache write failures are swallowed (runtime.py:29, 230-238). `GET /runtime/explain/{match_id}` serves it back — 403 on tenant mismatch, 404 after expiry (runtime.py:249-267).
9. Feedback loop: `POST /runtime/feedback` writes `retrieval_feedback` rows and `GET /runtime/feedback` lists them (runtime.py:352-391). `GET /runtime/playbooks/{stable_key}` fetches a published version under the same risk and domain gates (runtime.py:270-349).

## Example: Acme VPN data at this stage

**Input** (what arrives) — an analyst searches for VPN evidence:

```
GET /api/v1/evidence?query=VPN+certificate+expired
Authorization: Bearer <analyst-token>
```

**Output** (what the system produces) — a ranked, security-trimmed evidence list:

```json
[
  {
    "id": "ev-a1b2c3",
    "title": "INC0010427: VPN users unable to connect",
    "body_summary": "Multiple users report VPN disconnects. AUTH_CERT_EXPIRED on vpn-gw-east-01.",
    "evidence_type": "servicenow_incident",
    "source_type": "servicenow",
    "relevance_state": "operational"
  },
  {
    "id": "ev-d4e5f6",
    "title": "Teams thread: VPN outage troubleshooting",
    "body_summary": "Engineers confirmed AUTH_CERT_EXPIRED on vpn-gw-east-01; discussed certificate renewal.",
    "evidence_type": "message",
    "source_type": "teams",
    "relevance_state": "operational"
  }
]
```

Evidence items under a restricted access policy the analyst's role does not cover are absent entirely — not even placeholders. Searching the bare string "INC0010427" also works, via the raw-payload ticket-number fallback.

**Input** — a runtime match request during the incident:

```json
POST /api/v1/runtime/match
{
  "symptoms": ["VPN authentication failure", "users cannot connect"],
  "entities": ["vpn-gw-east-01"],
  "context": "AUTH_CERT_EXPIRED on the east gateway",
  "session_id": "sess-acme-vpn-01"
}
```

**Output** — hybrid-ranked playbook matches with the real breakdown keys:

```json
{
  "match_id": "9f2c...",
  "results": [
    {
      "playbook_title": "VPN Gateway Certificate Renewal",
      "stable_key": "pb-3fa8c21b04d9",
      "match_score": 0.71,
      "playbook_confidence": 0.85,
      "freshness_status": "fresh",
      "risk_tier": "medium",
      "scoring_breakdown": {
        "keyword": 0.82, "semantic": 0.77, "graph": 0.54,
        "quality": 0.79, "identity": 1.0, "recency": 0.9,
        "freshness": 0.9, "negative_penalty": 0.0
      }
    }
  ],
  "fallback_guidance": null
}
```

The breakdown lets a reviewer see, for instance, that the identity signal fired because the playbook carries a `references_identity` edge to `vpn-gw-east-01`. Had every candidate scored below 0.35, `results` would be empty and `fallback_guidance` set — an explicit abstention, not a silent miss.

## Design decisions

- **halfvec expression indexes instead of plain vector HNSW** — *Why:* pgvector caps HNSW on the `vector` type at 2,000 dims and the app stores 3,072; casting to `halfvec(3072)` in an expression index is the standard pattern, at negligible recall cost (vector_ops.py:1-15). *Tradeoff:* every query must order by the exact same expression, so all distance math is routed through one helper — a stray `cosine_distance` call silently reverts to sequential scans.
- **Chunk-level retrieval with MMR + parent rollup** — *Why:* chunk ANN gives recall (the one message that matters in a 40-message thread), MMR stops near-duplicates crowding the set, and the rollup returns one openable parent per hit with the matching snippet attached (chunk_rollup.py:1-19). *Tradeoff:* an oversampled chunk query plus a parent merge is two ANN passes per search; the oversample ceiling (240) bounds the embedding transfer and the similarity-matrix cost.
- **`ef_search = 200` per transaction** — *Why:* the indexes are tenant-global and every query post-filters by tenant; the default candidate set can miss a small tenant entirely (vector_ops.py:24-31). *Tradeoff:* a little latency on every ANN query; the complete fix (pgvector 0.8 iterative scan) is deferred because setting an unknown GUC aborts the transaction on 0.7.
- **Policy exclusion at the SQL layer** — *Why:* defense in depth versus filtering in Python only; a row that never leaves the database cannot leak through a serializer (pg_fts.py:73-79; vector_search.py:49-70). *Tradeoff:* every search surface must thread `exclude_policy_ids`, and semantic search additionally carries the legal-hold and redaction predicates on both passes.
- **Keyword-gated semantic score and abstention** — *Why:* pure vector similarity with zero lexical support is often vocabulary noise, and below 0.35 a recommendation is noise, full stop (hybrid_ranker.py:330, 168-171). *Tradeoff:* a genuinely paraphrased query with no shared vocabulary is discounted; callers must treat an empty list as "no recommendation", not an error.
- **Role-based risk caps at runtime, separate from tenant policy config** — *Why:* the cap depends on who is asking (`medium` for regular users, `high` for knowledge managers and service tokens, uncapped for admins), applied identically at match and at playbook retrieval (runtime.py:42-52, 308-313). *Tradeoff:* two places to keep in sync; a future policy engine may unify them.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evidence HTTP | `backend/src/contextedge/api/v1/evidence.py` | `search_evidence` (:29), `get_evidence` (:98) | API |
| Evidence FTS | `backend/src/contextedge/search/pg_fts.py` | `search_evidence_fts` (:12), `search_playbooks_fts` (:84) | Query time |
| Semantic search | `backend/src/contextedge/search/vector_search.py` | `search_evidence_semantic` (:204), `search_evidence_semantic_for_playbook` (:246), `_visibility_predicates` (:49) | Query / rank |
| Chunk MMR + rollup | `backend/src/contextedge/search/chunk_rollup.py` | `mmr_order` (:79), `rollup_best_chunk_per_evidence` (:111), `MMR_LAMBDA` (:31) | Inside semantic search |
| ANN plumbing | `backend/src/contextedge/search/vector_ops.py` | `halfvec_cosine_distance` (:40), `tune_ann_recall` (:34), `ANN_EF_SEARCH` (:31) | Every vector query |
| Hybrid ranker | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks` (:213), `RankingWeights` (:22), `MIN_RECOMMENDATION_SCORE` (:171) | Runtime / evaluation |
| Access policies | `backend/src/contextedge/search/access_control.py` | `resolve_excluded_access_policy_ids` (:15), `ADMIN_ROLES` (:12) | Retrieval |
| Risk ordering | `backend/src/contextedge/search/risk_policy.py` | `risk_within_cap` (:16), `playbook_risk_rank` (:12) | Runtime filter |
| Runtime API | `backend/src/contextedge/api/v1/runtime.py` | `runtime_match` (:89), `runtime_explain` (:249), `_effective_max_risk_tier` (:42) | HTTP |
| Query embedding | `backend/src/contextedge/ai/provider.py` | `generate_embedding` (:739) | Every semantic query |

## Acme VPN incident (this layer)

A knowledge manager searches "VPN gateway": FTS ranks Acme's normalized ServiceNow incident and the Teams thread; an analyst without clearance for a restricted policy simply sees fewer rows. A semantic search for "cert renewal after patch" finds the same incident even though those words never appear in its title, because a chunk of the engineer's root-cause email matched — and the result is the email item with that passage as its `best_chunk` snippet. When an assistant calls `/runtime/match` for the session, `rank_playbooks` scores the certificate-renewal playbook on all seven positive signals, subtracts the negative penalty, records a `retrieve` trace event on the session, and caches the explain payload for an hour — with playbooks above the caller's risk cap already dropped before any scoring ran.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where embeddings and chunks are written
- [CHUNKING_DESIGN.md](./CHUNKING_DESIGN.md) §6 — the search-integration design this implements
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — the graph edges behind the graph and identity signals
- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — runtime in context
- [`docs/API.md`](../docs/API.md) — evidence and runtime routes
