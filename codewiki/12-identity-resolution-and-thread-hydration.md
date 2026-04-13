# Identity resolution and thread hydration

## Summary

You will understand how ContextEdge **canonicalizes entities** (people, devices, versions, services) that appear under inconsistent names across tickets, chat, and email, and how **thread hydration** fetches the full conversation context for a message or thread on demand—two mechanisms that make evidence searchable and episode reconstruction accurate.

## Business picture

A VPN incident ticket might mention "John Smith," "jsmith," and "J. Smith (IT)" as the same engineer, or reference "Win11 KB5032190" and "November patch" for the same update. Without canonical identities, search and correlation treat each as different data points. **Identity resolution** normalises these into a single `CanonicalIdentity` row per entity so retrieval, graph scoring, and episode clustering all see the same entity.

Separately, many connectors (Slack, Teams, Gmail) deliver per-message or per-event streams. A single message mentioning a VPN problem might be part of a 40-message thread. **Thread hydration** fetches that full thread on demand so evidence rows carry the case context, not just an isolated message fragment.

## Technical walkthrough

### Identity extraction

- `ai/extractors/identity_extractor.py` prompts the LLM (`llm_complete_json`) to extract structured entities from evidence text, categorised as `person`, `device`, `application`, `vendor`, `version`, `patch`, `service`, or `environment`.
- The extractor limits input to 4 000 characters, asks for concise JSON (`{"entities": [...]}`), and guards against fabrication in the prompt.

### Resolution and alias matching

`identity_service.resolve_extracted_entities` is the core loop:

1. For each extracted entity, normalise its name with `_normalize_term` (whitespace collapse + case fold).
2. Query `identity_aliases` joined to `canonical_identities` for any existing alias that case-matches the normalised term.
3. If found, return the existing `CanonicalIdentity` as `matched_via: alias`.
4. If not found, create a new `CanonicalIdentity` and an `IdentityAlias` seeded with `confidence=0.8` and `created_by="system"`. This is the **create-on-first-sight** pattern; human reviewers or future signal can update confidence.

`resolve_entities_from_text` wraps extraction → resolution in a single call for callers who start from raw text.

### Linking identities to evidence

`link_evidence_identities` ties resolved entities back to an `EvidenceItem`:

1. Calls `resolve_entities_from_text`.
2. Upserts `EvidenceIdentityLink` rows (one per unique identity) with `match_type` and `confidence`.
3. Writes the resolved list into `evidence.canonical_entity_refs` as a JSONB column so queries can inspect identity links without a join.
4. Calls `graph/builder.link_node_to_identities` to add `GraphEdge` rows with `edge_type="mentions_identity"` so the hybrid ranker can boost playbooks sharing identity context.
5. Appends an `identity.resolved` operational event for observability.

`resolve_identity_ids_for_terms` and `find_related_evidence_ids_by_identity_ids` are query-side helpers: the first maps search-term strings to canonical IDs; the second returns all evidence that shares any of those IDs—enabling related-evidence discovery from a search query.

### Thread hydration

Thread hydration fills in conversation context lazily: the connector fetches detailed thread content only when policy and relevance warrant it, rather than pulling every message from every channel at sync time.

`workers/hydration_tasks.py` — `hydrate_thread` Celery task:

1. Loads the `Source` and its active `SourceCredential` from the DB.
2. Calls `decrypt_credentials` and builds the connector via `get_connector(source_type, config, creds)`.
3. Calls `connector.hydrate_thread(thread_id)` — each connector implements this to fetch complete thread messages and participant metadata.
4. Finds the matching `Thread` row and updates `hydration_status="complete"`, `message_count`, and `participant_count`.
5. Task retries up to 3 times with a 60-second delay on failure.

The `Thread` model (`models/evidence.py`) tracks `external_thread_id`, `source_id`, `tenant_id`, `hydration_status`, and message/participant counts. Evidence items link to threads, so after hydration the full conversation context is available for normalization and extraction.

## Design decisions

- **Create-on-first-sight for new identities** — *Why:* avoids blocking ingestion on reviewer approval; creates the canonical record immediately so subsequent references can match it. *Tradeoff:* the canonical table may accumulate near-duplicates (e.g., "VPN Gateway" and "vpn gateway") until a deduplication or merge pass is run. Reviewer tooling should surface low-confidence aliases for cleanup.

- **JSONB snapshot on evidence row** — *Why:* `canonical_entity_refs` avoids a join on the hot evidence retrieval path and lets the ranker inspect identity context without a separate query. *Tradeoff:* the snapshot can drift from the `evidence_identity_links` table if aliases are later merged or corrected; re-linking is an operational step.

- **Lazy thread hydration via worker** — *Why:* connectors cannot always enumerate thread depth efficiently at discovery time, and not every thread is operationally relevant. Pulling threads only when needed reduces API quota and storage costs. *Tradeoff:* evidence items may remain in `raw`/`shallow` state until a hydration task fires; retrieval over partially hydrated threads returns less context.

- **Graph edge for identity links** — *Why:* reuses the same `GraphEdge` store for ranking signals without a separate identity-search index. *Tradeoff:* graph edge volume grows proportionally to evidence × identity cardinality; large tenants may need index tuning.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Identity extraction (AI) | `backend/src/contextedge/ai/extractors/identity_extractor.py` | `extract_identities`, `IDENTITY_PROMPT` | Normalization worker |
| Resolution core | `backend/src/contextedge/services/identity_service.py` | `resolve_extracted_entities`, `resolve_entities_from_text` | Normalize / API |
| Evidence–identity linking | `backend/src/contextedge/services/identity_service.py` | `link_evidence_identities` | Normalization worker |
| Query helpers | `backend/src/contextedge/services/identity_service.py` | `resolve_identity_ids_for_terms`, `find_related_evidence_ids_by_identity_ids`, `get_identity_ids_for_evidence` | Search / correlation |
| Graph wiring | `backend/src/contextedge/graph/builder.py` | `link_node_to_identities` | After linking |
| Canonical identity model | `backend/src/contextedge/models/episode.py` | `CanonicalIdentity`, `IdentityAlias`, `EvidenceIdentityLink` | ORM |
| Thread model | `backend/src/contextedge/models/evidence.py` | `Thread` | ORM |
| Hydration worker | `backend/src/contextedge/workers/hydration_tasks.py` | `hydrate_thread` | Celery **hydration** queue |

## Acme VPN incident (this layer)

When Acme's VPN incident tickets land, `link_evidence_identities` resolves "jsmith," "J. Smith," and "John Smith" to a single `CanonicalIdentity` of type `person`, and "KB5032190" and "November patch" to the same `version` entity. A Teams message that references the outage triggers `hydrate_thread`, which fetches the 18-message conversation chain so the episode reconstructor sees the full diagnostic discussion—not just the isolated alert message.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where identity linking happens in the normalization pipeline
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — LiteLLM provider powering `extract_identities`
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — graph edges built from identity links
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — hydration queue and Celery topology
