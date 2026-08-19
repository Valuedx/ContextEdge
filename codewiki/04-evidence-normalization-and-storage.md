# Evidence normalization and storage

## Summary

You will learn how **raw** connector payloads become **evidence items** queryable in the product, when payloads move to **object storage**, how **deduplication** works, which classification and extraction steps run inline inside the normalize worker (and in what order), how the body is broken into **chunks** for high-recall retrieval, and what fans out after the transaction commits.

## Business picture

Raw data flowing in from connected tools — ticketing systems, chat platforms, email — arrives in inconsistent formats, with duplicates, boilerplate, and oversized payloads. If exposed directly, analysts would waste time sifting through noise instead of resolving incidents.

This stage solves that problem. It transforms every raw payload into a **clean, searchable evidence record** with a consistent title, body, source link, and content fingerprint. Duplicates are automatically collapsed: if the same VPN outage ticket is re-synced after being resolved, analysts see **one** trustworthy result whose status was refreshed, not two records. Obvious chatter ("Any update?") is filtered out before it costs anything. Sensitive strings (emails, tokens, keys) are redacted before any model or database sees them. Large payloads move to cheaper blob storage so the main database stays fast and backups stay small. Finally, each record is scored for relevance, linked to the people and systems it mentions, embedded for semantic search, and split into chunks so a single useful paragraph inside a long thread can still be found.

The outcome: whatever tool the data came from, analysts get one up-to-date, duplicate-free record per upstream object. Two filters deliberately hold things back from search — the noise gate never promotes coordination chatter to an evidence row at all, and confidently-irrelevant items keep their row for audit but get no embedding and no chunks. Both are covered below, because "what search cannot see" is as important to know as what it can.

## Technical walkthrough

The pipeline is: **persist raw → (commit) → Celery task `extraction.normalize_evidence` → post-commit fan-out**. Each numbered step below names the function that does the work.

1. **Persist ingestion** — `persist_ingestion_events` (backend/src/contextedge/services/ingestion_persistence.py:19) loops connector events, stamps the payload with `_connector_source_type`, `_connector_object_type`, `_connector_metadata`, and optional `_thread_id` / `_source_timestamp` (these underscore keys are what later derivation functions key on), computes a `content_hash` (SHA-256 of the canonical JSON of `{external_id, body}`), and skips inserts when `(tenant_id, source_id, external_id, content_hash)` already exists. New rows become `RawEvidenceObject` (backend/src/contextedge/models/evidence.py:25).

   **Offload rule:** if the serialized payload exceeds `OFFLOAD_THRESHOLD_BYTES = 32_768` (ingestion_persistence.py:16), the JSON is uploaded to MinIO via `upload_raw` under `raw/{tenant_id}/{raw_id}.json` (backend/src/contextedge/services/object_store.py:50) and the inline `raw_payload` becomes the stub `{"_offloaded": true, "size_bytes": N}` (ingestion_persistence.py:85-87). **Consequence to remember:** any SQL that filters on `raw_payload` fields silently sees only the stub for the biggest rows — ingest-priority ordering, reply-inheritance reconciliation, and ad-hoc backfills all skip offloaded rows (see the knowledge-state backfill caveat at the end of the "Retrieval ignored the source system's knowledge lifecycle" entry in [KNOWN_GAPS.md](./KNOWN_GAPS.md)).

   The caller commits, then `queue_normalize_raw_objects` (backend/src/contextedge/services/sync_ingestion_queue.py:16) dispatches one `normalize_evidence.delay(raw_id, tenant_id)` per row — after commit, so workers always see committed rows.

2. **Load raw for normalize** — the Celery task `extraction.normalize_evidence` (backend/src/contextedge/workers/extraction_tasks.py:1300-1306, `extraction` queue, 3 retries × 60 s) runs `_normalize` (extraction_tasks.py:122) inside one transaction (the `run_async` wrapper commits on success, rolls back on exception). It loads the raw row — wrong tenant or missing returns `{"error": "raw_not_found"}` (124-126) — and `load_raw_payload` (backend/src/contextedge/services/artifact_extraction_service.py:341) re-downloads offloaded payloads from MinIO; an offloaded stub with no storage key returns `{"error": "raw_payload_offloaded_without_storage_key"}` (128-131).

3. **Noise gate (deterministic, pre-LLM)** — only for hydrated thread messages (`is_hydrated_message`, backend/src/contextedge/services/message_filter.py:209). `message_noise_reason` (message_filter.py:174) returns `delivery_failure` / `quote_only` / `empty` / `coordination_only`, where `coordination_only` means: under `MIN_DIAGNOSTIC_CHARS = 150` after markup and signature stripping **and** no technical-signal regex fires (message_filter.py:52, 203). A hit ends the flow with `{"status": "skipped_noise_message", reason, filter_version}` — **no evidence row is created**, the raw object stays, and `MESSAGE_FILTER_VERSION = "v1"` (message_filter.py:108) travels with every rejection so a rule change can re-judge exactly the messages an older rule dropped (extraction_tasks.py:147-160). Measured: 47% of 18,907 live messages were rejected here before any model call.

4. **Title, body, and content hash** — `evidence_title_from_payload` (title/subject/summary/short_description → name fields → 60-char body snippet → "Untitled Evidence"; backend/src/contextedge/services/evidence_normalization.py:14) and `evidence_body_from_payload` (evidence_normalization.py:64), which strips quoted history and trailing boilerplate and reduces delivery failures / quote-only messages to fixed markers. `evidence_content_hash_from_payload` (evidence_normalization.py:138) hashes the **raw, pre-redaction** body so tuning cleaning or redaction rules never breaks dedup (extraction_tasks.py:162-168).

5. **Redaction** — `redact_evidence_fields(title, body, enabled=settings.redaction_enabled)` (backend/src/contextedge/services/redaction_service.py:179; flag default True) runs regex rules in priority order — API tokens, JWT, bearer tokens, secret assignments, then EMAIL, PHONE, SSN, CREDIT_CARD, AWS keys, PRIVATE_KEY blocks (the ordered `_RULES` tuple, redaction_service.py:36-149) — replacing hits with `[REDACTED:{kind}]` (redaction_service.py:29). Secrets run before numeric rules so a token is never half-redacted. Everything downstream — classifier, embedder, extractors, database — reads post-redaction text (extraction_tasks.py:173-182). The identity-extractor input (`identity_content` = title + body + first 2,000 chars of the payload JSON) is **re-redacted** as one blob, because nested custom fields can carry PII the field extractors missed (extraction_tasks.py:184-198).

6. **Dedup lookup and refresh** — a SELECT on `(tenant_id, content_hash)` (extraction_tasks.py:213-220). On a hit — a re-ingest, e.g. the Acme VPN ticket resolved without its description changing — the existing row is **refreshed, not duplicated**: `source_facets` re-derived and merged, `case_state` and `knowledge_state` re-derived (the content hash covers the body, and closing a ticket or retiring an article rarely rewrites the body — this refresh is exactly how those state changes land), `created_at_source` backfilled, thread linked, embedding repaired if NULL, identity/decision extraction re-run only when `canonical_entity_refs` lacks them, attachments re-registered (extraction_tasks.py:221-316). Chunking is **not** re-run on the dedup path. **Dedup is enforced at both layers:** the SELECT handles the common case; the partial unique index `uq_evidence_items_tenant_content_hash ... WHERE content_hash IS NOT NULL` (backend/alembic/versions/0026_dedup_uniqueness.py:73-75) catches the check-then-insert race — on `IntegrityError`, `_normalize` rolls back, re-fetches the winner, logs `normalize.dedup_race_resolved`, and returns `{"deduped": true, "raced": true}` without re-spending any LLM calls (extraction_tasks.py:376-409).

7. **New evidence row** — on a miss, `EvidenceItem` is inserted with scope copied from the Source (`workspace_id` always; `domain_id` only when the source has exactly one configured domain — a multi-domain source's evidence stays NULL, which by graph convention means tenant-global) and the derivations below (extraction_tasks.py:339-372). The first three read only the payload; `source_facets` also reads the Source's config:
   - `evidence_type` ← `derive_evidence_type` (backend/src/contextedge/services/evidence_typing.py:118): explicit payload `evidence_type` wins (Zoho stamps it), else a `(source_type, object_type)` map — e.g. `("servicenow","kb_knowledge") → "kb_article"` (evidence_typing.py:47), any `hydrated_message` → `"thread_message"` (68-73) — else a per-source default, else `"message"`.
   - `knowledge_state` ← `derive_knowledge_state` (backend/src/contextedge/services/knowledge_lifecycle.py:98): the source system's lifecycle (`draft/review/published/retired`); NULL means "the source did not say" and always serves.
   - `case_state` ← `derive_case_state` (backend/src/contextedge/services/case_state.py:89): `resolved` / `cancelled` / NULL from the ticket's own status field.
   - `source_facets` ← `derive_facets(payload, source.config["facet_fields"])` (backend/src/contextedge/services/source_facets.py:63): config-mapped `{root_cause, component, environment, version, customer, region, ticket_type}`.
   - `relevance_state = "unclassified"`, title truncated to 500 chars.

   Then `ensure_thread_for_evidence` (evidence_normalization.py:155) get-or-creates the `Thread` row from `payload["_thread_id"]` with `hydration_status="pending"`, and `register_attachment_artifacts` (artifact_extraction_service.py:349) registers payload attachments (extraction_tasks.py:410-418).

8. **Relevance classification — first LLM call, inline** — `run_relevance_classifier(title, body, ...)` (extraction_tasks.py:428-435; prompt `relevance`, default version **v2** — backend/src/contextedge/ai/prompts/relevance.py:78-83; v3, which adds atomic claims, is registered at relevance.py:123-124 but not default). It writes `relevance_state`, `relevance_score`, and `body_summary` (never overwriting an existing summary — attachment extraction owns its value), and persists any claims fail-soft (extraction_tasks.py:436-461). Classifier failure logs `relevance_classification_failed` and **falls through to the full pipeline** — classification never blocks ingestion (462-469).

9. **The skip gate** — `skip_extraction = (label == "not_relevant" AND confidence >= 0.75)` (extraction_tasks.py:475-479). The threshold is deliberately conservative: missing a real incident costs more than extracting on noise. Skipped items keep their evidence row (audit trail) but get no further LLM calls, no embedding, and no chunks — they are invisible to vector search by construction.

10. **Message function — second LLM call, conversational sources only** — `classify_message_function` runs when not skipped and `source_type` is conversational (extraction_tasks.py:487-499); writes `message_function` + `message_function_confidence`. Fail-soft.

11. **Error signatures — deterministic, runs even for skipped items** — `fingerprint_evidence` (regex, no LLM) because a confidently-irrelevant thread can still carry a pasted stack trace worth indexing (extraction_tasks.py:511-526). Fail-soft.

12. **Identity, decisions, embedding — only when not skipped** — `link_evidence_identities` (extraction_tasks.py:533-540; mechanism in [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md)), then `link_evidence_decisions` (551-557), then `_ensure_embedding` (568) → `embed_evidence(title, body)` → a 3,072-dim vector of `title + body[:8000]` (backend/src/contextedge/ai/embeddings.py). Each is individually try/except-wrapped — a failure degrades that one enrichment, never the ingest (542-571). **Observed caveat:** the parent-embedding call site passes no `tenant_id`/`db` (extraction_tasks.py:65-70), so unlike chunk embeddings it is neither budget-gated nor cost-attributed.

13. **Chunk dispatch** — `_dispatch_chunking` (extraction_tasks.py:73-119) runs **after** the parent embedding so a chunker bug cannot regress parent-embedding retrieval; the whole block is fail-soft (578-585). It stamps `EvidenceItem.source_type` from the parent `Source` row when missing (93-97 — the column came from `0029` and nothing else fills it), then chooses: **inline** when the body is under `INLINE_CHUNK_BUDGET_BYTES = 16 KiB` **and** the source is on the allowlist `{jira_sm, servicenow, gmail, teams, sapphireims, zoho_desk}` (extraction_tasks.py:54, 60-62) — calling `write_chunks` in the same transaction and queueing `embed_chunks_batch_task` for the new chunk ids (105-117); **async** otherwise via `chunk_evidence_task.delay` (119), so big attachments and unfamiliar sources never stall the normalize transaction.

14. **Chunk persistence and embedding** — `write_chunks` (backend/src/contextedge/services/evidence_chunk_service.py:43) resolves a chunker via `get_chunker(source_type, evidence_type)` (backend/src/contextedge/services/chunkers/registry.py:116; resolution order: `kb_article` → **document** chunker, ticket sources → **ticket**, gmail/teams → **thread**, `attachment` evidence → **attachment**, else **fallback** — registry.py:128-139), deletes any rows at the same `chunker_version` for re-run safety, inserts rows with per-chunk `content_hash` and a defaulted `source_authority`, and stamps `chunked_at` + `chunk_count` on the parent (evidence_chunk_service.py:118-119). The async task `extraction.chunk_evidence` (backend/src/contextedge/workers/chunk_tasks.py:210-212) is idempotent on `(evidence, chunker_version)` and degrades to body-text-only chunking when an offloaded payload has no storage key. `extraction.embed_chunks_batch` (chunk_tasks.py:238-240) filters `embedding IS NULL`, embeds in batches of `EMBED_BATCH_SIZE = 32` (chunk_tasks.py:51, 162-163) through the budget-gated, tenant-attributed `generate_embeddings_batch`, breaks (without raising) on a batch failure so surviving NULL rows are picked up on replay, and post-commit dispatches `evaluation.generate_correlation_suggestions` per embedded evidence (262). **Both chunk tasks route to the dedicated `embedding` queue** (backend/src/contextedge/workers/celery_app.py:267-268) — during the 2026-08-17 Zoho backfill, chunk embedding queued behind 10,000+ normalizations left 85% of chunks unembedded and silently unretrievable, hence the separate lane.

15. **Post-commit fan-out (task wrapper)** — after `run_async` commits, `normalize_evidence` dispatches: `artifact.extract_attachment` per attachment when attachments exist, otherwise `extraction.correlate_evidence` + `extraction.compute_evidence_baseline` (extraction_tasks.py:1315-1334); and — when the payload carried `_thread_id`, the record is **not** itself a hydrated message, and it was not a dedup — `hydration.hydrate_thread` (1341-1351). The hydrated-message guard (`is_hydrated_message`, same predicate as the noise gate; extraction_tasks.py:613-615) prevents each hydrated message from re-hydrating its own thread — a measured 10× request amplification against APIs (Zoho) that answer throttling with **empty results, not errors**. `classify_relevance_task` (extraction_tasks.py:1357-1363, fast `default` queue) is manual/sweep-only now: it re-runs classification, is the only path that runs applicability extraction (`_extract_applicability`, 685; knowledge evidence only, skipped entirely when source facets already state environment/version), and dispatches the missed chunk/correlate/baseline fan-out when a previously-skipped item turns out to be relevant (`needs_fanout`, 678-682, 1374-1381).

16. **Vector indexes** — the ANN (approximate nearest neighbour) indexes behind semantic search are **halfvec expression HNSW** indexes from migration `0032`. The four target tables are `evidence_items`, `evidence_chunks`, `decisions`, and `episodes` (backend/alembic/versions/0032_halfvec_hnsw_indexes.py:58-61), and each gets `USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)`, built `CONCURRENTLY` with a drop-before-create so an interrupted build cannot leave an INVALID index behind (0032:106-113). Plain HNSW on the `vector` type caps at 2,000 dimensions and the app stores 3,072, so the HNSW indexes that `0021`/`0030` appeared to create **never existed** — every similarity query was a sequential scan until `0032`. The migration requires pgvector ≥ 0.7 and fails loud below it (0032:90-97). Query-side, every cosine ordering must go through `halfvec_cosine_distance` (backend/src/contextedge/search/vector_ops.py) — a bare `column.cosine_distance(...)` bypasses the expression index.

17. **Access policy** — evidence rows may carry `access_policy_id`; search and GET routes filter using `resolve_excluded_access_policy_ids` (backend/src/contextedge/search/access_control.py:15; see [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md)). Chunks inherit access posture from the parent — they have no independent ACL column; the chunk-search pass applies legal-hold, pending-redaction, and access-policy predicates on the parent row.

## Example: Acme VPN data at this stage

**Input — raw evidence object from the Jira connector**

```json
{
  "raw_id": "raw-7f3a1b",
  "tenant_id": "acme-corp",
  "source_id": "src-jira-01",
  "external_id": "JIRA-4521",
  "content_hash": "sha256:9f3a2b4c...",
  "raw_payload": {
    "key": "JIRA-4521",
    "fields": {
      "summary": "VPN connection drops after Windows update KB5032190",
      "description": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED. Reported by jsmith@acme.com.",
      "priority": { "name": "High" },
      "created": "2026-03-15T09:23:00.000+0000"
    }
  }
}
```

**Processing — noise gate (n/a, not a thread message), title/body, redact, hash, dedupe, classify, extract, embed, chunk**

The worker derives a clean title and body, redacts `jsmith@acme.com` to `[REDACTED:EMAIL]`, computes the content hash on the pre-redaction body, finds no existing item with that hash, inserts the row, classifies it `operational`, links identities (`vpn-gw-east-01`, John Smith), extracts the embedding, and writes one `body` chunk inline.

**Output — normalized evidence item**

```json
{
  "evidence_id": "ev-a1b2c3",
  "tenant_id": "acme-corp",
  "source_id": "src-jira-01",
  "external_id": "JIRA-4521",
  "evidence_type": "ticket",
  "title": "VPN connection drops after Windows update KB5032190",
  "body_text": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED. Reported by [REDACTED:EMAIL].",
  "body_summary": "Multiple users report VPN disconnects following patch Tuesday. AUTH_CERT_EXPIRED on vpn-gw-east-01.",
  "content_hash": "sha256:b7d4e1...",
  "relevance_state": "operational",
  "relevance_score": 0.93,
  "case_state": null,
  "source_facets": { "component": "vpn", "environment": "production" },
  "embedding": "[3072 floats]",
  "chunked_at": "2026-03-15T09:24:11Z",
  "chunk_count": 1,
  "canonical_entity_refs": { "identities": ["..."], "decisions": [] }
}
```

Note: `canonical_entity_refs` is a JSONB object with separate keys. Identity resolution populates `identities` (see [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md)) and decision extraction populates `decisions` (see [09-graph-and-correlation.md](./09-graph-and-correlation.md)); both write non-destructively, preserving each other's keys. When the ticket is later **resolved** and re-synced, the body is unchanged so the content hash matches — the dedup-refresh path updates `case_state` to `"resolved"` and merges the resolution-time facets (root cause is typed at resolve time) onto this same row.

## Design decisions

- **Two hashes (raw canonical vs normalized body)** — *Why:* the raw-layer hash (`{external_id, body}`) prevents storing identical upstream snapshots twice; the evidence-layer hash of the normalized body aligns with "same operational meaning" for analysts, and is computed pre-redaction so regex tuning never breaks dedup. *Tradeoff:* two concepts to document; edge cases when titles differ but bodies match.

- **Filter noise deterministically, before any model call** — *Why:* thread hydration turns ~1,500 tickets into ~19,000 message rows, and a measured 47% are coordination chatter; paying a classifier to discover that costs millions of tokens. The gate is a versioned regex filter, and rejection leaves the raw object untouched, so a rule change can re-judge exactly what was dropped. *Tradeoff:* a deterministic filter has edge cases a model would catch; the technical-signal regexes deliberately rescue short messages that carry hostnames, error codes, or stack traces (a 33-character "Restarted IPSec on vpn-gw-east-01" is far under the 150-character floor and survives anyway, because it names a host).

- **Classify before embed, and gate the fan-out** — *Why:* at typical IT inbox noise rates, running relevance classification inline and skipping embedding + identity + decisions for confidently-irrelevant items short-circuits the expensive path for the majority of records (extraction_tasks.py:420-424). *Tradeoff:* skipped items are invisible to vector search until a manual or sweep re-classification flips them — which is why `classify_relevance_task` dispatches the missed chunk/correlate/baseline fan-out on a verdict change (`needs_fanout`).

- **Offload large JSON to object storage** — *Why:* keeps Postgres row size predictable; backups and replication cheaper. *Tradeoff:* the normalize path depends on object-store availability, and any SQL over `raw_payload` silently sees only the stub for offloaded rows — the biggest tickets (exactly the longest conversations) are the ones a payload-field query cannot see.

- **Inline embedding on normalize** — *Why:* semantic search works immediately without a second hop. *Tradeoff:* normalize task latency and provider rate limits; and the parent-embed call site currently passes no tenant context, so that spend is unattributed (chunk embedding, by contrast, is budget-gated and attributed).

- **Dedup enforced at the database, resolved in the application** — *Why:* the partial unique index from `0026` makes concurrent duplicate inserts impossible; the worker treats the `IntegrityError` as "adopt the winner" so the race costs a rollback, never a duplicate row or duplicate LLM spend. *Tradeoff:* the loser returns without re-running enrichment, so if the winner's enrichment partially failed, repair waits for the next re-ingest (the dedup-refresh path repairs missing embeddings and links).

- **Post-normalize baseline fan-out** — *Why:* Zone 4 of the reviewer console wants a current value plus a baseline comparison ("was 74% a week ago", "first observation in 7d window"); computing this at read time would require a scan of prior evidence on every render. *How:* `compute_evidence_baseline_task` fires alongside `correlate_evidence` post-commit (and again after attachment extraction), matches on tenant + evidence_type + source_object_id in a 7-day window, and writes `baseline_ref` + `delta_signal` into `EvidenceItem`. *Tradeoff:* the generic worker does relationship-only baselines ("last seen N days ago"); numeric baselines ("74% → 32%") are the connector's job to populate at ingest since only the connector knows what a meaningful delta looks like — the JSONB shape is open-ended so both coexist, and connector-stamped `delta_signal` values are never overwritten.

- **Chunks as a sibling table, not a 1:N split of `EvidenceItem`** (since 2026-05-08, migration `0030_evidence_chunks`) — *Why:* every existing FK targeting `evidence_items.id` (`attachment_artifacts`, `correlation_edges`, `playbook_evidence_links`, `contradiction_scan_state`, `threads.evidence_items`, `decision_evidence`, `claim_evidence`) keeps its target. Card identity stays one-row-per-upstream-object; chunks are a high-recall index, not a replacement. The parent's `embedding` column is preserved unchanged so contradiction scanning, similar-decision retrieval, and baseline matching keep working. *Tradeoff:* search-side rollup logic is required to render parent cards from chunk hits (shipped 2026-08-01 in `search/vector_search.py` + `search/chunk_rollup.py`), and chunk-row counts run ~5–10× higher than evidence rows — `evidence_chunks` hits the partition pain point first. Detail in [`CHUNKING_DESIGN.md`](./CHUNKING_DESIGN.md).

- **`chunker_version` on every chunk row** — *Why:* chunkers will evolve (semantic splitting heuristics, redaction-rule retunes that change boundaries, per-source parser improvements). The unique key `(evidence_id, chunk_index, chunker_version)` (models/evidence.py:183-186) lets a re-chunk write the new generation alongside the old; the atomic swap is just updating `EvidenceItem.chunked_at`. *Tradeoff:* a maintenance task is required to GC retired versions — **and that task does not exist yet** (all chunkers are still at version 1, so nothing has accumulated); until it lands, a version bump would leave old generations coexisting in the ANN, which the search rollup tolerates (one hit per parent).

- **Embed split from chunk write, on a dedicated queue** — *Why:* writing chunks is cheap; embedding 50 chunks per long item is expensive. `write_chunks` lands rows with `embedding = NULL`; `embed_chunks_batch_task` fans out in batches of 32 so the per-tenant budget gate fires per batch and retries are localized. The `embedding` queue exists because FIFO behind bulk normalization starved chunk embedding — evidence was "ingested and silently unretrievable" (celery_app.py:267-268 and the comment above it). *Tradeoff:* a window exists where chunks are written but unembedded; the chunk-search pass explicitly filters `embedding IS NOT NULL` (backend/src/contextedge/search/vector_search.py:97), and such evidence still surfaces through its parent embedding.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Raw insert + offload | `backend/src/contextedge/services/ingestion_persistence.py` | `persist_ingestion_events`, `OFFLOAD_THRESHOLD_BYTES` | After connector batch / hydration |
| Enqueue after commit | `backend/src/contextedge/services/sync_ingestion_queue.py` | `queue_normalize_raw_objects`, `NormalizeEnqueueError` | Post-commit of sync run |
| Normalize worker | `backend/src/contextedge/workers/extraction_tasks.py` | `_normalize`, `normalize_evidence` | Celery **extraction** queue |
| Noise gate | `backend/src/contextedge/services/message_filter.py` | `is_hydrated_message`, `message_noise_reason`, `MESSAGE_FILTER_VERSION` | First step of `_normalize`, hydrated messages only |
| Title/body/hash helpers | `backend/src/contextedge/services/evidence_normalization.py` | `evidence_title_from_payload`, `evidence_body_from_payload`, `evidence_content_hash_from_payload` | Normalize |
| Thread linking | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalize (new + dedup paths) |
| Redaction | `backend/src/contextedge/services/redaction_service.py` | `redact`, `redact_evidence_fields` | Normalize — before classifier / embed / extraction |
| Type + state derivation | `backend/src/contextedge/services/evidence_typing.py`, `knowledge_lifecycle.py`, `case_state.py`, `source_facets.py` | `derive_evidence_type`, `derive_knowledge_state`, `derive_case_state`, `derive_facets` | At insert; state/facets refreshed on dedup |
| Relevance classifier | `backend/src/contextedge/ai/classifiers/relevance.py` | `classify_relevance` (prompt `relevance` v2 default) | Inline in `_normalize`; `_classify` for re-runs |
| Decision extraction + linking | `backend/src/contextedge/ai/extractors/decision_extractor.py`, `services/decision_service.py` | `extract_decisions`, `link_evidence_decisions` | Normalize, post-gate |
| Payload load + attachments | `backend/src/contextedge/services/artifact_extraction_service.py` | `load_raw_payload`, `register_attachment_artifacts` | Normalize |
| Blob I/O | `backend/src/contextedge/services/object_store.py` | `upload_raw`, `download_raw`, `ensure_bucket` | Persist / normalize |
| Embeddings on item | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence` | Normalize / dedup repair |
| Chunk dispatch | `backend/src/contextedge/workers/extraction_tasks.py` | `_dispatch_chunking`, `INLINE_CHUNK_BUDGET_BYTES`, `INLINE_CHUNK_SOURCE_ALLOWLIST` | Inline in `_normalize`, after parent embed |
| Chunkers | `backend/src/contextedge/services/chunkers/` | `get_chunker`, `TicketChunker`, `ThreadChunker`, `DocumentChunker`, `AttachmentChunker`, `FallbackChunker` | Inline + chunk worker |
| Chunk persistence | `backend/src/contextedge/services/evidence_chunk_service.py` | `write_chunks`, `stamp_chunk_embeddings`, `_default_authority` | Inline + chunk worker |
| Chunk + embed worker | `backend/src/contextedge/workers/chunk_tasks.py` | `chunk_evidence_task`, `embed_chunks_batch_task`, `EMBED_BATCH_SIZE` | Celery **embedding** queue |
| ANN indexes | `backend/alembic/versions/0032_halfvec_hnsw_indexes.py`, `backend/src/contextedge/search/vector_ops.py` | halfvec HNSW expression indexes, `halfvec_cosine_distance`, `tune_ann_recall` | Migration / every semantic query |
| ORM | `backend/src/contextedge/models/evidence.py` | `RawEvidenceObject`, `EvidenceItem`, `EvidenceChunk`, `Thread`, `AttachmentArtifact` | Persistence |

## Acme VPN incident (this layer)

Three raws with the same VPN error text collapse to one `EvidenceItem` after content-hash match; when the ticket is resolved and re-synced, the dedup-refresh path stamps `case_state="resolved"` on the same row rather than creating a second card. A long Teams export exceeds 32 KiB and stores its JSON in **MinIO** while the database row points to it — Acme analysts still see one searchable card with correct provenance. Each surviving evidence record then chunks: the Jira description becomes one `body` chunk via the ticket chunker; a 40 KB post-mortem markdown attachment splits into ~12 `heading_section` chunks (each tagged with the heading breadcrumb, e.g. `"Postmortem > Timeline > 14:32"`); long Teams replies strip their quoted-prior-message tails and then go through the same character splitter as everything else, so a short reply is one `message` chunk and a long one is several (backend/src/contextedge/services/chunkers/thread.py:84-112); and the "How the corporate VPN works" KB article routes through the **document** chunker and carries `source_authority: "knowledge_article"` so it never competes with the incident record as if it were a ticket. The reranker gets per-chunk `priority`, `author`, and `source_authority` to score on, where before it only had a single coarse evidence vector.

## Partition note (chunks too)

`evidence_chunks` is the next partition-conversion candidate after `evidence_items` — chunk row counts will exceed evidence row counts by ~5–10× depending on per-source body sizes, so it will hit the maintenance pain point first. The partitioning plan below applies the same shape (`LIST (tenant_id)`, optional sub-partitioning by `RANGE (created_at)`) and the same per-partition halfvec-HNSW build pattern. When customer volume warrants the conversion, partition both tables in the same maintenance window.

## Partitioning plan (deferred, index-only groundwork shipped in `0024`)

`evidence_items` is the hottest append-mostly table in the schema. As tenants grow, maintenance operations (VACUUM, index rebuild, retention purge) on a single physical table become increasingly painful. The enterprise architecture review ([`ENTERPRISE_ARCHITECTURE_REVIEW.md`](../ENTERPRISE_ARCHITECTURE_REVIEW.md), §6 item 12, at the repo root) flagged table partitioning as the scale-foundation move and points back at this section for the runbook. This section captures the plan so the conversion can be executed when customer volume warrants it.

### What `0024_evidence_scale_indexes` ships today

Three indexes that improve the hot paths *without* a table rewrite:

- BRIN on `(tenant_id, ingested_at)` — excellent for time-range admin / drift / retention queries at ~1 KB overhead per million rows.
- Partial B-tree on `(tenant_id, relevance_state) WHERE relevance_state IN ('relevant','unclassified')` — keeps the reviewer-queue index size proportional to the actionable tail, not the archived/irrelevant bulk.
- Partial B-tree on `(tenant_id, updated_at) WHERE relevance_state = 'archived'` — same shape, tuned for `purge_archived_evidence` sweeps.

### Full partition design (when conversion is needed)

**Partition key.** `LIST (tenant_id)` as the top-level partition — each tenant isolated for per-tenant purge, per-tenant backup/restore, and zero noisy-neighbour effect on VACUUM. Optional sub-partitioning by `RANGE (ingested_at)` on a month boundary for very large tenants where retention runs across old months need to drop whole partitions.

**Indexes.** The halfvec-HNSW expression indexes and FTS GIN indexes must be declared per-partition (PostgreSQL 16 has no global HNSW). The partial + BRIN indexes in `0024` port cleanly to partitions.

**FK constraints.** `attachment_artifacts.evidence_id`, `playbook_evidence_links.evidence_id`, `correlation_edges`, `contradiction_scan_state` all currently FK to `evidence_items(id)`. PostgreSQL 12+ supports FKs to partitioned parents, so no referential change is required — but the FK is enforced via an index on every partition, so each partition carries its own partial unique index on `id`.

**Cutover runbook sketch.**

1. `CREATE TABLE evidence_items_new (... LIKE evidence_items INCLUDING ALL) PARTITION BY LIST (tenant_id);`
2. Create one partition per existing tenant plus a `DEFAULT` partition for future tenants.
3. `INSERT INTO evidence_items_new SELECT * FROM evidence_items;` under `REPEATABLE READ` — on a maintenance window, or via logical replication for zero-downtime.
4. Swap names in a single transaction: `ALTER TABLE evidence_items RENAME TO evidence_items_legacy; ALTER TABLE evidence_items_new RENAME TO evidence_items;`
5. Rebuild halfvec-HNSW + FTS indexes on each partition (parallelisable).
6. Drop `evidence_items_legacy` after a safety window (24h).

**Triggers for the default partition.** When a tenant is created, a Celery task must `CREATE TABLE evidence_items_tenant_<uuid> PARTITION OF evidence_items FOR VALUES IN ('<uuid>');` so new tenants don't fall into the default. The default partition is a safety net, not a hot path.

**What we won't know until customer volume ships.** Exact partition granularity (per-tenant vs per-tenant-month), whether HASH partitioning gives better skew than LIST at tenant count >100, and the downtime tolerance for the initial swap. All of those depend on real data.

## Further reading

- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — where raws are created
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — models behind embed and classify
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — canonical entity extraction and alias matching that runs inside normalization
- [CHUNKING_DESIGN.md](./CHUNKING_DESIGN.md) — sibling-table decision, per-source chunker strategy, search rollup, redaction interaction
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — offloaded-payload backfill blindness, HNSW history, pgvector stamp caveat
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) — ingestion diagram
