# Ingestion: connectors and sync

## Summary

You will understand how external systems (tickets, mail, chat, ITSM) are modeled as **sources**, how **connectors** are supposed to normalize vendor APIs into **ingestion events**, and how **sync jobs** persist **raw evidence** then hand off to normalization workers—including recovery when message brokers misbehave.

## Business picture

Your organization connects Jira, ServiceNow, Teams, Gmail, or similar. Each connection is a **source** with credentials and configuration. The product periodically **discovers** what to pull, **backfills** history, or **incrementally** fetches changes. Every fetched item becomes an internal event with a stable **external id** so the same ticket update is not stored as infinite copies. Reliability matters: if enqueueing work fails mid-batch, the system should **remember** what still needs processing.

## Technical walkthrough

1. **Connector contract** — `BaseConnector` in `connectors/base.py` defines the adapter surface: `validate_credentials`, `discover_objects`, `backfill`, `fetch_changes`, and optional thread hydration types (`IngestionEvent`, `Checkpoint`, `BackfillResult`, `ChangeResult`). Concrete implementations live under `connectors/<vendor>/connector.py`.

2. **Registry** — `get_connector(source_type, source_config, credentials)` in `connectors/registry.py` maps a string `source_type` to a class. The registry lazily imports connector classes. **Note:** registering concrete classes in `CONNECTOR_CLASSES` is required for `get_connector` to succeed; if registration is commented, unknown types raise—operators should align config with registered types.

3. **Credentials and discovery** — `sync_worker_service` loads the active `SourceCredential`, decrypts payload via `source_service.decrypt_credentials`, and builds a connector. `run_discovery_job` creates a `SyncRun`, calls `discover_source_objects`, and records status. `run_backfill_job` and `run_incremental_job` drive `backfill` / `fetch_changes` over date windows and checkpoints.

4. **Persist raw, then queue** — Connector output is turned into `IngestionEvent`-like objects; `persist_ingestion_events` (see [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md)) inserts `RawEvidenceObject` rows. After **commit**, `queue_normalize_raw_objects` in `sync_ingestion_queue.py` calls `normalize_evidence.delay` for each new raw id. `NormalizeEnqueueError` carries the **tail** of IDs that were not yet enqueued so callers can record them for retry.

5. **Handoff recovery** — `_claim_pending_raw_ids_for_handoff` and related helpers in `sync_worker_service.py` merge newly created raw IDs with any **pending** IDs stored on `SourceObject.metadata_extra` under keys such as `pending_normalize_raw_ids`, under row lock, so recovery uses the same content hash logic as normalization and does not loop on deduped raws.

6. **Sync run API** — `api/v1/sync.py` lists and fetches `SyncRun` rows tenant-scoped. **Retry** (`POST .../retry`) requires `domain_admin`, checks run status, and enqueues `run_incremental_sync` in `workers/sync_tasks.py`, which runs `run_incremental_job` inside `run_async`.

7. **Sources API** — `api/v1/sources.py` (with `source_service`) manages source CRUD, credentials, and **backfill** via `run_backfill.delay(...)` per selected source object.

Residual caveats (empty connector registry, sync queue workers, dedupe): see [KNOWN_GAPS.md](./KNOWN_GAPS.md).

## Design decisions

- **Adapter + registry pattern** — *Why:* each vendor API differs; the rest of the stack speaks `IngestionEvent`. *Tradeoff:* every new system needs a connector class and registry entry.

- **Commit-then-enqueue** — *Why:* workers only see committed rows, avoiding races. *Tradeoff:* a short window exists where raw exists but normalize is not queued unless recovery paths run.

- **Pending raw IDs on `SourceObject`** — *Why:* survives partial Celery publish failures without silent loss. *Tradeoff:* metadata keys become part of the operational contract; migrations or tools must respect them.

- **Sync runs as first-class rows** — *Why:* operators see history, failures, and counts in the UI/API. *Tradeoff:* retention of run rows may need policy over time.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Connector interface | `backend/src/contextedge/connectors/base.py` | `BaseConnector`, `IngestionEvent` | Connector implementations |
| Registry | `backend/src/contextedge/connectors/registry.py` | `get_connector`, `supported_source_types` | Sync worker loads connector |
| Sync orchestration | `backend/src/contextedge/services/sync_worker_service.py` | `run_discovery_job`, `run_backfill_job`, `run_incremental_job`, `_claim_pending_raw_ids_for_handoff` | Celery tasks / tests |
| Enqueue normalize | `backend/src/contextedge/services/sync_ingestion_queue.py` | `queue_normalize_raw_objects`, `NormalizeEnqueueError` | After transaction commit |
| Celery sync tasks | `backend/src/contextedge/workers/sync_tasks.py` | `discover_source`, `run_backfill`, `run_incremental_sync` | **sync** queue |
| Sync API | `backend/src/contextedge/api/v1/sync.py` | `list_all_sync_runs`, `retry_sync_run` | HTTP |
| Source helpers | `backend/src/contextedge/services/source_service.py` | `discover_source_objects`, `create_sync_run`, `decrypt_credentials` | Discovery/sync flows |

## Acme VPN incident (this layer)

Acme’s **Jira** source incrementally pulls three duplicate VPN incident tickets; each becomes an `IngestionEvent` with distinct or shared external ids; `persist_ingestion_events` dedupes exact content hashes; pending raw IDs are claimed and `normalize_evidence` tasks are queued so Teams messages ingested minutes later join the same normalization pipeline.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — raw vs `EvidenceItem`  
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — worker processes and queues  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — task routing  
