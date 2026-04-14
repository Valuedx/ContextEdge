# Evidence normalization and storage

## Summary

You will learn how **raw** connector payloads become **evidence items** queryable in the product, when payloads move to **object storage**, how **deduplication** works, and which follow-on steps (embeddings, identities, attachments) run inside the normalize worker.

## Business picture

Raw data flowing in from connected tools — ticketing systems, chat platforms, email — arrives in inconsistent formats, with duplicates and oversized payloads. If exposed directly, analysts would waste time sifting through noise instead of resolving incidents.

This stage solves that problem. It transforms every raw payload into a **clean, searchable evidence record** with a consistent title, body, source link, and content fingerprint. Duplicates are automatically collapsed: if three Jira tickets describe the same VPN outage in the same words, analysts see **one** trustworthy result, not three. Large payloads are moved to cheaper blob storage so the main database stays fast and backups stay small.

The outcome: analysts can trust that search results are complete, duplicate-free, and up to date — regardless of which tool the data originally came from.

## Technical walkthrough

1. **Persist ingestion** — `persist_ingestion_events` in `ingestion_persistence.py` loops events, builds a canonical JSON payload (including connector metadata and optional thread/timestamp), computes `content_hash` (SHA-256 of canonical JSON), and skips inserts when `(tenant_id, source_id, external_id, content_hash)` already exists. New rows become `RawEvidenceObject` with `raw_payload`. If serialized size exceeds **32 KiB** (`OFFLOAD_THRESHOLD_BYTES`), it calls `upload_raw` in `object_store.py`, stores the returned key on the row, and replaces `raw_payload` with a small `_offloaded` marker.

2. **Load raw for normalize** — `load_raw_payload` in `artifact_extraction_service.py` (used by the worker) rehydrates JSON either from the row or from MinIO/S3 via `download_raw`.

3. **Normalize** — `_normalize` inside `workers/extraction_tasks.py` reads the raw row, derives `title` / `body` via `evidence_title_from_payload` and `evidence_body_from_payload`, and computes **normalization hash** `evidence_content_hash_from_payload` (hash of normalized body text). If an `EvidenceItem` with the same `tenant_id` + `content_hash` exists, the path is **deduped**: it may repair embeddings, link identities, and register attachment artifacts against the existing row.

4. **Thread linking** — After the evidence row exists (new or deduped), `ensure_thread_for_evidence` in `evidence_normalization.py` checks the raw payload for `_thread_id`. If present, it finds or creates a `Thread` row (get-or-create by `tenant_id` + `source_id` + `external_thread_id`) and sets `EvidenceItem.thread_id`. The thread starts with `hydration_status="pending"` and can later be hydrated on demand via the threads API.

5. **New evidence row** — On no match, it inserts `EvidenceItem` with `relevance_state="unclassified"`, links the thread (step 4), links identities when content allows, **extracts and links decisions** from the text, embeds via `embed_evidence`, runs attachment registration, may enqueue/classify relevance, and triggers `correlate_evidence` for cross-source linking.

6. **Object store** — `object_store.py` provides `ensure_bucket`, `upload_raw`, `download_raw`, and artifact upload helpers using boto3 against the configured S3-compatible endpoint (MinIO in dev).

7. **Access policy** — Evidence rows may carry `access_policy_id`; search and GET routes filter using `resolve_excluded_access_policy_ids` (see [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md)).

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

**Processing — normalize, deduplicate, embed**

The worker extracts a clean title and body, computes a normalization hash (based on the meaningful text, not the raw JSON wrapper), and checks whether an evidence item with the same hash already exists. If JIRA-4522 contains effectively the same text, it is deduplicated against this record. An embedding vector is generated for semantic search.

**Output — normalized evidence item**

```json
{
  "evidence_id": "ev-a1b2c3",
  "tenant_id": "acme-corp",
  "domain_id": "vpn-connectivity",
  "source_id": "src-jira-01",
  "external_id": "JIRA-4521",
  "title": "VPN connection drops after Windows update KB5032190",
  "body_text": "Users reporting VPN disconnects since patch Tuesday. Gateway: vpn-gw-east-01. Error: AUTH_CERT_EXPIRED. Reported by jsmith@acme.com.",
  "body_summary": "Multiple users report VPN disconnects following patch Tuesday. AUTH_CERT_EXPIRED on vpn-gw-east-01.",
  "content_hash": "sha256:b7d4e1...",
  "relevance_state": "unclassified",
  "embedding": [0.023, -0.041, 0.018, "... 3072 dimensions ..."],
  "canonical_entity_refs": []
}
```

Note: `canonical_entity_refs` is empty at this point. Identity resolution populates the `identities` key (see [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md)) and decision extraction populates the `decisions` key (see [09-graph-and-correlation.md](./09-graph-and-correlation.md)) shortly after. Both write to `canonical_entity_refs` non-destructively, preserving each other's keys.

## Design decisions

- **Two hashes (raw canonical vs normalized body)** — *Why:* raw dedupe prevents storing identical upstream snapshots twice; normalized dedupe aligns with "same operational meaning" for analysts. *Tradeoff:* two concepts to document; edge cases if titles differ but bodies match.

- **Offload large JSON to object storage** — *Why:* keeps Postgres row size predictable; backups and replication cheaper. *Tradeoff:* normalize path depends on object store availability; `load_raw_payload` errors surface as worker failures.

- **Inline embedding on normalize** — *Why:* semantic search works immediately without a second hop. *Tradeoff:* normalize task latency and provider rate limits.

- **Application-layer dedupe** — *Why:* flexible while schemas evolve. *Tradeoff:* rare duplicate rows possible under concurrent writers until DB constraints harden (see root README known constraints).

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Raw insert + offload | `backend/src/contextedge/services/ingestion_persistence.py` | `persist_ingestion_events`, `OFFLOAD_THRESHOLD_BYTES` | After connector batch |
| Title/body/hash helpers | `backend/src/contextedge/services/evidence_normalization.py` | `evidence_title_from_payload`, `evidence_body_from_payload`, `evidence_content_hash_from_payload` | Normalize |
| Thread linking | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalize |
| Blob I/O | `backend/src/contextedge/services/object_store.py` | `upload_raw`, `download_raw`, `ensure_bucket` | Persist / normalize |
| Normalize worker | `backend/src/contextedge/workers/extraction_tasks.py` | `_normalize`, `normalize_evidence` | Celery **extraction** queue |
| Decision extraction | `backend/src/contextedge/ai/extractors/decision_extractor.py` | `extract_decisions` | Normalization worker |
| Decision linking | `backend/src/contextedge/services/decision_service.py` | `link_evidence_decisions` | Normalization worker |
| Payload load | `backend/src/contextedge/services/artifact_extraction_service.py` | `load_raw_payload`, `register_attachment_artifacts` | Normalize |
| Embeddings on item | `backend/src/contextedge/ai/embeddings.py` | `embed_evidence` | Normalize / repair |
| ORM | `backend/src/contextedge/models/evidence.py` | `RawEvidenceObject`, `EvidenceItem` | Persistence |

## Acme VPN incident (this layer)

Three raws with the same VPN error text collapse to one `EvidenceItem` after normalization hash match; a long Teams export exceeds 32 KiB and stores JSON in **MinIO**, while the database row points to it—so Acme analysts still see one searchable card with correct provenance.

## Further reading

- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — where raws are created  
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — models behind embed and classify  
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — canonical entity extraction and alias matching that runs after normalization  
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) — ingestion diagram  
