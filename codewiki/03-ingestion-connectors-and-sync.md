# Ingestion: connectors and sync

## Summary

You will understand how external systems (tickets, mail, chat, ITSM) are modeled as **sources**, how **connectors** normalize vendor APIs into **ingestion events**, and how **sync jobs** persist **raw evidence** — including the object-storage offload for large payloads — then hand off to normalization workers, with recovery when message brokers misbehave and cooperative pause/cancel for runs an operator needs to stop.

## Business picture

You connect each of your existing tools — Jira, ServiceNow, Teams, Gmail, Zoho Desk, or similar — once, and the platform handles ongoing data flow from that point forward. Each connection stores the credentials and settings needed to reach the external system. The product then periodically discovers new items, backfills historical data, or incrementally fetches recent changes — without manual intervention. Every fetched item receives a stable identifier so the same ticket update is never stored twice. Reliability is built in: if part of a sync is interrupted, the system remembers exactly where it left off and resumes on the next run. And because a long backfill can cost real money downstream (every ticket is classified and embedded), operators can pause or cancel a running sync without losing what was already fetched, and can tell the system which tickets to enrich *first* — the resolved ones with root causes, which are the ones the product exists to learn from.

## Technical walkthrough

1. **Connector contract** — `BaseConnector` in `connectors/base.py` defines the adapter surface: `validate_credentials`, `discover_objects`, `backfill`, `fetch_changes`, and `hydrate_thread`, plus shared types (`IngestionEvent`, `Checkpoint`, `BackfillResult`, `ChangeResult`, `HydratedThread`) (backend/src/contextedge/connectors/base.py:78-141, 7-68). Note `fetch_changes` takes a **non-optional** `Checkpoint` (base.py:128-134) — see step 12. The base class also carries the cooperative-stop hook: the sync job installs a callback with `set_control_check()`, and connectors call `await self._check_control()` inside their own loops; the default is a no-op and the check never raises (base.py:82-107). Each connector must emit `IngestionEvent.thread_id` values in a format its own `hydrate_thread(thread_ref)` can parse — the normalization pipeline creates `Thread` rows with that string as `external_thread_id`, and hydration later gets it back verbatim.

2. **Registry and catalog** — `get_connector(source_type, source_config, credentials)` in `connectors/registry.py` lazily registers **seven** classes: `teams`, `gmail`, `servicenow`, `jira_sm`, `manageengine`, `sapphireims`, `zoho_desk`; unknown types raise `ValueError` (backend/src/contextedge/connectors/registry.py:91-122). The source-creation UI's picker comes from `source_type_catalog()`, which iterates the display table `_SOURCE_TYPE_LABELS` and reads `connector_available` *from the registry* rather than from a hand-kept flag — so a label can never claim a connector that does not exist (registry.py:24-88). `confluence`, `sharepoint`, and `exchange` are catalog-only with status `planned` (registry.py:63-65). A new connector needs four edits: the connector module, a `CONNECTOR_CLASSES` entry (registry.py:100-110), a display row in `_SOURCE_TYPE_LABELS` (registry.py:37-66), and a matching alternative in the `SourceCreate.source_type` pattern (backend/src/contextedge/schemas/source.py:10-16) — miss the last one and the API rejects the source before the registry is ever consulted. The other direction is what the tests guard: because the catalog iterates the label table, a connector registered without a label row would be invisible in the picker, which is exactly how Zoho Desk and SapphireIMS were once unreachable. `tests/test_source_type_catalog.py` fails on that, and on a catalog entry the schema would reject.

3. **Credentials and discovery** — `run_discovery_job` creates a `SyncRun(run_type="discovery")` and calls `discover_source_objects` (backend/src/contextedge/services/sync_worker_service.py:45-65). Sync jobs load the connector via `_load_connector`: the active `SourceCredential` is decrypted with Fernet (`decrypt_credentials`) and handed to `get_connector` (sync_worker_service.py:68-80).

4. **Entry points and single-flight lock** — three ways a sync starts: Celery Beat's `sync.trigger_scheduled_syncs` every 900s dispatches one `run_incremental_sync` per `approved_for_sync` object (backend/src/contextedge/workers/sync_tasks.py:13-32; schedule at workers/celery_app.py:292-295); `POST /api/v1/sources/{id}/backfill` dispatches `run_backfill.delay(...)` per selected object (backend/src/contextedge/api/v1/sources.py:408-410); and approving an object for sync triggers an immediate incremental run (sources.py:280-282). Both job bodies first take a **transaction-scoped Postgres advisory lock** — `pg_try_advisory_xact_lock(hashtext("sync:<object_id>"))` — and a second worker returns `{"status": "skipped_locked"}` instead of racing checkpoint writes; transaction scope means a crashed worker cannot leak the lock (sync_worker_service.py:379-395, 427-433, 532-538).

5. **Backfill job, step by step** — `run_backfill_job` (sync_worker_service.py:419-523): lock → load `SourceObject` (tenant + source scoped), refuse unless `approved_for_backfill` (434-446) → create the `SyncRun` and stamp `run.celery_task_id` as the revocation escape hatch for a wedged worker (452-456) → load the newest `SyncCheckpoint` by `captured_at` (459-467) → window = now − `window_days` (default 90; workers/sync_tasks.py:41-47) → install the control callback (476, see step 7) → `await connector.backfill(...)` → `persist_ingestion_events` (step 8) → `run.status = finalize_status(control)` (`completed`, or `paused`/`cancelled` if the operator signalled — a stop still persists everything fetched) and ingestion counts land in `run.errors["ingestion"]` (479-498) → append a new `SyncCheckpoint` row if the connector returned one; `last_successful_sync_at` is stamped only on `completed` (499-508). Any exception marks the run `failed` with `errors={"message": ...}` (509-513). Only a `completed` run proceeds to the normalization handoff (515-523).

6. **Incremental job** — `run_incremental_job` (sync_worker_service.py:526-637) has the same shape with four differences: the gate is `approved_for_sync` (550-551); it calls `fetch_changes` (598); it installs **no** control callback, because an incremental tick is short enough that the between-invocation boundary is responsive on its own (contrast 476 on the backfill path); and **no checkpoint means skip, not a first full pull** (571-595). Two statuses come out of that skip and they are not the same. The `sync_runs` row is written `completed`, with `errors={"skipped": "no checkpoint yet — run a backfill for this object first"}`; only the task's *return value* says `{"status": "skipped_no_checkpoint"}` (583-595). So filtering `sync_runs` by status will never surface these — look for the `errors.skipped` key, or for the `sync.incremental_skipped_no_checkpoint` log line (590-594). The reasoning is in the code: `fetch_changes` dereferences its checkpoint in every connector, so passing `None` used to crash every run; and treating it as a full pull would silently ingest — and pay to extract — a source's whole history on a schedule nobody associated with a backfill.

7. **Pause / resume / cancel (cooperative, migration 0069)** — `POST /api/v1/sources/{source_id}/sync/control` with `{action: pause|resume|cancel}` requires `domain_admin` (api/v1/sources.py:295-312). Pause/cancel set `metadata_extra["sync_paused"]` and, if a run is active, write `sync_runs.control` via `signal_run` (sources.py:329-352; backend/src/contextedge/services/sync_control_service.py:64-94). A running **backfill** polls the signal per page and every 25 detail records through the callback built by `control_check_for` — which reads on a **fresh session**, because the job's own transaction was opened before the operator pressed anything and cannot see the write (sync_control_service.py:97-122). Both stops keep what was already fetched, with its checkpoint — cancel is not a rollback (sync_control_service.py:14-25). `resume` only clears the gate: the paused run has already ended and persisted, and the next run continues from the checkpoint (sources.py:331-337). Every control action is audited as `sync.<action>` (sources.py:354-363).

8. **Persist raw — with MinIO offload** — `persist_ingestion_events` (backend/src/contextedge/services/ingestion_persistence.py:19-91) is shared by sync jobs and thread hydration. Per event: (a) the payload is `event.content` plus injected `_`-prefixed keys — `_connector_source_type`, `_connector_object_type`, `_connector_metadata`, optional `_thread_id` and `_source_timestamp` — which downstream typing, lifecycle, and noise-filter logic key on (42-51); (b) `content_hash` = sha256 of the canonical JSON of `{external_id, body}` (53-58); (c) duplicates on `(tenant_id, source_id, external_id, content_hash)` are counted and skipped, no row (60-72); (d) the `RawEvidenceObject` is inserted and flushed to get its id (74-83); (e) **payloads over `OFFLOAD_THRESHOLD_BYTES` = 32,768** are uploaded to MinIO at `raw/{tenant_id}/{raw_id}.json` and the DB column is replaced by the stub `{"_offloaded": true, "size_bytes": N}`, with `object_storage_key` recording the location (84-87; backend/src/contextedge/services/object_store.py:50-59). Consequence worth memorizing: **any SQL that filters or reads `raw_payload` sees only the stub for offloaded rows** — and the biggest payloads are exactly the longest conversations. Ingest-priority ordering sorts them last (step 9), and ad-hoc SQL backfills over payload fields silently skip them ([KNOWN_GAPS.md](./KNOWN_GAPS.md), knowledge-lifecycle entry). The function returns `(created, skipped_duplicate, new_raw_ids)`; the **caller** commits and enqueues.

9. **Handoff to normalization — crash-safe pending ledger, priority-ordered** — `_commit_and_queue_normalization` (sync_worker_service.py:301-376) runs only for completed runs:
   1. `_claim_pending_raw_ids_for_handoff` locks the `SourceObject` row (`SELECT ... FOR UPDATE`), merges any `metadata_extra["pending_normalize_raw_ids"]` left by a previous failed enqueue with this run's new ids, filters out ids already normalized — by `raw_object_ref` **and** by content hash, so recovery cannot loop on deduplicated raws (`_filter_already_normalized_raw_ids`, 176-233) — clears the ledger, commits, and returns the claim plus the object's ingest priority (273-298).
   2. `order_raw_ids_by_priority` re-orders the handoff (backend/src/contextedge/services/ingest_priority.py:56-127). The mode lives in `metadata_extra["ingest_priority"]` — `none` (default, arrival order), `resolution_first` (rows whose payload has a non-empty `resolution` — a Zoho resolution note, a Jira resolution name — then the longest conversations, which is what a learning corpus wants), `threads_desc`, `threads_asc` (ingest_priority.py:36-53); the sources API writes it without a migration (api/v1/sources.py:258-262). Ordering is SQL over `raw_payload` (`thread_count`/`comment_count`, presence of `resolution`; ingest_priority.py:76-95) and fail-soft: an error returns the original list, and ids are never added or dropped (63-69, 108-120). Offloaded rows sort to the back regardless of mode — their stub carries neither key.
   3. `queue_normalize_raw_objects` dispatches one `normalize_evidence.delay(raw_id, tenant_id)` per id, **after commit** so workers see committed rows (backend/src/contextedge/services/sync_ingestion_queue.py:16-30).
   4. Failure path: `NormalizeEnqueueError` carries the not-yet-enqueued **tail** of ids (sync_ingestion_queue.py:8-30); those are written back to `metadata_extra["pending_normalize_raw_ids"]`, the run flips to `failed` with an `errors["handoff"]` blob (`message: normalize_enqueue_failed`, counts, the pending ids), and the exception re-raises so Celery retries the sync task; the next successful run's claim step re-drains the ledger (sync_worker_service.py:322-353). Any other enqueue exception parks the *entire* list the same way (354-376).

10. **What normalization fans out to (the boundary of this chapter)** — `extraction.normalize_evidence` runs the full pipeline described in [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md); after its transaction commits, the task wrapper dispatches the follow-ups (backend/src/contextedge/workers/extraction_tasks.py:1300-1354): attachments → `artifact.extract_attachment` each; otherwise `extraction.correlate_evidence` + `extraction.compute_evidence_baseline` (both on the `correlation` queue); chunking runs inline for small allow-listed bodies or via `extraction.chunk_evidence`, and chunk embedding via `extraction.embed_chunks_batch` (both on the `embedding` queue) (extraction_tasks.py:54-62, 73-119; workers/celery_app.py:256-268). **Auto-hydration**: if the payload carried `_thread_id`, the record is not itself a hydrated message, and it was not deduped, the wrapper dispatches `hydration.hydrate_thread` (extraction_tasks.py:610-615, 1336-1351) — hydrated messages then loop back through `persist_ingestion_events` and `normalize_evidence` exactly once (workers/hydration_tasks.py:147-158, 197-203; the parent-only predicate is what prevents a 10-50× re-hydration amplification). Operational consequence: the worker fleet must consume all **eight** queues — `default,sync,hydration,extraction,correlation,embedding,pattern,evaluation` (backend/dev.py:16). The `correlation` and `embedding` lanes are newer than the RUNBOOK's worker examples; a fleet started from an old command list silently starves the graph and retrieval lanes (celery_app.py:234-268).

11. **Sync run API** — `GET /api/v1/sync-runs` lists tenant-scoped runs with status/type filters; `POST /api/v1/sync-runs/{run_id}/retry` requires `domain_admin`, accepts only `failed`/`dead_letter` runs, and dispatches by `run.run_type` — `run_backfill.delay(...)` for backfills, else `run_incremental_sync.delay(...)` (backend/src/contextedge/api/v1/sync.py:13-63). Delete/purge endpoints exist for run-log hygiene, also `domain_admin` (sync.py:64-86). Task shells: `sync.run_backfill` retries 3× at 120s, `sync.run_incremental_sync` 5× at 30s, both on the `sync` queue (workers/sync_tasks.py:35-81; route at celery_app.py:227).

12. **Thread ref format contract** — each connector's `thread_id` must be a string its own `hydrate_thread` can parse. Verified formats:

    | Connector | `thread_id` format | Example | Cite |
    | --- | --- | --- | --- |
    | Gmail | `userEmail:gmailThreadId` | `shared@acme.com:18abcdef123` | connectors/gmail/connector.py:215 |
    | Teams | `teamId:channelId:messageId` | `team-001:chan-002:msg-003` | connectors/teams/connector.py:190 |
    | ServiceNow | `tableName:sys_id` | `incident:abc123def456` | connectors/servicenow/connector.py:319 |
    | Jira SM | `kind:issueKey` (kind-prefixed) | `incident:PROJ-123` | connectors/jira_sm/connector.py:293 |
    | SapphireIMS | `recordKind:ticketId` | `incident:INC-4021` | connectors/sapphireims/connector.py:218 |
    | Zoho Desk | `zoho_ticket:id` / `zoho_article:id` | `zoho_ticket:1892000000123456` | connectors/zoho_desk/connector.py:1107 |
    | ManageEngine | *(none emitted)* | — | connectors/manageengine/connector.py:137-182 |

    ManageEngine is the deliberate exception: its events carry no `thread_id` because worklogs and notes are fetched during sync and embedded *inside* the ticket payload (manageengine/connector.py:131-180), so no `Thread` row is created and auto-hydration never dispatches for it (its `hydrate_thread` exists but nothing calls it automatically). For everyone else, the normalization worker uses `_thread_id` from the raw payload to create `Thread` rows via `ensure_thread_for_evidence` (backend/src/contextedge/services/evidence_normalization.py:155), and the hydration worker hands the thread-ref string it was dispatched with straight to `connector.hydrate_thread()`, then loads the matching `Thread` row by `(external_thread_id, source_id, tenant_id)` to update it (workers/hydration_tasks.py:54, 56-64). It is the same string end to end, which is why the format is a per-connector contract rather than a shared convention. Threads are created lazily by normalization, so hydrating before normalize has processed the parent 404s at the API — by design ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Thread hydration requires normalization to run first").

13. **Backfill-to-incremental checkpoint bridging** — every connector seeds a usable checkpoint when backfill finishes, so incremental sync can start without manual intervention. The shapes, verified:
    - **Gmail**: mid-backfill pages checkpoint as `{"page_token"}`; the final page fetches the mailbox profile and stores `{"history_id"}` (gmail/connector.py:227-230). Caveat — and the tracker describes this one wrongly. An *interrupted* backfill leaves only a `page_token`. The next incremental tick does **not** fail: `fetch_changes` finds no `history_id`, reads a current one from the mailbox profile, and returns zero events (gmail/connector.py:249-254). The run appends that as the newest checkpoint, which overwrites the resume token, so the next backfill starts again from page one — the raw-layer content-hash dedupe absorbs the repeats — and mail older than the freshly seeded `history_id` that was never paged arrives only when that backfill re-walks it. [KNOWN_GAPS.md](./KNOWN_GAPS.md)'s Gmail entry still says "incremental sync will fail"; the code is the authority.
    - **Teams**: mid-backfill `{"skip_token"}` or `{"next_link"}`; the final page fetches a fresh delta link via `/messages/delta` and stores `{"delta_link"}` (teams/connector.py:202-207).
    - **ServiceNow**: mid-backfill `{"offset"}`; final page stores the **compound** `{"last_updated", "last_sys_id"}` keyset cursor that incremental paginates on — never `sysparm_offset`, which skips rows on a live table (servicenow/connector.py:329, 345-347, 373-377).
    - **Jira SM**: mid-backfill `{"start_at"}`; final page `{"last_updated"}`, consumed as a JQL minute cursor with a 30-minute overlap rewind (jira_sm/connector.py:335-344, 362-365).
    - **Zoho Desk**: budget-exhausted backfills checkpoint `{"offset"}` only, so a partial sweep can never seed incremental; completion writes `{"last_updated", "last_ids"}` — a timestamp plus the ids already emitted at it (zoho_desk/connector.py:1006-1019).
    - **SapphireIMS**: `{"last_updated"}` max-seen cursor on both paths (sapphireims/connector.py:365, 385).
    - **ManageEngine**: `{"start_index", "last_sync"}` on backfill, `{"last_sync"}` on incremental — a simple wall-clock cursor (manageengine/connector.py:186-192, 272-275).

14. **Checkpoint shapes are per-connector, and not interchangeable.** The compound `(timestamp, id)` keyset is correct for ServiceNow *because* ServiceNow honors `ORDERBY` across both columns. Zoho Desk does not: it sorts by `modifiedTime` descending and returns tied records id-**ascending**, so the same pattern would stop mid-tie and skip the tail of a bulk edit. Before copying a checkpoint strategy into a new connector, verify the vendor's actual ordering guarantee against a live instance — see [ZOHO_DESK_CONNECTOR.md](./ZOHO_DESK_CONNECTOR.md) for the full worked example.

15. **ServiceNow specifics learned against a live instance** (`connectors/servicenow/connector.py`):
    - **Discovery survives unavailable tables.** `em_alert` ships with ITOM Event Management, which a stock instance does not activate, and the Table API answers 400 for a table that does not exist. Discovery skips a table on 400/403/404, logs `servicenow.table_unavailable` (servicenow/connector.py:257), and returns the rest — one missing optional plugin no longer makes a healthy instance offer zero objects.
    - **`table_filters` scopes a sync server-side.** `source_config["table_filters"]` applies raw encoded-query syntax per table (e.g. `{"incident": "priority<=2"}`), built by `_table_extra_query` and appended to **every** branch of the sysparm query — the incremental query has two branches joined by `^NQ`, and a filter on only the first leaks records through the boundary branch (servicenow/connector.py:137-174; applied at 294 and 393-394). Filtered records never leave ServiceNow, so they cost no extraction and no storage.
    - **The request side is ingested: `sc_req_item` (RITM) and `sc_task`.** A RITM carries an *empty* `short_description` — its subject is the catalog item, dot-walked in as `cat_item.name`. `_with_derived_title` promotes it on both the backfill and incremental paths (servicenow/connector.py:318, 437, 560-579), or every requested item lands as "Untitled Evidence". `sc_request` (the REQ header) is deliberately **not** ingested: it is an envelope with no subject of its own — verified live, it normalises to "Untitled Evidence" — and each RITM carries `request.number`, so the parent stays recoverable without paying to classify empty records (servicenow/connector.py:76-95).

Residual caveats (a worker must consume the `sync` queue; blob lifecycle; offloaded rows invisible to SQL): see [KNOWN_GAPS.md](./KNOWN_GAPS.md).

## Example: Acme VPN data at this stage

**Input** (what arrives — the Jira connector fetches changes for Acme's approved source):

The connector calls Jira's API for issue updates since the `last_updated` checkpoint and returns ingestion events:

```json
[
  {
    "external_id": "JIRA-4521",
    "source_type": "jira_sm",
    "thread_id": "incident:JIRA-4521",
    "content": {
      "title": "VPN connection drops after Windows update KB5032190",
      "description": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED.",
      "reporter": "jsmith@acme.com",
      "priority": "high"
    },
    "timestamp": "2026-03-15T09:23:00Z"
  },
  {
    "external_id": "JIRA-4522",
    "source_type": "jira_sm",
    "thread_id": "incident:JIRA-4522",
    "content": {
      "title": "Cannot connect to corporate VPN after Tuesday update",
      "description": "Same VPN issue as JIRA-4521. Multiple users affected in Building 3.",
      "reporter": "mjones@acme.com",
      "priority": "high"
    },
    "timestamp": "2026-03-15T09:45:00Z"
  }
]
```

**Processing — persist and enqueue**

Each event becomes a `RawEvidenceObject` row with injected `_connector_*` keys and a content hash; if JIRA-4522's payload is byte-identical to a prior fetch, it is skipped as a duplicate. A payload over 32 KB (a long comment thread) would be offloaded to MinIO and leave only a stub in Postgres. After the run commits, one `normalize_evidence` task is queued per new raw id, ordered by the object's `ingest_priority`.

**Output** (what the system produces — the `sync_runs` row and its checkpoint):

```json
{
  "run_id": "run-5c8d2e",
  "run_type": "incremental",
  "status": "completed",
  "items_processed": 2,
  "errors": {
    "ingestion": { "raw_objects_created": 2, "raw_objects_deduped": 0 }
  },
  "completed_at": "2026-03-15T10:06:12Z"
}
```

```json
{ "checkpoint_data": { "last_updated": "2026-03-15T09:45:12.000+0000" } }
```

The checkpoint is an append-only `sync_checkpoints` row (latest by `captured_at` wins), storing the raw Jira `updated` stamp; the next tick converts it to a JQL minute cursor with an overlap rewind (jira_sm/connector.py:362-372). The ingestion counts live in `run.errors["ingestion"]`, not in separate columns.

## Design decisions

- **Adapter + registry pattern, with the picker computed from the registry** — *Why:* each vendor API differs; the rest of the stack speaks `IngestionEvent`, and computing the UI catalog from the registry ended the era when Confluence was offered but could not sync while Zoho Desk could sync but was not offered (registry.py:24-36). *Tradeoff:* every new system needs a connector class, a `CONNECTOR_CLASSES` entry, a `_SOURCE_TYPE_LABELS` row, and a schema-pattern alternative — four places, with `test_source_type_catalog.py` holding the last three together in both directions (a registered connector missing from the catalog, and a catalog entry the schema would reject, both fail the suite).

- **Commit-then-enqueue** — *Why:* workers only see committed rows, avoiding a race where a normalize task fires before its raw row is visible. *Tradeoff:* a short window exists where raw rows exist but normalize was not queued — which is exactly what the pending-ledger recovery path exists to drain.

- **Pending raw IDs on `SourceObject`, filtered by hash on reclaim** — *Why:* survives partial task-queue publish failures without silent loss, and the content-hash filter stops recovery from re-enqueuing work that already normalized under a different raw id. *Tradeoff:* `metadata_extra["pending_normalize_raw_ids"]` becomes part of the operational contract; tools and migrations must respect it.

- **Cooperative pause instead of task revocation** — *Why:* a backfill spends its time inside *one* `connector.backfill()` call (measured 913 seconds on the live Zoho corpus) and writes nothing until it returns; killing the worker would discard everything fetched, while the in-loop signal stops within a page or 25 detail records and persists records plus checkpoint (sync_control_service.py:1-26). *Tradeoff:* the stop is only as responsive as the connector's loop granularity, and connectors must be taught to poll — an untaught connector ignores the button. `celery_task_id` on the run is the escape hatch for a wedged worker (sync_worker_service.py:452-456).

- **Offload large raw payloads to object storage** — *Why:* multi-hundred-KB conversation payloads do not belong in a hot Postgres table; the stub keeps the row while MinIO keeps the bytes (ingestion_persistence.py:84-87). *Tradeoff:* every SQL reader of `raw_payload` silently sees the stub — ingest prioritization cannot rank offloaded tickets, and payload-field backfills skip precisely the longest records. Code that needs the real payload must go through `load_raw_payload`.

- **Sync runs as first-class rows** — *Why:* operators see history, failures, counts, and the handoff-recovery blob in the UI/API; retry is a button, not a shell session. *Tradeoff:* run-log retention needs housekeeping — hence the delete/purge endpoints (api/v1/sync.py:64-86).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Connector interface + control hook | `backend/src/contextedge/connectors/base.py` | `BaseConnector`, `IngestionEvent`, `set_control_check` | Connector implementations |
| Registry + UI catalog | `backend/src/contextedge/connectors/registry.py` | `get_connector`, `source_type_catalog` | Sync worker loads connector; picker renders |
| Sync orchestration | `backend/src/contextedge/services/sync_worker_service.py` | `run_backfill_job`, `run_incremental_job`, `acquire_sync_lock`, `_claim_pending_raw_ids_for_handoff`, `_commit_and_queue_normalization` | Celery tasks |
| Raw persistence + offload | `backend/src/contextedge/services/ingestion_persistence.py` | `persist_ingestion_events`, `OFFLOAD_THRESHOLD_BYTES` | Sync + hydration |
| Object storage | `backend/src/contextedge/services/object_store.py` | `upload_raw` (`raw/{tenant}/{raw_id}.json`) | Payloads > 32 KB |
| Enrichment ordering | `backend/src/contextedge/services/ingest_priority.py` | `order_raw_ids_by_priority`, `INGEST_PRIORITIES` | Normalization handoff |
| Enqueue normalize | `backend/src/contextedge/services/sync_ingestion_queue.py` | `queue_normalize_raw_objects`, `NormalizeEnqueueError` | After commit |
| Pause/cancel/resume | `backend/src/contextedge/services/sync_control_service.py` | `signal_run`, `control_check_for`, `finalize_status` | Operator control + in-loop polling |
| Celery sync tasks | `backend/src/contextedge/workers/sync_tasks.py` | `trigger_scheduled_syncs`, `run_backfill`, `run_incremental_sync` | **sync** queue |
| Thread linking | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalization worker |
| Thread hydration | `backend/src/contextedge/workers/hydration_tasks.py` | `hydrate_thread`, `_hydrate` | **hydration** queue |
| Sync API | `backend/src/contextedge/api/v1/sync.py` | `list_all_sync_runs`, `retry_sync_run` | HTTP |
| Sources API | `backend/src/contextedge/api/v1/sources.py` | `trigger_backfill`, `control_sync` | HTTP |
| Source helpers | `backend/src/contextedge/services/source_service.py` | `discover_source_objects`, `create_sync_run`, `decrypt_credentials` | Discovery/sync flows |

## Acme VPN incident (this layer)

Acme's **Jira** source incrementally pulls the duplicate VPN incident tickets; each becomes an `IngestionEvent` with a kind-prefixed thread id (`incident:JIRA-4521`); `persist_ingestion_events` dedupes exact content hashes and would offload an oversized comment dump to MinIO; the claim step drains any ids a previous broker hiccup left behind; and with `ingest_priority=resolution_first` the ticket that already carries "Restarted IPSec on vpn-gw-east-01" is normalized ahead of the me-too reports — so the Teams messages ingested minutes later join a graph that already knows the fix.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — raw vs `EvidenceItem`, the noise gate, chunk dispatch
- [ZOHO_DESK_CONNECTOR.md](./ZOHO_DESK_CONNECTOR.md) — the connector whose API broke the shared assumptions
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — worker processes and queues (verify the queue list against `backend/dev.py:16`)
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — task routing and the queue-lane rationale
