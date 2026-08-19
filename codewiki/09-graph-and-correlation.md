# Graph and correlation

## Summary

You will learn how the platform ties evidence together and turns it into a traversable graph: **correlation edges** (evidence ↔ evidence, two tiers), the **context graph** (`graph_edges`, a registered vocabulary with temporal validity), the **relational → graph materializer**, **decision edges**, and the scheduled **contradiction** scan that checks approved playbooks against the knowledge base. After reading you should know which Celery task or service function writes each edge, in what order, and where every threshold and guard lives.

## Business picture

Incidents rarely live in one ticket. When Acme's VPN outage hits, the story is spread across a ServiceNow incident, a Jira clone, a Teams troubleshooting thread, and an engineer's root-cause email. The platform connects those records automatically — by shared ticket identifiers when it can prove the link, and by shared infrastructure (the same gateway, in the same week) when the signal is strong enough — so an analyst sees the full picture without hunting.

Those connections then power everything downstream: episode reconstruction reads the connected component to narrate what happened; search ranks playbooks higher when they are well-connected to relevant evidence; and the agent's Context Graph projection traverses the same edges to answer questions. The platform also periodically checks whether **official guidance** contradicts what teams actually see in the field, flagging conflicts for a knowledge manager before outdated advice reaches the people who need it most. And every operational **decision** — governed executions, AI-extracted actions from chat, and first-class decision records — lands in the same graph, so "who decided what, based on what" is a query, not an archaeology project.

## Technical walkthrough

### Where correlation runs in the pipeline

After normalization commits, `extraction.normalize_evidence` dispatches `correlate_evidence.delay(evidence_id, tenant_id)` (backend/src/contextedge/workers/extraction_tasks.py:1304, 1333-1334). The task `extraction.correlate_evidence` is routed to its own **`correlation`** queue — deliberately separate from `extraction`, because FIFO behind bulk normalization once starved the graph entirely: during the 2026-08-17 Zoho backfill, correlation had been dispatched and never once received while the extraction queue grew by ~70 tasks/minute (backend/src/contextedge/workers/celery_app.py:234-258). The task retries twice with a 60s delay (backend/src/contextedge/workers/correlation_tasks.py:12-17). When a run creates at least one correlation, it schedules `extraction.reconstruct_episode` with `countdown = RECONSTRUCT_DEBOUNCE_SECONDS = 180` — debounced episode reconstruction, which re-checks settlement at run time (correlation_tasks.py:39-57; extraction_tasks.py:746). ServiceNow enrichment can also fan out `evaluation.warm_cmdb_topology` for stale CI references (correlation_tasks.py:58-67).

### `correlate_evidence_item` — the two correlation tiers

The service entry point is `correlate_evidence_item(db, tenant_id, evidence_id)` (backend/src/contextedge/services/correlation_service.py:197-791). Setup: load the evidence row, its `Source`, its `RawEvidenceObject` and payload — `load_raw_payload` fetches payloads offloaded to MinIO when they exceeded 32KB, falling back to the inline dict (correlation_service.py:210-220) — and the parent thread's external id (222-226).

**Tier 1 — deterministic case links (confidence 1.0).** `extract_case_link_candidates` builds `(system, external_id)` keys (correlation_service.py:116-194): the record's own external id; `{source}:thread` plus the thread id; ServiceNow task references (`problem_id` / `rfc` / `caused_by` / `parent_incident` sys_ids join the same namespace as the referenced records' own ids, so incident ↔ problem ↔ change correlate regardless of ingestion order, 140-153); Jira linked-issue keys (154-166); SapphireIMS related tickets (167-176); Zoho `ticket_number` plus related ids (177-191). CI and assignment-group references are deliberately **never** case-link keys — shared infrastructure would mass-merge unrelated cases (comment at 144-147). Each candidate either creates a `case_links` row at confidence 1.0 under the resolved `canonical_case_id` (the first existing link wins the case id; otherwise a fresh UUID, 246-250, 415-437) or refreshes `last_seen` on the existing row — the original `evidence_id` anchor is never overwritten (440-446).

**Tier 2 — identity co-occurrence (gated, scored).** Only identities in `resolution_state ∈ (resolved, verified)` and active count — a provisional identity is an unreviewed guess (correlation_service.py:261-274). Degree statistics (distinct evidence links per identity, tenant-wide) are computed *before* the link fetch so hub identities never fan out (276-301). The gates, with constants at correlation_service.py:36-50:

- `IDENTITY_CORRELATION_WINDOW = 7 days` — outside it, no signal; missing timestamps **fail closed** (329-338).
- `HUB_DEGREE_MIN = 200` — identities at or above carry zero signal (48, 66-71).
- Rare non-person entity (degree between 1 and `RARE_DEGREE_MAX = 5`) scores `RARE_ENTITY_CONFIDENCE = 0.75`; common non-person `0.65`; +0.1 when ≥2 non-hub identities are shared, capped at 0.85 (`_identity_correlation_signal`, 53-88). An identity with no degree statistic fails open into the common tier rather than the rare one — a missing count must not manufacture a strong signal (60-64, 78-82).
- Person-only overlap needs ≥2 shared non-hub identities → 0.5; a single shared person is dropped entirely — "John commented on both tickets" is not a correlation (86-88).
- **Conflicting-ticket veto:** when both items hold *anchor* case memberships (`primary_case`, `explicit_reference`, `reply_inheritance`, `thread_topic`) in disjoint case sets, the identity correlation is deleted and `correlation.conflicting_ticket_veto` logged — same infrastructure, different incidents (344-404).

**Edge writes.** One `correlation_edges` row per pair, direction-agnostic dedupe, created once and **never upgraded**; when both tiers matched, the deterministic tier wins: `correlation_type="case_link_match"`, confidence 1.0, explanation `"Matched canonical case {id}"` — otherwise `identity_match` with the tier's score (correlation_service.py:448-487). The model is `CorrelationEdge`: `source_evidence_id`, `target_evidence_id`, `correlation_type`, `confidence` (default 0.5), `explanation`, `created_by` (backend/src/contextedge/models/episode.py:187-210).

**Enrichment, fail-soft.** Each source-specific enrichment runs in its own SAVEPOINT (`begin_nested()`), so a failure loses enrichment but never the correlation: ServiceNow reference processing (typed `related_problem` / `caused_by_change` / `remediated_by_change` / `child_of_incident` edges plus CI and assignment-group entities, correlation_service.py:489-519); **ticket-number bridging** — ticket sources register their quotable number, conversational sources resolve quoted numbers into `evidence_case_memberships`, plus Teams message lifecycle, reply inheritance, corrections, thread topics, and indirect references as a last resort (521-685); SapphireIMS (687-707), Zoho Desk (709-729), and Jira (731-756). The run finishes with operational event `correlation.case_linked` carrying all counters (759-777). Known limit: bare-integer Zoho ticket numbers are deliberately not matched by the shared bridging regex, so a Teams message quoting `#4021` does not bridge (codewiki/KNOWN_GAPS.md:99).

### From correlation to episode clusters

Reconstruction never reads raw correlation state directly — `resolve_episode_cluster(db, tenant_id, seed_evidence_ids)` materializes the connected component over `case_links` (shared canonical case) plus `correlation_edges` (both directions) first (backend/src/contextedge/services/episode_cluster_service.py:108-283). Bounds: `MAX_CLUSTER_SIZE = 50`, `MAX_HOPS = 3`, `CLUSTER_TIME_WINDOW = 30 days` from the nearest seed (episode_cluster_service.py:47-49, 96-105). Visibility is fenced in SQL — legal hold and pending redaction never enter a cluster (66-93). Two membership types are explicitly never expanded through: `mentioned_only` (multi-ticket digest guard) and `recurrence` — a recurrence membership means "similar problem, **never** the same occurrence" (169-171, 188-190).

### Graph builder — the write API for `graph_edges`

(backend/src/contextedge/graph/builder.py)

- `add_edge` validates the type via `require_registered`, then inserts with `valid_from = now()` (builder.py:16-47).
- `ensure_edge` idempotently creates the *active* (`valid_to IS NULL`) logical edge: SELECT-first, then `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index `uq_graph_edges_active_logical`, then a re-select for the race loser — two racing workers cannot abort the enclosing transaction (builder.py:50-135). **`weight` is traversal importance; `confidence` is belief** — callers pass both when they mean both (63-72).
- `close_edge` sets `valid_to` on the active edge; the type is validated even here, because a typo would "close nothing and report success" (builder.py:138-173). `replace_edge` = close + re-add at one timestamp — temporal versioning; it has no production callers yet (176-217; codewiki/KNOWN_GAPS.md:66).
- Decision linkers create the decision edges: `based_on` (decision → evidence/episode/pattern), `considered` + `chose` (→ option), `applied_policy`, `resulted_in`, `followed_by` (builder.py:328-469). `link_decision_approval` (`decision -[required_approval]-> approval_request`, builder.py:424-438) sits in the same block but has no caller anywhere outside the builder, so that edge type is registered and never written.
- `persist_pattern_enrichment_edges` turns pattern enrichment (triggers, entities, errors, root causes) into real edges from **virtual concept nodes** — deterministic `uuid5` under `ENRICHMENT_NAMESPACE`, so re-runs are idempotent — at weight 1.5: `trigger_of`, `involved_in`, `discovered_in`, `causes` (builder.py:13, 472-518).

The `graph_edges` row carries `tenant_id`, `domain_id`, both endpoints as `(node_type, node_id)` pairs, `edge_type`, `weight` (≥0 check), `confidence` (0..1 check), `metadata_extra`, and `valid_from`/`valid_to`. The active-edge unique index covers the full logical key `WHERE valid_to IS NULL` with `NULLS NOT DISTINCT`, which is what makes `ensure_edge` race-safe (backend/src/contextedge/models/pattern.py:174-199).

**Edge-type registry (F2).** `graph/edge_types.py` declares the entire write-side vocabulary — 69 types in five semantic groups (backend/src/contextedge/graph/edge_types.py:36-137). `require_registered` raises `UnknownEdgeType` for anything else, and `add_edge` / `ensure_edge` / `close_edge` / `replace_edge` all call it (edge_types.py:31-32, 186-200). Adding a type is two decisions: register it, then either allowlist it for the agent projection or record the exclusion reason in `PROJECTION_EXCLUSIONS` — `tests/test_edge_type_registry.py` enforces the pairing (edge_types.py:15-25). 16 registered types are deliberately not agent-traversable, each with its reason in that dict (edge_types.py:142-183) — fan-out hubs like `mentions_identity` would spend the projection budget on identity hubs instead of topology. (KNOWN_GAPS.md:31 still says 18; the code is the 16 above.)

**Temporal predicates.** `edge_valid_at(as_of)` returns `valid_to IS NULL` for current-state queries, or the point-in-time window predicate when `as_of` is set (backend/src/contextedge/graph/temporal.py:29-36). `normalize_graph_as_of` rejects naive datetimes and timestamps more than 5 minutes in the future with 422 (temporal.py:12-26). Semantics caveat, do not gloss: historical **edges** combine with **current node facts**, so point-in-time reads answer "what was connected then", not "what was true then" (codewiki/KNOWN_GAPS.md:66).

### Relational → graph materializer

`GraphRelationshipMaterializer.reconcile_tenant(tenant_id, batch_size=500)` streams relational rows and calls `ensure_edge` for each — idempotent, additive-only (backend/src/contextedge/graph/agent/materializer.py:107-359; the `_edge` wrapper that skips NULL targets is at 78-105). It derives edges from sessions (`involves_user` / `targets_workflow` / `tracks_request` / `runs_on_agent`), execution runs (`has_execution`, `executes`), approvals (`requires_approval`), claims (`asserted_in`, `superseded_by`, and `supported_by` / `contradicted_by` / `weakened_by` from claim-evidence links), decision links (`based_on`, `supported_by_claim`, `applied_policy`), action policies (`governs`), error signatures (`aggregated_by`), fix patterns (`addresses` / `applies_to` / `recommends`), and case outcomes (`resulted_in`, plus `validated_fix` / `invalidated_fix` / `partially_validated_fix` from fix results, `FIX_RESULT_EDGE_TYPES` at materializer.py:42-45). Domain derivation follows migration `0031`'s rule — exactly one owning row per edge type — and every writer must agree, or the unique index treats the same logical edge with different domains as distinct (materializer.py:23-37). Scheduling: Beat task `evaluation.reconcile_graph_relationships` every 6 hours on the `evaluation` queue (backend/src/contextedge/workers/celery_app.py:329-333; task at backend/src/contextedge/workers/graph_tasks.py:33-35). There is no event-driven materialization; reconciliation is additive-only (KNOWN_GAPS.md:66). Caveats: `FixPattern` has no constructor anywhere, so the fix-validation edge family is dormant until Epic B populates the table (KNOWN_GAPS.md:10). `claim_evidence` and `decision_claims` have no writers either (KNOWN_GAPS.md:11), and neither does `decision_action_policies` — the only reference to that model in the whole backend is this materializer's own query (backend/src/contextedge/models/action_policy.py:133; materializer.py:262-264). Those derivations are wired but currently stream zero rows.

### Decision edges — three capture tiers

The graph captures operational decisions at three fidelity levels:

- **Tier 1 — observed decisions (AI-extracted).** During normalization, `link_evidence_decisions` runs the decision extractor over evidence text ("jsmith restarted vpn-gw-east-01"), resolves actor and target against canonical identities, and writes `evidence -[records_decision]-> identity(actor)` and `evidence -[records_action_on]-> identity(target)` edges with `decision_type` / `action` / `context` in metadata (backend/src/contextedge/services/decision_service.py:21-95; called from the normalization worker at backend/src/contextedge/workers/extraction_tasks.py:295, 551).
- **Tier 2 — governed execution edges.** `execution_service` writes `session -[executed_playbook]-> playbook` at start (backend/src/contextedge/services/execution_service.py:950-957), `approval_request -[approved_by | denied_by]-> user` at decide time (1429), `user -[modified_by]->` on the modify flow (1575), and `execution_run -[execution_outcome]-> playbook` at completion (1694).
- **Tier 3 — first-class decision records.** `decision_trace_service.create_decision` mints a `Decision` row and its typed edges via the builder linkers; `execution_service` embeds `create_decision` at start, decide, and modify (execution_service.py:978, 1441, 1589), and the MAF agent's write-back path uses the same function. See [16-decision-traces.md](./16-decision-traces.md) for the full write path, retrieval, and analytics.

### Issue signatures and recurrence (where they touch the graph)

When an episode is approved, one LLM call distills it into a generalized problem fingerprint (`issue_signatures`), linked by an `episode -[has_signature]-> issue_signature` edge — written fail-soft, so an edge failure never fails the extraction (backend/src/contextedge/services/issue_signature_service.py:216-236). When the signature already existed, `_link_recurrence` adds a **`recurrence` case membership** (confidence 0.6) from the new episode's seed evidence to the previous occurrence's case — a precedent pointer for retrieval, never a merge: the cluster resolver refuses to expand through it (issue_signature_service.py:249-312; episode_cluster_service.py:169-171). Signatures are also a maf.v1 node type and their own agent seed layer, matched by full-text search over the de-slugged signature fields (backend/src/contextedge/graph/agent/profiles.py:85; backend/src/contextedge/graph/agent/repository.py:249-297).

### Contradictions

`contradiction_service.scan_contradictions` keeps LLM spend bounded with a **three-gate** flow (backend/src/contextedge/services/contradiction_service.py:318+):

1. **ANN top-K KB candidates** — for each `(playbook_version, step)` pair, pull only the `top_k` (default `DEFAULT_TOP_K_CANDIDATES = 20`, contradiction_service.py:59) most similar knowledge-typed evidence rows, ordered by `halfvec_cosine_distance` after `tune_ann_recall` (236-242). All predicates — tenant, evidence type, embedding-not-null, domain scope, and the shared `exclude_legal_hold()` filter — are applied **before** the ORDER BY/LIMIT so the index cooperates with the filter and legal-hold evidence never reaches the LLM (206-242).
2. **Incremental cursor** — `contradiction_scan_state` (migration `0022`) holds one row per `(playbook_version_id, evidence_id)` pair the scan has already looked at. `_load_scan_cursor` reads them all for the version up front (248-263), `_record_scan_state` upserts on that unique pair (266-301), and `_needs_rescan` re-examines a pair only when it has never been scanned or the evidence changed after the last scan — otherwise the pair is skipped before any further work (304-315, checked at 407-410).
3. **Token-overlap gate + budget** — `should_compare_contradiction` requires ≥2 shared tokens, or exactly 1 against a genuinely tiny fragment (`min(len) ≤ 3`) (75-87). A hard `max_llm_calls` cap (default `DEFAULT_SCAN_BUDGET = 1000`, line 64) terminates the scan cleanly (424-431).

On a hit, the versioned `contradiction` prompt runs (`get_prompt("contradiction", tenant_id)`, line 134), a `Contradiction` row is upserted — `_get_or_create_contradiction` catches the `IntegrityError` from the unique pair index so concurrent scanners merge instead of duplicating (152-193, 479) — graph edges are added via `add_contradicts_edge` (builder.py:290-325), and notifications fire, capped at 10 individual sends per scan with one summary afterwards (357-363, 509-556). The scan runs on Beat every 12 hours (`evaluation.scan_contradictions_task`, celery_app.py:287-291; task at backend/src/contextedge/workers/evaluation_tasks.py:88-90). The hybrid ranker reads `contradicts` edges as its negative-penalty signal (backend/src/contextedge/search/hybrid_ranker.py:140-163).

### Graph read surface

`graph/queries.py` is the read module: `get_neighbors` does iterative breadth-first search up to `MAX_TRAVERSAL_DEPTH = 3`, returning per-edge dicts with `direction` and `depth`, honoring `edge_valid_at(as_of)` and the domain-or-NULL predicate (backend/src/contextedge/graph/queries.py:12, 20-81). `get_pattern_subgraph` (:84), `get_entity_subgraph` (:375), and `get_graph_stats` (:587) complete it.

One size bound worth being precise about: `MAX_SUBGRAPH_NODES = 250` / `MAX_SUBGRAPH_EDGES = 500` exist because the UI renders the whole response, but only `get_pattern_subgraph` applies them (queries.py:16-17, 120, 148). `get_entity_subgraph` — the function behind `GET /graph/subgraph/{entity_type}/{entity_id}` — has no node or edge cap at all; its only limit is the 1–3 hop depth (queries.py:375-385). A well-connected entity can therefore return a much larger payload than the pattern route ever will.

The `/api/v1/graph` router exposes (backend/src/contextedge/api/v1/graph.py):

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/graph/agent-subsets` | Ranked, bounded, authorization-scoped agent projection (:18-31) |
| `GET` | `/graph/neighbors` | BFS neighbors, `max_depth` 1–3, optional `domain_id` + `as_of` (:190-217) |
| `GET` | `/graph/subgraph/{entity_type}/{entity_id}` | `{nodes, edges}` for visualization (:220-239) |
| `GET` | `/graph/stats` | Edge-type and node-type counts (:242-255) |
| `GET` | `/graph/cmdb-topology` | Live ±1-hop ServiceNow CI neighborhood, cache-first (:34-50) |
| `POST` | `/graph/fix-outcomes` | Record a fix outcome against a CI; updates cohort counters (`knowledge_manager`) (:53-76) |
| `GET` | `/graph/fix-applicability` | Deterministic fix-applicability assessment for a CI (`knowledge_manager`) (:79-97) |
| `GET` | `/graph/change-risk` | Deterministic change-risk profile for a CI (:100-117) |
| `GET`/`POST` | `/graph/edge-proposals[...]` | Review queue for agent-proposed `proposed_depends_on` edges; approve promotes to authored `depends_on` and closes the proposal — supersede, never delete (`knowledge_manager`) (:120-187) |

Scope caveat (open, P1-6): `/graph/agent-subsets` builds a fully scoped projection, but `/graph/neighbors`, `/graph/subgraph`, and the CMDB/change-risk/fix routes filter by **tenant only** — a domain-limited principal can read wider here than its projection would allow (codewiki/KNOWN_GAPS.md:56).

## Example: Acme VPN data at this stage

**Input** (what arrives) — three evidence items from different systems:

| Evidence | Source | Summary |
| --- | --- | --- |
| ev-a1b2c3 | ServiceNow (INC0010427) | VPN users unable to connect; AUTH_CERT_EXPIRED on vpn-gw-east-01 |
| ev-d4e5f6 | Teams (#vpn-support) | Engineers discuss AUTH_CERT_EXPIRED on vpn-gw-east-01 |
| ev-g7h8i9 | Email | Engineer's root-cause note quoting "INC0010427" |

**Output** (what the system produces) — correlation edges from the two tiers:

```json
[
  {
    "source_evidence_id": "ev-g7h8i9",
    "target_evidence_id": "ev-a1b2c3",
    "correlation_type": "case_link_match",
    "confidence": 1.0,
    "explanation": "Matched canonical case 7d1f22c0-...",
    "created_by": "correlation_worker"
  },
  {
    "source_evidence_id": "ev-d4e5f6",
    "target_evidence_id": "ev-a1b2c3",
    "correlation_type": "identity_match",
    "confidence": 0.75,
    "explanation": "Shared rare operational entity within time window",
    "created_by": "correlation_worker"
  }
]
```

The email lands at 1.0 because its quoted ticket number resolved through the ticket bridge into the same canonical case; the Teams thread lands at 0.75 because `vpn-gw-east-01` is a rare entity (degree ≤ 5) shared inside the 7-day window. If the Teams thread had been firmly anchored to a *different* ticket, the conflicting-ticket veto would have deleted that second edge.

**Output** — context-graph edges around the same incident:

```json
[
  {
    "source_node": {"type": "playbook", "id": "pb-3fa8c21b04d9"},
    "target_node": {"type": "identity", "id": "id:vpn-gw-east-01"},
    "edge_type": "references_identity",
    "weight": 1.0, "valid_from": "2026-07-02T09:14:00Z", "valid_to": null
  },
  {
    "source_node": {"type": "evidence", "id": "ev-d4e5f6"},
    "target_node": {"type": "identity", "id": "id:jsmith"},
    "edge_type": "records_decision",
    "metadata": {"decision_type": "restart", "action": "restarted vpn-gw-east-01"}
  },
  {
    "source_node": {"type": "episode", "id": "ep-vpn-outage"},
    "target_node": {"type": "issue_signature", "id": "sig-cert-expired"},
    "edge_type": "has_signature",
    "weight": 1.0, "confidence": 0.8
  }
]
```

**Output** — a contradiction found by the scheduled scan:

```json
{
  "playbook_id": "pb-3fa8c21b04d9",
  "conflicting_evidence_id": "ev-kb-old-vpn",
  "description": "KB 'VPN Troubleshooting Guide v2.1' still recommends disabling MFA for VPN, conflicting with the approved certificate-rotation playbook",
  "severity": "medium"
}
```

The contradiction prompts the knowledge manager to update the KB article or revise the playbook; until then the `contradicts` edge also penalizes the playbook in runtime ranking.

## Design decisions

- **Adjacency in Postgres vs a dedicated graph DB** — *Why:* simpler ops and transactional consistency with evidence, playbooks, and decisions; `ensure_edge` rides the same transaction as the row it describes. *Tradeoff:* deep graph algorithms are harder than in Neo4j-style stores; traversal is bounded BFS with explicit caps.
- **Separate correlation edges from graph edges** — *Why:* correlation is evidence-centric, pairwise, and consumed by cluster resolution and ranking; `graph_edges` is the many-node-type context graph with temporal validity and a registered vocabulary. *Tradeoff:* two concepts to explain, and the hybrid ranker reads both.
- **A registered edge vocabulary enforced at write time (F2)** — *Why:* `edge_type` was free text written from 26 modules; a typo produced a real, queryable edge the agent projection silently dropped — the graph knew something the agent could never see, and nothing failed (edge_types.py:1-13). *Tradeoff:* adding a relationship now takes two deliberate decisions (register + project-or-exclude-with-reason), and the static test cannot see types assembled from constant tables — the runtime check is the real net.
- **Create-once correlation edges, deterministic tier wins** — *Why:* edges are never upgraded in place, so confidence always reflects the evidence that created the edge; when both tiers fire, the provable 1.0 case link beats the fuzzy identity score (correlation_service.py:256-259, 469-476). *Tradeoff:* a pair first linked at 0.65 stays at 0.65 even if stronger evidence arrives later — the case-link tier will add its own 1.0 path, but the old edge is not rewritten.
- **Additive-only materialization on a 6h clock** — *Why:* streaming relational rows through the idempotent `ensure_edge` is simple and safe to re-run; the ON CONFLICT index makes overlap free (materializer.py:78-105, 107-359). *Tradeoff:* deleted or changed relational rows are not reconciled (no closes), and new relationships can lag up to 6 hours in the graph — event-driven materialization is a recorded open item (KNOWN_GAPS.md:66).
- **Temporal validity on edges, but current-state node facts** — *Why:* `valid_from`/`valid_to` plus the active-edge partial unique index give supersede-never-delete semantics and point-in-time traversal cheaply. *Tradeoff:* `as_of` reads combine historical topology with current node facts, so callers must not draw historical operational conclusions — the projection warns about exactly this (KNOWN_GAPS.md:66).
- **LLM-assisted contradiction detection behind three deterministic gates** — *Why:* ANN top-K + a scan cursor + a token-overlap gate cut LLM calls by an expected 80–95% on warm tenants while a hard budget bounds the worst case (KNOWN_GAPS.md:281). *Tradeoff:* rare contradictions with full vocabulary mismatch can be missed by the token gate, and a budget-exhausted scan finishes the rest next cycle.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Correlation service | `backend/src/contextedge/services/correlation_service.py` | `correlate_evidence_item` (:197), `extract_case_link_candidates` (:116), `_identity_correlation_signal` (:53), `create_correlation` (:91) | correlation queue |
| Correlation worker | `backend/src/contextedge/workers/correlation_tasks.py` | `correlate_evidence` (:12), reconstruction dispatch (:39-57) | after normalize |
| Cluster resolution | `backend/src/contextedge/services/episode_cluster_service.py` | `resolve_episode_cluster` (:108), `MAX_CLUSTER_SIZE`/`MAX_HOPS`/window (:47-49) | before episode synthesis |
| Graph mutations | `backend/src/contextedge/graph/builder.py` | `add_edge` (:16), `ensure_edge` (:50), `close_edge` (:138), `replace_edge` (:176), decision linkers (:328-469), `persist_pattern_enrichment_edges` (:477) | all edge writers |
| Edge vocabulary | `backend/src/contextedge/graph/edge_types.py` | `UnknownEdgeType` (:31), `EDGE_TYPES` (:137), `PROJECTION_EXCLUSIONS` (:142), `require_registered` (:186) | every builder call |
| Temporal predicates | `backend/src/contextedge/graph/temporal.py` | `edge_valid_at` (:29), `normalize_graph_as_of` (:12) | graph reads |
| Materializer | `backend/src/contextedge/graph/agent/materializer.py` | `GraphRelationshipMaterializer.reconcile_tenant` (:107), `FIX_RESULT_EDGE_TYPES` (:42) | Beat, 6h |
| Graph reads | `backend/src/contextedge/graph/queries.py` | `get_neighbors` (:20), `get_pattern_subgraph` (:84), `get_entity_subgraph` (:375), `get_graph_stats` (:587) | services / API |
| Graph HTTP API | `backend/src/contextedge/api/v1/graph.py` | `graph_neighbors` (:190), `graph_subgraph` (:220), `graph_stats` (:242), edge proposals (:120-187) | HTTP |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | `scan_contradictions` (:318), `should_compare_contradiction` (:75), `_get_or_create_contradiction` (:152) | Beat, 12h |
| Observed decisions (Tier 1) | `backend/src/contextedge/services/decision_service.py` | `link_evidence_decisions` (:21) | normalization worker |
| Governed decisions (Tier 2/3) | `backend/src/contextedge/services/execution_service.py` | `executed_playbook` (:950), approve/deny edges (:1429), `create_decision` embeds (:978, :1441, :1589) | execution API |
| Issue signatures | `backend/src/contextedge/services/issue_signature_service.py` | `extract_issue_signature` (:89), `has_signature` edge (:216-236), `_link_recurrence` (:249) | after episode approval |
| Models | `backend/src/contextedge/models/pattern.py` | `GraphEdge` + `uq_graph_edges_active_logical` (:174-199) | ORM |
| Correlation model | `backend/src/contextedge/models/episode.py` | `CorrelationEdge` (:187-210) | ORM |

## Acme VPN incident (this layer)

When Acme's duplicate VPN tickets arrive, `correlate_evidence` (correlation queue) links the ServiceNow incident to its Jira clone at 1.0 through shared reference keys, the engineer's email joins the same canonical case through its quoted "INC0010427", and the Teams thread correlates at 0.75 through the rare `vpn-gw-east-01` identity inside the 7-day window. `resolve_episode_cluster` then hands reconstruction the whole connected component — legal-hold-fenced and time-fenced — and the resulting approved episode gains a `has_signature` edge to the `remote_access | tls_certificate | certificate_expired` signature. Six months later the same failure recurs, and the new episode's first evidence gets a 0.6 `recurrence` pointer back to this case — a precedent, never a merge. Meanwhile the 12-hour contradiction scan flags the KB article that still says "disable MFA for VPN", writing a `contradicts` edge that both alerts the knowledge manager and penalizes that guidance in ranking.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — how graph and correlation counts become ranking signals
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — what reconstruction does with the cluster
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — queue topology, including the dedicated correlation lane
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — where the identities behind Tier 2 come from
- [16-decision-traces.md](./16-decision-traces.md) — the decision records behind the decision edges
- [`docs/API.md`](../docs/API.md) — `/graph` endpoint catalog
