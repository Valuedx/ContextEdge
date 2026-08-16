# ContextEdge Backlog — consolidated from the design reviews

**Purpose.** One systematic, dependency-ordered backlog consolidating every open item
from the three design documents plus the gaps accumulated in [KNOWN_GAPS.md](KNOWN_GAPS.md).
Each item is sized, sourced, and carries acceptance criteria so it can be picked up
cold and shipped on its own stacked branch.

**Sources**
- **[Doc-1]** — the correlation/episode review (P0–P4 sequence, acceptance scenarios,
  conversational-shorthand appendix). P0–P4 shipped as PRs #27–#34.
- **[Doc-2]** — the 27-section conversational design (Teams messiness: thread topics,
  corrections, replies, bots, edits, ASR, negative evidence, provisional cases).
  Foundations shipped as PRs #28 and #30.
- **[Doc-3]** — the correlation / similarity / applicability document (LPT001 → LPT121:
  class taxonomy, problem fingerprints, fix applicability levels, cohort statistics).
  Nothing shipped yet.
- **[Gap]** — items recorded in KNOWN_GAPS during the 2026-07/08 shipments.
- **[Doc-4]** — the v6 Context Graph schema comparison and its validation against the
  code at `76c4e82` ([REVIEW-2026-08-V6-SCHEMA.md](REVIEW-2026-08-V6-SCHEMA.md)):
  unwired `0029` governance columns, and the prerequisites for a write-capable agent.
  Epic F.

**Working agreement** (how every item ships): stacked branch off
`feature/maf-context-graph-integration` → implement → three review-fix-review passes
(different lens each pass) → full backend suite → PR with `--body-file` → CI green
(backend pytest, frontend vitest, ruff — all required) → merge → sync → update
KNOWN_GAPS and the codewiki. Migrations are additive and re-runnable; prompt versions
are immutable (changes ship as new versions); LLM proposes, deterministic policy
disposes; the mass-merge guard applies to every new linking signal.

---

## Shipped foundation (context for everything below)

| Layer | What exists | PRs |
|---|---|---|
| Cluster materialization | `resolve_episode_cluster` (case links + memberships + correlation edges, SQL visibility fence, 30-day window, 50-cap, per-member reasons, fingerprint) | #27 |
| Conversational foundations | Debounced reconstruction (180s + settlement + starvation guard), Teams metadata capture (`reply_to_id`, bot flags, edits/deletes, attachments) | #28 |
| Ticket bridging | `case_identifiers` / `evidence_case_memberships` / `pending_identifier_mentions`; resolve-then-link; digest guard; order independence (migration 0038) | #29 |
| Reply inheritance | Single-case parents only; dissociation phrase veto; chains | #30 |
| Entity rarity | Degree-weighted identity tier; hub dampening (≥200 links = no signal) | #31 |
| Semantic suggestions | Chunk-ANN + similarity floor 0.7 + non-semantic corroborator → reviewer queue; reject permanent (migration 0039) | #32 |
| Field authority | Episode prompt v3 (authority by fact type), contradictions preserved (migration 0040), strict draft schema, per-source `synthesis_role`, quality metric | #34 |
| M6 surfaces & robustness | C4 queue cap + Review Queues console · C6 projection contradictions · A8 edits/deletes lifecycle · A9 ASR normalization · C5 episode_citation eval kind · C7 pattern domain audit | #53–#58 |
| M5 learning loops | B5 cohort stats + reviewer-gated promotion (0047: model/class/family counters, candidate rules always review-gated, failures narrow automatically) · C1 suggestion learning (per-pair floors from reviewer outcomes, stats endpoint) · C3 conflicting-ticket hard veto in the identity tier · B6 fleet grouping (0048: change-keyed detector, reviewer-gated parent-case minting, fleet_member memberships, 30m beat) | #49–#52 |
| M4 similarity & transfer | B3 issue signatures (0045: fingerprint dedupe per tenant, approval-gated extraction, P4-pattern Pydantic gate) · C2 recurrence membership (pointer to precedent, excluded from cluster expansion) · B4 applicability ladder (0046: rules with required/excluded trait predicates, 7-level deterministic assessment, level floors, named provisional weights, MAF tool + API; Doc-3's four LPT001 examples are the acceptance tests) | #47–#48 |
| M3 thread understanding | A3 thread topics (0044: anchored/provisional topics, unification sweep, thread_topic memberships) · A4 reference resolver (trigger-gated, identity-layer candidates, exactly-one-or-abstain) · A5 quoted content (quote detection, mentioned_only cap, weighted digest, quoted reconciliation) · A6 bot handling (structural card parsing at 0.95, prose downweight, no bot anchors, reduced bot inheritance) | #43–#46 |
| M2 applicability foundation | B1 entity class taxonomy (0042: 13-class seeded tree, deterministic uuid5 ids, instance_of/subclass_of edges, conservative sys_class_name map, configuration_item fallback; OS-as-trait design call) · B2 normalized traits (0043: manufacturer/model/os_name/os_version columns + partial model index, widened reference dot-walks + topology detail fields, present-wins/absent-never-clears refresh) | #40–#41 |
| M1 conversational precision | A1 message-function classifier (0041, prompt family `message_function`, veto upgrade) · A2 correction supersession (`status='corrected'`, propagation, audit event) · A7 negative evidence (`status='negative'`, thread blocking, resolver fence, reviewer-removal negation) · A10 reply reconciliation in debounced reconstruction | #36–#39 |

---

## Epic A — Conversational correlation, the remaining tiers [Doc-2, Doc-1 appendix]

The deterministic tiers (ticket tokens, reply structure) are live. What remains is the
*interpretive* layer: understanding what a message is doing, what it refers to, and
when it changes its mind. Ordered by the design's own priority: precision first.

### A1 · Message-function classifier — S/M — **SHIPPED 2026-08-02**
**What.** A small LLM classification per conversational evidence item: is this message
a status update, a question, a correction, an explicit dissociation, a resolution
confirmation, or noise? Persist the label on the evidence (JSONB or column).
**Why.** Three shipped features are waiting on it: the dissociation veto is a phrase
list (`DISSOCIATION_PHRASES`) that misses paraphrases; corrections (A2) need to know a
message *is* a correction; negative evidence (A7) needs explicit "not related"
detection. One classifier feeds all three.
**Sketch.** New prompt family `message_function` v1 (registry). Classify in the
correlate hook for conversational sources (budget-gated, fail-soft → label
`unclassified`). Replace `has_dissociation_language` with classifier output while
keeping the phrase list as a fallback when the LLM is unavailable.
**Dependencies.** None. **Unblocks** A2, A7.
**Acceptance.** "Different issue, is the ordering DB also down?" vetoes inheritance via
the classifier; a paraphrase ("this isn't about the VPN thing") also vetoes; phrase
list still vetoes when LLM budget is exhausted.

### A2 · Corrections supersede earlier links — M — **SHIPPED 2026-08-02**
**What.** When a later message corrects an earlier one ("Correction — it's Mary's
ticket, not John's"), the earlier message's derived memberships/links must be
superseded, not accumulated alongside.
**Why.** Doc-2 §16: without supersession, both the wrong and right case memberships
persist and the cluster resolver expands through both.
**Sketch.** On a `correction` function label (A1): resolve what the correction targets
(reply structure first, then recency within thread), mark the target's affected
`evidence_case_memberships` rows `status='superseded_by_correction'` (status column
already exists), write the corrected membership, and emit an operational event for the
audit trail. Never delete — supersede.
**Dependencies.** A1. **Acceptance.** The 10:00 "John's VPN ticket" / 10:05
"Correction — it is Mary's" sequence ends with exactly one active membership.

### A3 · Thread-topic state + provisional cases — M/L — **SHIPPED 2026-08-03**
**What.** A per-thread topic record: which case(s) a thread is currently "about",
updated as anchors arrive; and a *provisional case* for threads discussing an incident
that has no ticket yet.
**Why.** Doc-2's central model: a thread's messages inherit the thread topic without
each message needing its own anchor; incidents often live in chat before a ticket
exists, and the provisional case lets evidence accumulate under an identity that
merges into the real canonical case when INC0010427 finally appears.
**Sketch.** `thread_topics` table (thread_id, canonical_case_id | provisional, since,
confidence, set_by: anchor|inheritance|classifier). Provisional case = canonical_case_id
minted locally, later unified via the existing case-identifier registration path
(reconciliation already handles late tickets for mentions — extend to provisional
topics). Topic changes on explicit anchors and A2 corrections; never on mere mentions
(digest guard).
**Dependencies.** A1 helps, not required. **Acceptance.** A 40-message Teams thread
where only message 3 says "tracking under INC0010427" yields memberships for
subsequent messages via topic, not 40 pending mentions; a pre-ticket thread merges
cleanly when the ticket arrives.

### A4 · Conversational-reference resolver — L — **SHIPPED 2026-08-03**
**What.** Resolve indirect references: "John's ticket" (person→active-case index),
"the prod DB of the ordering server" (entity→entity traversal→active case).
**Why.** Doc-1's appendix + Doc-2: most chat references are indirect. Precision comes
from *indexes*, not free-form LLM guessing.
**Sketch.** Two query paths, both deterministic candidate generators with LLM
adjudication only between top candidates (identity-resolver pattern): (1) person-role
index — active cases where the person is assignee/reporter (from ticket fields already
ingested); (2) entity→active-case — entities linked via `affects_ci` to open cases,
one graph hop for possessives. Abstain when >1 strong candidate; emit membership
`relationship_type='explicit_reference'` with lower confidence (0.8), or a pending
mention when unresolved.
**Dependencies.** A3 (thread topic disambiguates), B1/B2 sharpen entity traversal.
**Acceptance.** "Can you look at John's ticket?" resolves when John has exactly one
active assigned case; abstains (logged) when he has three.

### A5 · Quoted/forwarded content + span-level references — M — **SHIPPED 2026-08-03**
*(Shipped with categorical span provenance — subject/body/quoted_body; numeric character offsets deferred to the review-console work C4/E5.)*
**What.** Detect quoted/forwarded blocks inside emails and Teams messages; extracted
ticket tokens inside quoted spans get `extraction_location='quoted'` with reduced
confidence, and memberships can carry character-span provenance.
**Why.** Doc-2: a forwarded digest inside a reply must not look like the author
mentioning three tickets first-hand. Span provenance makes review explainable.
**Sketch.** Quote detection heuristics per source (reply markers, `>` prefixes,
"---------- Forwarded message ----------"); `extract_ticket_tokens` gains span output;
`pending_identifier_mentions`/`evidence_case_memberships.extraction_location` gains
`quoted_body`; digest threshold counts quoted mentions with a lower weight.
**Dependencies.** None. **Acceptance.** A reply quoting last week's three-ticket digest
creates `mentioned_only`-at-most memberships from the quoted block while the author's
own new sentence keeps full confidence.

### A6 · Bot messages: weighting + structured card parsing — M — **SHIPPED 2026-08-03**
**What.** Bot/webhook messages (`is_bot`/`from_application`, already captured) get
role-aware treatment: a ServiceNow card is parsed *structurally* for ticket
fields/state (authoritative for those fields, per the P4 authority model); bot chatter
never counts toward correlation confidence like a human message.
**Why.** Doc-2 §15: the bot's payload is data, not prose — parsing it through an LLM
as conversation both wastes tokens and launders authority.
**Sketch.** Connector-level: recognized card schemas (adaptive card attachments from
known apps) → structured payload fields on the evidence; ticket-card ticket numbers
register through the identifier path with `extraction_location='bot_card'` (1.0-level
confidence — it IS the ticket system speaking); generic bot text excluded from
`bridge_conversational_mentions` body confidence (downweight to 0.7).
**Dependencies.** None. **Acceptance.** A ServiceNow connector card announcing
INC0010427 creates the membership without an LLM call; a random webhook bot's message
never anchors a thread topic by itself.

### A7 · Negative evidence store — S/M — **SHIPPED 2026-08-02**
**What.** Explicit dissociations ("this is NOT related to INC0010427") persist as
negative links that veto future automatic linking of that (evidence/thread, case) pair.
**Why.** Doc-2: today a veto only blocks one inheritance decision at one moment; the
next signal (a later mention, a semantic suggestion) can re-link what a human
explicitly severed.
**Sketch.** Reuse `evidence_case_memberships` with `status='negative'` (unique
constraint already prevents a duplicate positive row — the negative row *becomes* the
first writer and blocks it); cluster resolver and suggestion generator skip pairs with
a negative row; reviewer remove-evidence endpoint also writes one.
**Dependencies.** A1 (detection). **Acceptance.** After "not related to INC0010427",
a later plain mention of that ticket in the same thread does not re-create an active
membership without review.

### A8 · Edits & deletes reconciliation — M — **SHIPPED 2026-08-03**
**What.** Act on the edit/delete markers PR #28 captures: an edited message re-extracts
(tokens may have changed) and supersedes its prior derived rows; a deleted message's
derived memberships/pending mentions are retired.
**Why.** Doc-2: otherwise retracted statements keep steering correlation forever.
**Sketch.** In normalize/correlate, when `last_edited_at` advances for an existing
external_id: supersede prior memberships from that evidence, re-run bridging;
`is_deleted` → mark derived rows `status='retracted'` (evidence itself is immutable —
retention policy owns content deletion). Debounced reconstruction already picks up the
cluster change.
**Dependencies.** None. **Acceptance.** Editing "INC0010427" to "INC0010455" in Teams
moves the membership; deleting the message retires it.

### A9 · Transcript robustness (ASR + code-switching) — M — **SHIPPED 2026-08-03**
**What.** Ticket-token and entity extraction tolerant of speech-to-text mangling
("I N C zero zero one zero four two seven", "V P N gateway") and mixed-language text.
**Why.** Doc-2: meeting transcripts are a first-class evidence source
(`local_file`/future connector) and the current regex requires perfect formatting.
**Sketch.** A normalization pre-pass for transcript-role evidence: collapse spelled-out
digits/letters around known prefixes, unicode/diacritic folding; keep the conservative
regex as the only *matcher* (normalize-then-match, never fuzzy-match). Tag derived
memberships `extraction_location='transcript_normalized'` at reduced confidence.
**Dependencies.** None. **Acceptance.** "we're tracking this under I N C zero zero one
zero four two seven" resolves to INC0010427 as a pending mention/membership.

### A10 · Reply-inheritance ordering reconciliation — S [Gap] — **SHIPPED 2026-08-02**
**What.** A reply that correlated before its parent gained membership never retries.
Re-run `inherit_reply_membership` for un-anchored replies when their parent's
membership lands (mirror of pending-mention reconciliation) or during debounced
reconstruction.
**Why.** KNOWN_GAPS entry from PR #30 — membership precision loss on out-of-order
ingestion.
**Dependencies.** None. **Acceptance.** Parent gains its case after the reply was
processed → reply's `reply_inheritance` membership appears without manual action.

---

## Epic B — Similarity & fix applicability (LPT001 → LPT121) [Doc-3]

Three distinct relationships, never conflated: same occurrence (correlation), similar
problem (pattern), applicable fix (precondition match). Phases are strictly ordered —
every later phase predicates on traits and classes Phase B1/B2 create.

### B1 · Entity class taxonomy — M — **SHIPPED 2026-08-02**
**What.** `entity_classes` table (canonical_key, display_name, parent_class_id,
class_family, attributes_schema) with a small seeded hierarchy
(computing_device → endpoint → portable/fixed → laptop/desktop; server; network_device;
database_server …), plus `instance_of` / `subclass_of` graph edges connecting entities
to classes and classes to parents.
**Why.** The generalization unit for fix transfer. Today `CI_CLASS_ENTITY_TYPES` maps
five ServiceNow classes and everything else is flat `configuration_item`.
**Sketch.** Migration + seed data (idempotent, per seed-script conventions); map
ServiceNow `sys_class_name` → class keys (extend the existing map, keep raw class in
attributes); classifier fallback = `configuration_item` class. Graph edges via
`ensure_edge`.
**Dependencies.** None. **Unblocks** B2–B5, sharpens A4.
**Acceptance.** LPT001 → `instance_of` → Dell Latitude 5420 → `subclass_of` → laptop →
… → computing_device traversable in the graph; unknown classes degrade to today's
behavior.

### B2 · Normalized traits + widened CMDB ingestion — M — **SHIPPED 2026-08-02**
**What.** First-class searchable traits on entities: manufacturer, model, os_family /
os_version / os_build, device_role, environment, criticality (columns or indexed
structured attributes) — populated by widening the ServiceNow reference/topology pulls
(`cmdb_ci.manufacturer`, `model_id`, `cmdb_ci_computer.os`, `os_version`). Driver
versions / installed software modeled as *optional* traits (endpoint-management data we
do not ingest yet — do not pretend otherwise).
**Why.** Applicability rules can't predicate on traits that were never captured.
**Dependencies.** B1. **Acceptance.** A CI referenced by a ticket lands with
manufacturer/model/OS traits when ServiceNow has them; absent traits are absent, not
guessed.

### B3 · Issue signatures (problem fingerprints) — M/L — **SHIPPED 2026-08-03**
**What.** A structured `issue_signatures` record per approved episode: affected
capability, failing component class, failure mode, trigger/recent change, error
signature ref, environment, scope — LLM-extracted, schema-validated (P4 gate pattern),
linked to the episode and its entities/classes.
**Why.** "Wi-Fi adapter disappears after sleep" must match across devices even when
log wording differs; `ErrorSignature` alone is exact-shape.
**Sketch.** New table + extraction prompt family `issue_signature` v1; extraction runs
post-approval (reviewer-approved episodes only — unreviewed stories must not mint
signatures); dedupe by normalized signature key per tenant.
**Dependencies.** B1/B2 (component/class references). **Acceptance.** The LPT001 and
LPT121 "adapter_missing_after_resume" episodes produce one shared signature with two
episode links.

### B4 · Fix applicability rules + the 7-level ladder — L — **SHIPPED 2026-08-03**
**What.** `fix_applicability_rules` (fix_pattern_id, target_class_id, required_traits,
excluded_traits, applicability_level, minimum_evidence, confidence, approval_requirement)
plus a deterministic `assess_fix_applicability(target_ci, candidate_fixes)` service
returning the explicit level (exact CI / same model+config / same component+version /
same class / related class / cross-class capability / semantic only), matching factors,
differences, and `requires_review`.
**Why.** Doc-3's core deliverable: a laptop fix is not "for laptops" — it is for the
scope its causally-relevant traits define. LLM may *propose* a rule scope; the
deterministic matcher validates every required trait before recommending.
**Sketch.** Additive scoring with named constants (error signature +0.25, component
+0.20, driver version +0.15, OS build +0.10, class +0.10, semantic +0.05; contradiction
penalties; the risk-service transparency pattern — factors list = explanation).
Weights explicitly provisional pending B5 calibration. Surface as a MAF read-only tool
+ API endpoint.
**Dependencies.** B1–B3. **Acceptance.** Doc-3's four examples behave as specified:
Latitude-5420 BIOS/BitLocker → very high; Chrome-crash laptop→desktop → transfers on
software scope; battery→random-power-off → no precedent; AX201 Code 10 laptop→desktop
→ partial transfer with `requires_review=true`, Realtek desktop scores much lower.

### B5 · Cohort success statistics + promotion policy — M/L — **SHIPPED 2026-08-03**
**What.** Per-cohort outcome counts on fix patterns (9/10 on Latitude 5420, 0/4 on
desktops) fed by the existing `case_outcome_fix_patterns` + execution-verification
loop, and a reviewer-gated promotion ladder: one success = precedent → same-model rule
→ class rule → family rule; failures narrow scope automatically.
**Why.** Prevents one lucky laptop fix from being overstated as universal; provides
the calibration data B4's weights are waiting for.
**Dependencies.** B4. **Acceptance.** A fix succeeding twice on Latitude 5420 and
failing on a desktop yields a model-level candidate rule and an automatic desktop
exclusion; promotion to `windows_endpoint` requires reviewer approval.

### B6 · Fleet / major-incident grouping — M/L — **SHIPPED 2026-08-03**
**What.** Parent-incident correlation for "thirty endpoints failed after the same
patch": tickets referencing the same recent change + same error signature within a
tight window → a *suggested* parent grouping (reviewer-gated), never an automatic
case union.
**Why.** Doc-3's occurrence tier; nothing in A or B covers it. `caused_by_change`
edges exist but deliberately don't group.
**Sketch.** Detector over recent correlations (change ref + B3 signature + time
window); emits a grouping suggestion (suggestion-queue pattern from 0039, its own
type); accept materializes a parent case with child memberships
(`relationship_type='fleet_member'`).
**Dependencies.** B3 (signatures make grouping precise); can ship degraded on
`caused_by_change` + error text alone.
**Acceptance.** The Windows-patch boot-loop scenario yields one suggested parent with
LPT001/LPT121/DTP055 as children; two same-model Wi-Fi failures three months apart
yield nothing.

---

## Epic C — Correlation & episode quality remainders [Doc-1, Gap]

### C1 · Suggestion learning + source-pair thresholds — M — **SHIPPED 2026-08-03**
**What.** Doc-1 P3 items 2 & 6, not shipped in PR #32: per-source-pair similarity
thresholds (ticket↔ticket text is boilerplate-heavy and needs a higher floor than
chat↔chat), and feeding reviewer accept/reject outcomes back into thresholds/corroborator
weighting.
**Sketch.** Start with logged accept-rate per (source-pair, corroborator-type) from
`correlation_suggestions` review outcomes; adjust floors from observed precision once
volume exists. No ML — counting.
**Acceptance.** Accept/reject rates are queryable per pair; a pair with <20% accept
rate gets a raised floor (config, reviewer-visible).

### C2 · Remaining membership relationship types — S/M — **SHIPPED 2026-08-03**
*(`recurrence` shipped with B3; `related`/`follow_on` remain future explicit-reference work.)*
**What.** Doc-1 named `related`, `recurrence`, `follow_on` membership types;
`MEMBERSHIP_RELATIONSHIPS` ships four. `recurrence` lands naturally with B3 (same
issue signature, different occurrence); `related`/`follow_on` from explicit references
("follow-up to INC0010427").
**Dependencies.** B3 for recurrence. **Acceptance.** A new incident matching an old
episode's signature records a `recurrence` membership to the old case (never a merge).

### C3 · Correlation negative signals — M — **SHIPPED 2026-08-03**
*(Conflicting-ticket hard veto shipped; environment/customer signals await per-evidence trait plumbing.)*
**What.** Doc-1 P2 item 4, not shipped in PR #31: explicit penalties — different
production environment, different authoritative CI, conflicting ticket numbers, large
time separation, different customer/account — reducing or vetoing identity-tier
correlation.
**Sketch.** Deterministic checks in `_identity_correlation_signal`'s caller using
entity traits (B2 strengthens this; environment exists on Entity today); conflicting
ticket memberships between the two evidence items = hard veto.
**Acceptance.** Two evidence items sharing a rare device but carrying memberships to
two *different* cases do not get an identity-tier edge.

### C4 · Suggestion queue cap + reviewer console — M [Gap] — **SHIPPED 2026-08-03**
**What.** Per-tenant pending-suggestion cap (backfill storm protection) + minimal
review UI (list/accept/reject exist as API only).
**Acceptance.** A 10k-item backfill cannot create an unbounded pending queue; a
reviewer can work the queue without curl.

### C5 · Attribution-rate evaluation — M/L [Gap] — **SHIPPED 2026-08-03**
**What.** The labeled-data half of P4 item 6: an evaluation dataset with per-step gold
citations; measure unsupported-claim and wrong-source-attribution rates per prompt
version through the existing evaluation harness.
**Acceptance.** `evaluation_runs` can compare episode v2 vs v3 on citation accuracy.

### C6 · Agent projection renders contradictions — S [Gap] — **SHIPPED 2026-08-03**
**What.** `episodes.contradictions` reaches reviewers but not the MAF agent surface.
Render a bounded contradictions block in episode facts (budget-aware).
**Acceptance.** An agent consuming the Acme VPN episode sees that close notes and the
Teams thread disagreed on the fix.

### C7 · Historical pattern cleanup — S [Gap] — **SHIPPED 2026-08-03**
**What.** Pre-domain-guard patterns may contain cross-domain members (PR #17 caveat).
One-off audited cleanup task: recompute memberships, flag violations for review.

---

## Epic D — Connectors & platform boundaries [Gap]

### D1 · Jira platform-boundary features — L (each M standalone) — **PARTIAL 2026-08-03: customfield mapping + page-order guard shipped; Opsgenie/Assets/Confluence skipped (external access, see KNOWN_GAPS)**
Opsgenie alerts connector (alert rollups at parity with em_alert), Assets topology
(Premium API — config-gated), Confluence KB ingestion, request-type/change-window
customfield mapping via `source_config`, sync page-order guard.

### D2 · AutomationEdge connector — L — **SKIPPED 2026-08-03 (no AutomationEdge access, see KNOWN_GAPS)**
The long-standing backlog item: workflow/request execution events as evidence,
entity population for workflow entities, `remediated_by` references. Doc-3 makes it
more valuable: AutomationEdge is the authoritative source for
workflow/request status (P4 authority table) and a trait source for B2.

### D3 · HTTP CmdbTopologyClient — S — **SHIPPED 2026-08-03**
The in-process client shipped with PR #11; the HTTPS deployment-neutral twin (same
contract, token hygiene like the MAF client) is still open.

### D4 · SapphireIMS instance verification tooling — S — **SHIPPED 2026-08-03**
Config-mapped contract ships with verify-per-instance defaults; add a
`validate_credentials`-style probe report listing which configured fields/endpoints
responded, so operators verify mapping without reading logs.

---

## Epic E — Core platform hardening [Gap, standing]

| ID | Item | Size | Notes |
|---|---|---|---|
| E1 | LLM provider resilience: per-call timeout, circuit breaker, fallback — **SHIPPED 2026-08-03** | M | budget gates/retries/validation exist; `ai/provider.py` |
| E2 | Prompt-injection fencing at ingest extractors — **SHIPPED 2026-08-03** | M | MAF provider fences; episode/decision/identity extractors concatenate raw evidence |
| E3 | Ranking calibration + SLA priors — **PARTIAL 2026-08-03** (quality signal, abstention, version batching; SLA priors + per-candidate query batching open) | L | `quality_score=0.5` placeholder, no abstention threshold, N+1 playbook queries; SLA priors deferred from change-risk work |
| E4 | Sync single-flight — **SHIPPED 2026-08-03** | M | advisory lock per source object; overlapping backfills currently race (dedup is DB-safe since 0026) |
| E5 | Reviewer/admin consoles — **PARTIAL 2026-08-03** (suggestion + fleet + identity review queues live on /suggestions; admin-console CRUD remains) | L | identity `needs_review` queue, suggestion queue (C4), episode membership editing — all API-led today |
| E6 | Execution engine depth — **SAFETY SLICE 2026-08-03** (stale approval expiration on the verification beat; expiry never approves). Tool registry / rollback execution / cancellation / resume remain Release-2 scope per the plan | L | Release 2: tool registry, rollback execution, timeouts, resume; verification shipped (0036) |
| E7 | Prompt-family doubled-brace fixes — **SHIPPED 2026-08-03** | S | `decision`/`pattern`/`playbook` v1 system prompts still carry literal `{{ }}`; ship v2s (episode + identity already fixed) |

---

## Epic F — Truthful governance & the autonomy prerequisites [Doc-4]

Source: [REVIEW-2026-08-V6-SCHEMA.md](REVIEW-2026-08-V6-SCHEMA.md), which validated an
external v6-schema comparison against the code at `76c4e82`. Two findings shape this
epic:

1. **Migration `0029` provisioned 18 governance columns that no service writes** —
   six on `execution_step_runs` (`action_name`, `action_type`, `execution_mode`,
   `executed_by`, `idempotency_key`, `duplicate_check_status`), eight on
   `approval_requests` (`action_name`, `approver_role`, `approval_channel`,
   `approval_note`, `recommended_by`, `executed_by`, `sod_check_status`,
   `sod_violation_reason`), four on `decisions` (`decision_intent`,
   `decision_summary`, `risk_level`, `policy_result`). This was deliberate — `codewiki/17` lists
   service-code population as out of scope for `0029` — but the columns now read as
   shipped capability to anyone auditing the schema, and the partial unique index
   `uq_execution_step_runs_idempotency_key` guards a column that is always NULL.
2. **There is no executor and no write-capable agent tool.** All six MAF tools are
   read-or-propose; `execution_service` is a ledger driven by external callers. So the
   autonomy-safety items below are prerequisites for a capability that does not exist
   yet, not live exposure — which is what makes M7-before-M8 affordable.

**Hard gate.** No tool that performs a side effect on a customer system merges until
F6, F7 and F8 are in. "Just one low-risk action first" is the failure mode this gate
exists to prevent.

### F1 · Populate or retire the `0029` stub columns — M — **SHIPPED 2026-08-15**
**Shipped.** The audit found **79** unwritten columns, not the 18 the Doc-4 comparison
named. Eight are now written: `ExecutionStepRun.action_name` / `action_type` (declared
by the step or NULL — never inferred from the title) / `execution_mode` / `executed_by`,
`ApprovalRequest.action_name` / `approver_role` (the roles the policy actually
requires, or NULL when none is configured), `Decision.decision_intent` (derived from
`decision_type` through `INTENT_BY_DECISION_TYPE`, explicit argument wins) and
`Decision.risk_level` (from the **selected** option — the path taken, not the riskiest
one considered). `APPROVAL_STATUSES` gained `expired`, which the expiry sweep has been
writing since E6. The other 71 are in the register with an owner each. Two findings
the audit surfaced, both recorded in KNOWN_GAPS: `FixPattern` has no constructor
anywhere (so B4/B5/verification write-back are dormant, not merely unexercised), and
`claim_evidence` / `decision_claims` have none either (claims never reach a
validation status). 1551 tests.

**What.** Decide every unwired column: write it at its natural point, or drop it.
Minimum population set — `ExecutionStepRun.action_name` / `action_type` /
`execution_mode` / `executed_by` (from the step payload and run mode in
`start_execution`), `ApprovalRequest.action_name` / `approver_role` / `recommended_by`
(from the requesting decision), `Decision.decision_intent` / `policy_result` /
`risk_level`. Add a guard test that fails when a model column has no writer and no
allowlist entry naming its owning backlog item.
**Why.** An unwritten column is worse than a missing one: it makes every reviewer —
human or model — score capability that does not exist. That is exactly how the Doc-4
comparison reached 88%. It is also the prerequisite for F3 (the verdict must be
written before it can be versioned and snapshotted).
**Sketch.** Extend `tests/test_review_orm_ddl_drift.py`, or a sibling, with a
writer-coverage assertion over `models/`; the allowlist entry format is
`(model, column, owner_item, reason)`.
**Dependencies.** None. **Acceptance.** The guard test is red before the change and
green after; every remaining unwired column appears in the allowlist with an owner;
`decision_intent` and `policy_result` are populated on every gated decision.

### F2 · Relationship type registry — S — **SHIPPED 2026-08-15**
**Shipped.** `graph/edge_types.py`: 69 types in five semantic groups, enforced by
`require_registered` in `add_edge` / `ensure_edge` / `close_edge` / `replace_edge`
(closing too — a typo there closes nothing and reports success). 18 types are
written but deliberately not projected, each with its argument recorded rather than
implied. `tests/test_edge_type_registry.py` checks all three drift directions and
guards its own AST scan against matching nothing. The runtime check earned itself on
the first full-suite run: `involved_in`, a literal inside a tuple in
`persist_pattern_enrichment_edges`, was invisible to the static scan. 1558 tests.

**What.** A canonical `EDGE_TYPES` registry validated inside `graph/builder.add_edge`
and `ensure_edge`. A test asserts both directions: every type written anywhere is
registered, and every registered type is either present in `MAF_RELATIONSHIP_TYPES` or
carries an explicit exclusion reason.
**Why.** `edge_type` is free text written from 26 modules; the maf.v1 allowlist
governs *reads* only, so a typo at a write site is invisible until the edge silently
fails to project. The central helpers already exist, so this is small.
**Sketch.** The `mentions_identity` fan-out note in `graph/agent/profiles.py` is the
model for an exclusion reason string — the reason is data, not a comment.
**Dependencies.** None. **Acceptance.** An unregistered `edge_type` raises at the
builder; write registry and projection allowlist cannot silently diverge.

### F3 · Policy versioning + a real `PolicyCheck` record — M — **SHIPPED 2026-08-16**
**Re-scoped on contact.** The item said "version `action_policies`" — but that table has
no writer, no CRUD API and no evaluation engine, so versioning it would have added three
more never-written columns to the register F1 exists to keep honest. Versioning the
engine that does not run, while the one that does stayed unversioned, is the exact
failure this epic is about. So F3 versions and records **the policy the executor actually
enforces**, and the action-policy engine becomes **F3b** below.

**Shipped.** Migration `0056`: `version` / `effective_from` / `effective_to` on
`tenant_policies`, plus a `policy_checks` table (policy id + **version** + check name +
evaluated entity + result + reason + input snapshot + evaluator + timestamp). Recorded at
both real enforcement points in `execution_service` — the automation-mode cap at
`start_execution` (anchored to the playbook, because the run row does not exist yet at
gate time) and the decider gate at `decide_approval`. Three properties are deliberate:
the rule functions stay pure and synchronous, so recording cannot slow or break the gate;
**denials record before raising**, which is the evaluation an implementation that records
only the success path loses; and a broken audit write is logged and swallowed, because by
then the gate has already decided and additive evidence must never turn an allowed action
into a failed one. The version tracks the **rules**, not the labels — renaming or
deactivating a policy does not bump it, changing its config does. 1579 tests.

### F3b · Action-policy engine, CRUD and versioning — L — **SHIPPED 2026-08-16**
**The deferral no longer held.** This item was scheduled "with the executor, not before
it — a policy engine with nothing to gate is the same mistake in a new place". **F1
changed that** by populating `ExecutionStepRun.action_name`: the lookup key this table
is designed around now exists on every step, so the engine gates something real.

**Shipped.** `services/action_policy_service.py` decides in three steps — **scope
filter** (a NULL axis means "any"), then **specificity** (a rule that pins down more
axes wins; precedence that ignored that would make narrow rules pointless to write),
then **conflict resolution**, and only for a genuine tie. The default is
`most_restrictive` deliberately: when two equally specific rules disagree about whether
something may run unattended, the safe reading is the one that asks a human. Rules that
disagree about the *strategy* resolve most-restrictively too, and a full tie breaks by
policy name because row order is not a decision anyone made. An unknown verdict ranks
**most** restrictive — a typo must never read as `allowed_auto`.

Migration `0064` adds the versioning F3 gave `tenant_policies`, on the same terms: rules
bump it, labels do not. `api/v1/action-policies` ships with it, because a policy table
nobody can author is a vocabulary rather than a control.

Wired into `start_execution` per step: `approval_required` forces a gate, the blocking
verdicts refuse the run, and **`allowed_auto` grants nothing** — it means "this policy
does not object", and safety class, role and trust have already had their say. A policy
that could overturn them would be a way to grant privilege by writing a row.
`Decision.policy_result` now carries the run's strictest step verdict, closing another
F1 register entry. 1710 tests.

**Side effect worth knowing:** the CRUD surface writes column *names* that other models
share, so the F1 register's name-based scan can no longer see six genuinely-unwritten
columns elsewhere. They moved to a documented `SHADOWED_BY_NAME` map with a test that
they never drift back — a name collision should cost visibility, not knowledge.

**What.** `action_policies` is read only by the agent projection; `execution_service`
never queries it, there is no CRUD API, and nothing writes it — all 12 of its columns sit
in the F1 register. This item builds the evaluator (precedence, scope, conflict
resolution — the `priority` / `policy_scope` / `conflict_resolution` columns `0029`
provisioned), the CRUD surface, and its own versioning, recording through the same
`policy_checks` table F3 shipped.
**Why.** Until it exists, `Decision.policy_result` has no verdict to record and
`allowed_auto` is a vocabulary rather than a behaviour.
**Dependencies.** F3. Schedule with the executor (M8), not before it — a policy engine
with nothing to gate is the same mistake in a new place.

**Original scope, for the record.** `version` / `effective_from` / `effective_to` on
`action_policies`; a policy-check row per evaluation carrying policy id + version +
evaluated artifact ref + result + input snapshot + evaluator + timestamp.
**Why.** `DecisionActionPolicy.policy_result_snapshot` records a result with no policy
version, so "which policy version evaluated this, and what did it see?" is currently
unanswerable — the audit question every governed execution has to answer.
**Dependencies.** F1. **Acceptance.** For any gated decision, the evaluating policy
version and its input snapshot are recoverable by query; editing a policy does not
rewrite the history of decisions it already governed.

### F4 · Knowledge freshness + supersession in retrieval — M — **SHIPPED 2026-08-16 (support half)**
**Shipped.** Migration `0057`: `evidence_items.knowledge_support JSONB`, computed by
`knowledge_validation_service` and refreshed by the event that changes it — a
verification verdict, bounded to the knowledge cited by that playbook version rather
than a sweep over the corpus. `knowledge_retrieval_service` now multiplies cosine
distance by a support factor (proven 0.80, emerging 0.92, unproven 1.00, contested 1.25)
alongside the existing applicability penalty, and a contested article carries a SUPPORT
WARNING into the prompt so the generator can say a procedure is disputed rather than
quote it as settled. Two principles are pinned by tests: support **re-ranks and never
filters** (a procedure with a failure history is often the only guidance that exists),
and **silence is not failure** — `unproven` and never-computed are both exactly neutral,
because treating "no runs" as negative would demote the whole corpus on day one. 1588 tests.

**Deliberately not shipped here, with reasons:**
- **Age-based freshness.** A document's age is not evidence it is wrong, and inventing an
  age penalty would be precisely the unmeasured number this codebase keeps having to
  remove. Supersession is the real staleness signal.
- **Supersession edges (→ F4b).** Turning `services/documents/versioning.py`'s filename
  heuristic into `superseded_by` edges needs a proposal table and a reviewer surface —
  a filename is not grounds for retiring an SOP, so it follows the
  `IdentityMergeProposal` pattern, and that is its own item.

### F4b · Reviewer-gated knowledge supersession — M — **SHIPPED 2026-08-16**
**Shipped.** Migration `0065`: `knowledge_supersession_proposals`, plus
`services/knowledge_supersession_service.py`. The heuristic proposes; a human decides.
Confidence is tiered by what the filenames actually show — an explicit bump on both
sides (`v1`→`v2`) 0.9, a version appearing against an unversioned original 0.7, revision
words (`draft`→`final`) 0.55 — and anything pointing the other way or nowhere returns
**nothing**: proposing a reversed pair is worse than proposing nothing, because accepting
it demotes the current document in favour of the old one. Rejection is durable, checked
per pair rather than per run, so a scheduled pass never re-raises a declined pair; the
`signals` blob travels with the proposal, because a reviewer who cannot see WHY two
documents were paired will either rubber-stamp it or ignore it. Acceptance writes a
`superseded_by` edge — already in the F2 registry, already projected — and retrieval reads
the **edge, not a column**, so a supersession later closed stops demoting its predecessor
without anyone remembering to undo a flag. Retrieval **demotes rather than drops**
(`SUPERSEDED_RANK_FACTOR = 1.6`, above `contested`'s 1.25, because "a human said this was
replaced" is a stronger statement about an article than "its run record is mixed") and
labels the block: when the successor does not match the query, the predecessor is still
the only guidance that exists, and hiding it leaves the reviewer with nothing and no sign
anything was withheld. Ships with `api/v1/knowledge-supersessions` — list, on-demand
scan, decide — because a proposal table with nowhere to review it is the same gap in new
clothes: findings accumulate, nobody sees them, retrieval keeps serving the replaced
article. `knowledge_manager` throughout: retiring an SOP is a knowledge decision, not an
administrative one. 1737 tests.

**Side finding, fixed here (`0066`):** `TenantScopedMixin` carries `TimestampMixin`, so
every model using it declares `created_at` **and** `updated_at` — and the hand-written
`create_table` calls in `0062` (trust profiles) and `0063` (rollback plans, escalations)
listed the columns their authors typed. Three columns the ORM declares did not exist in a
migrated database, which is an `UndefinedColumn` on **every** SELECT of those models. The
suite could not see it: it runs without a live Postgres, and SQLAlchemy will happily
describe a column the database does not have. `0034` and `0049` exist for exactly this
bug, so this was the third occurrence — `tests/test_orm_migration_column_parity.py` now
reads the migration chain as text and fails on the next one.

**Deliberately not shipped here, with reasons:**
- **A scheduled proposal pass.** The scan is on demand. Filling a queue on a beat before
  anyone has reviewed a single proposal is how a review surface becomes noise; the
  schedule should follow evidence that the proposals are worth reading.
- **A UI.** The API is the contract the console will use; the queue's shape should follow
  the first real batch of proposals rather than predict it.

**What.** Persist `services/documents/versioning.py`'s duplicate/version findings as
supersession *proposals* (the `IdentityMergeProposal` pattern: stored, reviewer-decided,
rejection durable), and on acceptance write `superseded_by` edges between knowledge
evidence rows. Retrieval then demotes a superseded article in favour of its successor.
**Why.** The versioning module's own docstring names the gap it does not close:
retrieval "returns superseded guidance and nothing marks it as superseded". `superseded_by`
edges exist today only for claims.
**Dependencies.** F4. **Acceptance.** An accepted proposal makes the successor outrank
its predecessor for the same query; a rejected one never re-raises.

**Original scope.** Persist the `knowledge_validation_service` support level per knowledge
evidence item; add support level and evidence recency as ranking terms in
`knowledge_retrieval_service`; turn the `services/documents/versioning.py` heuristic
into reviewer-gated `superseded_by` edges between knowledge evidence rows.
**Why.** Validation already computes whether a procedure has ever worked, and
retrieval ignores it — so a superseded SOP still ranks on similarity alone. The
versioning module's own docstring names this gap.
**Sketch.** Supersession proposals follow the `IdentityMergeProposal` pattern:
persisted, reviewer-decided, rejection durable. Never auto-applied — a filename
heuristic is not grounds for retiring an SOP.
**Dependencies.** None (F5 makes the ranking change measurable).
**Acceptance.** An article with `failing` support, or one with an accepted successor,
ranks below its replacement for the same query; proposals surface in the review queue;
nothing is applied automatically.

### F5 · Generation provenance on derived entities — S/M — **SHIPPED 2026-08-15**
**Shipped.** Migration `0055`: `generation_provenance JSONB` on `episodes`, `patterns`,
`playbook_versions` — the three artifacts a prompt actually generates. Deliberately not
on `decisions` / `claims`, which services and humans write: a column NULL by
construction is the problem this epic exists to stop. Stamped by
`ai/provenance.generation_provenance` **after** each schema gate, so a model-supplied
key is overwritten rather than trusted. The field is `model_requested`, not `model` —
E1's breaker can substitute the fallback mid-call and only `llm.usage` sees it, so
`correlation_id` rides along as the join key to the event that knows the serving model.
`llm_complete` / `llm_complete_json` gained optional `subject_type` / `subject_id`,
which anchor the `llm.usage` event to the row a call is *about* (wired first on the
message-function classifier); the cost dashboard filters on `event_type`, so the anchor
change is invisible to it. One wiring bug found in review: the pattern worker assembles
`version_data` field by field, so the generator's stamp had to be forwarded explicitly —
both ends are now pinned by tests. 1569 tests.

**What.** Put the target entity type + id on `llm.usage` operational events, and record
`prompt_name` / `prompt_version` / model on the derived row (episodes, decisions,
claims) — a `generation_provenance` JSONB column is enough.
**Why.** Prompt and model versions reach the cost/observability plane but never the
artifact they produced, so "which prompt version wrote this episode" needs a
correlation-id join and only works when a `db` session was in scope. This is also the
measurement substrate the measure-first discipline assumes.
**Dependencies.** None. **Acceptance.** Prompt version and model for any episode,
decision or claim are one query; the answer survives a worker that had no session.

### F6 · Skill registry + `ExecutionContract` — L — **SHIPPED 2026-08-16**
**Shipped.** Migration `0058`: `skills` + `execution_contracts`. Two tables, because one
operational envelope governs many skills and because the contract is what F8's attempt
model reads while the skill is what the planner reads. Side-effect classification reuses
`SAFETY_CLASSES` rather than minting v6's parallel vocabulary.

Three registration invariants, enforced at the earliest point they can be — before a
planner can select the skill, before an approver can approve it, before an executor
exists to run it:
- a `low_side_effect`+ skill **needs a contract** (without one it has no timeout, no
  retry policy and no statement about replay, and the executor would invent all three at
  call time);
- a `high_side_effect` / `destructive` skill **may not register as `NOT_IDEMPOTENT`** —
  v6 invariant 8 made enforceable. The tool is not blocked from the system, only from
  being registered as if replay were safe;
- **`max_attempts > 1` requires a replay guarantee**, because retrying a call without one
  is how an action happens twice.

`tool_ref` is now a reference (`skill_key` → active version, or `skill_key@version`
pinned), resolved by `validate_step_bindings` at `create_playbook_version`. Steps naming
no tool are untouched — almost every step today — and the stronger "a step the executor
will run must be bound" rule belongs with the executor. Shadow mode gained the contract's
real safety content: a run refuses steps whose bound skill declares
`supports_dry_run=False`, because short-circuiting such a call into a recorded shadow
outcome would assert a rehearsal the tool could not perform. Both existing guards fired
and were answered — the F1 register caught that `ExecutionContract` had no creation path,
so `register_execution_contract` was added rather than the columns claimed. 1605 tests.

**Deliberately deferred:** deleting the shadow special case in `start_execution` in
favour of a contract-driven dry-run path. Today that would be a risky refactor with no
behavioural gain (there is no executor); the safety property it was meant to carry ships
here as the refusal above. Revisit with F8.

**What.** Promote `PlaybookStep.tool_ref` from free string to a registry reference.
`Skill`: id, name, version, action type, interface type (API / MCP / RPA / CLI /
SCRIPT / WORKFLOW / MANUAL), input + output JSON Schema, reversible, rollback skill
ref, risk level, allowed principals, status. `ExecutionContract`: idempotency mode
(NATIVE / CALLER_KEY / DEDUPE_ONLY / NOT_IDEMPOTENT), dedup window, timeout, retry
policy, max attempts, backoff, cancellation support, dry-run support, side-effect
classification, concurrency policy, rate limit, credential scope.
**Why.** E6's "tool registry" line, now with the contract the execution semantics
need. Without it there is nothing to hash (F7), nothing to retry against (F8), and no
declared side-effect class for policy to reason over.
**Sketch.** Reuse the existing `SAFETY_CLASSES` tuple as the side-effect
classification rather than minting a parallel vocabulary; express `shadow` mode as the
contract's dry-run path instead of the special case currently in `start_execution`.
**Dependencies.** F1. **Acceptance.** An executable step cannot publish without
resolving to a registered skill; a skill with side effects cannot register without a
contract; the shadow-mode special case is deleted, not duplicated.

### F7 · Immutable approval binding — M — **SHIPPED 2026-08-16**
**Shipped.** Migration `0059`: `artifact_version` / `artifact_hash` / `policy_snapshot` /
`expires_at` on `approval_requests`, plus a `BEFORE UPDATE` trigger making a **published**
version's `steps` immutable — a hash of a mutable row only proves it has not changed
since someone last looked, so the payload must not be able to drift underneath the
binding at all. `services/artifact_binding_service.py` hashes the step *in its version*
(two playbooks can hold identical steps; hashing the step alone would let an approval for
one satisfy execution of the other). Written at `request_approval`, re-checked in
`record_tool_invocation` — the last moment before a tool runs — and a violation is
recorded as an `approval.binding_violated` event, not merely raised. `expires_at` is 4h,
the incident working span: distinct from the 72h that expires an *unanswered* request,
because that one is about nobody answering and this one is about the answer going stale.

**RFC 8785 rather than `json.dumps(sort_keys=True)`**, via the `rfc8785` dependency
(pure Python, no transitive deps). Key order, whitespace and number formatting change
the bytes without changing the meaning, so a naive hash produces false mismatches on
re-serialization — and a check that cries wolf on every legitimate execution gets
disabled. ECMAScript number serialization and UTF-16 key ordering are exactly the parts
a hand-rolled canonicalizer gets wrong.

Approvals predating F7 carry no hash and are allowed through, logged: retro-blocking
approvals granted before the mechanism existed would break running deployments to
enforce a rule they had no way to satisfy, and they age out on their own. The hash is a
self-consistency check, **not a signature** — it proves the payload did not change, not
who produced it. 1622 tests.

**What.** Canonicalize the resolved step payload with RFC 8785 (JSON Canonicalization
Scheme), hash it, and store `artifact_version` + `artifact_hash` + `policy_snapshot` +
`expires_at` on the approval. Re-hash and compare immediately before execution; refuse
on mismatch or expiry with a distinct error. Add the constraint that makes a published
`PlaybookVersion.steps` payload immutable, and add `expired` to `APPROVAL_STATUSES`
(`approval_expiry_service` already writes it).
**Why.** Today nothing binds an approval to the exact thing that executes —
`PlaybookVersion.steps` is mutable JSONB with no content hash, so "which exact artifact
did the human approve?" cannot be answered. RFC 8785 rather than `json.dumps` because
key order, whitespace and number formatting change bytes without changing meaning.
**Dependencies.** F6. **Acceptance.** Mutating an approved step payload by one
character blocks execution; an expired approval blocks execution; both emit operational
events; expiry still never approves.

### F8 · `ExecutionAttempt` + live idempotency — M/L — **SHIPPED 2026-08-16**
**Shipped.** Migration `0060`: `execution_attempts`, one row per try, with
`attempt_number` derived from what is already recorded so a caller cannot renumber
history and a retry lands as N+1 without knowing N. `deduplicated` is the status that
matters — durable evidence a replay arrived and was recognised, which is the difference
between an idempotency control that works and one nobody can prove worked. `timeout` and
`cancelled` are distinct from `failed`: a timeout is an unknown outcome, and conflating
them tells retry logic the wrong thing.

The key (`services/idempotency_service.py`) derives from F7's artifact hash scoped to the
case — same case, same step payload, same action — so a re-run is a *retry of the same
logical operation*, which is what a key is for. Hashed rather than concatenated because
the unique index is global and a readable key would put tenant ids in a structure other
tenants' rows share. **Only side-effecting steps get one**: suppressing a repeated
diagnostic would be a bug wearing a safety control's clothes, and a skill whose contract
declares `NATIVE` idempotency gets none either because the tool is already safe to
replay. An unbound side-effecting step *does* get one — without a contract we cannot know
the tool is safe, and the conservative answer suppresses.

A recognised duplicate is skipped, recorded as a `deduplicated` attempt plus an
`execution.step_deduplicated` event, and refused again at `record_tool_invocation` so the
suppression holds at the call site rather than only at planning time. 1639 tests.

**With F6 and F7, the M8 hard gate is satisfied**: a side-effecting tool now has a
registry entry with a contract, an approval bound to the exact artifact, and a live
duplicate guard.

**What.** An attempts table (attempt number, skill + version, idempotency key, dedup
key, input hash, worker ref, started/completed, status including `DEDUPLICATED`,
`TIMEOUT`, `CANCELLED`). Generate and enforce the idempotency key so
`uq_execution_step_runs_idempotency_key` stops being decorative; set
`duplicate_check_status` on every attempt.
**Why.** Retries are first-class in any real executor, and at-least-once delivery
without a live duplicate guard is how a remediation runs twice. E6's cancellation and
resume hang off the same table.
**Dependencies.** F6, F7. **Acceptance.** Replaying an execution request with the same
key produces a `DEDUPLICATED` attempt and zero new side effects; a timed-out attempt
records as attempt N and retries as N+1 under the contract's backoff.

### F9 · Generalized verification criteria — L — **SHIPPED 2026-08-16**
**Shipped.** Migration `0061`: `verification_assessments` + `verification_observations`.
Each criterion is evaluated and recorded separately — type, human-facing name, the
parameters as evaluated, status, observed value, window — and the assessment aggregates
them and carries the rollback / retry / escalation flags a single word could not.
`user_confirmation` joins the two absence checks as the first **positive** signal, read
from the `message_function` the A1 classifier already writes at ingest: no new
extraction, no model call on the verification path.

**The behaviour change that is the point of the item:** absence now passes only when the
CI has actually produced an incident or alert in the last 30 days. Otherwise the
criterion is `not_observable` and the verdict is `inconclusive` — the case the old sweep
called `verified` and fed to the cohort counters as success.

**The calibration that keeps it useful:** `not_observable` ("could not apply") and
`inconclusive` ("applied, could not decide") are distinct. Only the latter holds a
verdict at `monitor_required`; the former does not hold back a success the other criteria
earned. Without that split, every telemetry-verified run with a quiet chat thread would
have been demoted, throwing away the signal 0036 shipped.

`execution_runs.verification_status` keeps its three words and is derived from the
assessment. `partial_success` and `monitor_required` both map to a **non-verified**
status: counting a half-fix or an unconfirmed quiet period as verified success is what
F9 exists to stop, and only `success` maps to `verified` (pinned by a test).

**Criteria are deliberately not a table** — they are declared in
`PlaybookVersion.verification_policy` plus the defaults, and each observation records
what it evaluated. A `verification_criteria` table with no authoring surface would be
another set of columns nothing writes. **Deviation from v6, recorded:**
`ESCALATE_TO_HUMAN` is a flag rather than a seventh result, because a verdict and a
routing decision are different things. 1654 tests.

**What.** `VerificationCriterion` / `VerificationObservation` / `VerificationAssessment`.
Keep today's two signals as criterion types (`incident_absence`, `alert_absence`) and
add at least one positive-signal type — ticket state or user confirmation, both already
available from the connectors. Assessment states: SUCCESS / PARTIAL_SUCCESS / FAILED /
INCONCLUSIVE / ROLLBACK_REQUIRED / MONITOR_REQUIRED / ESCALATE_TO_HUMAN.
**Why.** The current sweep infers success from silence, so a CI that stopped emitting
telemetry reads as `verified`. That is a false positive pointed straight at the
learning loop.
**Sketch.** `execution_verification_service` keeps its deterministic, no-LLM posture;
criteria come from `PlaybookVersion.verification_policy`, which already exists and is
already read.
**Dependencies.** None (independent of F6–F8), but must land **before** F10.
**Acceptance.** A run against a CI with no telemetry returns `INCONCLUSIVE`, not
`verified`; every verdict lists the criteria that produced it.

### F10 · Scoped `TrustProfile` — L — **SHIPPED 2026-08-16**
**Shipped.** Migration `0062`: one row per (agent × action type × resource class ×
environment × business criticality), the composite unique key. Unknown dimensions store
`'unspecified'` rather than NULL — NULLs in a unique key would let two "unknown
environment" profiles coexist and split the record in half.

Three properties are load-bearing:
- **The lower bound, not the rate.** `confidence_lower_bound` is a Wilson score
  interval, so 3/3 (rate 1.0, means almost nothing) scores below 340/350 (rate 0.97,
  means a great deal). No separate minimum-sample rule exists for someone to tune away.
- **Recent failure beats the long-run average.** `consecutive_failures` suspends a scope
  regardless of history, and the suspension check runs *before* the average so a good
  record cannot rescue a bad streak. No deploy needed.
- **Trust vetoes; it never grants.** A `suspended` scope blocks `start_execution` and
  records a `trust_scope` policy check; `autonomous` merely stops trust being the reason
  to block — policy still decides, per v6 §25. `advisory` deliberately does not block,
  because treating "unproven" as "forbidden" stops every new action from ever earning a
  record, which is how trust systems get switched off.

Outcomes come from F9's assessment — only `success` counts, `partial_success` is a
failure, `inconclusive` is neither and drags the bound down. **This is why F9 shipped
first**: fed by the old silence-equals-success verifier every number here would have been
inflated in exactly the direction that matters. Structurally the same machine as B5's
cohort counters, applied to (agent, action) instead of (fix, CI class).

**Acceptance met** — the v6 §25 worked example resolves from the same code: a 372-sample
service restart on a non-critical CI reaches `autonomous` while the same agent's
3-sample Oracle failover on a payment service does not, on the strength of the evidence
alone. 1673 tests.

**Side finding:** the F1 writer register reported every trust counter as unwritten,
because its detector did not treat `+=` as a write. Fixed in the detector — the counters
are only ever incremented, and a guard that cannot see that would have mis-registered
every counter added from here on.

**What.** Trust scoped to agent × action type × resource class × environment ×
business criticality × tenant. Metrics: sample size, success rate, verification pass
rate, rollback rate, human override rate, reopen rate, recent failure rate, and a
Wilson score lower bound. Autonomy verdict ADVISORY / SUPERVISED / AUTONOMOUS /
SUSPENDED, consumed by the control decision alongside policy.
**Why.** Autonomy today is a global mode on the playbook. The question that should
gate an autonomous action is "has *this agent* done *this action* on *this class of
thing* in *this environment*, and did it hold?" — and a raw ratio answers it wrong: 3/3
must not outrank 340/350.
**Sketch.** Structurally the same machine as B5 (cohort counters + reviewer-gated
promotion ladder in `services/fix_cohort_service.py`), applied to (agent, action)
instead of (fix, CI class). Build it as a sibling, not a new subsystem.
**Dependencies.** F9 (trust computed from a silence-equals-success verifier is
systematically inflated). **Acceptance.** A high-sample service restart on a
non-critical Windows host reaches AUTONOMOUS while a 3-sample Oracle failover on a
payment service stays SUPERVISED; a recent failure streak demotes without a deploy.

### F11 · Rollback + escalation objects — M — **SHIPPED 2026-08-16 · M8 COMPLETE**
**Shipped.** Migration `0063`: `rollback_plans`, `escalations`, and
`execution_runs.rolls_back_run_id`.

**Only the plan is new, deliberately.** v6 models RollbackPlan / RollbackAction /
RollbackExecution as three classes; running an undo needs steps, approvals, attempts, an
artifact binding and a verification — all of which `ExecutionRun` has after F6–F9. A
parallel execution hierarchy would duplicate every one and then drift, so
`rolls_back_run_id` is the whole difference, and a rollback is verified like anything
else rather than trusted because it was called a rollback.

The plan is derived when F9's verdict sets `rollback_recommended`: one action per
completed step that can be undone, **in reverse order** (the order is the plan), from the
bound skill's registered rollback skill or the step's free-text hint — weaker, but it is
what a responder needs at 3am. Irreversible steps are **named, not omitted**, and a plan
with no actions is stored as `infeasible`: "we cannot undo this" is the most important
thing to learn early, and a missing row reads as "nobody checked".

An escalation carries **refs, never copies** — assessment, run, playbook version,
per-criterion outcomes, rollback plan — because a copy is a second version of the truth
that ages away from the first. `acknowledgement_latency_min` is stored on
acknowledgement so the number survives an edit of either timestamp.

Also closed here: `verification.monitoring_window_sec` is now written by a
`monitor_required` verdict (4h — long enough for a slow recurrence, short enough that an
operator still associates the alert with the change). 1687 tests.

**Still claimed in the F1 register, with owners:** `rolls_back_run_id` (executor —
nothing executes a plan yet), `escalations.resolved_at` / `resolution_note`
(reviewer-console — raising and acknowledging are wired, closing is not), `trust.reopens`
(case-lifecycle — a reopen is a second `CaseOutcome`, which the verification sweep never
sees).

**What.** `RollbackPlan` / `RollbackAction` / `RollbackExecution` linked to the forward
execution and verified like any other execution; `Escalation` with reason,
escalated-by/to, priority, decision-trace ref, evidence-bundle ref, recommended next
actions, acknowledged/resolved timestamps.
**Why.** Rollback is free text today (`rollback_notes`, `rollback_hint`) and
`reversible` is a flag nothing consumes; escalation exists only as a decision type and
a case status, so a human receives a notification rather than the evidence bundle and
the alternatives that were rejected.
**Dependencies.** F6 (rollback skill ref), F9 (rollback is a verification outcome).
**Acceptance.** A failed verification can produce a rollback execution with its own
verification result; an escalation hands a human the evidence bundle and rejected
alternatives.

### F12 · Step-level execution endpoints — S — **SHIPPED 2026-08-16**
**Shipped.** `POST /api/v1/execution/runs/{run_id}/steps/{step_run_id}/invocations` and
`.../complete`. Found while scoping the SupportFlo (Bajaj) integration:
`record_tool_invocation` and `record_step_completion` had **no caller anywhere in the
codebase and no route**, which meant F7's artifact re-check, F8's duplicate refusal and
the whole attempt ledger were unreachable by any external executor. Built, correct, wired
to nothing — the shape F1 exists to stop, one layer up.

Same gate as abort and complete (initiator or `domain_admin`), and the step must belong to
the run in the URL — without that, any step in the tenant can be driven through any run's
endpoint and the run id in the audit trail stops meaning anything. Service refusals surface
as **409, not 500**: a duplicate replay and a stale approval binding are well-formed
requests that the *state* declines, and a caller needs to tell that apart from a bug. The
request body carries **no attempt number and no idempotency key** — both are derived from
what is already recorded, and a caller that can renumber history or hand in the key the
duplicate check tests against defeats the control by asserting the answer.

**Two defects found while building it, fixed here:**
- An invocation could declare a **higher safety class than its own step**. The step's class
  is what policy, the approval gate and the caller's `max_safety_class` were all evaluated
  against, so a destructive call recorded under a read-only step would leave every upstream
  control reading as satisfied. Refused in the *service*, so any future caller inherits it.
- `ExecutionStepRunResponse` embeds `tool_invocations`, and the completion path returned a
  step whose relationship was never loaded — `MissingGreenlet` from inside the serializer,
  invisible to a suite that runs without Postgres. The route now re-reads through
  `get_step_run`, which eager-loads; a test pins the loader.

1752 tests.

**Deliberately not shipped:** an executor. This makes the ledger drivable; it does not
drive it. Nothing schedules or resumes a run either, so a caller that stops calling leaves
one open — acceptable while the callers are external and few, not acceptable once anything
unattended uses it.

### Deferred tail (recorded, not scheduled)

| Item | Why deferred |
|---|---|
| Structured `Assertion` alongside `Claim` (subject/predicate/object, validity window, source ref) | Real v6 gap, but pure modelling gain until something queries it. Revisit when the agent needs "what was asserted about X, by whom, valid when". |
| Canonical `ResolutionObservation` | The facts already exist across `case_outcomes`, `case_outcome_fix_patterns`, `fix_cohort_stats` and `execution_runs.verification_status`. Ship as a read-model/API first; a table only if the read-model proves insufficient. |
| System-time (bitemporal) history | PG16 has no native system versioning; this means an append-only history table or an extension. `operational_events` already answers most audit forms of the question. |
| JSON-LD / SHACL export | Only when an external consumer asks. Then: read-only projection over existing tables, PROV-O + NORIA-O alignment rather than a private namespace, SHACL in CI over fixtures rather than at runtime. |

---

## Recommended order

Rationale: precision-first within conversational (A), foundation-first within
applicability (B), quality items (C) slotted where their dependencies land, and
governance (F) split into "make the schema honest" before "make autonomy safe".

1. **M1 — Conversational precision**: A1 → A2 → A7 → A10 (classifier + corrections +
   negative store + ordering fix; small, each compounds the shipped tiers)
2. **M2 — Applicability foundation**: B1 → B2 (taxonomy + traits; unblocks half the backlog)
3. **M3 — Thread understanding**: A3 → A4 → A5 → A6 (topic state, reference resolver,
   quotes, bots)
4. **M4 — Similarity & transfer**: B3 → C2 → B4 (signatures, recurrence, applicability ladder)
5. **M5 — Learning loops**: B5 → C1 → C3 → B6 (cohorts, suggestion learning, negative
   signals, fleet grouping)
6. **M6 — Surfaces & robustness**: C4 → C6 → A8 → A9 → C5 → C7
7. **Epics D/E** — schedule independently; E7/D3/D4 are small fillers, E1/E2 before
   any production tenant, D2 when AutomationEdge access exists.
8. **M7 — Truthful governance**: F1 → F2 → F5 → F3 → F4 (F1 first: F3 depends on it,
   F2 and F5 are independent fillers). Cheap, no prerequisites, and it removes the
   schema-reads-as-capability ambiguity that inflated the Doc-4 comparison.
9. **M8 — Autonomy prerequisites**: F6 → F7 → F8 → F9 → F10 → F11. Contract before
   hash, hash before attempts, verification before trust. Nothing here ships value on
   its own; all of it must land before the first side-effecting tool. F9 can start in
   parallel with F6–F8 if two branches are in flight.

---

## Goals for Claude Code

Ready-to-paste `/goal` statements. Each presupposes the working agreement above
(stacked branches, 3-pass review, CI-verified merges, KNOWN_GAPS/codewiki updates).

```text
/goal implement backlog milestone M1 (A1 message-function classifier, A2 corrections
supersede links, A7 negative evidence store, A10 reply-inheritance reconciliation)
from codewiki/BACKLOG.md, one stacked branch per item, 3 review-fix-review passes,
CI-verified merge each
```

```text
/goal implement backlog milestone M2 (B1 entity class taxonomy, B2 normalized traits
with widened CMDB ingestion) from codewiki/BACKLOG.md with 3 review passes per item;
seed data idempotent; unknown classes must degrade to current behavior
```

```text
/goal implement backlog milestone M3 (A3 thread-topic state + provisional cases,
A4 conversational-reference resolver, A5 quoted content + span refs, A6 bot handling)
from codewiki/BACKLOG.md, 3 review passes each, abstention over guessing throughout
```

```text
/goal implement backlog milestone M4 (B3 issue signatures, C2 recurrence membership
type, B4 fix applicability rules + 7-level ladder with MAF tool) from
codewiki/BACKLOG.md; Doc-3's four LPT001 examples are the acceptance tests
```

```text
/goal implement backlog milestone M5 (B5 cohort stats + promotion policy, C1
suggestion learning, C3 correlation negative signals, B6 fleet grouping) from
codewiki/BACKLOG.md; every broadening of scope stays reviewer-gated
```

```text
/goal work through backlog milestone M6 and epics D/E from codewiki/BACKLOG.md in
listed order, 3 review passes per item, skipping any item whose external dependency
(AutomationEdge access, Opsgenie credentials) is unavailable and recording the skip
in KNOWN_GAPS
```

```text
/goal implement backlog milestone M7 (F1 populate-or-retire the 0029 stub columns
behind a no-writer guard test, F2 relationship type registry validated in
graph/builder, F5 generation provenance on derived entities, F3 policy versioning +
PolicyCheck records, F4 knowledge freshness and supersession in retrieval) from
codewiki/BACKLOG.md, one stacked branch per item, 3 review-fix-review passes,
CI-verified merge each
```

```text
/goal implement backlog milestone M8 (F6 skill registry + ExecutionContract, F7
RFC-8785 immutable approval binding, F8 ExecutionAttempt + live idempotency, F9
generalized verification criteria, F10 scoped TrustProfile with a Wilson lower bound,
F11 rollback + escalation objects) from codewiki/BACKLOG.md; no side-effecting tool
merges until F6-F8 are in; verification (F9) lands before trust (F10); 3 review passes
per item
```

---

*Maintained alongside [KNOWN_GAPS.md](KNOWN_GAPS.md): when an item ships, move its
entry to a dated line in the shipped-foundation table; when a gap is discovered, add
it here with a [Gap] tag and cross-link.*
