# Identity resolution and thread hydration

## Summary

You will understand how ContextEdge **canonicalizes entities** (people, devices, applications, services) that appear under inconsistent names across tickets, chat, and email — through a four-layer resolution ladder with a candidacy gate and corroboration-based promotion — and how **thread hydration** fetches the full conversation behind a ticket or message and feeds it back through normalization. Together these make evidence searchable and episode reconstruction accurate.

## Business picture

People, devices, and software appear under different names across different tools. A VPN incident ticket might reference "John Smith," "jsmith," and "J. Smith (IT)" for the same engineer. Without a single source of truth for each entity, search results fragment, correlations miss connections, and episode reconstruction treats related data as unrelated.

**Identity resolution** ensures every variant points to one canonical record — so when you search for "John Smith," you also find tickets filed under "jsmith" and messages from "J. Smith (IT)." Just as important, it decides what should **not** become an identity: version numbers, environments, and generic noun phrases describe an incident rather than participate in it. Identity work was 78% of all model spend on the first live corpus, and the gate that turns those strings away removes about two fifths of it. This keeps search, correlation, and playbook matching accurate — and affordable — across all the tools your team uses.

**Thread hydration** solves a related gap: ticket and chat connectors deliver a ticket or a single message stripped of its surrounding conversation. The 40-message diagnostic discussion — where the actual root cause usually lives — is fetched separately. Hydration pulls that full conversation once, strips the quoted history that makes every reply repeat the whole thread, and turns each substantive message into its own searchable evidence item.

## Technical walkthrough

### Where it starts

The normalize worker calls `link_evidence_identities(db, tenant_id=…, evidence=…, content=identity_content, …)` for every evidence item that passed the relevance gate (backend/src/contextedge/workers/extraction_tasks.py:531-540), and again on the dedup path when the cached refs are empty. The `content` is the redacted title + body + first 2,000 chars of the payload JSON (extraction_tasks.py:184-198).

### Step 1 — extraction (LLM)

`extract_identities` (backend/src/contextedge/ai/extractors/identity_extractor.py:20) skips content under 10 characters (identity_extractor.py:32-33), resolves the prompt **`identity`, default version v3** (backend/src/contextedge/ai/prompts/identity.py:234-238; a measured-but-worse v4 stays registered and unused — see Design decisions), and sends `fence_untrusted(salient_slice(content, 4000))` — evidence is wrapped in untrusted-data markers so a pasted instruction cannot steer the model, and sliced to 4,000 chars by salience rather than head-first (identity_extractor.py:36). It returns whatever sits under the model's `entities` key (identity_extractor.py:46-48). The prompt asks for `display_name`, `entity_type` (v3 vocabulary: person, device, application, vendor, version, patch, service, environment), optional strong identifiers (`email`, `username`, `hostname`, `fqdn`, `serial_number`, `ip_addresses`), `source_identifiers`, `aliases`, and `context` (v3's categories at identity.py:122-130, its JSON contract at 164-177).

### Step 2 — normalization

`normalize_extracted_entity` (backend/src/contextedge/services/identity_normalizer.py:81) lowercases the entity type, normalizes the name with `lower()` — deliberately not `casefold()`, because the output must byte-match what the `0033` SQL backfill produced (identity_normalizer.py:28) — collects typed strong identifiers, and reclassifies shapes: a display name that *is* an email/IPv4/FQDN becomes that strong identifier (`_classify_bare_name`, identity_normalizer.py:69, applied at 131-133), and failing that, a `device` name matching the hostname pattern — e.g. `vpn-gw-east-01` — becomes a `hostname` strong identifier (identity_normalizer.py:134-136).

### Step 3 — the resolution ladder

`resolve_extracted_entities` (backend/src/contextedge/services/identity_service.py:616) runs each normalized entity down four layers. The first two are pure SQL and free; everything below the gate costs an LLM call or writes a row.

| Layer | Mechanism | Result | Where |
| --- | --- | --- | --- |
| 1. Strong identifier | Lookup on `(tenant, alias_type, normalized_alias)` mirroring the unique partial index `uq_identity_aliases_tenant_strong` — deliberately no entity-type or `is_active` filter (filtering created provisional-duplicate blackholes) | confidence **1.0**, method `strong:<type>`; the observed display name is learned as an alias at 0.95 | identity_service.py:149, 630-647 |
| 2. Typed exact alias | `normalized_alias` equality scoped by `compatible_entity_types` (only `application`/`service` are grouped; everything else matches itself) | confidence **0.95**, method `alias_exact` | identity_service.py:91, 182, 649-659 |
| — Candidacy gate | `identity_rejection_reason` (backend/src/contextedge/services/identity_candidacy.py:179): a strong identifier in `OVERRIDING_IDENTIFIERS` (email/fqdn/hostname/serial/IP — candidacy.py:134) overrides everything; otherwise reject `facet_type` (`environment`/`version`/`patch`/`vendor` — they belong in `source_facets`/`applicability`, candidacy.py:96), `unsupported_type` (only person/device/application/service bear identity), or `not_a_name` (`looks_like_a_name`, candidacy.py:164: ≤ 4 words, at least one proper-noun/identifier-shaped token). Rejections are counted per reason and logged once per call | rejected entities go no further — no candidates, no adjudication, no row | identity_service.py:661-676, 785-794 |
| 3. LLM adjudication | `_candidate_identities` (identity_service.py:227): substring tokens OR trigram `similarity > TRIGRAM_SIMILARITY_THRESHOLD = 0.3`, `LIMIT MAX_ADJUDICATION_CANDIDATES = 5` (identity_service.py:60, 69), with a SAVEPOINT-protected substring-only fallback if `pg_trgm` is missing. `_adjudicate_candidates` (identity_service.py:316) calls prompt **`identity_adjudication` v2** (ai/prompts/identity.py:482-486) through schema-validated JSON into `AdjudicationResult` (identity_service.py:103) | auto-link only when `confidence >= AUTO_LINK_THRESHOLDS` (`person: 0.95`, everything else 0.9 — identity_service.py:58-59, 141). A `match` below threshold, or an abstention, creates a **`needs_review`** identity — never a silent link, never a silent fork (687-763) | identity_service.py:678-763 |
| 4. Provisional creation | Unmatched mention → `CanonicalIdentity(resolution_state="provisional")`, method `unmatched_new`; strong aliases inserted `ON CONFLICT DO NOTHING` against the strong-alias index — a conflict means a concurrent worker owns the identifier, and the next mention resolves at layer 1 | confidence **0.5** | identity_service.py:765-783 |

Layers 1, 3 and 4 each write an `identity.resolution_decision` operational event with method, confidence, candidate ids, and reason (`_record_resolution_decision`, identity_service.py:587-613). Layer 2 is the one exception — an exact typed alias hit is already a recorded fact, so it writes nothing (649-659). Layer 1 and an auto-linked adjudication both learn the observed display name as an alias (635-638, 694-699), so the next bare "SFA" resolves deterministically at layer 2.

### Step 4 — linking, promotion, and graph edges

Back in `link_evidence_identities` (identity_service.py:810):

1. If nothing resolved, `canonical_entity_refs["identities"]` is set to `[]` — the "already attempted" marker the dedup path checks so it does not re-pay extraction (820-825).
2. `EvidenceIdentityLink` rows are created idempotently, one per unique identity, with `match_type` and `confidence` (847-857); `canonical_entity_refs["identities"]` is rewritten with `{canonical_id, canonical_name, entity_type, alias, matched_via, confidence, resolution_state}` (872-875).
3. **Corroboration promotion** — `promote_corroborated_identities` (called at 886; backend/src/contextedge/services/identity_promotion.py:72): a `provisional` identity linked by ≥ `CORROBORATION_DEGREE_MIN = 2` distinct evidence items and ≤ `RARE_DEGREE_MAX = 5` (identity_promotion.py:58, 65, band applied at 111-112) flips to `resolved`. `needs_review` identities are deliberately never auto-promoted. Never raises — a missed promotion costs an edge, not the evidence.
4. A `mentions_identity` graph edge is written per ref via `ensure_edge`, **weight = the resolution confidence** — with an explicit `None` check so an abstained 0.0 is not promoted to full trust by an `or` (identity_service.py:893-906). (The older doc text said this went through `graph/builder.link_node_to_identities`; that helper — backend/src/contextedge/graph/builder.py:220 — is used by the playbook writers and the episode graph builder instead (workers/pattern_tasks.py:661, api/v1/playbooks.py:739, graph/builder.py:274-282). Evidence identity linking writes its edges directly.)
5. An `identity.resolved` operational event closes the pass (907-917).

### Relationship with decision extraction

Decision extraction (`decision_service.link_evidence_decisions`) resolves actors (as `person`) and targets (as `service`) through the same `resolve_extracted_entities` ladder and the same tables. Normalization runs identities first, then decisions (extraction_tasks.py:533-557); both write to `canonical_entity_refs` non-destructively under separate keys (`identities`, `decisions`). See [09-graph-and-correlation.md](./09-graph-and-correlation.md) for decision graph edges.

Query-side helpers: `resolve_identity_ids_for_terms` (identity_service.py:921) maps search-term strings to canonical ids; `find_related_evidence_ids_by_identity_ids` (identity_service.py:967) returns all evidence sharing those ids — enabling related-evidence discovery from a search query.

### Merge, snapshot repair, and scheduled reconciliation

- `merge_canonical_identities` (identity_service.py:986) re-points aliases, evidence links, and graph edges from a duplicate to a survivor, marks the survivor `verified`, and enqueues `extraction.rebuild_identity_snapshots` (backend/src/contextedge/workers/identity_tasks.py:72-74) to repair the cached JSONB refs on evidence and episodes — the snapshot's drift risk has an operational fix.
- A daily Beat job `identity.reconcile_identities` (backend/src/contextedge/workers/celery_app.py:299-303; task at workers/identity_tasks.py:147-149) runs `reconcile_identities` (backend/src/contextedge/services/identity_reconciliation_service.py:306). It sweeps only `provisional`/`needs_review` rows (`ELIGIBLE_STATES`, reconciliation_service.py:73) and makes one pass per **type group**, not per type: `compatible_entity_types` folds `application` and `service` into a single pass over their union, because reconciling them separately is how "JMX" the service and "JMX" the application sat side by side forever (`_type_groups`, reconciliation_service.py:197-204, 345-346). Each pass runs in batches of `BATCH_SIZE = 60` with `BATCH_OVERLAP = 10` (reconciliation_service.py:54-55 — boundary pairs are exactly where near-duplicates cluster), proposing merges only above `MIN_CONFIDENCE = 0.95` (68). It **proposes, never merges**: rows land in `identity_merge_proposals` for a human (362-369), and rejections persist so the schedule never re-raises a declined pair (284-296).

### Thread creation during normalization

`Thread` rows are created lazily by `ensure_thread_for_evidence` (backend/src/contextedge/services/evidence_normalization.py:155). When the raw payload carries a `_thread_id` (set by connectors via `IngestionEvent.thread_id`), it get-or-creates the `Thread` by `(tenant_id, source_id, external_thread_id)` with `hydration_status="pending"` and stamps `EvidenceItem.thread_id`. This runs in both the new-evidence and dedup paths. The `external_thread_id` uses a connector-specific compound format (e.g. `incident:PROJ-123` for Jira, `zoho_ticket:<id>` for Zoho Desk) so the hydration worker can pass it straight to `connector.hydrate_thread()`. **Because threads are created by normalization, hydrating before normalize has processed the parent returns 404 at the API** — by design, and worth knowing when debugging (see [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Thread hydration requires normalization to run first").

### Thread hydration

Two triggers:

- **Automatic** — the `normalize_evidence` task wrapper dispatches `hydrate_thread.delay(...)` post-commit when the payload carried a `_thread_id`, the record is **not** itself a hydrated message, and it was not a dedup (extraction_tasks.py:1341-1351). The hydrated-message guard (`is_hydrated_message`, extraction_tasks.py:613-615) matters: hydration stamps `_thread_id` onto every message it writes, so without it each hydrated message would re-hydrate its own thread — a measured 10× request amplification, against APIs (Zoho Desk) that answer throttling with **empty results rather than errors**, the failure mode that once stored 11 of 20 tickets as empty while reporting success (extraction_tasks.py:598-609).
- **Manual** — `POST /api/v1/threads/{thread_id}/hydrate` (backend/src/contextedge/api/v1/threads.py:69-89).

The Celery task `hydration.hydrate_thread` (backend/src/contextedge/workers/hydration_tasks.py:185-191; queue `hydration`, 3 retries × 60 s) runs `_hydrate` (hydration_tasks.py:36):

1. Load the `Source` (tenant-checked) and its active `SourceCredential`, `decrypt_credentials`, build the connector via `get_connector`, and call `connector.hydrate_thread(thread_id)` (37-54). Missing source or credential returns `{"error": ...}` without retry-worthy failure.
2. Find the `Thread` row by `(external_thread_id, source_id, tenant_id)` (56-64).
3. **Cross-message quote stripping** — `clean_thread_bodies(bodies, senders)` (backend/src/contextedge/services/thread_text_service.py:346) removes text already seen earlier in the same thread. It lives here, not in a connector, because only hydration holds the whole thread in arrival order (hydration_tasks.py:70-89). Measured on 305 real messages: 89% of the substantive text was repetition. Each cleaned message reports `removed_chars`, `is_quote_only`, `is_delivery_failure`.
4. Each message becomes an `IngestionEvent`: `external_id = f"{thread_id}:msg:{msg_id}"`, `object_type="hydrated_message"`, content carrying the cleaned `body` plus `body_original` (audit copy — nothing is destroyed, only kept out of what gets embedded), `quoted_chars_removed`, `is_quote_only`, `is_delivery_failure`, `from`, `type`, and the parsed timestamp (ISO or Gmail epoch-milliseconds — `_parse_msg_timestamp`, hydration_tasks.py:21-33) (91-131).
5. The thread row flips to `hydration_status="complete"` with `message_count`, `participant_count`, `first/last_message_at`, and a title fallback from the first message (133-145).
6. `persist_ingestion_events` stores the messages as raw rows — same dedupe and same >32 KB MinIO offload as sync (147-155; see [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md)).
7. The task wrapper, post-commit, enqueues `normalize_evidence.delay(raw_id)` per **new** raw id (199-202). Each hydrated message then runs the full normalization pipeline — where the deterministic noise gate drops ~47% of them (coordination chatter, quote-only replies, bounces) before any model call, and survivors become `thread_message` evidence items.

The hydration↔normalization loop terminates: hydrated messages carry `_thread_id` but the wrapper refuses to dispatch hydration for them, and re-delivered messages dedupe at the raw layer, so the cycle converges after one pass.

Connector implementations vary (`hydrate_thread` is part of the base contract, backend/src/contextedge/connectors/base.py:137): Zoho Desk merges two endpoints — `/tickets/{id}/threads` (customer email exchange) and `/tickets/{id}/comments` (internal agent notes, usually where diagnosis lives) — re-raising if **both** listings fail so a thread is never stored "hydrated but empty," and treating auth/quota errors as fatal (backend/src/contextedge/connectors/zoho_desk/connector.py:1224-1349); Teams fetches the root message before the replies; SapphireIMS hydration is a documented no-op (its conversation endpoints are not public — [KNOWN_GAPS.md](./KNOWN_GAPS.md)).

## Example: Acme VPN data at this stage

**Input — entities the LLM extracted from the JIRA-4521 evidence text**

```json
{
  "evidence_id": "ev-a1b2c3",
  "entities": [
    { "display_name": "John Smith", "entity_type": "person", "email": "jsmith@acme.com" },
    { "display_name": "vpn-gw-east-01", "entity_type": "device" },
    { "display_name": "KB5032190", "entity_type": "patch" }
  ]
}
```

**What the ladder does with each**

- `jsmith@acme.com` is a strong identifier: if any prior evidence mentioned it, **layer 1** links at confidence 1.0 (`strong:email`); on true first sight the person is created `provisional` and the email alias claims the identifier for every later mention.
- `vpn-gw-east-01` is a single-token device name, so the normalizer promotes it to a `hostname` strong identifier — it resolves at layer 1 forever after its first sighting.
- `KB5032190` has entity type `patch`, a **facet type** — the candidacy gate rejects it (`facet_type`). It never becomes an identity; the patch level reaches agents as `source_facets.version` / `evidence.applicability`, where it can be filtered on instead of adjudicated against every other version string.

**Output — the evidence row's identity snapshot**

```json
{
  "evidence_id": "ev-a1b2c3",
  "canonical_entity_refs": {
    "identities": [
      { "canonical_id": "9c2e-...", "canonical_name": "John Smith", "entity_type": "person",
        "alias": "jsmith@acme.com", "matched_via": "strong:email", "confidence": 1.0,
        "resolution_state": "resolved" },
      { "canonical_id": "41d7-...", "canonical_name": "vpn-gw-east-01", "entity_type": "device",
        "matched_via": "provisional_new", "confidence": 0.5, "resolution_state": "provisional" }
    ]
  }
}
```

When the Teams thread about the same outage mentions `vpn-gw-east-01` again, that second link crosses `CORROBORATION_DEGREE_MIN = 2` and the identity is promoted to `resolved` — the exact moment it first *could* contribute a correlation edge.

**Thread hydration example**

```json
{
  "task": "hydration.hydrate_thread",
  "thread_id": "acme-teams:vpn-outage-channel:msg-001",
  "before": { "hydration_status": "pending", "message_count": null },
  "after": {
    "hydration_status": "complete",
    "message_count": 18,
    "participant_count": 5,
    "quoted_chars_removed": 41230,
    "raw_objects_created": 18
  }
}
```

After hydration, the 18 messages loop back through normalization: the "Any update on the VPN?" messages die at the noise gate (`coordination_only`), while "Restarted IPSec on vpn-gw-east-01, tunnel stable" survives (48 characters, far under the 150-character floor, but it names a host) and becomes its own `thread_message` evidence item — so episode reconstruction sees the full diagnostic discussion, not just the alert that triggered ingestion.

## Design decisions

- **A candidacy gate decides what may become an identity at all** (`services/identity_candidacy.py`) — *Why:* the identity table is a resolution space, and on the first live Zoho corpus it was not being used as one. 134 evidence items produced 605 canonical identities (4.5 per ticket) and identity work was **78% of all model spend** (identity_candidacy.py:8-9). Adjudication alone was 451 calls (identity_service.py:672) burning 663k tokens, of which 788 of every 867 output tokens were reasoning (`test_identity_candidacy.py:170-173`). Two distinct defects produced it. *Facet types*: `environment`, `version`, `patch` and `vendor` describe an incident rather than name a participant in it, and were adjudicated and stored anyway ("India" three times over). *Non-names*: "the project", "query timeout", "screenshot", "Workflow Request Not Executing in Production issue" — the extractor labelling noun phrases as `service`. Length does not separate them, since at one and two words `service` holds both `NewWorkflowQueue` and `screenshot`; *shape* does — a proper-noun or identifier signal versus lowercase running prose. On the live corpus the gate removes **41% of adjudication calls and 39% of identity rows** (recorded at ai/prompts/identity.py:285-288). *Tradeoff:* the shape rule needs a capital past the first letter, an all-caps token, or an internal dot/underscore/hyphen/slash (`_is_name_token`, candidacy.py:139-161), so a genuine product name written in all lowercase with no punctuation is rejected. That is the deliberate side to err on — the lowercase common-noun class is far larger. A strong identifier (email/FQDN/hostname/serial/IP) overrides both rules.

- **An identity earns trust by corroboration, not only by review** (`services/identity_promotion.py`) — *Why:* correlation's identity tier only trusts `resolved`/`verified` identities, every first-sighting identity is created `provisional`, and the only promotion path was a human in the review queue. On a cold start the consequence is total and silent: **957 active identities, zero resolved, 493 evidence items, zero episodes** — no identity trusted, so no correlation edge, so `reconstruct_episode` never fires, so no episodes, patterns or playbooks, while evidence counts, token spend and task success all look healthy. A fresh tenant produced a pile of evidence and called it a graph. Promotion now happens in `link_evidence_identities` the moment an identity is linked to its **second** evidence item: extraction from two independent documents is corroboration by the corpus, which beats a reviewer approving a list of 900 unfamiliar names. The floor is not arbitrary — correlation is a claim about *two* documents, so a degree-1 identity cannot produce an edge even if promoted; the instant it becomes useful is the instant it becomes corroborated. *Tradeoff:* trust granted by repetition rather than judgement, which is why the ceiling below exists.

- **Promotion stops at the rare band** (`RARE_DEGREE_MAX`, 5) — *Why:* in a single-product support corpus the product's own components appear in nearly every ticket. Measured: 34 identities in the common band (`Process Studio` ×49, `AE server` ×24) would have generated **9,036 correlation edges**, against **277** from the 105 rare ones — gluing unrelated incidents together, the mass-merge trap `correlation_service` already warns about. A shared *rare* entity means "same incident"; a shared product name means "same product". `needs_review` identities are excluded entirely: those came from `adjudication_abstained` / `adjudication_below_threshold`, where the model could not tell whether the identity duplicates an existing one, and a wrong merge there is the expensive mistake. *Tradeoff:* the constant is duplicated from `correlation_service` because importing it would close a cycle, so `test_identity_promotion.py` asserts the two stay equal.

- **Promotion is never reversed** — *Why:* an identity that later grows common keeps the trust it earned, because `correlation_service` re-reads degree on **every** call: a promoted identity that becomes common is scored down to `COMMON_ENTITY_CONFIDENCE`, and one that becomes a hub is dropped from correlation entirely. Demoting here would duplicate a decision already made, correctly, at the point of use. *Tradeoff:* edges created while an identity was still rare are never revisited — `correlation_service` creates edges once and never upgrades or removes them.

- **The gate sits BELOW the deterministic layers, not above them** — *Why:* prose does not preserve casing. A ticket says "acme vpn" for the node stored as "Acme VPN", and that string fails the shape rule. Layers 1–2 (strong identifier, exact typed alias) are pure SQL against rows that already exist and cost nothing, so they run first and let the graph recognise what it already knows; the gate guards only what follows — candidate generation, LLM adjudication, and row creation (identity_service.py:661-676). *Tradeoff:* a junk row that already exists keeps matching until it is retired (`is_active = false`), so the gate needs a one-off cleanup pass behind it, not just forward enforcement.

- **Facet types are re-homed, not discarded** — *Why:* this graph is queried by MAF agents resolving incidents, where "does this fix apply to 8.4.0?" and "is this the same customer's environment?" are decisive. Those facts are captured, and captured better: `source_facets.version` covers 99/99 tickets with 29 distinct values typed by the engineer who closed the ticket, `source_facets.environment` 99/99, `source_facets.customer` 93/99; knowledge articles carry the same through `evidence.applicability` (`applicability_from_facets`, else the dedicated extractor). A version is only meaningful attached to a product, so "8.2.5" as a free-standing node answers no question a facet does not answer more precisely — while costing adjudication against "8.2" and "8.5.0" forever. *Tradeoff:* a version appearing ONLY in thread prose while the custom field says otherwise is not promoted to a facet. It stays in the evidence body and remains retrievable, but not filterable.

- **Identity prompt v4 was built, measured, and not shipped** — *Why:* if the pipeline discards four of v3's eight categories, asking the model for them is paid for twice. v4 removes them and sharpens the name-versus-description rule. Scored on `evals/datasets/entity_extraction.jsonl` (19 labelled cases × 3 samples) it came out **worse**: forbidden extractions 3 → 6, stability 1.00 → 0.988, entities 42 → 39 with `missing = 0` on both. Removing categories did not make the model more careful about the four that remain — it redistributed some excluded strings into `service` and `application`. *Tradeoff:* v3 keeps emitting output the gate discards. That waste is real but bounded, and paying it beats shipping a measurably less stable extractor; v4 stays registered and unused so the negative result is not re-derived.

- **Reducing adjudication calls, not adjudication thinking** — *Why:* thinking is 91% of the adjudicator's output (779 of 851 tokens per call — `evals/adjudication_thinking_eval.py:3-6`) and capping it is the obvious saving, but the controlled comparison recorded in `config.py:156-163` and [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) moved confidence 0.95 → 0.80 at unchanged verdicts, and `AUTO_LINK_THRESHOLDS["person"]` is 0.95 — a cap silently converts auto-links into review-queue items. The gate removes whole calls instead, which is threshold-neutral. *Tradeoff:* a smaller saving than a cap would give, bought without re-tuning any confidence threshold.

- **Provisional on first sight, review or corroboration to earn trust** — *Why:* blocking ingestion on reviewer approval would stall the pipeline, but the pre-`0033` behaviour — trusting every first sighting at 0.8 — polluted correlation. The ladder splits the difference: unmatched mentions are created immediately at `provisional`/0.5 so subsequent references can match them, ambiguous adjudications park at `needs_review`, and trust arrives via corroboration (above) or a human. Near-duplicates that slip through are caught by the daily reconciliation pass, which **proposes** merges to `identity_merge_proposals` above 0.95 confidence and never merges on its own. *Tradeoff:* the canonical table carries provisional rows until corroboration or review; correlation simply ignores them, so the cost is deferred cleanup, not wrong edges.

- **JSONB snapshot on evidence row** — *Why:* `canonical_entity_refs` avoids a join on the hot evidence retrieval path and lets the ranker inspect identity context without a separate query. The JSONB is structured with separate keys (`identities`, `decisions`) so multiple extraction passes write non-destructively. *Tradeoff:* the snapshot can drift from `evidence_identity_links` when identities are merged — which is why `merge_canonical_identities` enqueues `extraction.rebuild_identity_snapshots` to repair the cached refs rather than leaving re-linking as a manual step.

- **Threads are created lazily; hydration is automatic but guarded** — *Why:* connectors cannot enumerate thread depth efficiently at sync time, and pulling every conversation eagerly would spend API quota on threads nobody needs. Threads are created by normalization and hydrated automatically the first time their parent record normalizes — but only the parent may request hydration (`is_hydrated_message` guard), because hydration stamps `_thread_id` on every message it writes and an unguarded dispatch re-hydrates each thread once per message (measured 10× amplification against an API whose throttling returns empty results). *Tradeoff:* hydration lag — evidence lands before its conversation does, and retrieval over a `pending` thread returns less context until the hydration task completes.

- **Graph edge weight carries resolution confidence** — *Why:* graph consumers should see a provisional mention (0.5) as weaker than a strong-identifier match (1.0), instead of every mention weighing the same. The write uses an explicit `None` check because `confidence or 1.0` would promote a legitimate 0.0 from an abstained adjudication to full trust — the exact inversion the weight exists to prevent (identity_service.py:888-906). *Tradeoff:* graph edge volume grows with evidence × identity cardinality; the maf.v1 projection deliberately excludes `mentions_identity` from traversal for exactly this fan-out reason.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Identity extraction (AI) | `backend/src/contextedge/ai/extractors/identity_extractor.py` | `extract_identities` (prompt `identity` v3 default, fenced + 4,000-char slice) | Normalize worker, post-relevance-gate |
| Entity normalization | `backend/src/contextedge/services/identity_normalizer.py` | `normalize_extracted_entity`, `_classify_bare_name` | Per extracted entity |
| Candidacy gate | `backend/src/contextedge/services/identity_candidacy.py` | `identity_rejection_reason`, `looks_like_a_name`, `FACET_TYPES`, `OVERRIDING_IDENTIFIERS` | Between layers 2 and 3 |
| Resolution ladder | `backend/src/contextedge/services/identity_service.py` | `resolve_extracted_entities`, `AUTO_LINK_THRESHOLDS`, `AdjudicationResult` | Normalize / API |
| Evidence–identity linking | `backend/src/contextedge/services/identity_service.py` | `link_evidence_identities` | Normalize worker |
| Promotion by corroboration | `backend/src/contextedge/services/identity_promotion.py` | `promote_corroborated_identities`, `CORROBORATION_DEGREE_MIN`, `RARE_DEGREE_MAX` | After links flush, per evidence item |
| Merge + snapshot repair | `backend/src/contextedge/services/identity_service.py`, `workers/identity_tasks.py` | `merge_canonical_identities`, `rebuild_identity_snapshots` | Human merge / post-merge repair |
| Scheduled reconciliation | `backend/src/contextedge/services/identity_reconciliation_service.py`, `workers/identity_tasks.py` | `reconcile_identities`, `reconcile_identities_task` (`identity.reconcile_identities`) | Daily Beat |
| Query helpers | `backend/src/contextedge/services/identity_service.py` | `resolve_identity_ids_for_terms`, `find_related_evidence_ids_by_identity_ids`, `get_identity_ids_for_evidence` | Search / correlation |
| Decision extraction + linking | `backend/src/contextedge/ai/extractors/decision_extractor.py`, `services/decision_service.py` | `extract_decisions`, `link_evidence_decisions` | Normalize worker, after identities |
| Canonical identity model | `backend/src/contextedge/models/episode.py` | `CanonicalIdentity`, `IdentityAlias`, `EvidenceIdentityLink` | ORM |
| Thread creation | `backend/src/contextedge/services/evidence_normalization.py` | `ensure_thread_for_evidence` | Normalize worker |
| Thread model | `backend/src/contextedge/models/evidence.py` | `Thread` | ORM |
| Hydration worker | `backend/src/contextedge/workers/hydration_tasks.py` | `_hydrate`, `hydrate_thread` (`hydration.hydrate_thread`) | Celery **hydration** queue |
| Cross-message quote stripping | `backend/src/contextedge/services/thread_text_service.py` | `clean_thread_bodies` | Inside `_hydrate`, whole thread in hand |
| Noise gate for hydrated messages | `backend/src/contextedge/services/message_filter.py` | `message_noise_reason`, `is_hydrated_message` | Normalize, before any model call |
| Pipeline health (queues, chain, latency) | `backend/src/contextedge/services/pipeline_health_service.py` | `get_pipeline_health` | `GET /admin/pipeline-health`, dashboard poll |

## Acme VPN incident (this layer)

When Acme's VPN incident tickets land, `link_evidence_identities` resolves "jsmith," "J. Smith," and "John Smith" to a single `CanonicalIdentity` of type `person` — the email resolves at layer 1, the bare names via learned aliases, and any ambiguous variant goes to adjudication, linking only at ≥ 0.95 because persons carry the stricter threshold. "KB5032190" and "November patch" do **not** become identities — the candidacy gate rejects facet types, and the patch level reaches the agent as `source_facets.version` / `evidence.applicability`, where it can be filtered on rather than adjudicated against every other version string. The device `vpn-gw-east-01` becomes a hostname-backed identity on first sight, is corroborated to `resolved` the moment the Teams thread mentions it a second time, and from then on anchors correlation across the ticket, the thread, and the engineer's email. Normalizing the ticket auto-dispatches `hydrate_thread`, which fetches the 18-message conversation, strips 40 KB of quoted history, and feeds each message back through normalization — the noise gate drops "Any update on the VPN?" while "Restarted IPSec on vpn-gw-east-01, tunnel stable" becomes its own evidence item, so the episode reconstructor sees the full diagnostic discussion, not just the isolated alert.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where identity linking and the noise gate sit in the normalization pipeline
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — LiteLLM provider powering `extract_identities` and the adjudicator
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — how resolved identities become correlation edges
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — hydration queue and Celery topology
- [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) — the thinking-budget measurement behind the adjudication decision
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — hydration-after-normalize 404, SapphireIMS no-op hydration, Zoho email threads not yet hydrated for the resolved corpus
