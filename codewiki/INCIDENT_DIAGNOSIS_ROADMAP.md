# Incident-diagnosis roadmap: evolving the context graph from memory to diagnostic instrument

**Status:** implemented 2026-08-07 (commits `8142ad1`..`55d361e`, one per item, each with its measurements in the commit message). Notable outcomes: the F4 thread flipped to operational at 0.95 with a faithful summary (A1/A2); 17 error signatures with 5 day-one recurrence hits (D1); signature-first entry validated live end-to-end (D2); 36 change records ingested (B1); the browser-upgrade change class caught by inventory diff (B3); a threads-pool event-loop lock bug found and fixed by the A3 sweep. Two negative results recorded and enforced by tests: claims-in-the-gate-call failed label stability 4/8, so relevance v3 is registered but NOT default (A4 ships dormant); thinking-budget caps remain relevance-only. **Qualifier (2026-08-09 review):** the outcome/fix flywheel is *schema-only* — `CaseOutcome`, `CaseStateTransition`, and fix-pattern statistics have models and projection types but no production writers, so "implemented" here means the diagnostic *read* path; the learn-from-outcome loop is not operating. Remaining follow-ups live in KNOWN_GAPS.
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

### C2. Criticality / owner / tier on entity facts

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

### E1. Efficacy rollups on the remediation path

Aggregate `validated_fix`/`invalidated_fix` outcomes into playbook node facts or `recommends` edge metadata: `success_count`, `failure_count`, `last_validated_at`, notable failure contexts. The agent chooses remediations by evidence-weighted success rate, and can say *why*: "resolved 8 of 9 matching incidents; failed twice on version ≥ 6.2."

### E2. Applicability constraints on edges, not buried in text

The applicability machinery exists (`version_floor`/ceiling extraction, `fix_applicability`). Project the constraints onto `recommends`/`addresses` edges so the agent can structurally *rule out* a fix that does not match the incident's product version or environment — mis-applied remediation being the classic agent failure.

### E3. Negative knowledge as a first-class projection

`[did not work]` step markers and `invalidated_fix` edges exist; surface them *with* every recommendation ("known to fail when X"), and write confirmed `preceded_by` suspicions back into `trigger_change` (closing B4's loop). An agent that repeats a documented-bad fix destroys trust faster than one that abstains.

**Boundary decision:** step→tool execution binding (mapping a playbook step to an executable automation) belongs to the orchestrator (AEAIHubOrchestrator), not to ContextEdge. The graph supplies *what* to do — preconditions, verification, rollback per step (the v4 playbook prompt already pushes this shape); binding steps to tools is the orchestrator's contract. Blurring this makes the knowledge layer deployment-specific.

---

## Workstream F — the learning loop: diagnoses that compound

*The biggest structural omission. Everything above is inventory; this is the flywheel.*

### F1. Agent decision write-back

The `session` / `decision` / `chose` / `resulted_in` machinery exists, but nothing makes the MAF agent's own diagnostic trail flow back into the graph. Each diagnosis should write back: hypotheses considered, which was chosen, outcome. The next agent facing the same signature then inherits "the connection-leak hypothesis was checked and disproven for this signature; it was the pool size." Without write-back, every diagnosis starts from zero and the graph learns only from human tickets, never from agent runs.
**In code:** the MAF adapter (`integrations/maf`) currently reads; write-back goes through the existing decisions/sessions API so governance (review, audit) applies to agent-authored records exactly as to human ones.

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
| 9 | Efficacy rollups + applicability + negative knowledge | E1–E3 | M | — | trustworthy remediation choice |
| 10 | Agent decision write-back | F1 | M–L | — | the compounding loop |
| 11 | Claims population | A4 | M–L | A2 | granular assertions, once summaries prove out |

Every item that changes model-facing prompts or projection composition follows the measurement discipline established for thinking budgets and projection caps ([18](18-cost-observability-and-containment.md)): measure before, A/B on real data, ship only what the numbers support, record negative results.

## Non-goals (decided, with reasons)

- **Live metrics in the graph** — incompatible freshness regime; the agent gets live state from monitoring tools (C, boundary decision).
- **Raw graph-query access for the agent** — ungoverned traversal is how budgets and tenancy leak; new needs become seed layers (C, boundary decision).
- **Chunks as graph nodes** — retrieval units, not meaning units (A, non-goal).
- **Step→tool binding in ContextEdge** — orchestrator's contract (E, boundary decision).
- **Replacing the ontology with a five-type operational schema** — it is a subset of what exists and would discard governed precedent (episodes, patterns, decisions, outcome tracking), which is the differentiator.
