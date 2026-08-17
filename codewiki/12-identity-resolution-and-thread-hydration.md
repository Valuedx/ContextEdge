# Identity resolution and thread hydration

## Summary

You will understand how ContextEdge **canonicalizes entities** (people, devices, versions, services) that appear under inconsistent names across tickets, chat, and email, and how **thread hydration** fetches the full conversation context for a message or thread on demand—two mechanisms that make evidence searchable and episode reconstruction accurate.

## Business picture

People, devices, and software versions appear under different names across different tools. A VPN incident ticket might reference "John Smith," "jsmith," and "J. Smith (IT)" for the same engineer, or mention "KB5032190" and "November patch" for the same update. Without a single source of truth for each entity, search results fragment, correlations miss connections, and episode reconstruction treats related data as unrelated.

**Identity resolution** ensures every variant points to one canonical record — so when you search for "John Smith," you also find tickets filed under "jsmith" and messages from "J. Smith (IT)." This keeps search, correlation, and playbook matching accurate across all the tools your team uses.

**Thread hydration** solves a related gap: many chat and email connectors deliver messages one at a time, stripped of their surrounding conversation. A single Slack or Teams message about a VPN outage may be part of a 40-message diagnostic discussion. Thread hydration fetches that full conversation on demand, so the system sees the complete context — not just an isolated snippet.

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
3. Merges the resolved list into `evidence.canonical_entity_refs["identities"]` as a JSONB column so queries can inspect identity links without a join. The merge preserves other keys (e.g., `decisions`) that may have been written by the decision extraction step.
4. Calls `graph/builder.link_node_to_identities` to add `GraphEdge` rows with `edge_type="mentions_identity"` so the hybrid ranker can boost playbooks sharing identity context.
5. Appends an `identity.resolved` operational event for observability.

### Relationship with decision extraction

Decision extraction (`decision_service.link_evidence_decisions`) also resolves actors and targets against canonical identities using `resolve_extracted_entities`. Both systems share the same `CanonicalIdentity` and `IdentityAlias` tables. The normalization pipeline runs identity linking first, then decision linking — both write to `canonical_entity_refs` non-destructively so each preserves the other's keys. See [09-graph-and-correlation.md](./09-graph-and-correlation.md) for details on decision graph edges.

`resolve_identity_ids_for_terms` and `find_related_evidence_ids_by_identity_ids` are query-side helpers: the first maps search-term strings to canonical IDs; the second returns all evidence that shares any of those IDs—enabling related-evidence discovery from a search query.

### Thread creation during normalization

`Thread` rows are created automatically during normalization by `ensure_thread_for_evidence` in `evidence_normalization.py`. When the raw payload carries a `_thread_id` (set by connectors via `IngestionEvent.thread_id`), the function:

1. Looks up an existing `Thread` by `(tenant_id, source_id, external_thread_id)`.
2. If none exists, creates a new `Thread` with `hydration_status="pending"` and `title` derived from the evidence payload.
3. Sets `EvidenceItem.thread_id` to link the evidence to the thread.

This runs in both the **new evidence** and **deduped evidence** paths. The `external_thread_id` uses a connector-specific compound format (e.g. `email:threadId` for Gmail, `teamId:channelId:msgId` for Teams) so that the hydration worker can pass it directly to `connector.hydrate_thread()`. See [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) for the full thread ref format table.

### Thread hydration

Thread hydration fills in conversation context lazily: the connector fetches detailed thread content only when policy and relevance warrant it, rather than pulling every message from every channel at sync time.

`workers/hydration_tasks.py` — `hydrate_thread` Celery task:

1. Loads the `Source` and its active `SourceCredential` from the DB.
2. Calls `decrypt_credentials` and builds the connector via `get_connector(source_type, config, creds)`.
3. Calls `connector.hydrate_thread(thread_id)` — each connector implements this to fetch complete thread messages and participant metadata. The `thread_id` argument is `Thread.external_thread_id`, which uses the compound format set during normalization.
4. Finds the matching `Thread` row and updates `hydration_status="complete"`, `message_count`, `participant_count`, `first_message_at`, and `last_message_at` from message timestamps. If the thread has no title, the first message's subject or body prefix is used.
5. Persists each hydrated message as a `RawEvidenceObject` via `persist_ingestion_events`, then enqueues `normalize_evidence` for each new raw ID — so hydrated content becomes searchable evidence.
6. Task retries up to 3 times with a 60-second delay on failure.

The `Thread` model (`models/evidence.py`) tracks `external_thread_id`, `source_id`, `tenant_id`, `hydration_status`, message/participant counts, and `first_message_at`/`last_message_at` timestamps. Evidence items link to threads via `EvidenceItem.thread_id`, so after hydration the full conversation context is available for correlation and extraction.

## Example: Acme VPN data at this stage

**Input — raw mentions extracted from evidence text**

```json
{
  "evidence_id": "ev-a1b2c3",
  "extracted_entities": [
    { "text": "jsmith@acme.com", "type": "person" },
    { "text": "KB5032190", "type": "patch" },
    { "text": "vpn-gw-east-01", "type": "device" },
    { "text": "November patch", "type": "version" }
  ]
}
```

**Output — resolved canonical identities**

```json
[
  {
    "canonical_identity_id": "id:john-smith",
    "name": "John Smith",
    "entity_type": "person",
    "aliases": [
      { "alias": "jsmith@acme.com", "confidence": 0.95, "created_by": "system" },
      { "alias": "jsmith", "confidence": 0.90, "created_by": "system" },
      { "alias": "J. Smith (IT)", "confidence": 0.80, "created_by": "system" }
    ],
    "matched_via": "alias"
  },
  {
    "canonical_identity_id": "id:kb5032190",
    "name": "Windows Update KB5032190",
    "entity_type": "patch",
    "aliases": [
      { "alias": "KB5032190", "confidence": 0.95, "created_by": "system" },
      { "alias": "November patch", "confidence": 0.80, "created_by": "system" }
    ],
    "matched_via": "alias"
  },
  {
    "canonical_identity_id": "id:vpn-gw-east-01",
    "name": "VPN Gateway East 01",
    "entity_type": "device",
    "aliases": [
      { "alias": "vpn-gw-east-01", "confidence": 0.95, "created_by": "system" }
    ],
    "matched_via": "new_identity_created"
  }
]
```

**Output — evidence item updated with identity links**

```json
{
  "evidence_id": "ev-a1b2c3",
  "canonical_entity_refs": ["id:john-smith", "id:kb5032190", "id:vpn-gw-east-01"],
  "identity_links": [
    { "identity_id": "id:john-smith", "match_type": "alias", "confidence": 0.95 },
    { "identity_id": "id:kb5032190", "match_type": "alias", "confidence": 0.95 },
    { "identity_id": "id:vpn-gw-east-01", "match_type": "new", "confidence": 0.95 }
  ]
}
```

**Thread hydration example**

```json
{
  "task": "hydrate_thread",
  "thread_id": "thread-teams-vpn-001",
  "source_type": "teams",
  "before": {
    "hydration_status": "pending",
    "message_count": null,
    "participant_count": null
  },
  "after": {
    "hydration_status": "complete",
    "message_count": 18,
    "participant_count": 5
  }
}
```

After hydration, the full 18-message Teams conversation is available for episode reconstruction, rather than just the single alert message that triggered ingestion.

## Design decisions

- **A candidacy gate decides what may become an identity at all** (`services/identity_candidacy.py`) — *Why:* the identity table is a resolution space, and on the first live Zoho corpus it was not being used as one. 134 evidence items produced 605 canonical identities (4.5 per ticket) and identity work was **78% of all model spend** — 614k tokens extracting, 663k adjudicating across 451 calls, of which 91% of the output was reasoning. Two distinct defects produced it. *Facet types*: `environment`, `version`, `patch` and `vendor` describe an incident rather than name a participant in it, and were adjudicated and stored anyway ("India" three times over). *Non-names*: "the project", "query timeout", "screenshot", "Workflow Request Not Executing in Production issue" — the extractor labelling noun phrases as `service`. Length does not separate them, since at one and two words `service` holds both `NewWorkflowQueue` and `screenshot`; *shape* does — a proper-noun or identifier signal versus lowercase running prose. Replaying the gate over the live decision log removes **41% of adjudication calls and 39% of identity rows**. *Tradeoff:* the shape rule costs genuine all-lowercase product names (`javassist`, `aeengine`), which is the deliberate side to err on — the lowercase common-noun class is far larger. A strong identifier (email/FQDN/hostname/serial/IP) overrides both rules.

- **An identity earns trust by corroboration, not only by review** (`services/identity_promotion.py`) — *Why:* correlation's identity tier only trusts `resolved`/`verified` identities, every first-sighting identity is created `provisional`, and the only promotion path was a human in the review queue. On a cold start the consequence is total and silent: **957 active identities, zero resolved, 493 evidence items, zero episodes** — no identity trusted, so no correlation edge, so `reconstruct_episode` never fires, so no episodes, patterns or playbooks, while evidence counts, token spend and task success all look healthy. A fresh tenant produced a pile of evidence and called it a graph. Promotion now happens in `link_evidence_identities` the moment an identity is linked to its **second** evidence item: extraction from two independent documents is corroboration by the corpus, which beats a reviewer approving a list of 900 unfamiliar names. The floor is not arbitrary — correlation is a claim about *two* documents, so a degree-1 identity cannot produce an edge even if promoted; the instant it becomes useful is the instant it becomes corroborated. *Tradeoff:* trust granted by repetition rather than judgement, which is why the ceiling below exists.

- **Promotion stops at the rare band** (`RARE_DEGREE_MAX`, 5) — *Why:* in a single-product support corpus the product's own components appear in nearly every ticket. Measured: 34 identities in the common band (`Process Studio` ×49, `AE server` ×24) would have generated **9,036 correlation edges**, against **277** from the 105 rare ones — gluing unrelated incidents together, the mass-merge trap `correlation_service` already warns about. A shared *rare* entity means "same incident"; a shared product name means "same product". `needs_review` identities are excluded entirely: those came from `adjudication_abstained` / `adjudication_below_threshold`, where the model could not tell whether the identity duplicates an existing one, and a wrong merge there is the expensive mistake. *Tradeoff:* the constant is duplicated from `correlation_service` because importing it would close a cycle, so `test_identity_promotion.py` asserts the two stay equal.

- **Promotion is never reversed** — *Why:* an identity that later grows common keeps the trust it earned, because `correlation_service` re-reads degree on **every** call: a promoted identity that becomes common is scored down to `COMMON_ENTITY_CONFIDENCE`, and one that becomes a hub is dropped from correlation entirely. Demoting here would duplicate a decision already made, correctly, at the point of use. *Tradeoff:* edges created while an identity was still rare are never revisited — `correlation_service` creates edges once and never upgrades or removes them.

- **The gate sits BELOW the deterministic layers, not above them** — *Why:* prose does not preserve casing. A ticket says "acme vpn" for the node stored as "Acme VPN", and that string fails the shape rule. Layers 1–2 (strong identifier, exact typed alias) are pure SQL against rows that already exist and cost nothing, so they run first and let the graph recognise what it already knows; the gate guards only what follows — candidate generation, LLM adjudication, and row creation. *Tradeoff:* a junk row that already exists keeps matching until it is retired (`is_active = false`), so the gate needs a one-off cleanup pass behind it, not just forward enforcement.

- **Facet types are re-homed, not discarded** — *Why:* this graph is queried by MAF agents resolving incidents, where "does this fix apply to 8.4.0?" and "is this the same customer's environment?" are decisive. Those facts are captured, and captured better: `source_facets.version` covers 99/99 tickets with 29 distinct values typed by the engineer who closed the ticket, `source_facets.environment` 99/99, `source_facets.customer` 93/99; knowledge articles carry the same through `evidence.applicability` (`applicability_from_facets`, else the dedicated extractor). A version is only meaningful attached to a product, so "8.2.5" as a free-standing node answers no question a facet does not answer more precisely — while costing adjudication against "8.2" and "8.5.0" forever. *Tradeoff:* a version appearing ONLY in thread prose while the custom field says otherwise is not promoted to a facet. It stays in the evidence body and remains retrievable, but not filterable.

- **Identity prompt v4 was built, measured, and not shipped** — *Why:* if the pipeline discards four of v3's eight categories, asking the model for them is paid for twice. v4 removes them and sharpens the name-versus-description rule. Scored on `evals/datasets/entity_extraction.jsonl` (19 labelled cases × 3 samples) it came out **worse**: forbidden extractions 3 → 6, stability 1.00 → 0.988, entities 42 → 39 with `missing = 0` on both. Removing categories did not make the model more careful about the four that remain — it redistributed some excluded strings into `service` and `application`. *Tradeoff:* v3 keeps emitting output the gate discards. That waste is real but bounded, and paying it beats shipping a measurably less stable extractor; v4 stays registered and unused so the negative result is not re-derived.

- **Reducing adjudication calls, not adjudication thinking** — *Why:* thinking is 91% of the adjudicator's output and capping it is the obvious saving, but [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) records that capping moved confidence 0.95 → 0.80 at unchanged verdicts, and `AUTO_LINK_THRESHOLDS["person"]` is 0.95 — a cap silently converts auto-links into review-queue items. The gate removes whole calls instead, which is threshold-neutral. *Tradeoff:* a smaller saving than a cap would give, bought without re-tuning any confidence threshold.

- **Create-on-first-sight for new identities** — *Why:* avoids blocking ingestion on reviewer approval; creates the canonical record immediately so subsequent references can match it. *Tradeoff:* the canonical table may accumulate near-duplicates (e.g., "VPN Gateway" and "vpn gateway") until a deduplication or merge pass is run. Reviewer tooling should surface low-confidence aliases for cleanup.

- **JSONB snapshot on evidence row** — *Why:* `canonical_entity_refs` avoids a join on the hot evidence retrieval path and lets the ranker inspect identity context without a separate query. The JSONB is structured with separate keys (`identities`, `decisions`) so multiple extraction passes can write non-destructively. *Tradeoff:* the snapshot can drift from the `evidence_identity_links` table if aliases are later merged or corrected; re-linking is an operational step.

- **Lazy thread hydration via worker** — *Why:* connectors cannot always enumerate thread depth efficiently at discovery time, and not every thread is operationally relevant. Pulling threads only when needed reduces API quota and storage costs. *Tradeoff:* evidence items may remain in `raw`/`shallow` state until a hydration task fires; retrieval over partially hydrated threads returns less context.

- **Graph edge for identity links** — *Why:* reuses the same `GraphEdge` store for ranking signals without a separate identity-search index. *Tradeoff:* graph edge volume grows proportionally to evidence × identity cardinality; large tenants may need index tuning.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Identity extraction (AI) | `backend/src/contextedge/ai/extractors/identity_extractor.py` | `extract_identities`, `IDENTITY_PROMPT` | Normalization worker |
| Resolution core | `backend/src/contextedge/services/identity_service.py` | `resolve_extracted_entities`, `resolve_entities_from_text` | Normalize / API |
| Evidence–identity linking | `backend/src/contextedge/services/identity_service.py` | `link_evidence_identities` | Normalization worker |
| Identity candidacy gate | `backend/src/contextedge/services/identity_candidacy.py` | `identity_rejection_reason`, `looks_like_a_name`, `FACET_TYPES`, `OVERRIDING_IDENTIFIERS` | Before candidate generation, per extracted entity |
| Promotion by corroboration | `backend/src/contextedge/services/identity_promotion.py` | `promote_corroborated_identities` | After links flush, per evidence item |
| Pipeline health (queues, chain, latency) | `backend/src/contextedge/services/pipeline_health_service.py` | `get_pipeline_health` | `GET /admin/pipeline-health`, dashboard poll |
| Query helpers | `backend/src/contextedge/services/identity_service.py` | `resolve_identity_ids_for_terms`, `find_related_evidence_ids_by_identity_ids`, `get_identity_ids_for_evidence` | Search / correlation |
| Decision extraction (AI) | `backend/src/contextedge/ai/extractors/decision_extractor.py` | `extract_decisions` | Normalization worker |
| Decision linking | `backend/src/contextedge/services/decision_service.py` | `link_evidence_decisions` | Normalization worker |
| Graph wiring | `backend/src/contextedge/graph/builder.py` | `link_node_to_identities` | After linking |
| Canonical identity model | `backend/src/contextedge/models/episode.py` | `CanonicalIdentity`, `IdentityAlias`, `EvidenceIdentityLink` | ORM |
| Thread creation | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalization worker |
| Thread model | `backend/src/contextedge/models/evidence.py` | `Thread` | ORM |
| Hydration worker | `backend/src/contextedge/workers/hydration_tasks.py` | `hydrate_thread` | Celery **hydration** queue |

## Acme VPN incident (this layer)

When Acme's VPN incident tickets land, `link_evidence_identities` resolves "jsmith," "J. Smith," and "John Smith" to a single `CanonicalIdentity` of type `person`. "KB5032190" and "November patch" do **not** become identities — since the candidacy gate they are facet types, and the patch level reaches the agent as `source_facets.version` / `evidence.applicability`, where it can be filtered on rather than adjudicated against every other version string. A Teams message that references the outage triggers `hydrate_thread`, which fetches the 18-message conversation chain so the episode reconstructor sees the full diagnostic discussion—not just the isolated alert message.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where identity linking happens in the normalization pipeline
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — LiteLLM provider powering `extract_identities`
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — graph edges built from identity links
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — hydration queue and Celery topology
