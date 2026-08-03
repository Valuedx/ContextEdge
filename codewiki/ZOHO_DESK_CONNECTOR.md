# Zoho Desk connector

## Summary

You will understand how a Zoho Desk help desk becomes a ContextEdge source: which two record families it contributes (tickets and knowledge-base articles), why its sync strategy looks nothing like the ServiceNow connector's despite solving the same problem, and which of its behaviours were **verified against a live instance** rather than inferred from documentation.

This page exists because Zoho Desk breaks three assumptions the other ticket connectors were built on, and each break is load-bearing. If you are adding a fifth connector, read the **Design decisions** section before you copy an existing one.

## Business picture

A support organization running Zoho Desk holds two different kinds of operational memory, and ContextEdge wants both.

The **tickets** are what broke and who fixed it — the incident record, the customer's email exchange, and the internal agent discussion where the actual diagnosis usually lives. The **knowledge base** is the distilled version: the article someone wrote after solving the problem the third time. A support engineer answering "has anyone seen this before" is really asking both questions at once, and today they have to ask them separately in two different Zoho screens.

Connecting Zoho Desk once brings both into the same evidence pool as the organization's Jira tickets, Teams threads, and ServiceNow incidents — so a search for "VPN gateway timeouts" returns the incident, the chat where it was diagnosed, *and* the KB article that documents the fix, ranked together.

One operational caveat is worth stating up front, because it is the most common way this integration disappoints: **Zoho's OAuth grant is per-module**. A token issued with only knowledge-base permission will sync 629 articles perfectly and silently contribute zero tickets. The connector reports this explicitly rather than looking empty — see **Scope reporting** below.

## What was verified against a live instance

Everything in the sync strategy was checked against a real Zoho Desk portal (`desk.zoho.in`, org `60001911841`, 629 published articles) rather than taken from documentation. The findings that changed the implementation:

| Finding | Consequence |
| --- | --- |
| `limit` is capped at **50**. `limit=51` returns `422 UNPROCESSABLE_ENTITY: exceeds the range of '1-50'`. | A page size copied from the ServiceNow connector (100) would 422 on **every** call. |
| **No modified-since filter exists.** `modifiedTimeRange` is rejected as an extra query parameter. | Incremental sync cannot be a server-side window. It is a newest-first walk with an early stop. |
| `sortBy=-modifiedTime` returns strictly descending order — confirmed across all 13 pages of the live KB. | This ordering is the entire foundation of incremental sync. |
| Records sharing a `modifiedTime` arrive in **ascending id** order inside that descending sequence. | A ServiceNow-style `(time, id)` compound cursor does not describe this API and cannot be used. See **Design decision 2**. |
| List rows carry `summary` but **not** the body. `answer` (articles) and `description` (tickets) only exist on the per-record detail call. | Sync issues one detail call per changed record, or ingests teaser-only evidence. |
| Missing permissions fail as `403 SCOPE_MISMATCH`, **per module**. | Discovery must skip an unreadable module, not abort. |
| The API root includes `/api/v1`; omitting it returns a bare `404`. | A `404` here reads exactly like a missing scope — a real bug found during live testing. |
| Rate limiting is metered by request *weight* per org (`X-Rate-Limit-Remaining-v3`), not a flat RPS. | 5 rps with a small burst leaves ample headroom for detail calls. |

## Technical walkthrough

1. **Authentication.** `_token()` exchanges a long-lived refresh token for a one-hour access token at the data center's accounts host, caches it for the life of the connector instance, and re-mints on a 401 so a token revoked mid-sync recovers instead of failing the run. Zoho pins accounts to the data center they were created in, so `credentials["data_center"]` (`com`, `in`, `eu`, `au`, `jp`, `ca`, `sa`, `uk`) selects a matching accounts/API host **pair** — a cross-DC call fails authentication.

2. **Discovery.** `discover_objects()` probes each module in `MODULES` and emits one `DiscoveredObject` per readable one, carrying the record count from `/ticketsCount` or `/articles/count`. A module answering `403`/`404` is logged and skipped. With `source_config["per_department"]`, tickets are offered as one object per department (`tickets:<departmentId>`) instead of one object for all of them.

3. **Incremental sync.** `_walk_desc()` reads newest-first with `sortBy=-modifiedTime` and 1-based `from` offset paging, stopping at the checkpoint. The checkpoint is a **timestamp plus the set of ids already emitted at that timestamp** — not a compound cursor (decision 2). A row strictly older than the boundary stops the walk; a row *at* the boundary is emitted only if its id is new and does not stop the walk, so the remainder of a tied bulk edit is always reached.

4. **Ordering guard.** Every page is checked for descending `modifiedTime` before its rows are consumed. Out-of-order pages stop the walk **without advancing the checkpoint** — refetching next tick is safe because dedupe absorbs it; skipping unreturned records is silent data loss. This is the same fail-closed contract as the ServiceNow connector's `page_order_violation`.

5. **Backfill.** The same descending walk, bounded by the requested window: rows newer than `window.end` are skipped and the walk continues; the first row older than `window.start` ends it. When the page budget runs out mid-window, the checkpoint stores an **offset** rather than a timestamp, so a partial sweep can never seed incremental sync from an incomplete picture.

6. **Detail hydration.** `_hydrate_rows()` fetches the per-record body that list rows omit, bounded by `DETAIL_FETCH_LIMIT`. A failed detail call degrades to the list row rather than dropping the record.

7. **Body conversion.** `html_text.html_to_text()` converts Zoho's rich text to heading-preserving plain text using only `html.parser` — no new dependency. Headings survive as `#` markers because `chunkers/attachment.py` splits documents on heading boundaries.

8. **Thread hydration.** For tickets, `hydrate_thread()` merges two endpoints that both matter: `/threads` (the customer email exchange) and `/comments` (the internal agent discussion — the ServiceNow work-notes equivalent). Thread bodies are not on the list response, so each needs its own detail call. Articles have no conversation and hydrate to a no-op.

9. **Reference enrichment.** `zoho_desk_reference_service` turns product, team, account, and KB category into graph entities with typed edges, article tags into topics, and related-ticket ids into symmetric case-link keys.

10. **Scope reporting.** `validate_credentials()` returns valid only when at least one module is readable, and names both what was granted and what was not. `probe_configuration()` is the fuller read-only setup report: granted scope string, per-module readability, record counts, and whether detail calls actually return a body.

## Example: Acme VPN data at this stage

**Input** (what arrives — a Zoho Desk ticket list row, after the detail call is merged in):

```json
{
  "id": "1892000000123456",
  "ticketNumber": "4021",
  "subject": "Users cannot log in to the VPN",
  "description": "<p>RADIUS timeouts reported by the field team.</p>",
  "resolution": "<p>Restarted the RADIUS service.</p>",
  "status": "Open", "priority": "High", "classification": "Incident",
  "product": {"name": "VPN Gateway", "id": "p1"},
  "team": {"name": "Network Ops", "id": "t1"},
  "account": {"name": "Acme Corp", "id": "a1"},
  "assignee": {"name": "Dana Reed", "email": "dana@example.com"},
  "modifiedTime": "2026-08-01T12:00:00.000Z",
  "relatedTickets": ["4019", "4020"]
}
```

**Output** (what the system produces — an `IngestionEvent.content`, truncated):

```json
{
  "ticket_id": "1892000000123456",
  "ticket_number": "4021",
  "title": "Users cannot log in to the VPN",
  "description": "RADIUS timeouts reported by the field team.\n\nRestarted the RADIUS service.",
  "record_kind": "incident",
  "evidence_type": "ticket",
  "product_name": "VPN Gateway", "team_name": "Network Ops",
  "account_name": "Acme Corp", "assignee": "Dana Reed",
  "related_tickets": ["4019", "4020"],
  "_thread_id": "zoho_ticket:1892000000123456"
}
```

**Output** (a KB article — note `evidence_type`, which routes it away from the ticket paths):

```json
{
  "article_id": "11270000096194057",
  "title": "Resolving Intermittent PostgreSQL Database Connection Drops",
  "description": "## Issue Description\n\nWorkflows experience intermittent database\nconnection failures during execution...",
  "evidence_type": "kb_article",
  "record_kind": "kb_article",
  "category_name": "REST Plugin",
  "tags": ["postgres", "connection pool"],
  "_thread_id": "zoho_article:11270000096194057"
}
```

**Checkpoint** (what incremental sync stores between runs):

```json
{"last_updated": "2026-08-03T05:12:05.000Z", "last_ids": ["11270000079869964"]}
```

## Design decisions

**1. Newest-first walk with an early stop, instead of a server-side time window.**
*Why:* Zoho Desk has no modified-since filter — verified, not assumed: the list endpoints reject `modifiedTimeRange` as an extra query parameter. What the API does honor is `sortBy=-modifiedTime`. Reading from the newest record backwards and stopping at the checkpoint costs one page in the steady state.
*Tradeoff:* A backfill window that sits far in the past must page through everything newer than it to reach the window, because there is no way to ask the server to skip ahead. That cost is bounded by `max_pages` and resumable via the offset checkpoint, but it is real: seeding a five-year-old window on a large portal takes several sync ticks.
*See also:* [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md).

**2. The checkpoint is a timestamp plus a boundary id set, not a `(time, id)` compound cursor.**
*Why:* This is the decision most likely to be "corrected" back into a bug by someone pattern-matching on the ServiceNow connector, so the evidence matters. The live instance returns records sharing a `modifiedTime` in **ascending** id order inside the descending-time sequence — three articles from one bulk edit at `2026-06-03T13:31:29.000Z` arrive low-id-first. A descending tuple sort therefore never matches the response, so a compound cursor would either trip the ordering guard on every call or, worse, stop mid-tie and skip the rest of a bulk edit **permanently**. Relying only on the ordering the server actually provides, and resolving ties by remembering which ids were already emitted at the boundary timestamp, is correct regardless of intra-tie ordering.
*Tradeoff:* The checkpoint carries a list rather than a scalar, bounded at `MAX_BOUNDARY_IDS` (500). A single timestamp shared by more than 500 records overflows the bound and re-delivers that group next tick — absorbed by dedupe, never skipped.

**3. Descending order is what makes offset paging safe.**
*Why:* The ServiceNow connector refuses `sysparm_offset` for incremental sync because offset paging over an ascending list shifts rows *left* when records are updated mid-walk, silently skipping them. Descending order inverts that: an updated record jumps to position 1 and shifts everything after it one place *later*, so concurrent updates can only re-deliver, never skip.
*Tradeoff:* A *deletion* mid-walk shifts rows earlier and can skip one. The early stop keeps that window to the records changed since the last tick, and a backfill repairs it.

**4. One detail call per changed record, on by default.**
*Why:* Verified live — an `/articles` list row has `summary` but no `answer`; the body only exists on `/articles/{id}`. Ingesting list rows alone produces evidence whose body is a one-line teaser: searchable in name only, and worthless to the chunker and reranker.
*Tradeoff:* One extra HTTP round trip per changed record. Bounded by `DETAIL_FETCH_LIMIT` per invocation and disableable via `source_config["fetch_detail"] = false` for operators who want cheap summary-level sync.

**5. KB articles carry `evidence_type: "kb_article"` and take the document path, not the ticket path.**
*Why:* A single Zoho source emits two record shapes, which no previous connector did. An article is a structured document whose author-written headings are the meaningful chunk boundaries, and whose synthesis authority is *document*, not *ticket*. Letting a general "how the VPN works" page inherit ticket authority would let it outrank the actual incident record on incident-specific fields.
*Tradeoff:* Two shared resolvers had to learn about evidence type — `chunkers/registry.get_chunker` and `extraction_tasks.resolve_synthesis_role`. Both changes are additive: no existing source emits `kb_article`, and the attachment-resolution order for existing sources is deliberately unchanged.

**6. A module the token cannot read is skipped, not fatal.**
*Why:* The same rule the ServiceNow connector applies to `em_alert` on an instance without ITOM — and verified necessary here. The live instance's token carries only `Desk.articles.READ`, so a connector that aborted discovery on the tickets `403` would offer **nothing** from a portal with 629 syncable articles.
*Tradeoff:* A partial grant looks like a working integration unless someone reads the message. Mitigated by `validate_credentials` naming the ungranted modules and their required scopes, and by `probe_configuration` reporting the granted scope string directly.

**7. Product, team, account, and category are entities — never case-link keys.**
*Why:* The mass-merge guard, third application. A case-link key asserts "same incident" at confidence 1.0. One product name as a key would union every ticket about that product into a single canonical case.
*Tradeoff:* "Related to the same product" is a weaker graph signal than a case link, and correlation through it requires a traversal rather than a direct match. That is the correct strength for the claim.

**8. Related-ticket edges stay untyped.**
*Why:* Zoho's related/linked ticket lists carry no relation semantics. A guessed `caused_by_change` would poison change-risk assessment.
*Tradeoff:* No causal edges from Zoho, so change-risk cannot reason about Zoho-recorded causality. No edge beats a wrong one — the same call the SapphireIMS connector made.

**9. HTML conversion on the standard library, not `bs4`.**
*Why:* A connector is a poor reason to add an HTML parser to every deployment, and `html.parser` is lenient about the malformed markup pasted email produces.
*Tradeoff:* Lower fidelity than a real parser — styling, classes, and nested table structure are dropped. Headings, lists, and image placeholders are preserved because those are what the chunker and the "is this body empty" check depend on.

## Code map

| Concern | Module | Key symbol | When it runs |
| --- | --- | --- | --- |
| OAuth + transport | `connectors/zoho_desk/connector.py` | `_token`, `_get` | Every API call |
| Module discovery | `connectors/zoho_desk/connector.py` | `discover_objects`, `MODULES` | Discovery job |
| Incremental strategy | `connectors/zoho_desk/connector.py` | `_walk_desc` | Incremental + backfill |
| Checkpoint model | `connectors/zoho_desk/connector.py` | `_read_checkpoint`, `_checkpoint_data` | Between sync runs |
| Payload mapping | `connectors/zoho_desk/connector.py` | `_ticket_content`, `_article_content` | Per record |
| Conversation | `connectors/zoho_desk/connector.py` | `hydrate_thread` | Hydration worker |
| Setup diagnostics | `connectors/zoho_desk/connector.py` | `probe_configuration` | Operator-invoked |
| HTML → text | `connectors/zoho_desk/html_text.py` | `html_to_text` | Per record |
| Graph enrichment | `services/zoho_desk_reference_service.py` | `process_zoho_desk_references` | Correlation hook |
| Chunker routing | `services/chunkers/registry.py` | `_DOCUMENT_EVIDENCE_TYPES` | Normalize |
| Synthesis authority | `workers/extraction_tasks.py` | `EVIDENCE_TYPE_ROLE_MAP` | Episode reconstruction |
| Deep links | `services/source_deep_link_service.py` | `_zoho_desk_link` | Reviewer console |

## Acme VPN incident (this layer)

When Acme's VPN outage reaches a Zoho Desk portal, ticket `#4021` arrives as evidence with `record_kind: "incident"`, a `zoho_ticket:` thread that hydration later fills with both the customer's email chain and Network Ops' internal comments, and graph edges to the **VPN Gateway** product and the **Network Ops** team. Its related tickets `4019` and `4020` become symmetric case-link keys, so the three duplicate reports correlate into one canonical case regardless of the order they were ingested — and if an engineer later publishes a KB article about the RADIUS fix, that article enters as `kb_article` evidence with document authority, chunked on its own `## Resolution` heading, and surfaces alongside the incident rather than competing with it.

## Configuration reference

**Credentials**

| Key | Required | Notes |
| --- | --- | --- |
| `client_id`, `client_secret`, `refresh_token` | yes | Self-client or server-based OAuth app |
| `org_id` | yes | Sent as the `orgId` header on every call |
| `data_center` | no | `com` (default), `in`, `eu`, `au`, `jp`, `ca`, `sa`, `uk` — must match the portal |
| `accounts_url`, `api_base_url` | no | Override the derived host pair for private/sandbox deployments |

**Required OAuth scopes:** `Desk.tickets.READ` for tickets, `Desk.articles.READ` for the knowledge base, `Desk.settings.READ` for per-department discovery. Scopes are granted at token-issue time — adding one later requires re-issuing the refresh token.

**Source config**

| Key | Default | Effect |
| --- | --- | --- |
| `modules` | all | Subset of `tickets` / `articles` to sync |
| `module_filters` | `{}` | Per-module query params merged into every list call, e.g. `{"tickets": {"status": "Open"}}` |
| `per_department` | `false` | Offer one syncable object per department instead of one for all tickets |
| `fetch_detail` | `true` | Set false to skip detail calls (summary-level, no body) |
| `max_pages` | `20` | Page budget per sync invocation |
| `type_kind_map` | see connector | Zoho classification → shared record-kind vocabulary |
| `portal_url` + `org_slug` | — | Enables agent-console deep links |
| `deep_link_template` | — | Generic override, wins over everything |

## Known limits

- **Conversational bridging of Zoho ticket numbers.** Zoho numbers are bare integers with no system prefix, and the shared ticket-token regex deliberately never matches those (`order #12345 is unrelated` is an explicit assertion in `test_ticket_bridging.py`). Zoho tickets still register their number as a `CaseIdentifier` and get their primary case membership — only the conversational direction (a Teams message quoting `#4021` attaching to the ticket's case) is unavailable. Widening the shared regex would also match order numbers and hex colors, so it is a product decision rather than a connector one; the narrower fix is to resolve numeric candidates against registered identifiers inside `bridge_conversational_mentions`, which has `db` + `tenant_id` in scope.
- **Attachment bytes are not downloaded.** Attachment *metadata* is carried under `attachment_refs`; fetching the bodies is a bandwidth and retention decision for the operator, not a connector default.
- **Ticket-side behaviour is unverified against a live instance.** The live token grants only `Desk.articles.READ`, so the tickets path is implemented against Zoho's documented v1 contract and covered by tests, but has not been exercised against real ticket data. Run `probe_configuration()` after granting `Desk.tickets.READ` before trusting a first production sync.
- **No CMDB equivalent.** Zoho Desk has no configuration-management database, so there is no topology lookup to offer — product and account are the closest anchors and are modeled as entities.

## Further reading

- [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) — the connector contract and sync job lifecycle
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — how case-link keys and entity edges are consumed
- [CHUNKING_DESIGN.md](./CHUNKING_DESIGN.md) — the per-source chunker strategy table
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the authoritative deferred-work tracker
