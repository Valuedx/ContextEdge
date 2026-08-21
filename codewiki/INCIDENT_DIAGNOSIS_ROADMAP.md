# Incident-diagnosis roadmap: evolving the context graph from memory to diagnostic instrument

**Status:** implemented 2026-08-07 (commits `8142ad1`..`55d361e`, one per item, each with its measurements in the commit message). Notable outcomes: the F4 thread flipped to operational at 0.95 with a faithful summary (A1/A2); 17 error signatures with 5 day-one recurrence hits (D1); signature-first entry validated live end-to-end (D2); 36 change records ingested (B1); the browser-upgrade change class caught by inventory diff (B3); a threads-pool event-loop lock bug found and fixed by the A3 sweep. Two negative results recorded and enforced by tests: claims-in-the-gate-call failed label stability 4/8, so relevance v3 is registered but NOT default (A4 ships dormant); thinking-budget caps remain relevance-only. **Qualifier (2026-08-09 review, writers shipped same day):** the outcome/fix flywheel originally shipped *schema-only*; `case_outcome_service` + session lifecycle hooks now write `CaseStateTransition` on every open/close and `CaseOutcome` (with MTTR and fix-pattern links) when a close asserts one. Decision-time aggregation of those results (F10 statistics) remains open. Remaining follow-ups live in KNOWN_GAPS.
**Scope:** what to add or change in the graph, ingestion, and `maf.v1` projection so a MAF agent can *diagnose and resolve incidents*, not just recall similar ones.
**Companion docs:** [09-graph-and-correlation](09-graph-and-correlation.md), [docs/08_Context_Graph](../docs/08_Context_Graph.md), [18-cost-observability-and-containment](18-cost-observability-and-containment.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Why this roadmap exists

A diagnosing agent works a loop: **recognize** the failure → **contextualize** it (topology, recent changes) → **hypothesize** causes → **choose** a remediation it can defend → **learn** from the outcome. We walked the current graph through that loop and measured where it falls short. The headline findings, each verified against the live database:

| # | Finding | Evidence (measured 2026-08-06/07) |
| --- | --- | --- |
| F1 | The fine-grained node types are built but empty | `claims` = 0 rows, `error_signatures` = 0 rows — both in the schema and in `MAF_NODE_TYPES`, nothing populates them |
| F2 | Where granularity exists, it works | `issue_signatures` = 53 rows of pure structure (`failing_component`, `failure_mode`, `trigger_change`); `episode_steps` = 1,438 rows, avg 107 chars |
| F3 | Evidence nodes are the right size *except* the thread tail | body p50 = 254 chars, max = 109,552 — the top rows are entire pasted email threads (one held **78 fused messages**) |
| F4 | Head-truncation silently loses knowledge | the 78-message thread was classified `not_relevant` because `body[:2000]` saw only scheduling chatter; the resolution (a browser-upgrade/web-driver fix) started at char 4,330 and was never extracted |
| F5 | Causal knowledge exists but the agent cannot see it | `trigger_of` (93), `causes` (75), `discovered_in` (62) edges exist — none of these edge types, nor `issue_signature` nodes, are in the `maf.v1` allowlists |
| F6 | Change records are absent | the ServiceNow connector supports `change_request` (with `rfc`, `caused_by`, `cmdb_ci` references) but zero change-shaped evidence exists |
| F7 | Most incident-causing changes have no record anywhere | sampled `trigger_change` values: `increased_user_load`, `log_accumulation`, `large_email_campaign`, `wan_interruption` — none would ever appear in change management. The F4 browser auto-upgrade is the canonical case |
| F8 | "Recent" does not exist in the projection | no seed layer, selector weight, or hydrated fact carries a usable timestamp; `graph_edges.valid_from/valid_to` exist but nothing queries a time window |
| F9 | No service/CI topology | incidents link to CIs (`affects_ci`), but there are no CI↔CI edges, so blast radius ("what depends on this?") is untraversable |
| F10 | Remediation choice has no statistics | `validated_fix`/`invalidated_fix` record individual outcomes, but nothing aggregates them into "resolved 8 of 9, failed on version ≥ 6.2" at decision time |

The theme: **the graph already stores most of the right facts; diagnosis needs them as structured, hop-reachable, time-aware, statistically summarized knowledge rather than text inside nodes.**

---

## Workstream A — text granularity: stop feeding the wrong characters to models

*Fixes F3/F4. Cheapest workstream, improves both ingestion quality and projection quality.*

### A1. Salience-aware slicing (replaces head-truncation)

Every `[:N]` slice in the pipeline takes the *first* N characters. Conversational bodies put the newest reply (greetings, scheduling) on top and the substance below — so the caps spend their budget on exactly the wrong text, and F4 shows the failure is not hypothetical: a complete, reusable fix was classified into the discard pile.

**Design:** one shared `salient_slice(text, n)` utility — strip greeting/sign-off/quoted-header lines, prefer the last substantive segments for conversational sources, fall back to plain truncation for non-conversational text. Deterministic, no LLM.
**In code:** call sites at `ai/classifiers/relevance.py` (`body[:2000]`), `ai/classifiers/message_function.py` (`[:2000]`), `ai/extractors/identity_extractor.py` / `decision_extractor.py` (`content[:4000]`), `ai/extractors/episode_extractor.py` (`PER_ITEM_CHAR_LIMIT`), and the hydrator's `EVIDENCE_SUMMARY_CHARS` (400) in `graph/agent/hydrators.py`.
**Validation:** re-classify the known-misclassified thread rows; the F4 ticket must flip to relevant. Add regression fixtures from real (anonymized) thread shapes.
**Effort:** small. **Risk:** low — deterministic and unit-testable.

### A2. Distill once at ingest, project the distillate

The relevance call already reads every body. Extend its response schema to also return a ≤300-char **operational summary** (symptom / component / action / outcome) for roughly 50 extra output tokens, stored as a column on `evidence_items`. Hydrators then project the summary instead of truncated raw body; correlation and review UI reuse it.

**In code:** `ai/prompts/relevance.py` (new prompt version — registry keeps the old one immutable), `workers/extraction_tasks.py` `_normalize`, `graph/agent/hydrators.py` evidence facts.
**Validation:** follow the prompt-registry A/B discipline (see the thinking-budget precedent in [18](18-cost-observability-and-containment.md)): sample N real tickets, compare summaries against bodies for faithfulness before switching the hydrator preference.
**Effort:** small-medium. **Risk:** low; the fallback (project raw body slice) remains when the summary is absent.

### A3. Thread re-segmentation backfill

Auto-hydration (merged 2026-08-06) splits threads into per-message evidence *going forward*. The existing ~5 giant rows — including the F4 ticket with its lost fix — stay wrong until re-segmented. Per-message evidence restores per-message classification (chatter skips extraction via the existing ≥0.75 gate), timestamps, message-function semantics, and citable provenance.

**In code:** one-off task reusing `workers/hydration_tasks.hydrate_thread` against rows whose body contains fused-thread markers; supersede the blob row.
**Validation:** the recovered resolution must appear as extractable evidence; episode reconstruction on the affected case should produce steps citing individual messages.
**Effort:** small. **Risk:** medium — supersession of the blob row must not orphan existing episode links; run against a copy first.

### A4. Claims population (phase 2 — after A2 proves out)

Emit 1–3 atomic claims per *relevant* evidence from the existing extraction pass (schema extension, no new LLM call), linked `asserted_in` → evidence with confidence. Projection prefers ~100-char claims over 400-char summaries; `contradicted_by`/`weakened_by` finally get attachment points. Deliberately sequenced after A2 because claims need dedup + supersession lifecycle — real work that the summary column does not.

**Explicit non-goal:** evidence chunks stay retrieval units. 1,142 chunks (avg 234 chars) as graph nodes would flood the 60-node projection budget with fragments.

---

## Workstream B — change and event awareness: "what happened recently near this CI?"

*Fixes F6/F7/F8. This is the capability the diagnosis scenario ("check recent related changes and their effect") actually requires.*

### B1. Recorded changes: enable `change_request`

The connector already defines the table with the right reference fields (`rfc`, `caused_by`, `parent_incident`, `cmdb_ci`). Enabling it on the ServiceNow source makes change records evidence; the existing reference enrichment gives them `affects_ci` edges to the same CI entities incidents point at — the `incident → CI ← change` join materializes with no new code.
**Effort:** trivial. Do first.

### B2. Event-evidence layer: the changes nobody records

F7 is the decisive argument: most incident-causing changes (auto-upgrades, load shifts, log accumulation, WAN events) never get a change record. The graph therefore needs **observed events**, with three disciplines:

1. **Events are evidence** — timestamped, source-attributed, `affects_ci`-linked. No new node machinery. Because events arrive structured (webhooks, snapshots), they **bypass the LLM pipeline entirely** — no classification or extraction cost, which is what makes "just keep them" affordable.
2. **State transitions only, never metrics.** An event is "version 118 → 119", "config key changed", "pool exhausted at 00:28" — not "CPU is 87%". Metric streams stay in the monitoring stack; the agent queries them live through a separate tool.
3. **Short retention.** A deploy event from four months ago has no diagnostic value; TTL events well below ticket retention (the retention machinery in [11](11-retention-and-operational-events.md) already supports per-type policies).

### B3. Inventory-diff detector (first event source)

Unrecorded changes must be *observed*: periodically snapshot agent-side state (agent version, plugin versions, browser/driver versions, config hashes), diff against the previous snapshot, emit change events. This single detector would have caught the F4 browser auto-upgrade on the day it happened. For the AutomationEdge product this is the highest-yield event source available.

### B4. Diagnosis-time `preceded_by` seed layer

The projection addition that makes diagnose-time correlation real: when the seed is an incident, pull change/event evidence **within a time window** (default 7 days) sharing a CI with the incident (or its 1-hop dependents once C1 lands), projected as `preceded_by` with `days_before`/`minutes_before` in edge metadata — *computed at projection time relative to the incident*, never stored.

Two rules keep this honest:

- **Correlation is a hypothesis, not a verdict.** `preceded_by` is a suspect edge; the agent argues from it, and `validated_fix`/`invalidated_fix` close the loop. Confirmed suspicions write back into the signature's `trigger_change` (see E3) — turning a one-off correlation into reusable knowledge, which is the ContextEdge thesis.
- **Events stay out of the standing projection.** The 60-node budget must not be crowded by last week's deploys; events enter the agent's view only through this seed layer.

**In code:** new seed layer in `graph/agent/repository.py` (same pattern as the knowledge-seeding layer), relationship registration in `graph/agent/profiles.py`, metadata computation in `graph/agent/hydrators.py`.
**Effort:** medium — this is the design-worthy piece of the workstream.

### B5. Project the causal vocabulary that already exists

Allowlist `trigger_of` / `causes` (with their node types, or preferably project issue-signature facts including `trigger_change` — see D2), and add timestamps to hydrated facts generally so the agent can do temporal reasoning at all (F8).
**Effort:** small.

---

## Workstream C — topology and blast radius

*Fixes F9. The genuinely new capability exposed by comparing against operational-topology schemas.*

### C1. CI↔CI dependency edges from `cmdb_rel_ci`

Without `depends_on`/`runs_on` edges between CIs, the agent cannot walk "checkout depends on payment; payment calls this DB." ServiceNow's `cmdb_rel_ci` table holds exactly these relationships: add it to the connector registry, map rows to entity↔entity `depends_on` edges via the existing `graph/builder.ensure_edge` path, allowlist in `maf.v1`.

### C2. Criticality / owner / tier on entity facts ✅ *(shipped 2026-08-21. Criticality is defined on `cmdb_ci_service` only, so it reaches infrastructure through the dependency edge rather than being stamped on every CI — which is semantically right: a switch is critical because a critical service depends on it. Tier is not modelled on this instance at all.)*

Blast radius without criticality cannot be prioritized, and remediation risk ("restart a Tier-1 service?") cannot be assessed. Sync these attributes from the CMDB during C1 and project them as entity facts. Owner also gives the agent an escalation target.

**Boundary decision (from the schema comparison):** live state (`error_rate`, `active_connections`, `status`) stays **out** of the graph. The graph is provenanced and review-gated because its contents are durable claims; live metrics are stale seconds after writing, and an agent reasoning over yesterday's error rate is worse than one with none. The division of labor: **the graph tells the agent what to look at; monitoring tools tell it what is true right now.** Likewise, raw Cypher-style query access stays out — the budget-aware server-controlled projection *is* the governed version of that capability; new query shapes land as seed layers.

---

## Workstream D — diagnosis entry precision: "have I seen this before?"

*Fixes F1/F5 at the recognition stage — the weakest link today, and the cheapest sharp gain.*

### D1. Error-signature fingerprints (deterministic, no LLM)

Populate the empty `error_signatures` table at ingestion with normalized fingerprints (error code + component + variable-stripped message) from logs and tickets, edged to evidence and episodes. An exact fingerprint match beats embedding similarity every time and gives the agent an O(1) answer to "is this exact failure known?" with full history one hop away.
**Priority: first item in the whole roadmap** — cheapest, sharpest diagnostic gain.

### D2. Make issue signatures seedable and projectable

The 53 `issue_signatures` rows are the best diagnostic index in the graph and the agent cannot see them (F5). Add: `issue_signature` to `MAF_NODE_TYPES` with facts (`failing_component`, `failure_mode`, `trigger_change`, `episode_count`); a seed layer matching incident symptom text against signatures (FTS over the structured fields; embeddings if needed later). Signature-first entry — symptom → signature → episodes → playbook — is how an experienced engineer thinks.

### D3. Populate `aggregated_by` from connector references

`parent_incident` / `problem_id` are already fetched by reference enrichment. Wiring them to `aggregated_by` edges lets the agent recognize "this is the 6th ticket of one ongoing event" — preventing six parallel diagnoses and giving true impact scale.
**Effort:** small.

---

## Workstream E — trustworthy remediation: from "a fix exists" to "this fix is defensible"

*Fixes F10. What separates an agent that suggests from one you would let act.*

### E1. Efficacy rollups on the remediation path ✅

Shipped 2026-08-21, **pulled forward from position 9** after a competitive review found this the one capability no vendor in the landscape ships.

The design above assumed the wrong substrate. **Measured 2026-08-21: `validated_fix` = 0, `invalidated_fix` = 0 and `case_outcomes` = 0 — in both databases, including the 15,260-episode reference corpus.** Those edges have no producer, so aggregating them would aggregate nothing. The outcome signal actually lives in `episodes.final_outcome`, which 10,247 episodes carry as free text in **9,014 distinct phrasings**, and in the `PatternEvidence` ledger, whose `outcome` column was NULL on all 1,551 rows.

So E1 became: normalize the text deterministically, write it into the ledger, aggregate per pattern. Result on the reference corpus — 1,416 empirical rows classified (697 success / 66 partial / 132 failure / 521 unknown), 533 patterns split 429 `EMPIRICAL` / 75 `DOCUMENTED_ONLY` / 29 `MIXED`, mean success rate 76.9%.

Knowledge drift returns **zero** there, and that is a negative result rather than a broken rule: 15 patterns cleared the sample threshold and none fell below the success threshold. Recorded so it is not re-litigated.
**In code:** `services/outcome_classification.py`, `services/efficacy_service.py`, `GET /api/v1/patterns/efficacy`, `GET /api/v1/patterns/knowledge-drift`. Design: [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md).

### E2. Applicability constraints on edges, not buried in text ✅ *(shipped 2026-08-21 — see [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md). `fix_applicability_rules` holds 0 rows; the real payload is on knowledge cases, and only `deployment` (100%) and `components` (94%) are populated enough to decide anything — version bounds sit at 7.5%.)*

The applicability machinery exists (`version_floor`/ceiling extraction, `fix_applicability`). Project the constraints onto `recommends`/`addresses` edges so the agent can structurally *rule out* a fix that does not match the incident's product version or environment — mis-applied remediation being the classic agent failure.

### E3. Negative knowledge as a first-class projection ✅ *(shipped 2026-08-21. `invalidated_fix` holds 0 rows; the real signal is `episode_steps.result_state` — 970 failed steps, 217 patterns carrying 510 failure statements. Writing confirmed `preceded_by` suspicions back into `trigger_change` remains open and belongs with H6.)*

`[did not work]` step markers and `invalidated_fix` edges exist; surface them *with* every recommendation ("known to fail when X"), and write confirmed `preceded_by` suspicions back into `trigger_change` (closing B4's loop). An agent that repeats a documented-bad fix destroys trust faster than one that abstains.

**Boundary decision:** step→tool execution binding (mapping a playbook step to an executable automation) belongs to the orchestrator (AEAIHubOrchestrator), not to ContextEdge. The graph supplies *what* to do — preconditions, verification, rollback per step (the v4 playbook prompt already pushes this shape); binding steps to tools is the orchestrator's contract. Blurring this makes the knowledge layer deployment-specific.

---

## Workstream F — the learning loop: diagnoses that compound

*The biggest structural omission. Everything above is inventory; this is the flywheel.*

### F1. Agent decision write-back ✅

Shipped 2026-08-21. The machinery existed and nothing called it; `DecisionOption` already carried `selected`, `rejection_reason` and `rejection_code`, which is exactly "hypotheses considered, which was chosen, and why the others were not".

The hazard that kept it unbuilt — an agent reading its own unreviewed conclusions as evidence — was already closed upstream: the projection drops any decision that is AI-authored and still `pending`. F1 relies on that and adds two more layers, because one guard on a path nobody re-reads is a guard with a short life: `prior_hypotheses` filters explicitly, and the agent's client port exposes no argument that could request unreviewed work.

An **outcome**, not age, promotes a diagnosis. Verified live: a diagnosis with two rejected hypotheses was invisible while pending, visible to a review surface, and inherited by the next reader only once the fix was recorded as successful.

Original text: The `session` / `decision` / `chose` / `resulted_in` machinery exists, but nothing makes the MAF agent's own diagnostic trail flow back into the graph. Each diagnosis should write back: hypotheses considered, which was chosen, outcome. The next agent facing the same signature then inherits "the connection-leak hypothesis was checked and disproven for this signature; it was the pool size." Without write-back, every diagnosis starts from zero and the graph learns only from human tickets, never from agent runs.
**In code:** the MAF adapter (`integrations/maf`) currently reads; write-back goes through the existing decisions/sessions API so governance (review, audit) applies to agent-authored records exactly as to human ones.

---

## Workstream G — epistemic separation: what happened vs what a source claims

*Shipped 2026-08-19/20. Recorded here because the rest of the roadmap now depends on the distinction.*

### G1. Knowledge is not observation ✅

A KB article reconstructed into an `episode` asserts that something happened. It did not — a document claims a resolution works. Everything downstream then read it as observed: the playbook prompt tells the model episode outcomes are empirical evidence a step works, patterns counted them as recurrence, the agent cited them as `[ep-N]`. Found when a knowledge backfill took the corpus from 53 articles to 629 and surfaced 299 all-knowledge episodes — 8 of them predating the backfill, so the gap was long-standing and merely too rare to see.

`_cluster_has_observational_evidence` now gates episode **synthesis** only. Knowledge still correlates, embeds, reaches the graph and seeds patterns; what it cannot do is become an account of something that happened.
**In code:** `workers/extraction_tasks.py`; migrations 0072–0073.

### G2. `KnowledgeCase` as a first-class object ✅

Not an `episodes.kind` discriminator: with a kind column every query that counts, clusters, scores, reviews or cites episodes stays correct only while everyone remembers `AND kind = 'observed'`, and one forgotten predicate silently recreates the contamination. A missing join fails loudly; a missing predicate returns a wrong number. 482 episodes migrated into 135 cases (duplicate reconstructions of one article collapsed, richest kept), originals tombstoned.

### G3. `PatternEvidence` — the evidence ledger ✅

`episode_count` cannot tell three KB articles from nineteen resolved incidents. The ledger records what each piece of evidence contributes and on what footing — `support_role` (including `contradicts_resolution`), `evidence_class`, `observed_at`, `outcome`. A CHECK constraint enforces that only an episode may be `empirical` and only empirical rows carry an outcome, because that is the one place a future code path cannot forget it.

This is what makes two capabilities possible: **cold start**, where a pattern exists on documentation alone and *graduates* as incidents arrive (measured: ~55% of knowledge cases match no existing pattern — most of the KB documents failure modes the incident history has never seen), and **knowledge drift**, where a documented resolution accumulating contradictions from recent episodes becomes a query rather than an impossibility.

### G4. Claim-level epistemic status — *not started*

Source type is not epistemic status. A Teams message saying "I think restarting IIS might help" is hypothesis; "restarted at 14:32, recovered at 14:34" is observation. The target taxonomy is prescriptive → documented → empirical → conversational → inferred, carried on claims rather than inferred from the connector. `claim` already has `claim_type` / `validation_status`; this extends rather than replaces it.

### G5. Prescriptive knowledge as its own object — *not started*

A known-error KB article and a mandatory SOP are both "approved knowledge" and mean different things: one reports what tends to work, the other dictates what must be done. Today both become `KnowledgeCase`. Splitting them matters when a playbook must reconcile "the SOP requires a backup step" with "no episode performed one".

---

## Workstream H — operational situation intelligence: "what is happening now?"

*Schema shipped 2026-08-20 (H1). The rest is blocked on connectors, not on code.*

ContextEdge can say what happened (episodes) and what a source claims (knowledge cases). It cannot say what is happening: an agent receiving an incident sees that incident and must work out unaided whether it is isolated or one signal of a wider occurrence.

An `OperationalSituation` is a bounded real-world occurrence assembled from many signals. Deliberately **not** a renamed `CorrelationEdge` — an edge says two pieces of evidence look related; a situation says many signals describe one thing. Nor is it an episode: a situation may exist while nothing is resolved, and an episode needs a resolution to reconstruct.

### H1. Situation schema and graph vocabulary ✅

`operational_situations`, `situation_evidence_memberships`, `situation_entity_impacts`, `situation_change_candidates`, plus seven registered graph relations (four MAF-traversable, three excluded with reasons). Three invariants live in the database: a change after onset cannot be a cause, a merged situation must name its survivor, and membership/impact are unique so a retry cannot invent a second occurrence.
**In code:** `models/situation.py`, `graph/edge_types.py`, migration 0074.

### H2. Coverage and missing-context reporting ✅

Shipped 2026-08-21. Ten facets, each answering with one of eight statuses, and a `blind_spots` list naming where an empty result must not be read as a zero. The discrimination that repays the work is `unavailable` vs `not_selected`: ServiceNow's `em_alert` needs ITOM, which a stock instance does not activate, so reporting it as "not approved for sync" sends an operator to a checkbox that does not exist.

Folded in: a canonical capability declaration (`services/source_capabilities.py`). Record kinds are *derived* from `evidence_typing`; relations are declared and cross-checked against the five reference services by tests in both directions. This is what lets coverage say "this source cannot supply change links" rather than silently reporting none — and it is the layer a new ITSM adapter declares itself into.
**In code:** `services/coverage_service.py`, `services/source_capabilities.py`, `GET /api/v1/graph/coverage`. Design: [COVERAGE_AND_CAPABILITY](COVERAGE_AND_CAPABILITY.md).

### H3. Deterministic situation correlation ✅

Shipped 2026-08-21. The signal audit above was written when Zoho Desk was the only connected source, and connecting ServiceNow inverted it. **Measured 2026-08-21:** `issue_signatures` = 0 and `source_facets` = 0, so issue signature and environment — the two the plan counted on — have *no* data. What arrived instead is what the plan assumed absent: `child_of_incident` duplicate links (human-authored), CI entities, and error signatures.

So the correlator merges on authoritative links and on same-CI + window + symptom agreement, and refuses to merge on a shared problem or a shared CI alone. Hub suppression fired on a real hub (`PolicyAdminService`, 12 incidents in three days). Live result: 51 groups considered, 1 situation created, 50 singletons left alone.

Two defects surfaced in review and were fixed: evidence carried `sys_updated_on` rather than occurrence time (making the window veto inert), and the first implementation created a duplicate situation on every run.
**In code:** `services/situation_correlation_service.py`, `workers/correlation_tasks.py`, `GET /api/v1/graph/situations`. Design: [SITUATION_CORRELATION](SITUATION_CORRELATION.md).

### H4. Topology correlation and blast radius — *blocked on CMDB*

`depends_on` / `hosted_on` / `runs_on` are registered in the edge vocabulary and **zero rows exist**; all 849 entities are `topic` or `knowledge_category`. Depends on C1.

### H5. Monitoring integration — *blocked on a monitoring connector*

Alert rollups, event grouping, source lineage (an alert, the ticket it opened and the mail it sent are one observation, not three), independent corroboration (three monitoring systems agreeing genuinely is three), recovery evidence, storm velocity. No alert evidence exists today.

### H6. Situation-aware change correlation ✅

Shipped 2026-08-21, and it supersedes B4's same-CI lookup as planned: situation → affected entities → one dependency hop → ranked candidates. `correlation_score` stayed a ranking and `confirmed` stayed reachable only from governed evidence — a ServiceNow `caused_by` a human filled in, recorded with what asserted it.

Measured on the canonical incident: two candidates, correctly ordered. The same-CI change at `confirmed`, a change one hop away at `candidate` 0.55, and the deliberately coincidental control on an unrelated CI absent entirely rather than merely ranked low.

Building it surfaced a defect worth more than the feature: one PDI record dated 2035 had pinned the `change_request` keyset checkpoint nine years ahead, so every incremental sync since returned zero rows and reported success.
**In code:** `services/change_correlation_service.py`, `GET /graph/situations/{id}/change-candidates`. Design: [CHANGE_CORRELATION](CHANGE_CORRELATION.md).

### H7. Diagnostic context service ✅

Shipped 2026-08-21, and **the acceptance test passes**: an agent given one incident identifier obtains the operational context around it rather than reasoning from the description alone.

Seven facets — situation, impact, duplicates, changes, recurrence, remediation, coverage — each with its own status, provenance, count and truncation flag, plus a `blind_spots` list naming what must not be read as a zero. Bounded per facet, and security-filtered on every record-bearing facet rather than only at the entry point.

Two defects found in review, both of the same family the rest of this roadmap keeps producing: `blind_spots` reported `[]` on a deployment with no monitoring connector, because facet-level and deployment-level absences were tracked separately and coverage had "successfully" reported its own inability; and domain scoping was applied to the incident lookup alone, so a restricted reader would have seen duplicates, recurrence and change candidates from outside their scope.

Monitoring (H5) remains the one facet this deployment cannot answer, and the bundle says so rather than returning silence.
**In code:** `services/diagnostic_context_service.py`, `GET /graph/diagnostic-context/{id}`. Design: [DIAGNOSTIC_CONTEXT](DIAGNOSTIC_CONTEXT.md).

### H8. Lifecycle, merge and review ✅

Shipped 2026-08-21. `emerging → active → stabilizing → resolved`, plus reopen, recurrence and merge — and the rule held: **absence of signal is never recovery**. Only member incidents carrying a resolution in the source system move a situation toward resolved, so a quiet situation with no resolved members stays `active`, which looks wrong on a wallboard and is the only honest reading.

Reopen and recurrence stayed distinct, which is the S1/S5 distinction one level up. A reopen keeps the situation's identity and clears its recovery stamps; a recurrence is a *new* situation linked by `recurred_from`. Merge moves memberships and retires rather than deletes the duplicates, because which situation a signal was first filed under is the lineage; the database refuses a `merged` row that names no survivor, verified live.

Automatic split remains out of scope, as specified.
**In code:** `services/situation_lifecycle_service.py`, `POST /graph/situations/lifecycle`, `POST /graph/situations/{id}/merge`. Design: [SITUATION_LIFECYCLE](SITUATION_LIFECYCLE.md).

---

## Sequencing

| Order | Item | Workstream | Effort | Depends on | Rationale |
| --- | --- | --- | --- | --- | --- |
| 1 | Error-signature fingerprints | D1 | S | — | cheapest, sharpest diagnostic gain |
| 2 | Salient slicing + summary distillation | A1, A2 | S–M | — | fixes a proven knowledge-loss bug (F4); improves every LLM call |
| 3 | Thread backfill | A3 | S | A1 | recovers known-lost knowledge |
| 4 | `change_request` + causal-vocabulary projection | B1, B5 | S | — | near-free; unlocks the change join |
| 5 | Signature seeding/projection + `aggregated_by` | D2, D3 | S–M | — | signature-first entry |
| 6 | Event layer + `preceded_by` seed layer | B2, B4 | M | B1 | the diagnose-time correlation capability |
| 7 | Inventory-diff detector | B3 | M | B2 | first high-yield event source |
| 8 | `cmdb_rel_ci` topology + criticality facts | C1, C2 | M | — | blast radius |
| ✅ | Efficacy rollups + applicability + negative knowledge | E1–E3 | M | — | **shipped 2026-08-21, pulled forward** — the one capability no competitor ships; see [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md) |
| ✅ | Agent decision write-back | F1 | M–L | — | **shipped 2026-08-21** — the compounding loop, with the self-training hazard contained in three places. See [AGENT_DECISION_WRITEBACK](AGENT_DECISION_WRITEBACK.md) |
| 11 | Claims population | A4 | M–L | A2 | granular assertions, once summaries prove out |

Revised 2026-08-20. Workstream G shipped out of order because a knowledge backfill exposed the contamination as a live defect rather than a planned improvement; the sequence below reflects what the corpus can now support.

| Order | Item | Workstream | Effort | Depends on | Rationale |
| --- | --- | --- | --- | --- | --- |
| ✅ | Epistemic separation (knowledge ≠ observation) | G1–G3 | — | — | shipped; a document's claim was being counted as an observed outcome |
| ✅ | Situation schema and graph vocabulary | H1 | — | — | shipped ahead of its connectors so the data has somewhere to arrive |
| ✅ | Coverage / missing-context reporting | H2 | S | — | **shipped 2026-08-21** — eight statuses, ten facets, plus a canonical capability declaration; see [COVERAGE_AND_CAPABILITY](COVERAGE_AND_CAPABILITY.md) |
| ✅ | Situation correlation | H3 | M | — | **shipped 2026-08-21** — in fuller form than planned: ServiceNow supplied authoritative duplicate links, which the plan assumed absent. See [SITUATION_CORRELATION](SITUATION_CORRELATION.md) |
| ✅ | `change_request` ingestion | B1 | S | — | **shipped 2026-08-21** — 39 change evidence rows; the change join is data |
| ✅ | CI entities + `depends_on` topology | C1 | M | — | **largely already wired** — the topology cache warms itself once a real CMDB is connected; 28 CIs, 19 `depends_on` edges |
| ✅ | Criticality / owner / tier on entity facts | C2 | S | — | **shipped 2026-08-21** — three defects, each of which looked wired: attributes were write-once, criticality is only on `cmdb_ci_service`, and `owned_by` was captured nowhere. See [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md) |
| — | Monitoring alert/event ingestion | H5 | M | **an instance with ITOM** | blocked on the instance, not the connector: `em_alert` is absent, discovery skips it |
| ✅ | Situation-aware change correlation | H6 | M | — | **shipped 2026-08-21** — ranked candidates with one-hop blast radius; building it found a single future-dated row that had silently stopped change ingestion entirely. See [CHANGE_CORRELATION](CHANGE_CORRELATION.md) |
| ✅ | Diagnostic context service | H7 | M–L | — | **shipped 2026-08-21** — the acceptance criterion is met: one incident identifier returns seven provenanced facets and an honest blind-spot list. See [DIAGNOSTIC_CONTEXT](DIAGNOSTIC_CONTEXT.md) |
| ✅ | Situation lifecycle, merge, review | H8 | M | — | **shipped 2026-08-21** — recovery is evidenced, never inferred from silence; reopen and recurrence stay distinct. See [SITUATION_LIFECYCLE](SITUATION_LIFECYCLE.md) |
| 9 | Claim-level epistemic status | G4 | M–L | — | source type is not epistemic status |
| 10 | Prescriptive knowledge as its own object | G5 | M | G4 | an SOP and a known-error article are not the same claim |

**Revised again 2026-08-21: the connector block is mostly lifted.** A live ServiceNow instance supplies changes, CIs, CI relationships and problems, so B1 and the bulk of C1 are shipped and H6 has data to rank. Only monitoring remains absent, and for a narrower reason — the alert connector exists, the instance lacks the ITOM plugin. See [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md).

The warning that motivated the block still stands for everything built on top. Correlation logic validated against a PDI's randomly generated records proves the code runs, not that it works: those records encode no causality — no change precedes the incident it caused, no CI depends on another, no incident duplicates its neighbour. The scenarios in `evals/fixtures/servicenow_scenarios.py` supply the causality, each with a stated assertion, including two (S2, S4) that exist to make sure a correlator does **not** fire. The instance's ~600 random records stay in the corpus as the adversarial noise those two are measured against.

Every item that changes model-facing prompts or projection composition follows the measurement discipline established for thinking budgets and projection caps ([18](18-cost-observability-and-containment.md)): measure before, A/B on real data, ship only what the numbers support, record negative results.

## Non-goals (decided, with reasons)

- **Live metrics in the graph** — incompatible freshness regime; the agent gets live state from monitoring tools (C, boundary decision).
- **Raw graph-query access for the agent** — ungoverned traversal is how budgets and tenancy leak; new needs become seed layers (C, boundary decision).
- **Chunks as graph nodes** — retrieval units, not meaning units (A, non-goal).
- **Step→tool binding in ContextEdge** — orchestrator's contract (E, boundary decision).
- **Replacing the ontology with a five-type operational schema** — it is a subset of what exists and would discard governed precedent (episodes, patterns, decisions, outcome tracking), which is the differentiator.
