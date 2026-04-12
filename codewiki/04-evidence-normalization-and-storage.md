# Evidence normalization and storage

## Summary

You will learn how **raw** connector payloads become **evidence items** queryable in the product, when payloads move to **object storage**, how **deduplication** works, and which follow-on steps (embeddings, identities, attachments) run inside the normalize worker.

## Business picture

Raw dumps from tools are too noisy and too large to expose directly to analysts. The platform copies them into a durable **raw** record, then produces a **clean row**: title, body, type, hashes, and links back to the source. Large payloads are offloaded to blob storage so the database stays fast. Duplicate emails or ticket updates should collapse to one logical evidence row when the **meaningful text** matches, so search results stay trustworthy.

## Technical walkthrough

1. **Persist ingestion** — `persist_ingestion_events` in `ingestion_persistence.py` loops events, builds a canonical JSON payload (including connector metadata and optional thread/timestamp), computes `content_hash` (SHA-256 of canonical JSON), and skips inserts when `(tenant_id, source_id, external_id, content_hash)` already exists. New rows become `RawEvidenceObject` with `raw_payload`. If serialized size exceeds **32 KiB** (`OFFLOAD_THRESHOLD_BYTES`), it calls `upload_raw` in `object_store.py`, stores the returned key on the row, and replaces `raw_payload` with a small `_offloaded` marker.

2. **Load raw for normalize** — `load_raw_payload` in `artifact_extraction_service.py` (used by the worker) rehydrates JSON either from the row or from MinIO/S3 via `download_raw`.

3. **Normalize** — `_normalize` inside `workers/extraction_tasks.py` reads the raw row, derives `title` / `body` via `evidence_title_from_payload` and `evidence_body_from_payload`, and computes **normalization hash** `evidence_content_hash_from_payload` (hash of normalized body text). If an `EvidenceItem` with the same `tenant_id` + `content_hash` exists, the path is **deduped**: it may repair embeddings, link identities, and register attachment artifacts against the existing row.

4. **New evidence row** — On no match, it inserts `EvidenceItem` with `relevance_state="unclassified"`, links identities when content allows, embeds via `embed_evidence`, runs attachment registration, may enqueue/classify relevance, and triggers `correlate_evidence` for cross-source linking.

5. **Object store** — `object_store.py` provides `ensure_bucket`, `upload_raw`, `download_raw`, and artifact upload helpers using boto3 against the configured S3-compatible endpoint (MinIO in dev).

6. **Access policy** — Evidence rows may carry `access_policy_id`; search and GET routes filter using `resolve_excluded_access_policy_ids` (see [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md)).

## Design decisions

- **Two hashes (raw canonical vs normalized body)** — *Why:* raw dedupe prevents storing identical upstream snapshots twice; normalized dedupe aligns with “same operational meaning” for analysts. *Tradeoff:* two concepts to document; edge cases if titles differ but bodies match.

- **Offload large JSON to object storage** — *Why:* keeps Postgres row size predictable; backups and replication cheaper. *Tradeoff:* normalize path depends on object store availability; `load_raw_payload` errors surface as worker failures.

- **Inline embedding on normalize** — *Why:* semantic search works immediately without a second hop. *Tradeoff:* normalize task latency and provider rate limits.

- **Application-layer dedupe** — *Why:* flexible while schemas evolve. *Tradeoff:* rare duplicate rows possible under concurrent writers until DB constraints harden (see root README known constraints).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Raw insert + offload | `backend/src/contextedge/services/ingestion_persistence.py` | `persist_ingestion_events`, `OFFLOAD_THRESHOLD_BYTES` | After connector batch |
| Title/body/hash helpers | `backend/src/contextedge/services/evidence_normalization.py` | `evidence_title_from_payload`, `evidence_body_from_payload`, `evidence_content_hash_from_payload` | Normalize |
| Blob I/O | `backend/src/contextedge/services/object_store.py` | `upload_raw`, `download_raw`, `ensure_bucket` | Persist / normalize |
| Normalize worker | `backend/src/contextedge/workers/extraction_tasks.py` | `_normalize`, `normalize_evidence` | Celery **extraction** queue |
| Payload load | `backend/src/contextedge/services/artifact_extraction_service.py` | `load_raw_payload`, `register_attachment_artifacts` | Normalize |
| Embeddings on item | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence` | Normalize / repair |
| ORM | `backend/src/contextedge/models/evidence.py` | `RawEvidenceObject`, `EvidenceItem` | Persistence |

## Acme VPN incident (this layer)

Three raws with the same VPN error text collapse to one `EvidenceItem` after normalization hash match; a long Teams export exceeds 32 KiB and stores JSON in **MinIO**, while the database row points to it—so Acme analysts still see one searchable card with correct provenance.

## Further reading

- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — where raws are created  
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — models behind embed and classify  
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) — ingestion diagram  
