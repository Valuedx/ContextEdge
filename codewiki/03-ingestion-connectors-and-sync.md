# Ingestion: connectors and sync

## Summary

You will understand how external systems (tickets, mail, chat, ITSM) are modeled as **sources**, how **connectors** are supposed to normalize vendor APIs into **ingestion events**, and how **sync jobs** persist **raw evidence** then hand off to normalization workers—including recovery when message brokers misbehave.

## Business picture

You connect each of your existing tools—Jira, ServiceNow, Teams, Gmail, or similar—once, and the platform handles ongoing data flow from that point forward. Each connection stores the credentials and settings needed to reach the external system. The product then periodically discovers new items, backfills historical data, or incrementally fetches recent changes—without manual intervention. Every fetched item receives a stable identifier so the same ticket update is never stored twice. Reliability is built in: if part of a sync is interrupted, the system remembers exactly where it left off and resumes automatically on the next run.

## Technical walkthrough

1. **Connector contract** — `BaseConnector` in `connectors/base.py` defines the adapter surface: `validate_credentials`, `discover_objects`, `backfill`, `fetch_changes`, and `hydrate_thread`, plus shared types (`IngestionEvent`, `Checkpoint`, `BackfillResult`, `ChangeResult`, `HydratedThread`). Concrete implementations live under `connectors/<vendor>/connector.py`. Each connector must emit `IngestionEvent.thread_id` values in the compound format that its own `hydrate_thread(thread_ref)` can parse — this ensures the normalization pipeline creates `Thread` rows with an `external_thread_id` that hydration can later resolve.

2. **Registry** — `get_connector(source_type, source_config, credentials)` in `connectors/registry.py` maps a string `source_type` to a class. `_register_connectors()` lazily imports and registers `teams`, `gmail`, `servicenow`, and `jira_sm`. New connector types need a module plus an entry in `CONNECTOR_CLASSES`.

3. **Credentials and discovery** — `sync_worker_service` loads the active `SourceCredential`, decrypts payload via `source_service.decrypt_credentials`, and builds a connector. `run_discovery_job` creates a `SyncRun`, calls `discover_source_objects`, and records status. `run_backfill_job` and `run_incremental_job` drive `backfill` / `fetch_changes` over date windows and checkpoints.

4. **Persist raw, then queue** — Connector output is turned into `IngestionEvent`-like objects; `persist_ingestion_events` (see [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md)) inserts `RawEvidenceObject` rows. After **commit**, `queue_normalize_raw_objects` in `sync_ingestion_queue.py` calls `normalize_evidence.delay` for each new raw id. `NormalizeEnqueueError` carries the **tail** of IDs that were not yet enqueued so callers can record them for retry.

5. **Handoff recovery** — `_claim_pending_raw_ids_for_handoff` and related helpers in `sync_worker_service.py` merge newly created raw IDs with any **pending** IDs stored on `SourceObject.metadata_extra` under keys such as `pending_normalize_raw_ids`, under row lock, so recovery uses the same content hash logic as normalization and does not loop on deduplicated raws.

6. **Sync run API** — `api/v1/sync.py` lists and fetches `SyncRun` rows tenant-scoped. **Retry** (`POST .../retry`) requires `domain_admin`, checks run status, and enqueues `run_incremental_sync` in `workers/sync_tasks.py`, which runs `run_incremental_job` inside `run_async`.

7. **Sources API** — `api/v1/sources.py` (with `source_service`) manages source CRUD, credentials, and **backfill** via `run_backfill.delay(...)` per selected source object.

8. **Thread ref format contract** — Each connector's `thread_id` must be a compound string that its own `hydrate_thread` can parse. The formats are:

   | Connector | `thread_id` format | Example |
   | --- | --- | --- |
   | Gmail | `email:gmailThreadId` | `shared@acme.com:18abcdef123` |
   | Teams | `teamId:channelId:messageId` | `team-001:chan-002:msg-003` |
   | ServiceNow | `tableName:sys_id` | `incident:abc123def456` |
   | Jira SM | `issueKey` | `PROJ-123` |

   The normalization worker uses `_thread_id` from the raw payload to create `Thread` rows (via `ensure_thread_for_evidence` in `evidence_normalization.py`), and the hydration worker passes `Thread.external_thread_id` directly to `connector.hydrate_thread()`.

Residual caveats (sync queue in prod, dedupe): see [KNOWN_GAPS.md](./KNOWN_GAPS.md).

## Example: Acme VPN data at this stage

**Input — Jira connector fetches changes for Acme's approved source**

The connector calls Jira's API for ticket updates since the last checkpoint and returns ingestion events:

```json
[
  {
    "external_id": "JIRA-4521",
    "source_type": "jira_sm",
    "title": "VPN connection drops after Windows update KB5032190",
    "body": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED.",
    "author": "jsmith@acme.com",
    "created_at": "2026-03-15T09:23:00Z",
    "metadata": { "priority": "high", "project": "IT-OPS" }
  },
  {
    "external_id": "JIRA-4522",
    "source_type": "jira_sm",
    "title": "Cannot connect to corporate VPN after Tuesday update",
    "body": "Same VPN issue as JIRA-4521. Multiple users affected in Building 3.",
    "author": "mjones@acme.com",
    "created_at": "2026-03-15T09:45:00Z",
    "metadata": { "priority": "high", "project": "IT-OPS" }
  }
]
```

**Processing — persist and enqueue**

Each event becomes a `RawEvidenceObject` row. Content hashes are computed; if JIRA-4522 has identical text to a prior fetch, it is deduplicated. The checkpoint advances to the latest timestamp so the next sync starts where this one left off.

**Output — sync run record**

```json
{
  "sync_run_id": "run-5c8d2e",
  "source_id": "src-jira-01",
  "tenant_id": "acme-corp",
  "status": "completed",
  "events_received": 2,
  "events_persisted": 2,
  "events_deduplicated": 0,
  "checkpoint": "2026-03-15T09:45:00Z",
  "completed_at": "2026-03-15T10:06:12Z"
}
```

After commit, `normalize_evidence` tasks are queued for each new raw record, handing off to the evidence normalization pipeline.

## Design decisions

- **Adapter + registry pattern** — *Why:* each vendor API differs; the rest of the stack speaks `IngestionEvent`. *Tradeoff:* every new system needs a connector class and registry entry.

- **Commit-then-enqueue** — *Why:* workers only see committed rows, avoiding races. *Tradeoff:* a short window exists where raw exists but normalize is not queued unless recovery paths run.

- **Pending raw IDs on `SourceObject`** — *Why:* survives partial task-queue publish failures without silent loss. *Tradeoff:* metadata keys become part of the operational contract; migrations or tools must respect them.

- **Sync runs as first-class rows** — *Why:* operators see history, failures, and counts in the UI/API. *Tradeoff:* retention of run rows may need policy over time.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Connector interface | `backend/src/contextedge/connectors/base.py` | `BaseConnector`, `IngestionEvent`, `HydratedThread` | Connector implementations |
| Registry | `backend/src/contextedge/connectors/registry.py` | `get_connector`, `supported_source_types` | Sync worker loads connector |
| Thread linking | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalization worker |
| Sync orchestration | `backend/src/contextedge/services/sync_worker_service.py` | `run_discovery_job`, `run_backfill_job`, `run_incremental_job`, `_claim_pending_raw_ids_for_handoff` | Celery tasks / tests |
| Enqueue normalize | `backend/src/contextedge/services/sync_ingestion_queue.py` | `queue_normalize_raw_objects`, `NormalizeEnqueueError` | After transaction commit |
| Celery sync tasks | `backend/src/contextedge/workers/sync_tasks.py` | `discover_source`, `run_backfill`, `run_incremental_sync` | **sync** queue |
| Sync API | `backend/src/contextedge/api/v1/sync.py` | `list_all_sync_runs`, `retry_sync_run` | HTTP |
| Source helpers | `backend/src/contextedge/services/source_service.py` | `discover_source_objects`, `create_sync_run`, `decrypt_credentials` | Discovery/sync flows |

## Acme VPN incident (this layer)

Acme's **Jira** source incrementally pulls three duplicate VPN incident tickets; each becomes an `IngestionEvent` with distinct or shared external ids; `persist_ingestion_events` dedupes exact content hashes; pending raw IDs are claimed and `normalize_evidence` tasks are queued so Teams messages ingested minutes later join the same normalization pipeline.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — raw vs `EvidenceItem`  
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — worker processes and queues  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — task routing  
