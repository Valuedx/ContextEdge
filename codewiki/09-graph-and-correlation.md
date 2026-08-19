# Graph and correlation

## Summary

You will learn how the platform ties evidence together and turns it into a traversable graph: **correlation edges** (evidence ↔ evidence, two tiers), the **context graph** (`graph_edges`, a registered vocabulary with temporal validity), the **relational → graph materializer**, **decision edges**, and the scheduled **contradiction** scan that checks approved playbooks against the knowledge base. After reading you should know which Celery task or service function writes each edge, in what order, and where every threshold and guard lives.

You will also learn what a cluster is allowed to *become*. Three objects sit downstream of correlation and the code works hard to keep them apart: an **episode** is an account of something that happened, a **knowledge case** is what a document claims works, and an **operational situation** is one bounded occurrence that is happening right now. The first two are live; the third is schema and vocabulary only, and this page says so wherever it comes up.

## Business picture

Incidents rarely live in one ticket. When Acme's VPN outage hits, the story is spread across a ServiceNow incident, a Jira clone, a Teams troubleshooting thread, and an engineer's root-cause email. The platform connects those records automatically — by shared ticket identifiers when it can prove the link, and by shared infrastructure (the same gateway, in the same week) when the signal is strong enough — so an analyst sees the full picture without hunting.

Those connections then power everything downstream: episode reconstruction reads the connected component to narrate what happened; search ranks playbooks higher when they are well-connected to relevant evidence; and the agent's Context Graph projection traverses the same edges to answer questions. The platform also periodically checks whether **official guidance** contradicts what teams actually see in the field, flagging conflicts for a knowledge manager before outdated advice reaches the people who need it most. And every operational **decision** — governed executions, AI-extracted actions from chat, and first-class decision records — lands in the same graph, so "who decided what, based on what" is a query, not an archaeology project.

There is a matching discipline about what correlation is *not* allowed to conclude. A KB article explaining how to fix an expired VPN certificate is genuinely useful, and it correlates, embeds, reaches the graph and seeds patterns like anything else — but it is not a record that an engineer fixed anything. The moment the system narrates it as an episode, every downstream reader treats a document's claim as an observed success: the playbook prompt is told episode outcomes are empirical evidence a step works, patterns count them as recurrence, and the agent cites them as `[ep-N]`. So a cluster made only of knowledge is refused synthesis, and its content belongs instead in a **knowledge case** — a separate object whose supported pattern keeps a ledger of *which kind* of evidence it is standing on, "two articles document this, nineteen incidents observed it, fourteen of those succeeded" rather than a single number that cannot tell those apart. Be precise about how much of that runs today: the refusal is live on every reconstruction, the tables and the ledger exist and are populated, but **no ingest path mints a knowledge case from a newly-arrived article**. The 135 cases in the table were created by an operational run, and the gate today simply stops — it does not hand the cluster on to anything.

## Technical walkthrough

### Where correlation runs in the pipeline

After normalization commits, `extraction.normalize_evidence` dispatches `correlate_evidence.delay(evidence_id, tenant_id)` (backend/src/contextedge/workers/extraction_tasks.py:1427; the re-classification fan-out for items a stale verdict skipped dispatches the same task at :1487). The task `extraction.correlate_evidence` is routed to its own **`correlation`** queue — deliberately separate from `extraction`, because FIFO behind bulk normalization once starved the graph entirely: during the 2026-08-17 Zoho backfill, correlation had been dispatched and never once received while the extraction queue grew by ~70 tasks/minute (backend/src/contextedge/workers/celery_app.py:234-258). The task retries twice with a 60s delay (backend/src/contextedge/workers/correlation_tasks.py:12-17). When a run creates at least one correlation, it schedules `extraction.reconstruct_episode` with `countdown = RECONSTRUCT_DEBOUNCE_SECONDS = 180` — debounced episode reconstruction, which re-checks settlement at run time (correlation_tasks.py:39-57; extraction_tasks.py:765). ServiceNow enrichment can also fan out `evaluation.warm_cmdb_topology` for stale CI references (correlation_tasks.py:58-67).

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

**Enrichment, fail-soft.** Each source-specific enrichment runs in its own SAVEPOINT (`begin_nested()`), so a failure loses enrichment but never the correlation: ServiceNow reference processing (typed `related_problem` / `caused_by_change` / `remediated_by_change` / `child_of_incident` edges plus CI and assignment-group entities, correlation_service.py:489-519); **ticket-number bridging** — ticket sources register their quotable number, conversational sources resolve quoted numbers into `evidence_case_memberships`, plus Teams message lifecycle, reply inheritance, corrections, thread topics, and indirect references as a last resort (521-685); SapphireIMS (687-707), Zoho Desk (709-729), and Jira (731-756). The run finishes with operational event `correlation.case_linked` carrying all counters (759-777). Known limit: bare-integer Zoho ticket numbers are deliberately not matched by the shared bridging regex, so a Teams message quoting `#4021` does not bridge ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Resolved: Zoho Desk connector", the *Not built, honestly* paragraph).

### From correlation to episode clusters

Reconstruction never reads raw correlation state directly — `resolve_episode_cluster(db, tenant_id, seed_evidence_ids)` materializes the connected component over `case_links` (shared canonical case) plus `correlation_edges` (both directions) first (backend/src/contextedge/services/episode_cluster_service.py:108-283). Bounds: `MAX_CLUSTER_SIZE = 50`, `MAX_HOPS = 3`, `CLUSTER_TIME_WINDOW = 30 days` from the nearest seed (episode_cluster_service.py:47-49, 96-105). Visibility is fenced in SQL — legal hold and pending redaction never enter a cluster (`_visible_times`, 65-92). Two membership types are explicitly never expanded through: `mentioned_only` (multi-ticket digest guard) and `recurrence` — a recurrence membership means "similar problem, **never** the same occurrence" (169-171, 188-190).

### The observational gate — a cluster must contain something that happened

Resolving a cluster is not the same as being allowed to narrate it. `_reconstruct` runs one last check before it spends an LLM call: `_cluster_has_observational_evidence(db, tenant_id, evidence_ids)` selects the distinct `evidence_type` values in the cluster and returns True as soon as one of them is *not* knowledge (backend/src/contextedge/workers/extraction_tasks.py:1014-1049, called at :1219-1230). "Knowledge" is exactly three types — `kb_article`, `sop`, `documentation` — defined next to the code that produces them, in `KNOWLEDGE_EVIDENCE_TYPES` (backend/src/contextedge/services/evidence_typing.py:92; the `is_knowledge_evidence` helper is at :149-157). A cluster of only those returns `{"status": "skipped_knowledge_only_cluster"}` and logs `episode.skipped_knowledge_only_cluster`; nothing else changes.

Three things about this gate are deliberate and worth stating plainly:

- **It gates synthesis only.** Knowledge still correlates, still embeds, still reaches `graph_edges`, still seeds patterns and is still retrievable. What it may not do is become an account of an event (comment at extraction_tasks.py:1195-1218).
- **It fails OPEN.** An empty id list, a query that raises, a NULL `evidence_type`, a result with no real type strings — every one of those returns True and lets synthesis proceed (:1027-1048). The reasoning is stated in the docstring (:1017-1024): wrongly allowing synthesis costs one reviewable draft, wrongly blocking it costs a real incident that silently never becomes an episode. Only a cluster *positively identified* as knowledge-only is refused.
- **It sits last, not first.** Every cheaper exit runs above it — the `MIN_AUTO_SYNTHESIS_CLUSTER` too-small check (:1073-1088), the resolution-signal gate (:1090-1114), the per-cluster advisory lock (:1116-1137), the debounce settlement check (:1139-1174) and the duplicate-draft fingerprint check (:1176-1193) — so the extra query is paid only by a cluster that was otherwise about to call the model.

The prompting fact: 299 episodes on this corpus had all-knowledge evidence, 8 of them predating the knowledge backfill that took the article corpus from 53 to 629 — so this was never a backfill artifact, just too rare to notice (same comment block). One inconsistency to know about: `runbook` is an accepted *upload* type (`UPLOADABLE_EVIDENCE_TYPES`, evidence_typing.py:104-115) but is **not** in `KNOWLEDGE_EVIDENCE_TYPES`, so a cluster of only uploaded runbooks still passes this gate — while migration `0073`'s own source filter does list `runbook` (0073:136). Recorded in [KNOWN_GAPS.md](./KNOWN_GAPS.md) as "The synthesis gate and the migration disagree about `runbook`".

### Knowledge cases and the pattern evidence ledger

An all-knowledge cluster's reconstruction is not thrown away — it is often the only structured description of a failure mode nobody has hit yet. It goes into `knowledge_cases` (+ `knowledge_case_steps`), added by migration `0072` alongside `pattern_evidence` (backend/alembic/versions/0072_knowledge_case_and_pattern_evidence.py:1-36).

**`KnowledgeCase`** (backend/src/contextedge/models/knowledge_case.py:46-136) carries the same reconstructed semantics an episode does — title, `symptom_summary`, entity refs, applicability, embedding, extraction confidence — plus its provenance: `source_evidence_id`, `source_kind`, `source_authority` (a vendor advisory and a community post are both "documented" and not equally trustworthy), and `source_state`, the article's lifecycle in *its own* system. What it deliberately does **not** have is any of the fields that only mean something for an occurrence: no outcome, no reopen count, no duration, no `occurred_at`, no empirical confidence. And the cause field is `documented_cause`, not `root_cause` — the source asserts it; nobody confirmed it here (:92-94). One case per source document, enforced by `uq_knowledge_case_source` on `(tenant_id, source_evidence_id)`: an article reconstructed twice is a duplicate, not a second opinion (:127-136).

**`KnowledgeCaseStep`** (:139-183) mirrors `EpisodeStep`'s shape on purpose, minus `failed_flag` / `successful_flag` / `result_state`, and with `expected_outcome` where an episode step has `observation` (:164). A document describes an action to take; it does not report that the action was taken (docstring at :140-147).

**Why a separate table and not `episodes.kind`.** With a discriminator column, every query that counts, clusters, scores, reviews or cites episodes is correct only while everybody remembers `AND kind = 'observed'` — and one forgotten predicate silently reintroduces exactly the contamination the split exists to prevent. A separate table makes that failure a missing join (loud) instead of a missing predicate (quiet). The argument is written where a future author will hit it, in the model docstring (knowledge_case.py:1-26).

**`PatternEvidence`** (backend/src/contextedge/models/pattern.py:87-182) is the ledger. `PatternEvidenceLink` already recorded *that* an episode belongs to a pattern; this records what a contributor is worth and on what epistemic footing: `(evidence_object_type, evidence_object_id)` polymorphically, `support_role` (including `contradicts_resolution` — a row that argues against the resolution is evidence too), `evidence_class` ∈ empirical / documented / prescriptive / conversational / inferred, `strength`, `confidence`, `observed_at` and `outcome`. The invariant lives in the database, not in a service: CHECK `ck_pattern_evidence_empirical_is_episode` requires that an `empirical` row's object type is `episode`, and that every non-empirical row has `outcome IS NULL` (:177-181). A documented claim cannot become an observed success because some later code path set a field.

**Migration `0073`** moved the historical rows (backend/alembic/versions/0073_migrate_knowledge_episodes_to_cases.py). It selects episodes already stamped `reviewer_state='invalidated'` with `generation_provenance->>'invalid_reason' = 'source_not_observational'` (:68-72) — that stamp is written by an operational script, not by anything in the backend, so on a database where nothing stamped it the migration returns immediately (:79-83). Then: copy both tables verbatim into `episodes_knowledge_migrated_backup` / `episode_steps_knowledge_migrated_backup` before deleting anything (:86-102); resolve each episode to its earliest knowledge-typed source evidence; **collapse duplicates by keeping the richest reconstruction per article** — most steps, then highest extraction confidence, then newest, via `row_number()` rather than letting `ON CONFLICT DO NOTHING` keep whichever row the planner inserted first (:116-143). Two fields are re-labelled rather than copied, and both are recorded in provenance so the substitution is auditable: `episodes.final_outcome` → `documented_resolution` (`"documented_resolution_from": "episodes.final_outcome"`, :169-175) and `episode_steps.observation` → `expected_outcome` (:199). Multi-source syntheses are migrated against their first article with the full list under `synthesised_from_evidence_ids` and `needs_review: true` — a synthesis across documents is really a candidate pattern, and that belongs to the pattern layer. Episodes that resolve to **no** knowledge source are deliberately left alone, still `invalidated`: migrate-then-delete must never become delete-without-migrate (:216-220). The migration's docstring was written against 299 such episodes resolving to 116 distinct articles; the run on this deployment reported **482 episodes → 135 knowledge cases**, with **3 left invalidated** because no knowledge source could be found for them.

### Attach-or-seed: `knowledge_case_service`

A knowledge case does not cluster with other knowledge cases. Two incidents are similar because they happened similarly; two articles are similar because somebody wrote them similarly, and 600 articles behaving like 600 incidents is the failure the whole split exists to avoid (module docstring, backend/src/contextedge/services/knowledge_case_service.py:1-27). So a case attaches to the pattern it documents, or seeds one.

`attach_case(db, tenant_id, case, validate=True)` (:144-243):

1. `_nearest_pattern` measures the case embedding against pattern **member episodes** — patterns carry no embedding of their own — and takes the minimum per pattern with `ORDER BY distance ASC LIMIT 1` (:58-113, ordering at :100). The ordering is the point: an unordered `LIMIT 1` returns an arbitrary qualifying pattern, which on a dense corpus is very nearly a random one.
2. The match must be within `KNOWLEDGE_ATTACH_MAX_DISTANCE = 0.27` (:49) — deliberately tighter than clustering's own `PATTERN_MATCH_MAX_DISTANCE = 0.30` (backend/src/contextedge/workers/pattern_tasks.py:50), because a wrong attachment here puts a document behind a procedure it does not describe and the playbook generator will cite it. Calibration note in the constant's comment: random approved-episode pairs on this corpus sit at p01 0.257, median 0.409.
3. Distance says "same subject", not "this document describes this pattern's problem", so the same LLM adjudicator clustering uses — `validate_pattern_match` — gets the final say (:169-196). If the adjudication call *fails*, the code falls back to the distance verdict rather than dropping the case.
4. Accepted → one `PatternEvidence` row at `evidence_class="documented"`, `observed_at=None` ("a document did not occur"), `outcome=None`, confidence `1 - distance` (`_record`, :116-141).
5. Rejected or nothing within 0.27 → **seed a new pattern** at `DOCUMENTED_ONLY_PATTERN_CONFIDENCE = 0.4` with `episode_count = 0` and `generation_provenance.support = "documented_only"` (:217-243). 0.4 is below `PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE = 0.5` on purpose (pattern_tasks.py:34, checked at :487): a documented-only pattern is a candidate, and **no playbook is generated from it** until an incident lifts its confidence. The seeded pattern's own ledger row lands at confidence 0.6.

`pattern_support(db, tenant_id, pattern_id)` (:246-301) reads the ledger back grouped by `(evidence_class, support_role, outcome)` and derives the state a reviewer actually needs: `empirically_supported` if any empirical row exists, else `documented_only` if anything documented or prescriptive does, else `unsupported`. `documented_only` is not a deficiency — it is a pattern that exists because somebody wrote the failure mode down before it happened here.

**Stated plainly: nothing in the backend calls either function.** `attach_case` and `pattern_support` have no route, no worker and no ingest-path caller, and `_record` is the only constructor of `PatternEvidence` anywhere — so the ledger has no live writer either. The measured live numbers (135 cases → 75 seeded, 60 attached; 1,416 empirical rows backfilled from existing episode links) came from an operational run, not from a wired pipeline, and `pattern.cluster_episodes` does not add a ledger row when it links a new episode. Recorded in [KNOWN_GAPS.md](./KNOWN_GAPS.md) as "Nothing on the live ingest path creates a knowledge case" and "`pattern_evidence` has no empirical writer".

### Graph builder — the write API for `graph_edges`

(backend/src/contextedge/graph/builder.py)

- `add_edge` validates the type via `require_registered`, then inserts with `valid_from = now()` (builder.py:16-47).
- `ensure_edge` idempotently creates the *active* (`valid_to IS NULL`) logical edge: SELECT-first, then `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index `uq_graph_edges_active_logical`, then a re-select for the race loser — two racing workers cannot abort the enclosing transaction (builder.py:50-135). **`weight` is traversal importance; `confidence` is belief** — callers pass both when they mean both (63-72).
- `close_edge` sets `valid_to` on the active edge; the type is validated even here, because a typo would "close nothing and report success" (builder.py:138-173). `replace_edge` = close + re-add at one timestamp — temporal versioning; it has no production callers yet (176-217; [KNOWN_GAPS.md](./KNOWN_GAPS.md), "Still open from the graph review").
- Decision linkers create the decision edges: `based_on` (decision → evidence/episode/pattern), `considered` + `chose` (→ option), `applied_policy`, `resulted_in`, `followed_by` (builder.py:328-469). `link_decision_approval` (`decision -[required_approval]-> approval_request`, builder.py:424-438) sits in the same block but has no caller anywhere outside the builder, so that edge type is registered and never written.
- `persist_pattern_enrichment_edges` turns pattern enrichment (triggers, entities, errors, root causes) into real edges from **virtual concept nodes** — deterministic `uuid5` under `ENRICHMENT_NAMESPACE`, so re-runs are idempotent — at weight 1.5: `trigger_of`, `involved_in`, `discovered_in`, `causes` (builder.py:13, 472-518).

The `graph_edges` row carries `tenant_id`, `domain_id`, both endpoints as `(node_type, node_id)` pairs, `edge_type`, `weight` (≥0 check), `confidence` (0..1 check), `metadata_extra`, and `valid_from`/`valid_to`. The active-edge unique index covers the full logical key `WHERE valid_to IS NULL` with `NULLS NOT DISTINCT`, which is what makes `ensure_edge` race-safe (backend/src/contextedge/models/pattern.py:272-379; the index at :285-297).

**Edge-type registry (F2).** `graph/edge_types.py` declares the entire write-side vocabulary — **76 types in six semantic groups** since `_SITUATION` joined it (backend/src/contextedge/graph/edge_types.py:36-155). `require_registered` raises `UnknownEdgeType` for anything else, and `add_edge` / `ensure_edge` / `close_edge` / `replace_edge` all call it (edge_types.py:31-32, 220-234). Adding a type is two decisions: register it, then either allowlist it for the agent projection or record the exclusion reason in `PROJECTION_EXCLUSIONS` — `tests/test_edge_type_registry.py` enforces the pairing (edge_types.py:15-25). **19** registered types are deliberately not agent-traversable, each with its reason in that dict (edge_types.py:160-217) — fan-out hubs like `mentions_identity` would spend the projection budget on identity hubs instead of topology. That leaves **57** projected, which is exactly `len(MAF_RELATIONSHIP_TYPES)` (backend/src/contextedge/graph/agent/profiles.py:89-189; counts verified by import). Earlier revisions of this page said 69 / 16 / 53 — correct before the situation vocabulary landed; [KNOWN_GAPS.md](./KNOWN_GAPS.md) carries the dated correction to its F2 entry.

**Temporal predicates.** `edge_valid_at(as_of)` returns `valid_to IS NULL` for current-state queries, or the point-in-time window predicate when `as_of` is set (backend/src/contextedge/graph/temporal.py:29-36). `normalize_graph_as_of` rejects naive datetimes and timestamps more than 5 minutes in the future with 422 (temporal.py:12-26). Semantics caveat, do not gloss: historical **edges** combine with **current node facts**, so point-in-time reads answer "what was connected then", not "what was true then" ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Still open from the graph review").

### Operational situations — the schema for "what is happening now"

**Read this section as a schema description. No correlation logic runs yet, nothing writes these tables, and on this deployment they are empty.** Migration `0074` landed the shape first, deliberately, so it could be reviewed against a real schema rather than a sketch (backend/alembic/versions/0074_operational_situations.py:1-48).

**What a situation is, and what it is not.** A `CorrelationEdge` says *two pieces of evidence look related*. A situation says *these many signals describe ONE occurrence* — a strictly stronger claim, which is why it is a new object rather than a renamed edge. It is also not an episode: a situation may exist while nothing is resolved, and it must not become an episode merely by existing, because an episode needs a resolution to reconstruct (backend/src/contextedge/models/situation.py:1-22). The three-line version the model file itself uses: *situation* = what is happening, *episode* = what happened and what worked, *knowledge case* = what a source says works.

**Four tables** (models/situation.py):

- **`operational_situations`** (:118-223) — type, state (`emerging` → `active` → `stabilizing` → `resolved`, plus `reopened` / `merged` / `invalidated`, :60-68), title, severity, counters, and two distinct clocks: `onset_at` is when the occurrence began in the world, `detected_at` is when we first saw it, and late evidence is allowed to move onset *backwards* because causality is classified against that instant (:150-159). `situation_confidence` is kept separate from a membership's confidence and from a change candidate's score — one number answering three questions makes every reader guess which (:142-148). `fingerprint` is a lookup and duplicate-suppression key and is **deliberately not unique**: the same service can fail twice in one window for unrelated reasons (:187-191).
- **`situation_evidence_memberships`** (:226-314) — why one evidence item is considered part of one situation. Membership is not binary: `provisional` / `inferred` / `confirmed` / `rejected` / `retired` (:87), and a rejected row is meant to be **kept** rather than deleted, because the machine score beside the human verdict is the only record of what the model got wrong. `score_breakdown` is shaped to hold the decomposed score rather than a total, so "why was INC1002 associated with SIT44" will have an answer once something scores. `source_lineage_group` is there so that one source event which produced an alert, a ticket and a mail cannot later be counted three times (:262-267).
- **`situation_entity_impacts`** (:317-376) — what a situation appears to affect **and what appears fine**. `healthy_control` is a first-class impact role (:95), because what is not broken narrows a diagnosis as much as what is; and every impact carries `signal_observed_at`, since "database healthy" is useful at two minutes old and dangerous at eight hours (:346-352).
- **`situation_change_candidates`** (:379-460) — a change that might explain it, with a lifecycle from `weak_candidate` to `confirmed` (:104-113). `correlation_score` is documented as a **ranking, never a probability** (:404-409). The rule that `confirmed` may be reached only from governed evidence — an ITSM caused-by relation, an approved RCA, a human decision, never a score and never an agent's opinion — is written into the `confirmation_basis` column comment on exactly the reasoning the decision projection already enforces (:420-424; see [16-decision-traces.md](./16-decision-traces.md)). Note what that is and is not: a rule recorded where the first writer will find it, **not** a database constraint. The two CHECKs below are the enforced pair; this one waits for the service that will honour it.

**Two invariants are enforced by the database**, because both are the kind of rule a later code path forgets:

- `ck_change_after_onset_not_causal` — a change whose `temporal_relation` is `after_onset` may not be `suspected`, `corroborated` or `confirmed`. It can still be `remediation` or `rollback`; it cannot be the thing that started what preceded it (situation.py:455-459).
- `ck_situation_merged_has_target` — a `merged` situation must name `merged_into_situation_id`, and a situation that has not merged must not pretend to. Merged rows are never deleted: they are how "why did these two become one" stays answerable (:218-222).

**Graph vocabulary.** Seven relations are registered in `_SITUATION` (backend/src/contextedge/graph/edge_types.py:141-151): `part_of_situation`, `situation_affects`, `suspected_change`, `confirmed_change`, `similar_situation`, `recurred_from`, `merged_into`. Four are allowlisted for maf.v1 — `situation_affects`, `suspected_change`, `confirmed_change`, `similar_situation` — as what an agent needs to reason about a live occurrence: what it hit, what changed, what it resembles (profiles.py:91-103). Three carry exclusion reasons (edge_types.py:201-216): `part_of_situation` because an incident storm is hundreds of hub memberships and the situation node already carries the count; `merged_into` because it is audit lineage, not reasoning; `recurred_from` because precedent belongs to the historical half of diagnostic context and traversing it here would put an older occurrence beside current signals as though it were also happening now.

**Two honest limits on that projection story.** First, no code writes any of these edges yet. Second, `situation` is **not** in `MAF_NODE_TYPES` (profiles.py:59-87) and there is no situation hydrator or repository seed layer — and the selector keeps an edge only when *both* endpoints' node types are in the profile (backend/src/contextedge/graph/agent/selector.py:60-61). So even once something writes them, the four allowlisted relations would be dropped at selection until the node type, hydrator and seed layer land. The relationship allowlist is ahead of the node allowlist; that is recorded in [KNOWN_GAPS.md](./KNOWN_GAPS.md) under "Situation correlation is not built" rather than glossed here.

**And the data reality.** On this deployment the tables are empty and the inputs largely do not exist: only `zoho_desk` is connected, the evidence types present are `thread_message`, `ticket` and `kb_article`, and there are **zero** change records, **zero** monitoring alerts, **zero** CI or service entities (all 849 entities are `topic` or `knowledge_category`) and **zero** topology edges. Change candidates, monitoring-alert memberships and topology-distance impacts have nothing to be computed from until a change/alert/CMDB connector exists.

### Relational → graph materializer

`GraphRelationshipMaterializer.reconcile_tenant(tenant_id, batch_size=500)` streams relational rows and calls `ensure_edge` for each — idempotent, additive-only (backend/src/contextedge/graph/agent/materializer.py:107-359; the `_edge` wrapper that skips NULL targets is at 78-105). It derives edges from sessions (`involves_user` / `targets_workflow` / `tracks_request` / `runs_on_agent`), execution runs (`has_execution`, `executes`), approvals (`requires_approval`), claims (`asserted_in`, `superseded_by`, and `supported_by` / `contradicted_by` / `weakened_by` from claim-evidence links), decision links (`based_on`, `supported_by_claim`, `applied_policy`), action policies (`governs`), error signatures (`aggregated_by`), fix patterns (`addresses` / `applies_to` / `recommends`), and case outcomes (`resulted_in`, plus `validated_fix` / `invalidated_fix` / `partially_validated_fix` from fix results, `FIX_RESULT_EDGE_TYPES` at materializer.py:42-45). Domain derivation follows migration `0031`'s rule — exactly one owning row per edge type — and every writer must agree, or the unique index treats the same logical edge with different domains as distinct (materializer.py:23-37). Scheduling: Beat task `evaluation.reconcile_graph_relationships` every 6 hours on the `evaluation` queue (backend/src/contextedge/workers/celery_app.py:329-333; task at backend/src/contextedge/workers/graph_tasks.py:33-35). There is no event-driven materialization; reconciliation is additive-only ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Still open from the graph review"). Caveats: `FixPattern` has no constructor anywhere, so the fix-validation edge family is dormant until Epic B populates the table (KNOWN_GAPS.md, "`FixPattern` has no constructor anywhere"). `claim_evidence` and `decision_claims` have no writers either (same file, "`claim_evidence` and `decision_claims` have no constructors"), and neither does `decision_action_policies` — the only reference to that model in the whole backend is this materializer's own query (backend/src/contextedge/models/action_policy.py:133; materializer.py:262-264). Those derivations are wired but currently stream zero rows.

### Decision edges — three capture tiers

The graph captures operational decisions at three fidelity levels:

- **Tier 1 — observed decisions (AI-extracted).** During normalization, `link_evidence_decisions` runs the decision extractor over evidence text ("jsmith restarted vpn-gw-east-01"), resolves actor and target against canonical identities, and writes `evidence -[records_decision]-> identity(actor)` and `evidence -[records_action_on]-> identity(target)` edges with `decision_type` / `action` / `context` in metadata (backend/src/contextedge/services/decision_service.py:21-95; called from the normalization worker at backend/src/contextedge/workers/extraction_tasks.py:298, 567).
- **Tier 2 — governed execution edges.** `execution_service` writes `session -[executed_playbook]-> playbook` at start (backend/src/contextedge/services/execution_service.py:950-957), `approval_request -[approved_by | denied_by]-> user` at decide time (1429), `user -[modified_by]->` on the modify flow (1575), and `execution_run -[execution_outcome]-> playbook` at completion (1694).
- **Tier 3 — first-class decision records.** `decision_trace_service.create_decision` mints a `Decision` row and its typed edges via the builder linkers; `execution_service` embeds `create_decision` at start, decide, and modify (execution_service.py:978, 1441, 1589), and the MAF agent's write-back path uses the same function. See [16-decision-traces.md](./16-decision-traces.md) for the full write path, retrieval, and analytics.

### Issue signatures and recurrence (where they touch the graph)

When an episode is approved, one LLM call distills it into a generalized problem fingerprint (`issue_signatures`), linked by an `episode -[has_signature]-> issue_signature` edge — written fail-soft, so an edge failure never fails the extraction (backend/src/contextedge/services/issue_signature_service.py:216-236). When the signature already existed, `_link_recurrence` adds a **`recurrence` case membership** (confidence 0.6) from the new episode's seed evidence to the previous occurrence's case — a precedent pointer for retrieval, never a merge: the cluster resolver refuses to expand through it (issue_signature_service.py:249-312; episode_cluster_service.py:169-171). Signatures are also a maf.v1 node type and their own agent seed layer, matched by full-text search over the de-slugged signature fields (backend/src/contextedge/graph/agent/profiles.py:85; the seed layer is backend/src/contextedge/graph/agent/repository.py:262-310).

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

Scope caveat (open, P1-6): `/graph/agent-subsets` builds a fully scoped projection, but `/graph/neighbors`, `/graph/subgraph`, and the CMDB/change-risk/fix routes filter by **tenant only** — a domain-limited principal can read wider here than its projection would allow ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Graph API scope consistency (P1-6)").

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

**Input** — a fourth item arrives, and it is knowledge, not an event:

| Evidence | Source | Type | Summary |
| --- | --- | --- | --- |
| ev-kb-vpn-cert | Zoho Desk KB | `kb_article` | "Renewing the VPN gateway certificate" — symptoms, cause, five steps |

It correlates and embeds like anything else, but a cluster containing only `ev-kb-vpn-cert` never reaches synthesis: `_cluster_has_observational_evidence` returns False and reconstruction exits with `skipped_knowledge_only_cluster`. That exit is where the live path stops today. **Output** below is therefore the *shape* a knowledge case and its ledger row take — produced by `attach_case` when something calls it, which on this deployment was an operational run rather than the ingest path:

```json
{
  "knowledge_case": {
    "id": "kc-441",
    "source_evidence_id": "ev-kb-vpn-cert",
    "source_kind": "kb_article",
    "source_authority": "internal_kb",
    "title": "Renewing the VPN gateway certificate",
    "documented_cause": "Gateway TLS certificate reaches expiry; AUTH_CERT_EXPIRED is returned to clients",
    "documented_resolution": "Reissue and install the gateway certificate, then restart RADIUS",
    "steps": [
      {"step_order": 1, "step_type": "diagnostic", "text": "Check the gateway certificate expiry",
       "expected_outcome": "notAfter is in the past"}
    ]
  },
  "pattern_evidence": {
    "pattern_id": "p-42",
    "evidence_object_type": "knowledge_case",
    "evidence_object_id": "kc-441",
    "evidence_class": "documented",
    "support_role": "supports_resolution",
    "observed_at": null,
    "outcome": null,
    "confidence": 0.79
  }
}
```

Note what is absent: no outcome, no `occurred_at`, no empirical confidence. If no pattern had sat within 0.27, `attach_case` would instead have seeded a fresh pattern at confidence 0.4 with `episode_count: 0` — visible to reviewers and search, below the 0.5 floor that gates playbook generation. Then `pattern_support(p-42)` reads back `{"documented": 1, "empirical": 19, "empirical_success": 14, "state": "empirically_supported"}` — the sentence a bare `episode_count` could never say.

**Output that does not exist yet** — for orientation only, this is the shape `operational_situations` is designed to hold if the Acme outage had monitoring and change data behind it. Nothing writes it today, and this deployment has no alerts, no change records and no CI entities:

```json
{
  "situation": {"id": "sit-44", "situation_type": "outage", "state": "active",
                "title": "VPN access unavailable — east gateway",
                "onset_at": "2026-07-02T08:52:00Z", "detected_at": "2026-07-02T09:03:00Z",
                "fingerprint": "vpn-gw-east-01:auth_cert_expired", "situation_confidence": 0.0},
  "impacts": [{"entity": "vpn-gw-east-01", "impact_role": "primary_affected"},
              {"entity": "radius-02", "impact_role": "healthy_control",
               "signal_observed_at": "2026-07-02T09:01:00Z"}],
  "change_candidates": [{"change_evidence_id": "ev-chg-9981", "status": "suspected",
                         "temporal_relation": "before_onset", "correlation_score": 0.86}]
}
```

The change candidate above is legal only because its `temporal_relation` is `before_onset`; the same row with `after_onset` would be refused by `ck_change_after_onset_not_causal` unless its status were `remediation` or `rollback`.

## Design decisions

- **Adjacency in Postgres vs a dedicated graph DB** — *Why:* simpler ops and transactional consistency with evidence, playbooks, and decisions; `ensure_edge` rides the same transaction as the row it describes. *Tradeoff:* deep graph algorithms are harder than in Neo4j-style stores; traversal is bounded BFS with explicit caps.
- **Separate correlation edges from graph edges** — *Why:* correlation is evidence-centric, pairwise, and consumed by cluster resolution and ranking; `graph_edges` is the many-node-type context graph with temporal validity and a registered vocabulary. *Tradeoff:* two concepts to explain, and the hybrid ranker reads both.
- **A registered edge vocabulary enforced at write time (F2)** — *Why:* `edge_type` was free text written from 26 modules; a typo produced a real, queryable edge the agent projection silently dropped — the graph knew something the agent could never see, and nothing failed (edge_types.py:1-13). *Tradeoff:* adding a relationship now takes two deliberate decisions (register + project-or-exclude-with-reason), and the static test cannot see types assembled from constant tables — the runtime check is the real net.
- **Create-once correlation edges, deterministic tier wins** — *Why:* edges are never upgraded in place, so confidence always reflects the evidence that created the edge; when both tiers fire, the provable 1.0 case link beats the fuzzy identity score (correlation_service.py:256-259, 469-476). *Tradeoff:* a pair first linked at 0.65 stays at 0.65 even if stronger evidence arrives later — the case-link tier will add its own 1.0 path, but the old edge is not rewritten.
- **Additive-only materialization on a 6h clock** — *Why:* streaming relational rows through the idempotent `ensure_edge` is simple and safe to re-run; the ON CONFLICT index makes overlap free (materializer.py:78-105, 107-359). *Tradeoff:* deleted or changed relational rows are not reconciled (no closes), and new relationships can lag up to 6 hours in the graph — event-driven materialization is a recorded open item ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Still open from the graph review").
- **Temporal validity on edges, but current-state node facts** — *Why:* `valid_from`/`valid_to` plus the active-edge partial unique index give supersede-never-delete semantics and point-in-time traversal cheaply. *Tradeoff:* `as_of` reads combine historical topology with current node facts, so callers must not draw historical operational conclusions — the projection warns about exactly this ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Still open from the graph review").
- **LLM-assisted contradiction detection behind three deterministic gates** — *Why:* ANN top-K + a scan cursor + a token-overlap gate cut LLM calls by an expected 80–95% on warm tenants while a hard budget bounds the worst case ([KNOWN_GAPS.md](./KNOWN_GAPS.md), "Contradiction scanner redesign"). *Tradeoff:* rare contradictions with full vocabulary mismatch can be missed by the token gate, and a budget-exhausted scan finishes the rest next cycle.
- **An episode requires an observational source, and the check fails open** — *Why:* narrating a KB article as an episode manufactures an observation, and everything downstream — the playbook prompt, pattern recurrence counts, the agent's `[ep-N]` citations — then reads it as one (extraction_tasks.py:1195-1218). Failing open is the deliberate half: only a cluster *positively identified* as knowledge-only is refused, because a wrongly-allowed draft costs one review and a wrongly-blocked one costs a real incident that silently never becomes an episode (:1017-1024). *Tradeoff:* an unclassifiable cluster still synthesizes, and the gate is exactly as good as `evidence_type` — `runbook` is not in `KNOWLEDGE_EVIDENCE_TYPES`, so an uploaded-runbook-only cluster passes.
- **A separate `knowledge_cases` table, not an `episodes.kind` column** — *Why:* a discriminator makes every query that counts, clusters, scores, reviews or cites episodes correct only while everyone remembers `AND kind = 'observed'`; one forgotten predicate quietly reintroduces the contamination. A separate table turns that into a missing join, which fails loudly (knowledge_case.py:10-17). *Tradeoff:* two objects with near-identical shape to keep in sync, and every reader that legitimately wants "documented and observed together" now writes a UNION.
- **The empirical/documented invariant lives in a CHECK constraint** — *Why:* `ck_pattern_evidence_empirical_is_episode` makes it impossible for a non-episode to be `empirical` or for a non-empirical row to carry an `outcome` — the one place a future code path cannot forget it (pattern.py:177-181). *Tradeoff:* adding a genuinely empirical non-episode contributor later (an automation execution, say) is a migration, not a code change — which is the intended cost.
- **A documented-only pattern is real, but priced below the playbook floor** — *Why:* cold start matters — a documented failure mode should be visible before somebody hits it, which is exactly when the documentation would have helped — so `attach_case` seeds a pattern at 0.4 with `episode_count = 0` rather than dropping the case (knowledge_case_service.py:214-243). *Tradeoff:* pattern counts now include patterns nothing has ever observed; on this deployment about 55% of knowledge cases seeded one. They generate no playbooks (0.4 < the 0.5 floor), but any report that counts patterns without reading `pattern_support` will overstate what the estate has actually learned.
- **Attach at 0.27, stricter than clustering's 0.30** — *Why:* a wrong attachment puts a document behind a procedure it does not describe, and the playbook generator will cite it; a missed attachment merely seeds a separate pattern that can be merged later (knowledge_case_service.py:42-49). *Tradeoff:* asymmetric thresholds are one more number to re-tune whenever the embedding model changes, and the two live in different modules.
- **Situations are a new object, not a renamed correlation edge** — *Why:* an edge asserts "these two look related"; a situation asserts "these many signals are one occurrence", and quietly promoting one into the other would claim something the evidence does not support (situation.py:18-22). Schema-first was also deliberate: `0074` ships the shape so the invariants can be reviewed against real DDL before any inference exists. *Tradeoff:* a schema with no writer reads as shipped capability to anyone auditing the tables — hence the explicit "no correlation runs yet" in this page, in the migration docstring, and in KNOWN_GAPS.md.
- **Four of seven situation relations are agent-traversable** — *Why:* an agent reasoning about a live occurrence needs what it hit, what changed and what it resembles; memberships are a hub relation (an incident storm is hundreds of them) whose count the situation node already carries (edge_types.py:201-216). *Tradeoff:* the allowlist currently outruns the projection — `situation` is not a `MAF_NODE_TYPES` member, so the selector's both-endpoints rule would drop all four today.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Correlation service | `backend/src/contextedge/services/correlation_service.py` | `correlate_evidence_item` (:197), `extract_case_link_candidates` (:116), `_identity_correlation_signal` (:53), `create_correlation` (:91) | correlation queue |
| Correlation worker | `backend/src/contextedge/workers/correlation_tasks.py` | `correlate_evidence` (:12), reconstruction dispatch (:39-57) | after normalize |
| Cluster resolution | `backend/src/contextedge/services/episode_cluster_service.py` | `resolve_episode_cluster` (:108), `MAX_CLUSTER_SIZE`/`MAX_HOPS`/window (:47-49) | before episode synthesis |
| Observational gate | `backend/src/contextedge/workers/extraction_tasks.py` | `_cluster_has_observational_evidence` (:1014), call site (:1219) | last check before synthesis |
| Knowledge-vs-event typing | `backend/src/contextedge/services/evidence_typing.py` | `KNOWLEDGE_EVIDENCE_TYPES` (:92), `is_knowledge_evidence` (:149), `UPLOADABLE_EVIDENCE_TYPES` (:104) | normalization + the gate |
| Knowledge cases | `backend/src/contextedge/models/knowledge_case.py` | `KnowledgeCase` (:46), `KnowledgeCaseStep` (:139), `uq_knowledge_case_source` (:127) | ORM |
| Pattern evidence ledger | `backend/src/contextedge/models/pattern.py` | `PatternEvidence` (:87), `ck_pattern_evidence_empirical_is_episode` (:177) | ORM |
| Attach-or-seed | `backend/src/contextedge/services/knowledge_case_service.py` | `attach_case` (:144), `_nearest_pattern` (:58), `pattern_support` (:246), `KNOWLEDGE_ATTACH_MAX_DISTANCE` (:49), `DOCUMENTED_ONLY_PATTERN_CONFIDENCE` (:55) | no production caller yet |
| Situation schema | `backend/src/contextedge/models/situation.py` | `OperationalSituation` (:118), `SituationEvidenceMembership` (:226), `SituationEntityImpact` (:317), `SituationChangeCandidate` (:379), the two CHECKs (:218, :455) | ORM only — no writer |
| Graph mutations | `backend/src/contextedge/graph/builder.py` | `add_edge` (:16), `ensure_edge` (:50), `close_edge` (:138), `replace_edge` (:176), decision linkers (:328-469), `persist_pattern_enrichment_edges` (:477) | all edge writers |
| Edge vocabulary | `backend/src/contextedge/graph/edge_types.py` | `UnknownEdgeType` (:31), `_SITUATION` (:141), `EDGE_TYPES` (:153), `PROJECTION_EXCLUSIONS` (:160), `require_registered` (:220) | every builder call |
| Temporal predicates | `backend/src/contextedge/graph/temporal.py` | `edge_valid_at` (:29), `normalize_graph_as_of` (:12) | graph reads |
| Materializer | `backend/src/contextedge/graph/agent/materializer.py` | `GraphRelationshipMaterializer.reconcile_tenant` (:107), `FIX_RESULT_EDGE_TYPES` (:42) | Beat, 6h |
| Graph reads | `backend/src/contextedge/graph/queries.py` | `get_neighbors` (:20), `get_pattern_subgraph` (:84), `get_entity_subgraph` (:375), `get_graph_stats` (:587) | services / API |
| Graph HTTP API | `backend/src/contextedge/api/v1/graph.py` | `graph_neighbors` (:190), `graph_subgraph` (:220), `graph_stats` (:242), edge proposals (:120-187) | HTTP |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | `scan_contradictions` (:318), `should_compare_contradiction` (:75), `_get_or_create_contradiction` (:152) | Beat, 12h |
| Observed decisions (Tier 1) | `backend/src/contextedge/services/decision_service.py` | `link_evidence_decisions` (:21) | normalization worker |
| Governed decisions (Tier 2/3) | `backend/src/contextedge/services/execution_service.py` | `executed_playbook` (:950), approve/deny edges (:1429), `create_decision` embeds (:978, :1441, :1589) | execution API |
| Issue signatures | `backend/src/contextedge/services/issue_signature_service.py` | `extract_issue_signature` (:89), `has_signature` edge (:216-236), `_link_recurrence` (:249) | after episode approval |
| Models | `backend/src/contextedge/models/pattern.py` | `GraphEdge` (:272) + `uq_graph_edges_active_logical` (:285) | ORM |
| Correlation model | `backend/src/contextedge/models/episode.py` | `CorrelationEdge` (:187-210) | ORM |

## Acme VPN incident (this layer)

When Acme's duplicate VPN tickets arrive, `correlate_evidence` (correlation queue) links the ServiceNow incident to its Jira clone at 1.0 through shared reference keys, the engineer's email joins the same canonical case through its quoted "INC0010427", and the Teams thread correlates at 0.75 through the rare `vpn-gw-east-01` identity inside the 7-day window. `resolve_episode_cluster` then hands reconstruction the whole connected component — legal-hold-fenced and time-fenced — and the resulting approved episode gains a `has_signature` edge to the `remote_access | tls_certificate | certificate_expired` signature. Six months later the same failure recurs, and the new episode's first evidence gets a 0.6 `recurrence` pointer back to this case — a precedent, never a merge. Meanwhile the 12-hour contradiction scan flags the KB article that still says "disable MFA for VPN", writing a `contradicts` edge that both alerts the knowledge manager and penalizes that guidance in ranking.

The Acme KB article "Renewing the VPN gateway certificate" travels a different road. It correlates and embeds like any other evidence, but a cluster containing only that article stops at `_cluster_has_observational_evidence` and never becomes an episode. Its content belongs in knowledge case KC-441, which attaches to the certificate-expiry pattern as a `documented` ledger row with no outcome and no `observed_at` — the shape `attach_case` produces, though on this deployment that step was run operationally rather than reached from ingest. The pattern can then say "one article documents this, nineteen incidents observed it, fourteen succeeded" instead of a single count that hides the difference. Had no pattern sat within 0.27 of it, KC-441 would have seeded one at confidence 0.4 with `episode_count: 0` — real, searchable, and below the floor that would let a playbook be written from a document alone. And if Acme's estate had monitoring and change data, the whole thing — the incident, the Teams thread, the alert storm, the suspected firewall change — would also be one `operational_situation` row while it was still unfolding. The tables exist and four of the seven relations are allowlisted for the agent — but nothing writes any of them, the tables are empty, and until `situation` joins `MAF_NODE_TYPES` the selector would drop those four edges anyway. Acme's estate here has no alerts, no changes and no CI entities to build such a row from in the first place.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — how graph and correlation counts become ranking signals
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — what reconstruction does with the cluster
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — queue topology, including the dedicated correlation lane
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — where the identities behind Tier 2 come from
- [16-decision-traces.md](./16-decision-traces.md) — the decision records behind the decision edges
- [`docs/API.md`](../docs/API.md) — `/graph` endpoint catalog
