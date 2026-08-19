# Retention and operational events

## Summary

You will understand how the two scheduled **retention** phases work — archive, then purge — how the **memory class** of a piece of evidence decides its window, how **legal hold** blocks both phases in SQL rather than after the fact, what the daily **orphan cleanup** sweep exists to reap, and how the append-only **operational events** stream records that any of it happened.

## Business picture

Organizations need to keep incident data long enough to learn from it, but not forever. Retention runs in two moves that mean different things to the business:

1. **Archive** is cheap and reversible: the record stops being treated as current, but it is still there, still searchable, and can be brought back. Nothing is lost.
2. **Purge** is the compliance move: after a grace period the content is either scrubbed in place (the record keeps its identity so existing citations don't break) or the row is genuinely deleted (right-to-erasure).

The window depends on what the record *is*. A one-line chat message that nobody ever linked to a person or a system ages out quickly. A ticket that resolved to a named engineer and a named gateway, or a knowledge-base article, is worth keeping much longer. The platform decides that automatically, so nobody has to tag records by hand.

**Legal hold** freezes specific items so they are never archived, purged, or deleted regardless of age — including through the operator-facing "delete" and "purge everything" buttons, which refuse rather than clear the hold.

And an append-only event timeline records what the system actually did — retention counts, session openings, executions, spend — so an auditor asking "did last night's cleanup run, and what did it touch?" has an answer that is not a log file.

## Technical walkthrough

### 1. Both phases run on Celery beat

Retention is **scheduled**, not an operator chore:

| Beat entry | Task | Cadence | Args |
| --- | --- | --- | --- |
| `retention-archive-daily` | `evaluation.apply_retention_archive` | 24 h | `("all",)` |
| `retention-purge-weekly` | `evaluation.purge_archived` | 7 d | `("all",)` |
| `cleanup-hard-deleted-daily` | `evaluation.cleanup_hard_deleted_evidence` | 24 h | `("all",)` |

All three are registered in `backend/src/contextedge/workers/celery_app.py:319-345`, route to the **evaluation** queue via the `evaluation.*` rule (`celery_app.py:272`), and carry `max_retries=1, default_retry_delay=300`.

> Two older notes in [KNOWN_GAPS.md](./KNOWN_GAPS.md) still say otherwise: "neither is wired into Celery Beat" (`codewiki/KNOWN_GAPS.md:211`) and a "Scheduled jobs that need wiring" list that names both retention tasks (`KNOWN_GAPS.md:213-217`). Both are superseded — by the newer entry at `KNOWN_GAPS.md:75` and, decisively, by the beat schedule itself. KNOWN_GAPS layers newer findings above older ones, so always prefer the newest entry and check the code.

Each task resolves its tenant list (`"all"` means every tenant), then loops with a per-tenant `try/except … rollback` so one bad tenant never stops the sweep (`backend/src/contextedge/workers/retention_tasks.py:32-35, 79-91, 112-125`).

### 2. Where the retention window comes from

`_tenant_retention_days` reads the **most recently updated active `TenantPolicy` of type `retention`** and takes `config.retention_days`; anything missing, non-numeric, or non-positive falls back to `settings.retention_default_days`, which is **365** (`workers/retention_tasks.py:38-65`; `backend/src/contextedge/config.py:216-219`).

One guard is worth knowing: a boolean value is rejected explicitly, because `bool` is an `int` subclass in Python and a config typo of `true` would otherwise mean a **one-day** retention window (`retention_tasks.py:56-58`).

> **Correction to older docs:** `Tenant.retention_defaults` (`backend/src/contextedge/models/tenant.py:22`, surfaced on `TenantCreate` / `TenantUpdate` at `backend/src/contextedge/schemas/tenant.py:27,33`) is **stored and read by nothing** — a repo-wide search finds no consumer. Setting it at provisioning time has no effect on retention. The window that actually applies comes from a `retention` **policy row**, or the deployment default.

### 3. Phase 1 — archive

`apply_retention_policy(db, tenant_id, retention_days, source_class=None)` (`backend/src/contextedge/services/retention_service.py:71-116`):

1. Select the tenant's evidence with `exclude_legal_hold()` **in the WHERE clause**, never as a post-filter (`retention_service.py:84-87`; the shared fragment lives at `backend/src/contextedge/services/evidence_filters.py:25-34`). Optional `source_class` narrows by `evidence_type`.
2. Classify each row's memory class and compare `ingested_at` against that class's cutoff.
3. Set `relevance_state = "archived"` on the expired rows and log `retention.applied` with per-class counts (`retention_service.py:99-116`).

Archived rows **stay in the database and stay searchable** — they still have their embeddings, and no search path filters on `relevance_state` unless a caller explicitly asks it to (`backend/src/contextedge/search/pg_fts.py:41`; a repo-wide search finds no other `relevance_state == "archived"` predicate outside retention itself). Archiving is "stop treating this as current," not "hide it."

**Memory classes and windows** (`backend/src/contextedge/services/memory_service.py:25-79`):

```
memory_retention_windows(base_retention_days):
  base       = max(1, int(base_retention_days))   # a 0 or negative window floors at 1 day
  short_term = base
  long_term  = max(base * 6, 180)   days
  reasoning  = max(base * 3, 90)    days
```

```
classify_evidence_memory_class(evidence):
  evidence_type in {kb_article, sop, documentation}  -> long_term
  canonical_entity_refs["identities"] is non-empty   -> long_term
  otherwise                                          -> short_term
```

Two things older docs got wrong here. First, classification looks at **evidence type and resolved identities** — not at pattern or episode links. Second, `reasoning` is a class used by the runtime memory context for decision and execution material (`memory_service.py:273-282`); **evidence classification only ever returns short or long term** (`memory_service.py:73-79`).

With the default base of 365 days: a ticket carrying resolved identities is long-term and archives after `max(365 × 6, 180)` = **2,190 days**; a drive-by chat message with no identities archives after **365 days**.

### 4. Phase 2 — purge

`purge_archived_evidence(db, *, tenant_id, archive_grace_days=30, mode="hard_delete", dry_run=False, limit=1000)` (`retention_service.py:139-147`; body through `:258`). The scheduled task reads the mode once from `settings.retention_purge_mode`, whose default is the conservative **`soft_purge`**, and passes it explicitly (`workers/retention_tasks.py:108, 114-116`; `config.py:212-215`).

> Mind the asymmetry: the **service's** own default is `hard_delete` (`retention_service.py:144`). Only the scheduled task consults the setting, and it is the only production caller in the repo — so a script or future route that calls the service without naming a mode deletes rows rather than scrubbing them. An unrecognised mode raises `ValueError` before anything is read (`retention_service.py:171-172`).

**Candidate selection** (`retention_service.py:177-197`): `relevance_state == "archived"`, not under legal hold, and `updated_at < now - DEFAULT_ARCHIVE_GRACE_DAYS` (**30**, `retention_service.py:66`). Ordered `updated_at ASC` so a backlog drains oldest-first across ticks — without the ORDER BY, `LIMIT` picks arbitrary rows and genuinely ancient records can linger forever. `limit_reached` in the return dict tells the caller another tick will find more.

`dry_run=True` returns `candidate_count` without mutating anything (`retention_service.py:199-206`). **Nothing in production passes it** — the beat task is the service's only caller and never sets it, so the pre-purge preview the docstring describes (`retention_service.py:158-160`) is a service capability, not a shipped admin screen.

**`hard_delete`** issues a real `DELETE` (`retention_service.py:210-211`). Foreign keys cascade to `attachment_artifacts`, `correlation_edges`, and `contradiction_scan_state`; `playbook_evidence_links.evidence_id` is `ON DELETE SET NULL` (migration `0027`) so the citation record survives as an audit trail with a null pointer. Chunks cascade with the parent. Use for right-to-erasure.

**`soft_purge`** keeps the row and destroys the content (`retention_service.py:212-224`): NULLs `embedding`, `body_text`, `body_summary`, `canonical_entity_refs` (which carries extracted person and service names in clear text), and `raw_object_ref` (so the object-storage blob can be lifecycle-reaped and a re-ingest cannot rehydrate the body), and sets `title = "[purged]"`. Then it **explicitly deletes the row's `evidence_chunks`** (`retention_service.py:226-242`) — chunks carry the same content and the same embeddings, and hard-delete's cascade does not apply when the parent row survives. After a soft purge the row still exists for reference integrity, but similarity search can no longer match it.

### 5. Legal hold

`sensitivity_label == "legal_hold"` is the single column, checked through one shared SQL fragment so new query authors have a one-line import rather than an open-coded comparison (`evidence_filters.py:1-34`). It is honoured in:

- retention archive (`retention_service.py:86`) and purge (`retention_service.py:182`);
- the single-item delete route, which returns **409** rather than deleting (`backend/src/contextedge/api/v1/evidence.py:508-512`);
- bulk delete, which refuses the **whole request** with 409 if any id is held — a hold that the delete button can clear is not a hold (`api/v1/evidence.py:363-368`);
- tenant-wide purge, which preserves held rows *and* the raw objects backing them, and records `legal_hold_preserved` in the audit event (`api/v1/evidence.py:419-441, 467-490`).

`apply_legal_hold(db, tenant_id=..., evidence_ids=[...])` sets the label and is **tenant-scoped**: an id belonging to another tenant is silently skipped rather than held or leaked (`retention_service.py:119-136`).

> **Honest gap:** `apply_legal_hold` has **no caller anywhere in the repository** — no route, no worker, no test. Placing a hold today means an operator setting `sensitivity_label` directly, or calling the service from a script. Everything that *reads* the hold is wired; only the write path is missing a surface.

### 6. Orphan cleanup after a hard delete

Hard delete deliberately leaves two classes of orphan, because neither has a foreign key to follow. The daily `evaluation.cleanup_hard_deleted_evidence` task reaps them (`backend/src/contextedge/workers/cleanup_tasks.py:1-223`):

- **Raw rows and their object-storage blobs.** `raw_evidence_objects` is not FK-connected to `evidence_items`, so the sweep finds raws no `evidence_items.raw_object_ref` points at, deletes the blob, then the row. A blob delete failure leaves the DB row in place so the next sweep retries — losing the row would strand the blob forever (`cleanup_tasks.py:50-97`).
- **Dangling graph edges.** `graph_edges.source_node_id` / `target_node_id` are plain UUIDs with no FK, so edges whose node type is `"evidence"` and whose id no longer exists are deleted from both sides (`cleanup_tasks.py:121-158`).

**Artifact blobs are a documented stub returning 0** (`cleanup_tasks.py:100-118`): the artifact rows cascade away with the evidence, and once they are gone there is no way to find their blobs by database scan. Operators should run an object-storage lifecycle rule against the `artifacts/` prefix.

The sweep writes a `retention.hard_delete_cleanup` operational event **only when it actually reaped something**, so a quiet night leaves no noise (`cleanup_tasks.py:185-192`).

### 7. Raw payloads in object storage

Connector payloads over `OFFLOAD_THRESHOLD_BYTES = 32_768` are written to MinIO under `raw/{tenant_id}/{raw_id}.json` and the inline `raw_payload` column keeps only the stub `{"_offloaded": true, "size_bytes": N}` (`backend/src/contextedge/services/ingestion_persistence.py:16, 84-87`).

Two consequences that belong in any retention conversation:

- **Any SQL that filters on `raw_payload` silently skips the biggest rows.** Backfill tasks that need payload fields deliberately skip offloaded rows rather than guess: the inline stub is still a `dict`, so deriving from it would return a *wrong* answer and overwrite a correct one — the opposite of a repair (`backend/src/contextedge/workers/evidence_typing_tasks.py:71-79`). That particular task is the pattern, not a runnable tool — see [08-workers-celery-queues.md](./08-workers-celery-queues.md) §1 for why its module never registers on a worker.
- **Offloaded blobs for *live* evidence have no TTL or garbage collection in code.** The application uploads and reads them, and only deletes them through the orphan sweep above — that is, only after the evidence row is gone. Blob retention for everything else relies on bucket lifecycle rules ([KNOWN_GAPS.md](./KNOWN_GAPS.md):220-222, "Object storage blobs are not lifecycle-managed in-app").

### 8. Operational events

`operational_events` is the append-only narration of what the system did (`backend/src/contextedge/models/events.py:13-61`). Columns: `tenant_id`, indexed `entity_type(80)` and `entity_id` (TEXT, so non-UUID subjects fit), nullable `session_id` FK, indexed `event_type(120)`, `occurred_at` (caller-supplied or now) alongside a server-defaulted `recorded_at`, `correlation_id`, `causation_id`, `actor_id`, and a JSONB `payload`.

`append_operational_event` is the only writer (`backend/src/contextedge/services/event_log_service.py:32-61`). The detail that makes the stream useful: `correlation_id`, `causation_id`, and `actor_id` **default from the request context** when the caller does not pass them (`event_log_service.py:54-56`). Because Celery signal handlers carry those ids from the HTTP request into the worker (see [08-workers-celery-queues.md](./08-workers-celery-queues.md) §10), one operator click and every event the resulting background work emitted share a correlation id.

`list_operational_events` filters by tenant, entity type/id, session, or correlation id, newest first by `recorded_at`, default limit 100 (`event_log_service.py:64-85`).

Retention-family event types: `retention.hard_delete_cleanup` (`cleanup_tasks.py:190`). The archive and purge passes currently log **structlog only** — `retention.applied` (`retention_service.py:109-115`) and `retention.purged` (`retention_service.py:245-251`) are log lines, not `operational_events` rows. If your audit story requires the nightly counts to be queryable in SQL, that is the gap to close.

Other families the stream carries include `llm.usage` and `llm.budget_warning`, `session.created` / `session.closed`, `execution.*`, `approval.*`, `identity.*`, `decision.*`, `episode.*`, `memory.pattern_promoted` / `memory.playbook_promoted`, `contradiction.*`, and `correlation.*`.

## Example: Acme VPN data at this stage

**Input — Acme's retention policy row** (a `TenantPolicy`, not `Tenant.retention_defaults`)

```json
{
  "policy_type": "retention",
  "name": "Standard IT retention",
  "is_active": true,
  "version": 3,
  "config": { "retention_days": 365 }
}
```

**Processing — the nightly archive pass resolves windows**

```
base = 365
short_term = 365 days
long_term  = max(365 * 6, 180) = 2190 days
```

| Evidence | Why that class | Class | Age | Window | Action |
| --- | --- | --- | --- | --- | --- |
| `ev-a1b2c3` — ServiceNow `INC0010427` | `canonical_entity_refs.identities` has `vpn-gw-east-01` + `jsmith` | long_term | 400 d | 2190 d | keep |
| `ev-kb-vpn-01` — "How the corporate VPN works" | `evidence_type = "kb_article"` | long_term | 900 d | 2190 d | keep |
| `ev-chat-77` — Teams "anyone else seeing this?" | no identities resolved | short_term | 400 d | 365 d | **archive** |
| `ev-legal-01` — engineer's RCA email | held | long_term | 400 d | — | **skipped (legal hold)** |

**Output — the archive task's return value**

```json
{ "tenants": 1, "archived": 83 }
```

**Processing — a week later, the purge pass**

`ev-chat-77` has sat at `relevance_state = "archived"` for more than 30 days, so it becomes a candidate. With the default `soft_purge` mode:

```json
{
  "id": "ev-chat-77",
  "title": "[purged]",
  "body_text": null,
  "body_summary": null,
  "embedding": null,
  "canonical_entity_refs": null,
  "raw_object_ref": null,
  "relevance_state": "archived",
  "evidence_chunks": "3 rows deleted"
}
```

**Output — the purge task's return value**

```json
{ "tenants": 1, "processed": 41, "mode": "soft_purge" }
```

**Output — what the daily cleanup sweep recorded, as an operational event**

```json
{
  "event_type": "retention.hard_delete_cleanup",
  "entity_type": "retention",
  "entity_id": null,
  "tenant_id": "acme-corp",
  "correlation_id": null,
  "payload": {
    "blob_count": 12,
    "raw_row_count": 12,
    "artifact_blob_count": 0,
    "edge_count": 27
  }
}
```

Two details in that row are worth reading closely. `artifact_blob_count` is `0` by construction, not by luck — see §6. And `correlation_id` is **null on a beat run**: the id is inherited from an HTTP request's context, and beat dispatches from a process where no request is bound, so there is nothing to inherit (`event_log_service.py:54-56`; the propagation chain is in [08-workers-celery-queues.md](./08-workers-celery-queues.md) §10). No API route dispatches any of the three retention tasks (search of `backend/src/contextedge/api`, 2026-08-19), so today every one of these events carries a null correlation id; the field earns its keep the moment an operator-triggered path exists. `entity_id` is null because the sweep names no single subject — the call passes `entity_type` and `payload` only (`cleanup_tasks.py:186-192`).

## Design decisions

- **Two phases, archive then purge** — *Why:* "stop surfacing this" is cheap and reversible; "remove it for compliance" is neither. The grace window between them is the recovery aperture, and the dry-run path lets an operator see the blast radius first. *Tradeoff:* two schedules instead of one, and a misconfigured grace window deletes more than intended — which is why a boolean `retention_days` is rejected outright rather than coerced (`retention_tasks.py:56-58`).

- **`updated_at` stands in for "archived at"** — *Why:* `updated_at` bumps when `relevance_state` flips to archived, so a day-accurate grace window needs no new column on a hot table (`retention_service.py:183-186`). *Tradeoff:* minute-accurate compliance would need an explicit `archived_at`; also, any *other* write to the row restarts the clock.

- **Oldest-first draining, deliberately** — *Why:* without `ORDER BY updated_at ASC`, `LIMIT 1000` picks arbitrary rows and the "backlog drains over several ticks" promise silently fails — ancient rows linger while recent archives get purged first (`retention_service.py:189-194`). *Tradeoff:* a sort on every tick, which is cheap next to the correctness it buys.

- **Soft purge is the default mode** — *Why:* some customers need content gone but refuse to break reference integrity — postmortems and playbook citations point at these ids. Scrubbing content while keeping the id serves that, and it is the safe default for an unattended weekly job (`config.py:212-215`). *Tradeoff:* more modes means more per-tenant policy decisions, and a soft-purged row still occupies a row.

- **Soft purge deletes the chunks explicitly** — *Why:* chunks hold the same text and the same embeddings as the parent; scrubbing only the parent would leave the content fully retrievable through chunk search, so "unrecoverable" would be false (`retention_service.py:226-242`). *Tradeoff:* one extra statement per purge batch, and re-chunking a soft-purged row is impossible by design.

- **Legal hold lives in the WHERE clause, in one shared fragment** — *Why:* a post-filter is one forgotten line away from shipping held content to an LLM, and that exact omission was found twice in review (episode reconstruction and the contradiction scan). One importable fragment makes `grep legal_hold` a complete call-site list (`evidence_filters.py:1-14`). *Tradeoff:* label semantics must never be reused for anything else, and there is still no UI to *apply* a hold (§5).

- **Orphans are reaped on a schedule, not inside the delete** — *Why:* `raw_evidence_objects` and `graph_edges` have no foreign keys to cascade through, and doing blob I/O inside a delete transaction would make retention fail on an object-storage hiccup. *Tradeoff:* for up to about 24 hours after a hard delete, the graph can hold edges to missing evidence and the bucket still holds the blob.

- **Memory class comes from rules, not from a per-row operator decision** — *Why:* nobody tags thousands of ingested records by hand, and the two signals used (knowledge type, resolved identities) are exactly the ones already computed during normalization. *Tradeoff:* an item whose identity extraction failed is classified short-term and ages out sooner than it should — the classification is only as good as the enrichment behind it.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Archive + purge service | `backend/src/contextedge/services/retention_service.py` | `apply_retention_policy` (71), `apply_legal_hold` (119), `purge_archived_evidence` (139), `DEFAULT_ARCHIVE_GRACE_DAYS = 30` (66) | Beat tasks |
| Scheduled tasks | `backend/src/contextedge/workers/retention_tasks.py` | `apply_retention_archive` (74), `purge_archived` (106), `_tenant_retention_days` (38) | evaluation queue, daily / weekly |
| Beat wiring | `backend/src/contextedge/workers/celery_app.py` | `retention-archive-daily` (336), `retention-purge-weekly` (341), `cleanup-hard-deleted-daily` (321) | Beat |
| Orphan cleanup | `backend/src/contextedge/workers/cleanup_tasks.py` | `cleanup_hard_deleted_evidence` (165), `_reap_orphan_raw_blobs` (50), `_reap_orphan_graph_edges` (121), `_reap_orphan_artifact_blobs` (100, stub) | evaluation queue, daily |
| Memory classes | `backend/src/contextedge/services/memory_service.py` | `classify_evidence_memory_class` (73), `memory_retention_windows` (64), `KB_LONG_TERM_TYPES` (28) | Retention + session events |
| Legal-hold predicate | `backend/src/contextedge/services/evidence_filters.py` | `exclude_legal_hold` (25) | Every content-shipping query |
| Hold-aware delete routes | `backend/src/contextedge/api/v1/evidence.py` | `bulk_delete_evidence` (316), `purge_evidence` (409), `delete_evidence` (494) | HTTP, `domain_admin` |
| Evidence model | `backend/src/contextedge/models/evidence.py` | `EvidenceItem` (`relevance_state`, `sensitivity_label`, `ingested_at`, `raw_object_ref`) | ORM |
| Raw offload | `backend/src/contextedge/services/ingestion_persistence.py` | `OFFLOAD_THRESHOLD_BYTES = 32_768` (16), offload branch (84-87) | Ingest |
| Object storage | `backend/src/contextedge/services/object_store.py` | `upload_raw`, `delete_object` | Ingest / cleanup |
| Retention settings | `backend/src/contextedge/config.py` | `retention_purge_mode = "soft_purge"` (215), `retention_default_days = 365` (219) | Import time |
| Operational events | `backend/src/contextedge/services/event_log_service.py` | `append_operational_event` (32), `list_operational_events` (64) | Throughout |
| Event model | `backend/src/contextedge/models/events.py` | `OperationalEvent` (13) | ORM |

## Acme VPN incident (this layer)

A year after the VPN outage closes, Acme's nightly archive pass sweeps the tenant. `INC0010427` and the knowledge-base article on the corporate VPN both classify long-term — the ticket because identity resolution attached `vpn-gw-east-01` and `jsmith` to it, the article because its `evidence_type` is `kb_article` — so both stay current for 2,190 days. The stray Teams messages nobody ever linked to a person or a CI are short-term, pass 365 days, and flip to `archived`. The engineer's root-cause email is under **legal hold** while Legal reviews the outage's liability, so it is excluded in the SQL of both passes and would also refuse the delete button. Thirty days later the weekly purge scrubs the archived chat fragments in place — title `[purged]`, body and embeddings gone, chunks deleted — while their ids survive so the episode that cited them still resolves. The daily cleanup sweep then reaps the raw blobs those rows no longer point at and writes one `retention.hard_delete_cleanup` event carrying the counts — with a null correlation id, because beat, not a person, started it.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — the fields retention reads, and where `canonical_entity_refs` is written
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — the beat entries, the evaluation queue, and correlation-id propagation
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — `audit_logs` versus `operational_events`, and which to use when
- [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) — where a `retention` policy is authored
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — running the workers that execute these sweeps
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — object-storage lifecycle, and the superseded "retention not on Beat" note
