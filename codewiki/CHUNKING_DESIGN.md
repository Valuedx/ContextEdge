# Evidence chunking design

> Status: foundation merged on `feature/evidence-chunking` (commit
> `ad8b195`, stacked on `feature/ae-ops-context-graph-alignment`).
> Migration `0030_evidence_chunks`, ORM, per-source chunker bodies
> (`ticket` / `thread` / `attachment` / `fallback`), persistence
> service, async + batched-embed Celery tasks, and the `_normalize`
> wiring all landed in that branch. The search-side rollup
> (§6 below), the backfill task for `chunked_at IS NULL` rows, the
> tree-sitter code chunker, and per-tenant authority overrides remain
> follow-ups — see "What's *not* in this design" at the end and the
> "Resolved: Evidence chunking foundation" section in
> [`KNOWN_GAPS.md`](./KNOWN_GAPS.md).

## Summary

You will understand why long evidence bodies are invisible to today's
retrieval, how the new `evidence_chunks` sibling table fixes that
without disturbing the rest of the schema, where chunking slots into
the existing normalize worker, what each per-source chunker is
supposed to do, and which open questions remain before this can ship.

## Business picture

Today, when a reviewer asks "what do we know about VPN cert
expiry?", the search layer looks at one embedding per Jira ticket,
one per Teams thread, one per Gmail thread — regardless of whether
that thread is two lines or two thousand lines. Anything past the
first few thousand characters of body text never reached the
embedding model in the first place, so the system is blind to it.
Long incident threads with the actual root-cause discussion in reply
#7, post-mortems with the lessons-learned section at the bottom, and
log files attached to tickets all suffer from the same cliff: useful
information stored on disk, but not retrievable.

Chunking solves this by indexing the **inside** of long records,
not just the record as a whole. A reviewer searching for an error
code that appears once in a 50 KB Teams thread will now get a hit
that points to the specific message, not just to "this thread exists
somewhere." The card UIs stay unchanged — the user still sees one
ticket, one thread, one email — but ranked retrieval is now far more
precise.

This is the foundation for the higher-quality work that follows
(re-ranking, the confidence model, typed graph edges over chunks
rather than over coarse evidence rows).

## What's broken today (and the evidence)

| Concern | Current limit | File |
| --- | --- | --- |
| Evidence body persisted | full text on `EvidenceItem.body_text` | `services/evidence_normalization.py:46` |
| Embedding input | `title + body[:8000]` — single 3072-dim vector per item | `ai/embeddings.py:31` |
| Decision extractor | hard-truncated to 4,000 chars | `KNOWN_GAPS.md:64` |
| Episode extractor | per-item truncation at 2,000 chars | `06-ai-extraction-and-embeddings.md:21` |
| Vector search target | `evidence_items.embedding` HNSW (`0021`) | `06-ai-extraction-and-embeddings.md:103` |

**Read together:** the body lands intact in Postgres, but every
downstream consumer reads it through a small window. The first 8 KB
shape semantic search; the first 2–4 KB shape extraction. Past those
bounds, content exists but is functionally invisible.

## Technical walkthrough

### 1. Sibling table, not a split

`evidence_chunks` lives next to `evidence_items`. Every existing FK
that targets `evidence_items.id` (`attachment_artifacts`,
`correlation_edges`, `playbook_evidence_links`,
`contradiction_scan_state`, `threads.evidence_items`,
`decision_evidence`, `claim_evidence`) keeps its target. The card
surface stays one row per upstream object. Chunks are a
high-recall index, **not** a replacement for the parent record.

The parent's `embedding` column is preserved unchanged. Contradiction
scanning, similar-decision retrieval, baseline matching, and the
existing hybrid ranker all keep working. New search paths read chunk
embeddings; old paths read the parent embedding. No rip-and-replace.

Decision rationale captured in the migration docstring
(`0030_evidence_chunks.py`). The alternative — splitting `EvidenceItem`
1:N with a `parent_id` self-FK — was rejected because it would
rewrite every join in the codebase that currently treats
`evidence_id` as a card identity.

### 2. Schema (migration `0030_evidence_chunks`)

```text
evidence_chunks
  id                 uuid PK
  tenant_id          uuid  FK tenants.id
  evidence_id        uuid  FK evidence_items.id  ON DELETE CASCADE
  chunk_index        int
  chunk_kind         varchar(40)   -- controlled vocab, indexed
  text               text
  char_offset_start  int  NULL
  char_offset_end    int  NULL
  parent_section     text NULL     -- heading breadcrumb for hierarchical
  embedding          vector(3072) NULL
  content_hash       varchar(64)
  metadata           jsonb         -- author/ts/severity/language/etc
  chunker_version    int
  created_at         timestamptz
  UNIQUE (evidence_id, chunk_index, chunker_version)

evidence_items  (additive)
  + chunked_at   timestamptz NULL
  + chunk_count  int NOT NULL DEFAULT 0
```

Indexes mirror the existing patterns in the codebase:

- HNSW on `embedding` (`vector_cosine_ops`, `m=16`, `ef_construction=64`)
  — built `CONCURRENTLY` in an `autocommit_block`, same shape as
  `0021_hnsw_vector_indexes`.
- GIN `jsonb_path_ops` on `metadata` — same choice as
  `0025_jsonb_gin_indexes` for `metadata @>` filters
  (`{"author": …}`, `{"severity": "error"}`).
- B-tree on `chunk_kind`, `tenant_id`, `evidence_id`, `content_hash`.
- Partial B-tree on `evidence_items (tenant_id, ingested_at DESC)
  WHERE chunked_at IS NULL` — drives the backfill scanner. Stays
  small as the backfill drains.

### 3. Chunker contract

`services/chunkers/base.py` defines a `Chunker` Protocol:

```python
class Chunker(Protocol):
    name: str
    version: int
    def chunk(
        self,
        *,
        title: str | None,
        body: str | None,
        payload: dict,
    ) -> list[ChunkSpec]:
        ...
```

Chunkers are **pure** — no I/O, no DB, no LLM. They take the
post-redaction title + body + payload (already loaded by the
normalize worker) and return ordered `ChunkSpec` objects. Persistence
lives in `services.evidence_chunk_service.write_chunks`, which is the
only thing that touches the DB.

The purity rule lets unit tests be plain "feed input, assert spec
list" and lets the inline path and the async path run identical
code.

### 4. Per-source chunker strategy table

| Source | `source_type` / `evidence_type` | Chunker | Chunk kind | Boundary rule | Notes |
| --- | --- | --- | --- | --- | --- |
| Jira ticket | `jira_sm` | `ticket` | `comment` | title+description as chunk 0; each comment as its own chunk | Author + ts in `metadata`. Long single comments fall through to recursive split. |
| ServiceNow incident | `servicenow` | `ticket` | `comment` | same | `metadata.priority`, `metadata.severity` from payload. |
| SapphireIMS ticket | `sapphireims` | `ticket` | `body` | title+description as chunk 0 | No hydration endpoint, so there are no comment chunks. |
| Zoho Desk ticket | `zoho_desk`, `evidence_type=ticket` | `ticket` | `comment` | title + description+resolution as chunk 0; each hydrated thread/comment as its own chunk | Zoho splits the conversation across `/threads` (customer email) and `/comments` (internal agent notes); both hydrate. |
| Zoho Desk KB article | `evidence_type=kb_article` | `attachment` | `heading_section` | split on heading boundaries — the article's own `<h1>`–`<h6>` survive HTML→text conversion as `#` markers | **Resolved by evidence type, ahead of source type**: one Zoho source emits both tickets and articles, and an article's author-written sections beat a character-count split. See `ZOHO_DESK_CONNECTOR.md`. |
| Gmail thread | `gmail` | `thread` | `message` | one chunk per reply; **strip quoted prior-reply blocks** before persisting | Quote blocks inflate similarity garbage. Keep `metadata.replies_to` instead of embedding the quote. |
| Teams thread | `teams` | `thread` | `message` | one chunk per message; `parent_section` = thread title | Cluster by ≤5 min gap into "conversation chunks" for retrieval-time return only — single-message rows for embedding, joined cluster for the LLM context. |
| Runbook / post-mortem (markdown attachment) | `evidence_type=attachment`, mime `text/markdown` | `attachment` | `heading_section` | split on heading boundaries; chunks ~300–500 tokens | `parent_section` = breadcrumb of headings, e.g. `"Postmortem > Timeline > 14:32"`. |
| Log file attachment | `evidence_type=attachment`, mime `text/plain` matching log signatures | `attachment` | `log_event` | per log-event boundary (regex per format: syslog, journald, JSON-lines); stack trace = one chunk | Group consecutive same-correlation events into windows. |
| Code attachment | `evidence_type=attachment`, recognized language | `attachment` | `code_block` | function / class boundary via tree-sitter or regex per language | `metadata.language`, `metadata.symbol`. |
| Generic prose >2 KB without recognized structure | anything else | `fallback` | `body` | recursive splitter on `\n\n` → `\n` → sentence; ~400 tokens, 50-token overlap | Last-resort path. Explicit so we can measure how often it fires. |

The registry resolution policy is captured in
`services/chunkers/registry.py:get_chunker` and is in resolution
order:

1. `evidence_type` in `_DOCUMENT_EVIDENCE_TYPES` (`{"kb_article"}`) → `attachment`
2. `source_type` in `{"jira_sm", "servicenow", "sapphireims", "zoho_desk"}` → `ticket`
3. `source_type` in `{"gmail", "teams"}` → `thread`
4. `evidence_type == "attachment"` → `attachment`
5. otherwise → `fallback`

Rule 1 was added with the Zoho Desk connector, the first source to emit
more than one record shape. It is checked ahead of source type so a
record's own shape wins; rule 4 keeps its original position so
attachment resolution for the existing ticket and thread sources is
unchanged.

Adding a new chunker family = new module under
`services/chunkers/`, register it in `_register_chunkers`, add the
mapping clause in `get_chunker`. Same shape as `connectors/registry.py`.

### 5. Where it slots into `_normalize`

Today `workers/extraction_tasks._normalize` runs:

> redact → classify → (skip if not_relevant ≥0.75) → identity → decision → embed parent → return

After this lands:

> redact → classify → (skip if not_relevant ≥0.75) → identity → decision → embed parent → **chunk (inline or async)** → return

The chunk step is gated:

```python
INLINE_CHUNK_BUDGET = 16 * 1024  # bytes, post-redaction body

inline_eligible = (
    body_size < INLINE_CHUNK_BUDGET
    and source_type in {"jira_sm", "servicenow", "gmail", "teams"}
)

if inline_eligible:
    await write_chunks(
        db,
        tenant_id=tenant_id,
        evidence=ev,
        payload=payload,
        source_type=source_type,
    )
    # Embeddings batched in a follow-on task to keep the critical path tight.
    enqueue_embed_chunks(ev.id)
else:
    chunk_evidence_task.delay(str(ev.id), str(tenant_id))
```

Inline path keeps card-level latency similar to today for the common
case (short tickets, short Teams messages). Async path matches the
shape `process_attachment_artifact` already uses, so no new
operational pattern is introduced.

The inline path runs *after* the parent embedding so a chunker
failure cannot regress today's search. The chunk write is its own
flush; failures log + continue rather than rolling back the
EvidenceItem insert.

#### Diff sketch

```diff
 # workers/extraction_tasks.py
 from contextedge.services.evidence_normalization import (
     ensure_thread_for_evidence,
     ...
 )
+from contextedge.services.evidence_chunk_service import write_chunks
+from contextedge.workers.chunk_tasks import (
+    chunk_evidence_task,
+    embed_chunks_batch_task,
+)

 async def _normalize(db: AsyncSession, raw_object_id: str, tenant_id: uuid.UUID):
     ...
     # existing flow up to embed
     try:
         embedded = await _ensure_embedding(db, ev)
     except Exception as embed_exc:
         ...

+    # Chunk after embed so a chunker failure cannot block today's
+    # retrieval. ``source_type`` is on the evidence row (column
+    # added by 0029) once the normalize_evidence flow stamps it —
+    # until that lands, we resolve from raw.source_type or the
+    # connector key.
+    body_size = len((ev.body_text or "").encode("utf-8"))
+    inline_eligible = (
+        body_size < 16 * 1024
+        and (ev.source_type or "") in {"jira_sm", "servicenow", "gmail", "teams"}
+    )
+    if inline_eligible:
+        try:
+            chunks = await write_chunks(
+                db,
+                tenant_id=tenant_id,
+                evidence=ev,
+                payload=payload,
+                source_type=ev.source_type,
+            )
+            embed_chunks_batch_task.delay(
+                [str(c.id) for c in chunks], str(tenant_id),
+            )
+        except Exception as chunk_exc:
+            logger.warning(
+                "chunking_failed", evidence_id=str(ev.id), error=str(chunk_exc),
+            )
+    else:
+        chunk_evidence_task.delay(str(ev.id), str(tenant_id))

     return {...}
```

The `try/except` on the inline path is intentional: chunking
quality is a Phase 4 concern and a chunker bug must not regress
today's pipeline. Async failures already retry via Celery's
`max_retries=3`.

### 6. Search integration (rollup pattern)

`search/vector_search.py` and `search/hybrid_ranker.py` evolve to
add a chunk-vector path:

1. New query: `SELECT evidence_id, id AS chunk_id, embedding <=> :q
   AS score FROM evidence_chunks WHERE tenant_id = :t ORDER BY score
   LIMIT :k`. K should be 50–100 (oversample for the rollup).
2. Group by `evidence_id`, take `MIN(score)` (closest chunk per
   evidence). That's the parent score.
3. **MMR at the chunk level before grouping** — otherwise five
   near-duplicate chunks of the same long thread crowd out four
   distinct evidences. The same MMR window is what keeps the
   re-ranker's input diverse.
4. Hybrid ranker fuses lexical (FTS on parent body) + semantic
   (chunk-rolled-up) + per-source authority weight (chunk
   `metadata.source_authority`). Last term is the day-1 lever the
   re-ranker also reads.

The `chunk_id` is preserved through the pipeline so the LLM-context
path returns the *specific chunk* with its `parent_section`
breadcrumb, not the entire parent body.

Today's parent-embedding path stays in place behind a feature flag
until the chunked path proves out. After that, the parent embedding
becomes a coarse pre-filter (e.g. "is this evidence even relevant
before we open the chunk index").

### 7. Backfill

Existing `EvidenceItem` rows have `chunked_at IS NULL`. A one-shot
Celery task drains them tenant by tenant:

```python
SELECT id, source_id, ingested_at
FROM evidence_items
WHERE tenant_id = :t AND chunked_at IS NULL
ORDER BY ingested_at DESC
LIMIT :batch
```

`ingested_at DESC` so recent items chunk first — those are the ones
the reviewer console queries against. The partial index
`ix_evidence_items_chunked_at_null` keeps this cheap.

Idempotent: re-running the task on a row that's already chunked at
the current `chunker_version` is a no-op (the unique key on
`(evidence_id, chunk_index, chunker_version)` plus the explicit
`DELETE WHERE chunker_version` in `write_chunks` make this safe).

For tenants with millions of evidence rows the backfill is large.
Throttle via the existing per-tenant LLM budget gate — the embedding
calls go through `generate_embedding(tenant_id=…)` and respect
`tenant_llm_budgets` per `0023`.

### 8. Re-chunking under schema change

When a chunker changes its boundary rules (e.g. moving from
sentence-based to heading-based for runbooks), bump
`Chunker.version`. The next chunk run writes new rows alongside the
old version. Atomic swap = update `EvidenceItem.chunked_at` to the
new run's timestamp. A maintenance task GCs old versions on a
schedule once the new version is verified in the eval harness.

Tuning a regex inside a chunker doesn't need a version bump. Only
rule-of-thumb changes that make new chunks structurally
incomparable to old ones do.

### 9. Redaction interaction

`redact_evidence_fields` runs on `title` + `body` *before*
chunking. Chunks therefore inherit the redaction. The parent's
`content_hash` is computed pre-redaction (existing invariant from
`0026_dedup_uniqueness`), so future redaction-rule retunes don't
break parent dedup.

If a redaction rule ever changes in a way that affects chunk
boundaries (e.g. a new placeholder of a different length), pair the
redaction-version bump with a chunker-version bump and re-chunk.
Tracking pair: `redaction_version` (from the redaction service) +
`chunker_version` (this design). For now `redaction_status` lives on
`EvidenceItem` (column added by `0029`) and is read alongside.

## Design decisions

- **Sibling table, not 1:N split** — *Why:* card identity is
  load-bearing across the schema and UI. Splitting EvidenceItem would
  rewrite every join. Sibling table is purely additive.
  *Tradeoff:* search-side rollup logic is required to render parent
  cards from chunk hits.

- **`chunker_version` on every row** — *Why:* boundaries will
  change as the chunker library matures. Without a version column,
  re-chunking is a destructive operation; with it, re-chunks land
  alongside old data and atomic swap is just a timestamp update on
  the parent. *Tradeoff:* a maintenance task is required to GC old
  versions. Acceptable cost.

- **Pure-function chunkers** — *Why:* lets unit tests be trivial
  and lets inline + async paths run identical code without a
  subclass split. *Tradeoff:* I/O-aware chunkers (e.g. ones that
  want to fetch the upstream system for richer context) cannot exist
  — that work has to happen *before* chunking, in the
  connector / hydration layer.

- **Embedding split from chunking** — *Why:* writing chunks is
  cheap, but embedding 50 chunks per long item is expensive. Keeping
  the embed call in a follow-on batched task means the critical
  ingest path stays tight, the per-tenant LLM budget gate fires
  per-batch, and retries are localized to the embedding call rather
  than the whole chunking flow. *Tradeoff:* search has a brief
  window where chunks exist but embeddings are NULL — chunk-level
  vector queries skip those rows naturally (`embedding IS NULL`
  returns nothing under `<=> :q ORDER BY`).

- **Source authority defaulted at chunk time** — *Why:* the
  reranker and the future confidence model both need a "how much
  should I trust this source" signal. If we don't capture it at
  chunk time, no later phase recovers it cheaply. *Tradeoff:* the
  initial mapping (`runbook` / `ticket` / `email` / `chat` / `gist`)
  is opinionated and may need a per-tenant override table later.
  Don't try to ML-classify authority on day 1.

- **Inline + async dispatch by size threshold** — *Why:* most ingested
  items (Teams messages, short Jira tickets) fit comfortably under
  16 KB and chunk in <50ms inline; the rest go async. This avoids
  ballooning the critical-path latency for the bulk of items.
  *Tradeoff:* threshold value is a guess (16 KB) until we have
  production p50/p95 numbers; the constant lives in
  `chunk_tasks.py` so it's tunable without a migration.

- **Partial index on `chunked_at IS NULL`** — *Why:* drives the
  backfill scanner cheaply. As the backfill drains, the index
  shrinks toward zero. *Tradeoff:* a maintenance window during a
  re-chunk (schema bump) re-populates it temporarily; Postgres
  handles this fine but expect a short index-write spike.

- **No partition key for chunks today** — *Why:* mirrors the
  partition-deferred posture of `evidence_items` (see
  `04-evidence-normalization-and-storage.md` §Partitioning plan).
  Chunk row counts will exceed evidence row counts by ~5–10×, so
  this is the table that will hit the partition pain point first.
  When the customer-volume signal lands, partition both tables
  together by `tenant_id`. *Tradeoff:* will need a planned migration
  later.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Schema | `backend/alembic/versions/0030_evidence_chunks.py` | `evidence_chunks` table, `chunked_at` + `chunk_count` columns, HNSW + GIN indexes | One-shot |
| ORM | `backend/src/contextedge/models/evidence.py` | `EvidenceChunk` class, `EvidenceItem.chunks` relationship, `EvidenceItem.chunked_at`, `EvidenceItem.chunk_count` | Always |
| Chunker contract | `backend/src/contextedge/services/chunkers/base.py` | `Chunker` Protocol, `ChunkSpec` dataclass | Always |
| Registry | `backend/src/contextedge/services/chunkers/registry.py` | `get_chunker`, `_register_chunkers`, `supported_chunker_names` | First chunker resolution |
| Persistence | `backend/src/contextedge/services/evidence_chunk_service.py` | `write_chunks`, `chunk_ids_pending_embedding`, `stamp_chunk_embeddings`, `_default_authority` | Inline normalize + async chunk task |
| Async tasks | `backend/src/contextedge/workers/chunk_tasks.py` | `chunk_evidence_task`, `embed_chunks_batch_task`, `EMBED_BATCH_SIZE` | Celery `extraction` queue |
| Normalize wiring | `backend/src/contextedge/workers/extraction_tasks.py` | `_normalize` (sketch above; not yet applied) | Celery `extraction` queue |

## Risks and open questions

1. **Storage cost.** Chunks ≈ 5–10× embedding rows. At 3072 dims that
   is real spend. Mitigations to evaluate before scaling: (a) MMR +
   chunk dedup at insert for boilerplate (signatures, footers);
   (b) Matryoshka-truncated 1024-dim vectors for the chunk index
   while keeping 3072 on the parent for legacy paths; (c) per-source
   `embedding IS NULL` policy for low-authority chunks (chat noise).
2. **HNSW build cost on backfill day.** The chunk HNSW index will
   build via `CONCURRENTLY` but the tenant backfill itself will write
   millions of rows. Stage by tenant; throttle via the LLM budget
   gate for the embedding side; let HNSW maintenance cost be
   amortized over the backfill window.
3. **Threshold tuning.** The 16 KB inline threshold is a guess. Pull
   p50/p95 body sizes from a week of production normalize logs
   before merging the wiring; pick a threshold that keeps p95 inline
   latency within today's bounds.
4. **Search-side rollup correctness.** MMR at chunk level is the
   feature that keeps top-K diverse. Without it, hybrid ranking
   returns five hits from one long thread. Add a MMR test to the
   golden eval set as part of the chunked-search rollout.
5. **Authority mapping.** The day-1 mapping is opinionated. Plan to
   surface a per-tenant override table once the first customer
   complains that "our wiki *is* authoritative" or "Teams chat for
   us is the canonical incident log." Don't over-engineer day 1.
6. **Decision extractor still truncates at 4 KB.** This design fixes
   retrieval but not the extractor's cap. Pair this work with a
   decision-extractor change that runs per-chunk and dedupes
   decisions across chunks; that closes the `KNOWN_GAPS.md:64` item.
7. **Per-source chunker bodies.** The hard work is in the
   `ticket` / `thread` / `attachment` / `fallback` chunker
   implementations — boundary rules, quote stripping, log-event
   parsers, semantic markdown splitting. The skeleton lets that work
   land independently in follow-on PRs.

## What's *not* in this design

- A re-ranker. That's Phase 3; the chunk surface is its input but
  this PR doesn't introduce one.
- A confidence model with success counts and reinforcement. That's
  Phase 1 generalized; chunks just expose the right granularity for
  it.
- Cross-evidence chunk dedup (the same boilerplate footer in 50
  tickets). Worthwhile but a separate phase — needs a clustering
  pass and a clear policy on when to merge vs keep distinct.
- Connector emission of `source_authority`. Today the chunker assigns
  defaults from `source_type`. A future connector contract change
  could thread an authority hint per source — that's the right place
  for tenant-specific overrides.

## Further reading

- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — where raws are created
- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — `_normalize` flow that this design extends
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — embedding cost gate + HNSW pattern
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — where the rollup pattern will land
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the 4 KB / 8 KB / 2 KB truncation gaps this addresses
