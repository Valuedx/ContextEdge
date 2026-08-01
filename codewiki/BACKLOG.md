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

### A3 · Thread-topic state + provisional cases — M/L
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

### A4 · Conversational-reference resolver — L
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

### A5 · Quoted/forwarded content + span-level references — M
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

### A6 · Bot messages: weighting + structured card parsing — M
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

### A8 · Edits & deletes reconciliation — M
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

### A9 · Transcript robustness (ASR + code-switching) — M
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

### B1 · Entity class taxonomy — M
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

### B2 · Normalized traits + widened CMDB ingestion — M
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

### B3 · Issue signatures (problem fingerprints) — M/L
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

### B4 · Fix applicability rules + the 7-level ladder — L
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

### B5 · Cohort success statistics + promotion policy — M/L
**What.** Per-cohort outcome counts on fix patterns (9/10 on Latitude 5420, 0/4 on
desktops) fed by the existing `case_outcome_fix_patterns` + execution-verification
loop, and a reviewer-gated promotion ladder: one success = precedent → same-model rule
→ class rule → family rule; failures narrow scope automatically.
**Why.** Prevents one lucky laptop fix from being overstated as universal; provides
the calibration data B4's weights are waiting for.
**Dependencies.** B4. **Acceptance.** A fix succeeding twice on Latitude 5420 and
failing on a desktop yields a model-level candidate rule and an automatic desktop
exclusion; promotion to `windows_endpoint` requires reviewer approval.

### B6 · Fleet / major-incident grouping — M/L
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

### C1 · Suggestion learning + source-pair thresholds — M
**What.** Doc-1 P3 items 2 & 6, not shipped in PR #32: per-source-pair similarity
thresholds (ticket↔ticket text is boilerplate-heavy and needs a higher floor than
chat↔chat), and feeding reviewer accept/reject outcomes back into thresholds/corroborator
weighting.
**Sketch.** Start with logged accept-rate per (source-pair, corroborator-type) from
`correlation_suggestions` review outcomes; adjust floors from observed precision once
volume exists. No ML — counting.
**Acceptance.** Accept/reject rates are queryable per pair; a pair with <20% accept
rate gets a raised floor (config, reviewer-visible).

### C2 · Remaining membership relationship types — S/M
**What.** Doc-1 named `related`, `recurrence`, `follow_on` membership types;
`MEMBERSHIP_RELATIONSHIPS` ships four. `recurrence` lands naturally with B3 (same
issue signature, different occurrence); `related`/`follow_on` from explicit references
("follow-up to INC0010427").
**Dependencies.** B3 for recurrence. **Acceptance.** A new incident matching an old
episode's signature records a `recurrence` membership to the old case (never a merge).

### C3 · Correlation negative signals — M
**What.** Doc-1 P2 item 4, not shipped in PR #31: explicit penalties — different
production environment, different authoritative CI, conflicting ticket numbers, large
time separation, different customer/account — reducing or vetoing identity-tier
correlation.
**Sketch.** Deterministic checks in `_identity_correlation_signal`'s caller using
entity traits (B2 strengthens this; environment exists on Entity today); conflicting
ticket memberships between the two evidence items = hard veto.
**Acceptance.** Two evidence items sharing a rare device but carrying memberships to
two *different* cases do not get an identity-tier edge.

### C4 · Suggestion queue cap + reviewer console — M [Gap]
**What.** Per-tenant pending-suggestion cap (backfill storm protection) + minimal
review UI (list/accept/reject exist as API only).
**Acceptance.** A 10k-item backfill cannot create an unbounded pending queue; a
reviewer can work the queue without curl.

### C5 · Attribution-rate evaluation — M/L [Gap]
**What.** The labeled-data half of P4 item 6: an evaluation dataset with per-step gold
citations; measure unsupported-claim and wrong-source-attribution rates per prompt
version through the existing evaluation harness.
**Acceptance.** `evaluation_runs` can compare episode v2 vs v3 on citation accuracy.

### C6 · Agent projection renders contradictions — S [Gap]
**What.** `episodes.contradictions` reaches reviewers but not the MAF agent surface.
Render a bounded contradictions block in episode facts (budget-aware).
**Acceptance.** An agent consuming the Acme VPN episode sees that close notes and the
Teams thread disagreed on the fix.

### C7 · Historical pattern cleanup — S [Gap]
**What.** Pre-domain-guard patterns may contain cross-domain members (PR #17 caveat).
One-off audited cleanup task: recompute memberships, flag violations for review.

---

## Epic D — Connectors & platform boundaries [Gap]

### D1 · Jira platform-boundary features — L (each M standalone)
Opsgenie alerts connector (alert rollups at parity with em_alert), Assets topology
(Premium API — config-gated), Confluence KB ingestion, request-type/change-window
customfield mapping via `source_config`, sync page-order guard.

### D2 · AutomationEdge connector — L
The long-standing backlog item: workflow/request execution events as evidence,
entity population for workflow entities, `remediated_by` references. Doc-3 makes it
more valuable: AutomationEdge is the authoritative source for
workflow/request status (P4 authority table) and a trait source for B2.

### D3 · HTTP CmdbTopologyClient — S
The in-process client shipped with PR #11; the HTTPS deployment-neutral twin (same
contract, token hygiene like the MAF client) is still open.

### D4 · SapphireIMS instance verification tooling — S
Config-mapped contract ships with verify-per-instance defaults; add a
`validate_credentials`-style probe report listing which configured fields/endpoints
responded, so operators verify mapping without reading logs.

---

## Epic E — Core platform hardening [Gap, standing]

| ID | Item | Size | Notes |
|---|---|---|---|
| E1 | LLM provider resilience: per-call timeout, circuit breaker, fallback | M | budget gates/retries/validation exist; `ai/provider.py` |
| E2 | Prompt-injection fencing at ingest extractors | M | MAF provider fences; episode/decision/identity extractors concatenate raw evidence |
| E3 | Ranking calibration + SLA priors | L | `quality_score=0.5` placeholder, no abstention threshold, N+1 playbook queries; SLA priors deferred from change-risk work |
| E4 | Sync single-flight | M | advisory lock per source object; overlapping backfills currently race (dedup is DB-safe since 0026) |
| E5 | Reviewer/admin consoles | L | identity `needs_review` queue, suggestion queue (C4), episode membership editing — all API-led today |
| E6 | Execution engine depth | L | Release 2: tool registry, rollback execution, timeouts, resume; verification shipped (0036) |
| E7 | Prompt-family doubled-brace fixes | S | `decision`/`pattern`/`playbook` v1 system prompts still carry literal `{{ }}`; ship v2s (episode + identity already fixed) |

---

## Recommended order

Rationale: precision-first within conversational (A), foundation-first within
applicability (B), and quality items (C) slotted where their dependencies land.

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

---

*Maintained alongside [KNOWN_GAPS.md](KNOWN_GAPS.md): when an item ships, move its
entry to a dated line in the shipped-foundation table; when a gap is discovered, add
it here with a [Gap] tag and cross-link.*
