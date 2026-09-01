# Playbook Quality — Permanent Improvement Architecture and Implementation Plan

**Document version:** 4.0.0
**Status:** Phase 1 implemented in shadow mode. Phases 0, 2–7 planned.
**Target systems:** ContextEdge ingest, episode and pattern processing, playbook generation and governance, AutomationEdge SupportCopilot retrieval
**Primary objective:** Prevent incorrect, incomplete, generic, inconsistent, unsupported, unsafe, or misleading playbooks from reaching a reviewer or an engineer, regardless of the specific product issue that exposed the gap

---

## 0. What changed in v4.0

v3.0 was validated against the implementation, the 90-row support review sheet, and the corpus-remediation reports. Its architecture held up: every structural claim it made about the code was confirmed in source. Seven things did not, or were missing.

| # | Change | Where |
|---|---|---|
| 1 | Review-sheet statistics were stale (80 rows assumed, 90 actual) and understated reviewer disagreement | §2.1 rewritten |
| 2 | The plan gated *publication*; nothing is published. 420 candidates, 0 approved, 0 agent-retrievable | §14.0 added |
| 3 | Approval **is** publication in this code. v3 described them as two boundaries with different rules | §14.0 |
| 4 | The policy pack — the only control that catches the largest rejection class — had no owner, no seed, and no phase | §12.2, Phase 2.5 added |
| 5 | The generation prompt *mandates* the step class reviewers reject. v3 designed a detector for output the prompt requires | §11.4, Phase 2.6 added |
| 6 | The corpus was machine-rewritten before it was reviewed, and one residual defect remains | §2.4 added |
| 7 | Nine further gaps: manual-generation divergence, defect artifacts, retrieval precision, keyword-derived obligations, edited-grounding staleness, title-equality dedup, episode fragmentation, a free suitability signal | §4.11–4.19 |

**Correction to the validation itself.** An earlier draft of the validation asserted that the 95-step remediation repair had not been run, and that the remediation had injected a fabricated literal. Both were wrong, and the record is corrected in §2.4. `verify_playbook_corpus.py` independently diffs every current step against its pre-remediation original and reports **1** residual defect, not 95 — the repair was applied. The `plugin release 4.5 (ticket 219894)` text came from `strip_unverified_version_suffixes.VERIFIED_REPLACEMENTS` and is ticket-verified, not fabricated. The residual defect is content *loss* — a dropped parenthetical example — which is a real but much smaller problem than the one first reported.

---

## 1. Executive decision

The permanent solution must provide three independent quality decisions:

1. **Subject and title quality** — whether the playbook accurately describes one evidenced operational subject.
2. **Actual step quality** — whether the instructions are accurate, supported, complete, ordered, executable, safe, and non-redundant.
3. **Cross-content coherence** — whether the title, symptoms, cause, applicability, steps, validation, and resolution describe the same issue.

None of these may compensate for another through a weighted average. A strong title cannot make incorrect steps acceptable. Correct steps cannot make a misleading title acceptable.

To that, v4 adds a fourth decision that none of the three can make:

4. **Organisational policy** — whether the procedure is one this support organisation will actually perform. Four of twenty-eight rejections are for actions that are grounded, accurate, complete, coherent, and forbidden. See §12.2.

The recommended design is a versioned Playbook Quality System composed of:

- An immutable quality-bearing content revision. **(built — §6)**
- A source-derived quality contract created before generation.
- Claim-level evidence links and semantic validation.
- Independent subject, step, and coherence gates.
- Data-driven tenant and product policy packs. **(schema built, unpopulated — §12)**
- Complete enforcement at every mutation and publication boundary. **(assessment wired at every boundary; enforcement is Phase 5)**
- Continuous revalidation when content, evidence, policy, or applicability changes.

---

## 2. Investigation scope and evidence

Re-derived from the AutomationEdge Support Team review sheet, the current backend implementation, the corpus-remediation reports and verification scripts, and a source-level validation of every claim in v3.

### 2.1 Review-data facts (corrected)

| Item | v3 assumed | Actual (90 rows) |
|---|---:|---:|
| Review rows | 80 | **90** |
| Approved | 52 | **62** |
| Rejected | 28 | 28 |
| Unique playbook UUIDs | 70 | **77** |
| Rows without a usable UUID | 1 | 1 (row 14, SharePoint) |
| Effective identity groups | 71 | **78** |
| Identities with conflicting verdicts | 4 | **5** |

Rows 81–90 were added after v3 was written. Two of them **flip a prior rejection**: row 86 approves `dc6a3e33` which row 48 rejected; row 88 approves `851d2fd2` which row 49 rejected.

The five conflicting identities:

| UUID | Verdicts | Note |
|---|---|---|
| `02c24e93` | Approved (Aniket) / Rejected (Priyanka) | one title |
| `da345261` | Approved (Aniket) / Rejected (Harshal) / Rejected (Srujan) | 2:1 against |
| `851d2fd2` | Rejected (Priyanka) / Approved (Prakash) | |
| `dc6a3e33` | Approved (Aniket) / Rejected (Priyanka) / Approved (Prakash) | **two titles** |
| `dd39c4cd` | Rejected (Ritesh) / Approved (Priyanka) | **two titles** |

### 2.2 The sheet contains direct proof of the title problem

`dc6a3e33` was reviewed as *"Application File Upload Size Limit Misconfiguration"* and as *"AutomationEdge File Upload and Configuration Troubleshooting"*. `dd39c4cd` was reviewed as *"Workflow Processing Bottleneck and Queue Saturation Recovery"* and as *"Automation Agent Request Processing Inefficiencies Recovery"*.

Same playbook. Different titles. Opposite verdicts. Nothing in the data says whether the steps changed too — which is the entire argument for the immutable content revision in §6, made by the corpus rather than by assertion.

It also means the sheet cannot be deduplicated by title, and that any labelling exercise that groups by title will silently merge two different reviewed artifacts.

### 2.3 Reviewer labels are inconsistent within a single reviewer

Srujan wrote effectively the same comment twice:

- Row 67, *"Article is good, But need more details to be added in reference to issue"* → **Approved**
- Row 69, *"Article is good, But need more details to be added in reference to issue"* → **Rejected**

The binary verdict is not a reliable function of the assessment. §15.2 therefore requires **intra-rater** agreement to be measured alongside inter-rater agreement, and treats the free-text comment — not the verdict column — as the primary label source.

### 2.4 The reviewed corpus had already been machine-rewritten

The 2026-08-26 remediation applied **233 IMPROVE rewrites and 20 SUPPRESS retirements** across 440 playbooks (`apply_summary.json`). `REMEDIATION_GAP_VALIDATION.md` then found the rewriting function defective in four ways, damaging 95 steps across 72 playbooks.

**That repair has been applied.** `verify_playbook_corpus.py` diffs every current step against its pre-remediation original and reports `residual_defects: 1`. The one remaining case is content loss, not fabrication:

```
original: "If the behavior is a confirmed bug (such as a plugin sync failure), log the
           bug details, inform the customer of the upcoming patch release version…"
current:  "If the behavior is a confirmed bug, log the bug details, inform the customer
           of plugin release 4.5 (ticket 219894) containing the fix…"
reasons:  ["article-example-collapsed", "content-loss:7w"]
```

The version literal is a ticket-verified replacement and is correct. The dropped parenthetical is the defect, and `fix_remediation_defects.py` skips it because the step is protected as a verified edit — so it needs a decision rather than a re-run.

**The residual validity threat is about timing, not damage.** The support review and the repair both happened in the same window. Until it is established which reviewed playbooks were read before the repair, a rejection cannot be attributed to the generator. Two rejection comments read like the repair's damage classes:

- Row 23, *"ae.property file name not mention"* — defect 4 deleted the space before any leading dot, corrupting filenames.
- Row 77, *"The fourth step is not clear and is difficult to understand"* — the signature of defect 2, which truncated the remainder of a sentence.

Phase 0 resolves this by comparing review timestamps against edit timestamps and diffing each reviewed playbook against `playbook_original_steps.json`.

### 2.5 Consequence for evaluation

The current counts are useful observational feedback but are not valid golden-set denominators. A trustworthy evaluation set must identify the exact content revision reviewed. Conflicting verdicts must be preserved as reviewer disagreement until the reviewed snapshots are recovered and adjudicated.

### 2.6 Correction to the earlier root-cause narrative (unchanged from v3, and confirmed)

`ai/extractors/episode_extractor.py:1-20` states outright that chunks are concatenated as separate episodes with no cross-chunk synthesis pass. The former theory that multi-chunk extraction caused the historical stacked timelines is not established, and this plan does not build on it. See §4.18 for a *different*, currently-documented mechanism that is worth testing.

---

## 3. Verified current end-to-end flow

1. Source records and documents are ingested and normalized.
2. Evidence is classified, redacted, chunked, embedded, and correlated.
3. Correlated observational evidence is reconstructed into episodes.
4. Approved episodes receive issue signatures.
5. Approved episodes are grouped into patterns using embeddings and model-assisted matching.
6. Approved knowledge is retrieved for the pattern.
7. A playbook candidate is generated from the pattern, episode summaries, knowledge, and negative knowledge.
8. Structural post-processing validates citation labels, classifies citation presence, and sanitizes branching structure.
9. A playbook shell and version are persisted. **A content revision is now minted and assessed here.**
10. Reviewers edit, transition, approve, and publish playbooks. **Each of those paths now invalidates and reassesses.**
11. Runtime retrieval serves approved playbooks with published versions — **currently zero, see §14.0**.
12. Scheduled services scan published playbooks for drift and playbook-versus-knowledge contradictions.

### 3.1 Existing strengths to preserve

Verified present in source and composed into the new system rather than replaced: tenant and domain scoping; approved-episode filtering during pattern mining; knowledge lifecycle and supersession; applicability and version signals; separation of normative knowledge from empirical episodes; citation-label resolution (`playbook_generator.validate_source_refs`); grounded-versus-best-practice tagging (`classify_step_grounding`); branch sanitisation (`sanitize_branching_logic`); empty-step prevention at transition; step-binding and draft-edit validation; immutable published step content (DB trigger); contradiction scanning; drift, expiry and negative-feedback monitoring; prompt and model provenance.

Two the plan previously under-credited:

- **`restricted` already works.** It is a lifecycle state (`playbook_service.py:25`) and every runtime arm requires `approved` (`search/playbook_candidates.py:55,166,207,316,399`) plus `published_at IS NOT NULL`. §20.3's "restrict from runtime immediately" is implementable today with no new machinery.
- **`PlaybookVersion.conflicts` is already three-valued.** NULL means "not assessed", distinct from an empty list meaning "assessed, none found" (`models/playbook.py:203-207`). The six-state model in §5.2 is an extension of an established house convention, not a new abstraction.

### 3.2 What current controls do not prove

That a citation supports the step claim; that a cited passage authorizes rather than prohibits an action; that exact values, paths, versions, component names or parameters are correct; that retrieved knowledge is relevant to the specific issue; that required prerequisites, actions, validations and rollback are covered; that the title describes the playbook; that steps solve the stated issue; that step order is technically correct; that the procedure is internally consistent; that a generic best-practice step adds value; that an existing decision remains valid after a title, source, policy or applicability change; **and that the organisation is willing to perform the procedure.**

---

## 4. Gaps this plan closes

§4.1–4.10 are v3's, all confirmed against source. §4.11–4.19 are new.

### 4.1 Title and step quality were not truly separated — CONFIRMED

Title and description are on the playbook shell (`models/playbook.py:91-92`); steps are on the version (`:196-207`). `PATCH /api/v1/playbooks/{id}` requires only `knowledge_manager`, checks no lifecycle state, and on a title change does exactly one thing: re-embeds (`api/v1/playbooks.py:544-551`). A version-level quality record cannot represent the quality of mutable shell content.

### 4.2 Citation presence was treated as grounding — CONFIRMED

`classify_step_grounding` (`playbook_generator.py:294-330`) marks any step with non-empty `source_refs` as grounded. That is structural and deliberate, and it proves only that the label was supplied to the prompt. It does not establish entailment, polarity, applicability, authority or freshness.

### 4.3 Product-specific rules were embedded in the permanent layer — CONFIRMED, and already happening (see §4.14)

### 4.4 Concreteness could be gamed

Counting product terms or file-like tokens rewards a step that is precise but wrong. The existing scorer is `scripts/remediate_playbook_corpus.py :: remaining_quality`, scoring on `extract_coords or AE_PRODUCT`. Four concreteness scorers now exist and none reproduces the specificity audit. **Action: `verify_playbook_corpus.py`'s series becomes the one of record; the others are retired or demoted to dashboard-only.**

### 4.5 KB coverage was too broad — and obligations are keyword-derived (see §4.14)

### 4.6 Exact signature partitioning was too strict

Signature compatibility is a clustering feature, not an equality rule.

### 4.7 Duplicate detection was underspecified — and the live defect is the mirror image (see §4.17)

### 4.8 Enforcement paths were incomplete — CONFIRMED, and worse than stated (see §4.11)

### 4.9 The boolean result was insufficient — CONFIRMED

There was no quality persistence at all. A repo-wide search for `quality` returned only `evidence_quality`, a model-authored string.

### 4.10 Contradiction and drift were not integrated — CONFIRMED

`drift_service.py:13-81` is five heuristics: expiry, 90-day staleness, ≥3 negative feedback, pattern-updated-after-playbook. It reads; it never gates. It has no "a cited source changed" trigger, which §14.3 requires.

### 4.11 `POST /playbooks/generate` diverges from the worker on five guards — NEW

| Guard | Worker (`pattern_tasks.py`) | Manual endpoint (`api/v1/playbooks.py`) |
|---|---|---|
| Existing-playbook dedup | `:430-440` | absent |
| Pattern-confidence floor | `:457-470` | absent |
| Empty-steps rejection | `:560-579` | **absent** — persists a stepless playbook |
| `_effective_risk_tier` normalisation | `:77`, `:581` | absent — model's value verbatim, defaults `medium` (`:1362`) |
| Explicit `lifecycle_state` | `:594` | relies on column default |

The empty-steps case is partly recovered by `transition_playbook`, but only when someone tries to move it — the row is already in the corpus and the review queue by then. **The assessment now records it as a critical finding; blocking it is Phase 5.**

### 4.12 The quality contract has no product-defect shape — NEW

Row 18: *"The article does not clearly mention the proper error, cause, solution, or the product version in which this issue is fixed."* Several AutomationEdge issues are product defects, where the required content is defect identity, affected versions, fixed-in version and interim workaround — not a remediation procedure. §7.1 has none of those fields and §8.2's artifact-type list has no defect-notice type. **Both are added.**

### 4.13 Nothing gates retrieval *precision* — NEW

Row 66: *"The issue is more related to external integration; the details referring in KB is more related to GUI automation or Web-Automation."* Row 38: *"The solution provided does not appear to be appropriate for the reported issue."*

§8.1 checks that retrieval *succeeded*. It does not check that what came back is about this issue. This defeats evidence grounding rather than being caught by it: if the retrieved KB is off-topic, an off-topic step can be entailed by it and score as grounded. **§8.4 gains a source-relevance gate.**

### 4.14 Obligations are currently derived by keyword match, then made binding by prompt — NEW

`knowledge_retrieval_service.py:532-612` labels every KB section ACTION / PREREQUISITE / VALIDATION / ROLLBACK by substring match on a hardcoded list — `"required"`, `"test"`, `"confirm"`, `"run "`, `"click "`, `"applies to"` — with a `_COMMANDISH_RE` fallback. Prompt v7 rules 13–14 then make those labels binding: *"Treat labelled KB sections as a coverage checklist… If any required item is missing, add it or record a conflict."*

A paragraph containing the word "required" becomes a prerequisite the generator must include. This is a plausible mechanical source of two rejection classes at once — the padding rows (45, 48, 49) and the irrelevant-points rows (63, 69) — and it is exactly the §12.3 violation this plan forbids, sitting upstream of generation. **§7's contract replaces this labelling; the keyword labeller is retired in Phase 2.**

### 4.15 Editing a grounded step keeps its citations — NEW

`playbook_editing.py:54-69` lists `source_refs`, `grounding_status`, `evidence_quality` and `step_classification` in `PROTECTED_KEYS`. The intent is right — a typed round-trip must not strip provenance. The effect is that a reviewer can rewrite a grounded step's instruction and it keeps the citations of the sentence it replaced, still reading as evidenced. `human_edited` is set (`:192-198`); nothing acted on it. **`validators/grounding.py` now does: an edited grounded step is a `stale_grounding` finding at major severity.**

### 4.16 Approval is publication — NEW

`transition_playbook` sets `published_at`/`published_by` on the `approved` transition (`playbook_service.py:266-275`). There is no separate publish action. See §14.0.

### 4.17 Generation dedup is exact-title equality — NEW

`pattern_tasks.py:430-440` skips generation when a playbook exists with the same `pattern_id` **or** `lower(title)`. §4.7 warns against retiring on title equality; the live defect is **admitting** on title inequality.

Browser/driver compatibility alone has nine distinct playbooks under review: `892b0cee`, `2b39038a`, `e413c9b3`, `bd0d6a45`, `17d0c7d2`, `61aac0a3`, `8f77b4a5`, `a8d8c1fc`, `207407ab`. Five are rejected, two (rows 24, 27) with the *identical* comment "We not suggest to change JAR". One policy decision, five near-identical playbooks, five separate rejections. **Phase 6 gains a corpus-level clustering pass so a family is adjudicated once.**

### 4.18 Episode fragmentation is the untested upstream cause of the over-merge rejections — NEW

Row 22: *"This Article is mix solution, we didn't understand exact issue: 1st symptom is request raised in tab but agent not pickup, and 5th symptom is system shows agent utilizes 90% memory."* Two mutually exclusive symptoms in one playbook. Row 62 is the same shape across two plugins.

`episode_extractor.py:1-20` concatenates chunk outputs as separate episodes with no reduce pass, and names the risk itself: *"If cross-chunk duplication shows up as a real problem (same incident split across two episodes from adjacent chunks), add a reduce pass."* Two fragments of one incident can then cluster into one pattern.

This is cheap to test and Phase 0 tests it: do the patterns behind `dd39c4cd` and `da345261` draw episodes from adjacent chunks of one correlation cluster? If so, §8.3's pattern-level gate needs an episode-level containment check upstream of it.

### 4.19 A free, high-precision suitability signal already exists — NEW

`corpus_verification.json` lists 17 active playbooks with ≤2 steps; only **154 of 420** have a verification step and **17 of 420** have an escalation step. Four of the ≤2-step playbooks are in the review sheet:

| Playbook | Steps | Verdict |
|---|---:|---|
| Audit Log Purging Failure Due to Product Defect | 2 | Rejected (18) |
| Planned Cloud Patching Impact Communication | 2 | Rejected (57) |
| New Feature Planning and Guidance | 2 | Rejected (60) |
| Dormant Account Activation | 2 | Approved (4) |

`steps ≤ 2 AND no verification AND no escalation` is a deterministic, zero-cost signal for "this is probably not a procedure". **The structural validator now emits it at info severity** — as a calibration anchor the semantic classifier must beat, never as the classifier.

---

## 5. Permanent quality model

### 5.1 Independent assessment dimensions

| Dimension | Required decision | Status |
|---|---|---|
| Structure | Is the artifact well-formed and internally addressable? | **built** |
| Artifact suitability | Is a procedural playbook the correct artifact type? | Phase 2 |
| Subject/title truth | Are all title claims evidenced and applicable? | Phase 3 |
| Subject/title specificity | One useful operational subject, not misleadingly broad? | Phase 3 |
| Step accuracy | Is each instruction supported or explicitly marked unresolved? | Phase 3 |
| Step completeness | Are all applicable requirements represented? | partial (built: verification presence) |
| Step executability | Can an operator perform it with the details and permissions given? | Phase 3 |
| Step ordering | Dependencies, decisions, validations, rollback, escalation correctly ordered? | partial (built: branch reachability) |
| Step consistency | Do steps agree with each other and the declared cause? | Phase 3 |
| Evidence grounding | Does each material claim map to an applicable supporting passage? | partial (built: claim self-consistency and staleness) |
| Safety and policy | Does the procedure comply with tenant policy and risk controls? | **Phase 2.5 — blocked on the pack** |
| Minimality | Does every step add diagnosis, action, validation, safety or routing value? | Phase 3 |
| Cross-content coherence | Do title, symptoms, conditions, cause, steps and resolution describe one issue? | Phase 3 |
| Duplicate or variant status | New, revision, scoped variant, broad overlap, or true duplicate? | Phase 3 |

### 5.2 Decision rule

No composite score. Dashboard scores may be calculated for prioritisation; blocking dimensions stay independent and worst-wins.

| State | Meaning |
|---|---|
| pass | Required validators completed and no blocking finding remains |
| fail | One or more blocking quality findings remain |
| inconclusive | Evidence or policy is insufficient to decide safely |
| error | An evaluator or dependency failed |
| stale | Content, source, policy, applicability, or evaluator inputs changed |
| overridden | A permitted reviewer accepted a scoped exception |

An error, inconclusive result, or stale assessment must never be interpreted as a pass. This is enforced in `quality/states.py :: resolve_overall`, which takes the worst state and returns `inconclusive` for an empty dimension map — assessing nothing is not the same as finding nothing wrong.

**A consequence worth stating plainly, because it will look like a bug:** in the current bundle every assessment resolves to `inconclusive` at best. Nothing can pass until Stages B–J exist. In shadow mode that costs nothing and is honest.

---

## 6. Immutable quality-bearing content revision — BUILT

`playbook_content_revisions` (migration 0094) snapshots title, description, risk tier, automation mode, trigger conditions, applicability, inputs, outputs, steps, branching logic, rollback notes, evidence references, conflicts and generator provenance as one immutable object addressed by an RFC 8785 content hash.

v3 offered two options (§6.3): move title onto the version, or make the hash span both rows. **The second was taken**, because it is additive — no existing writer changes, and the shell keeps its identity and lifecycle metadata where the rest of the system expects them.

Behaviour:

- Identical content is one revision. Re-saving a draft unchanged does not mint a revision or invalidate a good assessment.
- Editor bookkeeping (`updated_at`, `revision`, `index`, `edited_at`) is excluded from the hash. `human_edited` is **not** — it is a claim about the grounding.
- Any quality-bearing edit mints a new revision, invalidates assessments whose input hash no longer matches, and triggers reassessment.
- A title-only change produces a different hash. This is pinned by `test_title_only_change_changes_the_hash`.

---

## 7. Source-derived quality contract — Phase 2

### 7.1 Contract schema

Artifact type and audience; primary operational subject; affected capability and component; failure mode and scope; observed symptoms and error claims; supported cause claims and uncertainty; environment and version applicability; preconditions; required actions; optional actions; alternative branches and conditions; required validations and success criteria; rollback obligations; escalation criteria; restricted actions; known failed actions; source conflicts; unresolved information requirements.

**Added in v4 (§4.12):** defect identity; affected versions; fixed-in version; workaround validity window.

### 7.2 Claim provenance

Source identity; source type and authority; exact section, chunk or span; source lifecycle; applicability; polarity; conditionality; freshness; extraction confidence.

### 7.3 Contract outcomes

Ready for procedural generation; ready for a different artifact type; requires pattern split; requires additional evidence; requires conflict adjudication; invalid input.

The generator must not fill unresolved contract gaps with unsupported technical detail.

---

## 8. Pre-generation quality gates — Phase 2

### 8.1 Pipeline readiness

Required sources retrieved; retrieval failure distinguishable from no relevant knowledge; sources visible, current and permitted; episode inputs approved and not superseded; episode step order and identity invariants hold; pattern membership not stale; source content available at the expected hash; applicability determinable or explicitly unknown.

### 8.2 Artifact suitability

Data-driven and configurable. Informational, limitation, planning, procedural, diagnostic, change, communication **and defect-record** artifacts must not be forced into one troubleshooting template.

### 8.3 Pattern coherence

Compatibility across subject, component, failure mode, symptoms, applicability, root cause, resolution mechanism. Hard rejection reserved for confidently incompatible dimensions; low-confidence features cause split review or abstention. **Complemented by an episode-level containment check if §4.18's test confirms fragmentation.**

### 8.4 Evidence sufficiency **and source relevance** (extended in v4)

Determine whether the available evidence can support a truthful subject, at least one actionable or diagnostic path, a verifiable completion state, and required safety and applicability conditions.

**New:** each retrieved document must be judged applicable to the contract's failure mode and component *before* it becomes an obligation. An inapplicable document is dropped from the checklist rather than covered. Without this, off-topic retrieval defeats evidence grounding instead of being caught by it (§4.13).

Insufficient evidence produces an inconclusive result, not generic generated padding.

---

## 9. Generation contract

The generator remains a proposer. It does not decide admissibility.

### 9.1 Structured output

Stable step identity; step type; action; target; parameters; preconditions; expected observation; failure route; verification; risk and reversibility; rollback relation; source-claim references.

### 9.2 Source-use rules

Normative knowledge says what should be done. Empirical episodes say what was done and observed. Negative knowledge says what failed. Tenant policy defines approved operational constraints. Model background knowledge cannot silently become an authoritative product instruction.

### 9.3 Abstention

Missing information becomes an information requirement, conflict or reviewer decision — never an invented path, version, value, command, component name or remediation.

---

## 10. Post-generation validator architecture

Staged cascade. Stage A is built; B–J are registered as explicitly inconclusive so their absence cannot read as clean.

| Stage | Purpose | Status |
|---|---|---|
| A | Deterministic structure, citation resolvability, branch reachability, grounding self-consistency | **built** |
| B | Claim extraction from title, symptoms, cause, step text, outcomes, failure routes, rollback | Phase 2 |
| C | Evidence support: entailed / contradicted / partial / unsupported / N-A / uncertain, with authority, polarity, conditionality, applicability, freshness, exact spans | Phase 3 |
| D | Independent subject/title validation | Phase 3 |
| E | Per-step quality | Phase 3 |
| F | Completeness against contract obligations | Phase 3 |
| G | Ordering and branching dependency graph | Phase 3 |
| H | Cross-content coherence | Phase 3 |
| I | Safety and policy against a versioned pack | **Phase 2.5** |
| J | Duplicate and consolidation classification | Phase 3 |

Deterministic equality is appropriate for literal values the source supplies. Semantic entailment is required for paraphrased operational claims. Stage H's result stays independent of both title and step quality by construction — it is a separately registered validator so it cannot be quietly folded into either.

---

## 11. Generic padding and redundancy detection

### 11.1 Step utility test

A step is useful when it changes relevant system state, collects evidence that narrows the diagnosis, tests a branch condition, verifies an action, reduces a demonstrated risk, satisfies an applicable policy requirement, or defines a necessary rollback or escalation decision.

### 11.2 Padding signals

No source or policy support; no new action, target, condition, observation or decision; low information gain; semantic duplication; high template similarity across unrelated playbooks; removal does not reduce contract, safety, branch or validation coverage; generic wording with no actionable object or observable outcome.

No single signal blocks publication until calibrated.

**`grounding_status == "non_grounded"` is a ready-made, already-persisted, zero-cost first population** — `classify_step_grounding` force-tags it at generation, and all four padding rejections (45, 48, 49, 56) fall inside it. The grounding validator already counts and reports the ratio.

### 11.3 Unsupported specificity

Detect unsupported precision separately from genericity. A precise but unsupported command, path, version, setting, component or threshold is more dangerous than a visibly incomplete instruction and gets a blocking evidence finding.

### 11.4 The prompt must stop mandating what the detector will flag — NEW in v4

Prompt v5 rule 9 (`ai/prompts/playbook.py:326-347`) hands the model a thirteen-item checklist of best-practice steps to add. Prompt v6 rule 11 then tells it to produce the minimal set and emit no filler. The two pull in opposite directions, and the review shows which wins:

| Rejection | Checklist item |
|---|---|
| Row 45 — *"no need to check agent state"* | prerequisite validation |
| Row 48 — *"no need to check size … [or] version … 8.1.0 or higher"* | version compatibility checks |
| Row 49 — *"no need to check log4j file"* | file integrity verification |

The generator was doing exactly what it was told. Building a detector against output the prompt requires is fighting yourself.

**Prompt v10 is registered** (not default) replacing the enumeration with a utility test: a best-practice step must name, in `reason`, the concrete risk it removes for *this* issue on *this* component, and "it is generally good practice" is explicitly not sufficient. v9 remains default until the A/B in `evals/playbook_prompt_ab.py` decides. That harness already exists.

---

## 12. Product ontology and tenant policy

### 12.1 Product ontology — schema built, unpopulated

`product_ontology_versions` / `product_ontology_terms`: canonical components and capabilities, aliases and terminology, relationships, version and environment concepts, artifact and action types, known incompatibilities. Populated from approved product sources and support-owner curation, versioned, hash recorded in every assessment.

Row 71 is the motivating case: *"Instead of 'Deployment Environment' or 'Target Environment', the article should explicitly refer to the AutomationEdge Server."*

### 12.2 Policy pack — the largest gap in v3

Classifying all 28 rejections:

| Class | Rows | Count | Caught by |
|---|---|---:|---|
| **Policy-prohibited or discouraged action** | 21, 24, 27, 76 | 4 | §10 Stage I **only** |
| Padding / unnecessary step | 45, 48, 49, 56 | 4 | §11 |
| Wrong artifact type | 55, 57, 58, 60 | 4 | §8.2 |
| Missing required literal | 14, 23, 37, 59 | 4 | §10 F + §8.4 |
| Subject wrong, too broad, or mismatched | 47, 54, 62, 65 | 4 | §10 D / H |
| Pattern over-merge | 22, 63, 66 | 3 | §8.3 |
| Cause↔remediation mismatch; wrong knowledge | 38, 66 | 2 | §10 H / §8.4 |
| Incompleteness | 69 | 1 | §10 F |
| Ontology / terminology | 71 | 1 | §12.1 |
| Missing defect metadata | 18 | 1 | §7.1 (added) |
| Executability | 77 | 1 | §10 E |

The top row is the problem. *"We do not suggest changing the JAR"* and *"the article should not instruct users to re-register a new AutomationEdge Agent"* are **not evidence failures**. The KB almost certainly documents replacing a JAR; episodes almost certainly did re-register agents. These steps pass claim extraction, evidence support, per-step validation, completeness, ordering and coherence. They are grounded, accurate, complete, coherent — and forbidden.

v3 described the pack's schema precisely and said nothing operational about it: no phase created it, no owner, no seeding path from a rejection comment to a policy row, and no way to express *preference* rather than prohibition.

**v4 adds:**

1. **Phase 2.5 — Policy pack bootstrap** (see §17), with a named owner in the AutomationEdge support organisation, a review-UI affordance that turns a rejection comment into a proposed policy row, an initial pack seeded by adjudicating the 28 rejections, and a review cadence.
2. **A `discouraged` decision**, alongside allowed / prohibited / requires-evidence / requires-approval / requires-conditions / requires-rollback / requires-role. "We do not suggest" is a preference with an alternative. Collapsing it into prohibited blocks procedures that are sometimes right; collapsing it into allowed reproduces these rejections. `alternative_action` on the rule is what makes it actionable — "we do not suggest changing the JAR" is useless to a generator without the sentence that follows it.
3. **A governance question for §22:** when the KB documents an action the support organisation will not perform, which wins, and who records that?

Policy stays tenant-scoped data: normalized action pattern, applicability, decision, alternative, required evidence authority, required reviewer role, required safeguards, effective dates, source and owner. Overrides are scoped, reasoned, approved and expirable.

### 12.3 No hardcoded product rules

Current review examples may seed evaluation labels, initial ontology entries, initial policy proposals and regression fixtures. They must not become fixed generic code rules or prompt literals. **§4.14 documents where this rule is already being broken and when that code is retired.**

---

## 13. Quality assessment persistence — BUILT

### 13.1 Assessment identity

`playbook_quality_assessments` records tenant, playbook, content revision, content hash, contract hash, source snapshot hash, ontology version, policy-pack version, validator bundle version, model provenance, evaluation mode, start and completion timestamps, per-dimension states, and overall state.

### 13.2 Findings

`playbook_quality_findings` records generic category, dimension, severity, target kind and reference (field name or `step_id` — never a line number, because steps get reordered), normalized claim, human-readable explanation, supporting and contradicting spans, validator, confidence, remediation category.

Categories describe failure semantics — `unsupported_specificity`, `subject_overbroad`, `policy_prohibited_action` — never the name of the issue that first exposed them.

### 13.3 History

Append-only. A later assessment supersedes; a dependency change marks stale. Nothing is overwritten or deleted. This is what makes auditability, threshold comparison, validator A/B, override analysis and regression tracking possible — a system that overwrites can only answer "what do we think now", not "what did we think when this was approved".

---

## 14. Lifecycle and enforcement

### 14.0 Current lifecycle reality — NEW in v4, read this first

```
playbooks_total: 440   lifecycle: {candidate: 420, retired: 20}
versions: 863          published_versions: 0
agent_retrievable_playbooks: 0
```

Three consequences:

1. **The publication gate guards zero traffic.** `POST /api/v1/runtime/match` returns nothing for this tenant. Phase 5's runtime filtering and §23's "runtime does not serve known critical failures" are correct and currently vacuous.
2. **Approval *is* publication.** `transition_playbook` stamps `published_at` on the `approved` transition. There is no separate publish action. Either the plan's language collapses to a single `approved` gate — which is what v4 does — or a distinct publish step is introduced as owned scope.
3. **The boundary that matters today is `candidate → under_review`**, because that is where the support team's attention is actually being spent. `GET /api/v1/playbooks` has no lifecycle filter at all (`api/v1/playbooks.py:170-294`), which is how reviewers are reading and judging `candidate` content.

§14.1 says review entry must not be blocked, and that stays right. But it means **the assessment must be visible in the review UI before enforcement exists**, or shadow mode produces findings nobody sees at the moment they are deciding. That moves out of Phase 5's dashboards into **Phase 4 exit criteria**.

### 14.1 Save, review, approve

- Generated or edited drafts are always persistable for inspection.
- Failed and inconclusive drafts enter a quality-remediation queue.
- Review entry is not blocked merely because a finding needs human adjudication.
- Approval requires a fresh acceptable assessment (Phase 5).
- Critical safety, evidence, contradiction or coherence failures block approval (Phase 5).

### 14.2 Mandatory integration points

| Path | Required behavior | Status |
|---|---|---|
| Automatic generation | Build contract, assess candidate, persist findings | **assessed** via `create_playbook_version` |
| Manual generation | Same orchestration as automatic | **assessed**; five guards still divergent (§4.11) |
| Manual version creation | Invalidate and assess | **done** |
| Draft step edit | Invalidate affected dimensions and reassess | **done** — and `human_edited` on a grounded step now produces a `stale_grounding` finding (§4.15) |
| Title or description edit | Create or invalidate revision, reassess subject plus coherence | **done** |
| Version fork | Copy content, not assessment status | **done** (fork routes through `create_playbook_version`) |
| Rollback | Reassess against current sources and policies before republishing | **assessed**; the gate belongs between version creation and the `published_at` stamp in Phase 5 |
| Single transition | Fresh assessment at review entry and approval | **assessed** at both |
| Bulk transition | Same check per playbook, atomically | **assessed** (shares `transition_playbook`) |
| Import or migration | Mark unevaluated or run an explicit trusted-import policy | Phase 6 |
| Runtime retrieval | Exclude failed or critically stale revisions | Phase 5 |
| **Review-queue list and detail** | Show current assessment and findings | **Phase 4 exit criterion** (new) |

### 14.3 Staleness triggers

Content changes; a cited source changes lifecycle or content; a cited source is superseded; applicability changes; a new contradiction is confirmed; negative knowledge changes; policy or ontology changes; a validator bundle is retired for a critical defect; required revalidation age expires.

Constants exist for each (`STALE_CONTENT_CHANGED`, `STALE_SHELL_EDITED`, `STALE_STEPS_EDITED`, `STALE_SOURCE_CHANGED`, `STALE_POLICY_CHANGED`, `STALE_ONTOLOGY_CHANGED`, `STALE_VALIDATOR_RETIRED`). Content-driven triggers are wired; source, policy and ontology triggers are Phase 2.

### 14.4 Failure behavior

- Draft save: fail open for persistence, fail visible in assessment status. **Implemented — `assess_playbook` never raises.**
- Review queue: allow human access.
- Approval: fail closed on missing, error, inconclusive, stale-critical or failed assessment (Phase 5). `publication_readiness()` already computes this answer so switching it on is a call-site change.
- Runtime: follow a tenant rollout policy for legacy unevaluated content; never serve known critical failures.

---

## 15. Evaluation dataset and calibration

### 15.1 Review event schema

Review event ID; playbook ID; exact content revision ID; content hash; review timestamp; reviewer; artifact type; independent dimension labels; finding categories; severity; supporting evidence spans; reviewer rationale; adjudication status.

**Added in v4:** whether the reviewed text pre- or post-dates the 2026-08-26 remediation and its repair (§2.4).

### 15.2 Labelling process

1. Recover exact reviewed snapshots where possible.
2. Preserve unresolved conflicting reviews.
3. At least two qualified reviewers label calibration and test cases.
4. Adjudicate disagreements using source evidence.
5. Record both original and adjudicated labels.
6. Measure inter-rater agreement by dimension **and intra-rater agreement** (§2.3).
7. **Treat the free-text comment, not the Approved/Rejected column, as the primary label source.** The column is demonstrably inconsistent; the comments are specific and actionable.

### 15.3 Dataset splits

Split by issue or procedure family, not random rows. Near-duplicate revisions and playbooks stay in the same split — note that the browser-driver family alone spans nine playbooks (§4.17). Maintain a development set, a threshold-calibration set, a locked holdout set, and a production-drift sample.

### 15.4 Test categories

Good title with bad steps; bad title with good steps; correct but incomplete; complete but misordered; unsupported technical detail; missing required detail; source contradiction; negation and conditionality; version or environment mismatch; generic padding; duplicate and near-duplicate steps; broad pattern consolidation; insufficient evidence; retrieval or evaluator failure; stale source or policy.

**Added in v4:** policy-forbidden but fully-evidenced procedure (the §12.2 class, which no other category covers); wrong artifact type; the ≤2-step no-verification shape as a precision baseline (§4.19).

---

## 16. Success metrics

### 16.1 Offline

Critical-defect false-pass rate; good-revision false-block rate; per-dimension precision and recall; macro recall across finding categories; claim entailment accuracy; contradiction accuracy; requirement coverage precision and recall; ordering accuracy; padding and redundancy accuracy; duplicate classification accuracy; calibration error and abstention quality.

### 16.2 Workflow

Assessment completion and error rate; time from draft to reviewable result; reviewer correction time; reviewer agreement; override rate and outcomes; findings per revision and recurrence after correction; published revisions with fresh assessments; stale-assessment backlog.

**Added:** the count of playbooks the Phase 5 gate *would* have blocked, computed continuously in shadow from `publication_readiness()`. This is the number product and support need before they will agree to turn enforcement on.

### 16.3 Runtime outcomes

Wrong-match feedback; ineffective-step feedback; expired-workaround feedback; reopen rate; resolution success; escalation after playbook use; rollback after playbook use; negative feedback by validator version.

### 16.4 Enforcement bar

Thresholds calibrated on the locked set and approved by product, support and QA. Critical false passes take priority over aggregate recall. A target is not accepted if it improves overall metrics while allowing unsafe or unsupported procedures through.

---

## 17. Implementation phases

### Phase 0 — Correct the baseline and label model

- Convert the 90-row sheet into review events.
- **Establish the remediation timeline**: compare review timestamps against edit timestamps; diff every reviewed playbook against `playbook_original_steps.json`; tag each of the 28 rejections `generator-defect` / `remediation-defect` / `both`. (§2.4)
- **Resolve the one residual text defect** in "Agent Upgrade Feature Misunderstanding and Clarification" — the fixer skips it because the step is protected as a ticket-verified edit, so it needs a decision, not a re-run.
- Recover revision snapshots and hashes; resolve identity and version ambiguity, remembering that two identities carry two titles each (§2.2).
- Define dimension labels and the generic finding taxonomy.
- Double-label and adjudicate the calibration and holdout sets; measure inter- and intra-rater agreement.
- **Test the episode-fragmentation hypothesis** on `dd39c4cd` and `da345261` (§4.18).
- Baseline existing structural, contradiction, drift and remediation signals.
- **Settle the concreteness scorer**: make `verify_playbook_corpus.py`'s series the one of record; retire or demote the other three (§4.4).

Exit: review guidelines approved; locked holdout available; agreement measured; baseline metrics by dimension; every rejection attributed to a cause.

### Phase 1 — Revision and assessment foundation — **COMPLETE (shadow)**

Delivered:

- Migration `0094_playbook_quality_foundation`: content revisions, append-only assessments, findings, policy packs and rules, ontology versions and terms. RLS and composite tenant FKs per house convention; zero ORM/DDL drift.
- `contextedge/quality/`: six-state model, RFC 8785 content hashing, the shell+version revision snapshot, validator registry, orchestrator.
- Two real validators (structural, grounding integrity) and seven registered-inconclusive placeholders.
- `services/playbook_quality_service.py`: the shared orchestration — mint revision, assess, persist, invalidate — wired into every mutation path in §14.2.
- `publication_readiness()`, computing the Phase 5 answer without acting on it.
- 64 unit and metamorphic tests.

Migration 0094 was executed against a real PostgreSQL 16 instance and verified: all 7 tables create; RLS is enabled **and forced** with a `tenant_isolation` policy on each; the 6 check constraints reject invalid values; `uq_pcr_tenant_playbook_hash` rejects a duplicate content hash; the partial index `ix_pqa_current` and the `created_at DESC` index build as intended; the composite tenant FK cascades a playbook delete through to revisions. Tested as a non-superuser role, RLS returns 0 rows for a foreign tenant, 0 rows when no tenant is set (it fails closed), and refuses a cross-tenant insert. `downgrade()` drops everything cleanly and upgrade/downgrade/upgrade round-trips.

Four defects found in post-implementation review and fixed:

| Defect | Fix |
|---|---|
| `PATCH /playbooks/{id}` reassessed on `title`/`description` only, while `risk_tier` and `automation_mode` are also in the content hash — patching either left a stale verdict on moved content | The hook now tests `set(update_data) & SHELL_QUALITY_FIELDS`, reading the field list from `revision.py` rather than repeating it. A pinned-keys test makes adding a snapshot field a conscious act |
| §4.19's suitability signal checked `steps ≤ 2 AND no verification`, omitting the escalation half | All three conditions. A two-step playbook that routes to a human is doing procedural work, and flagging it dilutes a signal whose only value is precision |
| `build_content` omitted `playbook_confidence`, `execution_confidence_guidance`, `verification_policy` | All three included; the first is argued in the code comment, the other two are plainly operator-facing content |
| Every version-create path stamped `origin="version_create"`, so the audit trail could not distinguish generation from a fork or a rollback | `create_playbook_version(..., origin=...)`, threaded through all five call sites |
| Discarding a draft left the open assessment describing content the playbook no longer presents | `DELETE .../versions/{id}` now invalidates and reassesses |

Exit criteria, met:

- Every quality-bearing mutation invalidates the prior assessment.
- Assessment history is reproducible and append-only.
- No title-only edit can retain a stale pass — pinned by test, and `PATCH /api/v1/playbooks/{id}` now invalidates and reassesses.
- No validator failure, and no unbuilt validator, can produce a pass.

### Phase 2 — Quality contract and pre-generation gates

Source-claim extraction with span provenance; artifact suitability routing (including `defect_record`); evidence readiness, sufficiency **and source relevance**; confidence-aware pattern coherence; ontology and policy-pack interfaces; distinguish retrieval failure from no applicable knowledge; **retire the keyword section labeller** (§4.14).

Exit: generation receives a source-derived contract; insufficient or conflicting evidence produces an explicit non-pass; incompatible patterns do not silently generate a combined procedure; no obligation originates from a substring match.

### Phase 2.5 — Policy pack bootstrap — NEW

Not a code phase. Without it, Stage I cannot be built and the largest rejection class stays invisible.

- Name an owner in the AutomationEdge support organisation.
- Adjudicate the 28 rejections into an initial pack; start with the four policy rows (JAR replacement ×3, agent re-registration).
- Build the review-UI affordance that turns a rejection comment into a proposed policy row.
- Agree a review cadence and an expiry convention for overrides.
- Answer §22.11.

Exit: an `active` pack exists with a version, a hash, an owner, and at least the four rules the current corpus demands.

### Phase 2.6 — Prompt realignment — NEW

- A/B prompt v10 against v9 on the existing `evals/playbook_prompt_ab.py` harness.
- Measure step economy, grounded ratio, and the count of best-practice steps whose `reason` names a concrete risk.
- Promote v10 to default only on evidence.

Exit: the prompt no longer mandates a step class the padding detector is built to flag.

### Phase 3 — Independent post-generation validators

Subject/title validation; per-step claim validation; completeness against the contract; ordering and branch dependency validation; cross-content coherence; utility-based padding detection; safety policy (needs Phase 2.5) and duplicate classification. Reuse contradiction, applicability, lifecycle and negative-knowledge signals.

Exit: subject, steps and coherence have independent results; findings identify exact claims and evidence spans; evaluator failures cannot produce a pass (already true).

### Phase 4 — Shadow mode

**Read API delivered.** `GET /playbooks/{playbook_id}/quality` returns the current assessment, per-dimension states, severity-ordered findings and the Phase 5 readiness verdict; `GET /playbooks?include_quality=true` attaches a compact summary to each list row in three queries for the page rather than three per row. Both are read-only — opening a playbook does not mint a revision or trigger an assessment, or the history stops recording what happened to the content and starts recording who looked at it.

Two fields exist to stop the panel lying, and both are easy for a UI to skip:

- `summary.matches_current_content` is false when the content moved after it was assessed. An assessment can look perfectly healthy and be about text nobody can see any more.
- `summary.coverage` gives `decided` / `undecided` / `total`. In this bundle 11 of 14 dimensions are undecided, so `state` is mostly a statement about our coverage rather than about the playbook. The panel should say "3 of 14 checks run" and withhold the verdict until that number is worth showing; a warning badge on all 420 playbooks teaches reviewers to ignore badges before Phase 3 makes them mean anything.

`scripts/verify_quality_persistence.py` runs the whole persistence layer against a real database inside a rolled-back transaction — revision idempotency, title-edit revisioning, append-only supersede, finding ordering, the batched histogram, summary freshness, readiness, and staleness — and exits non-zero on failure, so it can be wired in after `alembic upgrade head`.

**Reviewer panel delivered.** `components/playbooks/quality-panel.tsx` renders the assessment on the playbook detail page, a `QualityCell` adds a column to the playbook list, and findings addressed to a step now render *against that step* in `PlaybookSteps` — a finding three panels away from the step it describes is a finding the reviewer scrolls past. Panel and step list share one fetch through `usePlaybookQuality`.

Four things the panel is built to refuse to do, each pinned by a test:

- **It leads with coverage, not the verdict.** "3 of 14 checks run" comes before the badge, and the description says outright that findings are real but their absence is not evidence of quality.
- **It never renders never-assessed as clean.** An empty panel reads as approval, so that case says so in words.
- **Out-of-date content gets the loudest banner** and visually demotes the findings beneath it, because a healthy-looking assessment about text nobody can see is worse than no assessment.
- **Structure is a banner, not a fourth tab**, and an unevaluated group reads "not checked" rather than showing a clean badge.

`inconclusive` is deliberately grey rather than amber in `status-badge.tsx`: colouring "not checked yet" as a warning would put an alarm on all 420 playbooks and train reviewers to ignore it before Phase 3 makes it mean anything. `fail` was also missing from that map and rendered grey — the one state that must not look neutral.

Verified with the real toolchain: `tsc --noEmit` clean across both modified pages and the whole reachable component tree, and 24 vitest tests green.

Phase 4's remaining work is threshold calibration, which needs shadow data from real reviewer use.

Run assessments without blocking (already running). Review false passes and false blocks by dimension. Calibrate thresholds and confidence bands. Compare deterministic and semantic validators. Measure cost, latency, cache efficiency, reviewer usefulness.

Exit: critical false-pass and good-revision false-block targets met; **findings visible in the review queue list and detail views** (§14.0); support confirms the explanations are actionable; product and QA approve the enforcement scope.

### Phase 5 — Enforcement

Gate approval — which in this codebase is also publication (§14.0). Close the five manual-generation guard gaps (§4.11) by unifying orchestration. Cover every generation, edit, fork, rollback, import and transition path. Add audited scoped overrides. Apply runtime filtering. Add dashboards and alerts.

Exit: no publication bypass; all enforced decisions reproducible; override and rollback drills pass.

### Phase 6 — Existing corpus

- **Cluster by subject/component/failure-mode family first**, then route — so the nine browser-driver playbooks are adjudicated once, not nine times (§4.17).
- Assess existing revisions without mutating their text.
- Route each to pass, regenerate, human repair, artifact conversion, restrict or retire.
- **Treat the 233 already-applied IMPROVE edits as unassessed content**: they carry no `human_edited` provenance and were never re-grounded.
- Re-review risk-prioritised samples.
- Resolve `REMEDIATION_GAP_VALIDATION.md`'s open items: 48 agreed-worthless playbooks still active (G7); 109 KEEP-vs-CRITICAL-GAP conflicts unadjudicated (G8).
- **Decide the publication path** (§14.0). Nothing in Phases 1–5 has operational effect on runtime until playbooks move to `approved` with published versions.

Exit: active corpus has known assessment coverage; no bulk regex rewriting used as a quality substitute; runtime serves only policy-compliant revisions under the selected rollout policy.

### Phase 7 — Continuous improvement

Feed reviewer corrections and runtime outcomes back into labelled data; recalibrate on drift; reassess when source, policy, ontology or applicability changes; maintain locked regression suites across validator and model upgrades.

---

## 18. Test strategy

### 18.1 Unit tests

Positive, negative, uncertain, malformed, empty, conflicting, stale, tenant-isolation cases per validator.

### 18.2 Metamorphic invariants

| Invariant | Status |
|---|---|
| Changing only the title cannot change the step-quality result | **tested** |
| Changing only steps cannot change the historical title assessment, but must invalidate overall status | **tested** |
| Adding an irrelevant supported source cannot make an unsupported claim grounded | Phase 3 |
| Removing the only supporting span changes the claim to unsupported | Phase 3 |
| Reversing a dependency-sensitive step pair triggers ordering failure | partial (hash changes; ordering validator is Phase 3) |
| Adding a redundant step triggers minimality without changing title quality | Phase 3 |
| Changing a source from affirmative to negative reverses the evidence decision | Phase 3 |
| Changing applicability invalidates the old result | Phase 2 |

### 18.3 Integration tests

Automatic and manual generation parity; manual creation and editing; title and description editing; forking; rollback; single and bulk transitions; imports and migrations; runtime retrieval; contradiction and drift updates; concurrent edit and assessment races; evaluator timeout and retry.

### 18.4 End-to-end

Draft persists even when quality fails; reviewers see exact findings and evidence; publication blocked correctly; corrected revision receives a new assessment; published content is the assessed content hash; source or policy change marks the published assessment stale; runtime stops serving a known critical failure.

---

## 19. Scalability and reliability

### 19.1 Cascade

Content hash and cache lookup → structural validation → literal value and ontology checks → embedding-based candidate selection → semantic claim evaluation only for relevant or uncertain pairs → human review for unresolved high-risk findings.

### 19.2 Caching

By claim hash, source-span hash, applicability hash, policy-pack hash, validator and model version. Never reuse a semantic result when any dependency hash changes.

### 19.3 Cost and latency

Batch claim evaluations; limit semantic comparison to candidate passages; record skipped-budget and evaluator-error states; allow asynchronous draft assessment; fail closed only at approval, not at draft persistence.

**Sizing:** 420 active playbooks × ~4.4 steps ≈ 1,850 steps. A full corpus reassessment is bounded and cheap, which removes the main objection to Phase 6.

### 19.4 Concurrency

Lock the revision or verify the content hash before writing an assessment; recheck the current revision at approval; reject approval if assessment and content hashes differ (`publication_readiness` does this); make bulk transitions atomic per item with explicit partial-failure reporting.

---

## 20. Operational workflow

### 20.1 Reviewer experience

The quality panel shows independent subject, step and coherence status; findings grouped by dimension and severity; the exact affected step or field; supporting and contradicting passages; applicability and source authority; suggested remediation category; assessment version and freshness.

**This is a Phase 4 exit criterion, not a Phase 5 nicety** (§14.0).

### 20.2 Override workflow

Authorized role; finding-specific decision; rationale; supporting evidence; scope; expiry or review date; audit event. An override does not rewrite the finding and does not silently become a global policy exception.

### 20.3 Incident response

Restrict from runtime immediately — **`restricted` already does this** (§3.1); preserve the published revision and evidence; record the defect and affected assessment version; identify other revisions sharing the same claim, source, policy or validator dependency; reassess the impacted set; correct through new revisions.

---

## 21. Implementation areas

| Area | Modules | Phase 1 status |
|---|---|---|
| Quality core | `quality/` (new package) | **built** |
| Quality persistence | `models/playbook_quality.py`, migration 0094 | **built** |
| Shared orchestration | `services/playbook_quality_service.py` | **built** |
| Generation orchestration | `workers/pattern_tasks.py`, `api/v1/playbooks.py` | assessed; guards to unify in Phase 5 |
| Playbook generator | `ai/generators/playbook_generator.py`, `ai/prompts/playbook.py` | v10 registered |
| Pattern quality | `workers/pattern_tasks.py`, `services/pattern_service.py` | Phase 2 |
| Issue signatures | `services/issue_signature_service.py` | Phase 2 |
| Knowledge retrieval | `services/knowledge_retrieval_service.py` | Phase 2 — keyword labeller retires |
| Evidence and applicability | evidence, knowledge lifecycle, applicability, supersession | Phase 2 |
| Playbook persistence | `models/playbook.py`, `services/playbook_service.py`, migrations | hooked |
| Editing and rollback | `api/v1/playbooks.py`, `services/playbook_editing.py` | hooked |
| Contradictions and drift | `services/contradiction_service.py`, `services/drift_service.py` | **staleness wired** — scans call `signal_quality_stale()` |
| Runtime retrieval | `search/playbook_candidates.py`, `search/hybrid_ranker.py`, `api/v1/runtime.py` | Phase 5 |
| Quality read API | `GET /playbooks/{id}/quality`, `GET /playbooks?include_quality=true` | **built** |
| Review UI | playbook detail and playbook list components | **built** (Phase 4a) |
| Evaluation | `evals/`, datasets, regression tests | Phase 0 / 2.6 |

---

## 22. Decisions required before enforcement

1. Supported artifact types and routing behavior.
2. Blocking dimensions by risk tier.
3. Evidence authority hierarchy.
4. Behavior when authoritative knowledge is absent.
5. Policy ownership and approval workflow.
6. Override roles, scope, and expiry.
7. Legacy unevaluated runtime behavior.
8. Revalidation frequency and staleness grace periods.
9. Critical false-pass and good-revision false-block targets.
10. Human-review requirements for inconclusive semantic findings.
11. **When the KB documents an action the support organisation will not perform, which wins, and who records that?** (§12.2)
12. **Does the corpus get published at all, and on what schedule?** Phases 1–5 have no runtime effect until it does (§14.0).

Stored as versioned configuration or governance policy, not embedded in validator code.

---

## 23. Definition of done

- Subject/title quality is evaluated independently from step quality.
- Step quality covers accuracy, completeness, order, consistency, executability, safety and verification.
- Cross-content coherence is independently evaluated.
- Every material claim is supported, contradicted, unresolved, or explicitly policy-derived.
- Product specificity comes from sources and ontology, not hardcoded generic rules — **including in retrieval** (§4.14).
- Padding detection is utility- and evidence-based, **and the prompt no longer mandates what it flags** (§11.4).
- **An active policy pack exists, with an owner, and Stage I runs against it** (§12.2).
- Every quality-bearing mutation invalidates stale assessments. ✔
- Every approval path requires a fresh acceptable assessment.
- Runtime does not serve known critical failures.
- Assessment history and overrides are auditable. ✔
- Evaluation uses exact revision snapshots and adjudicated dimension labels, **with remediation-era content identified** (§2.4).
- Thresholds are calibrated on a locked non-leaking holdout set.
- Continuous source, policy, contradiction, drift and runtime feedback can invalidate or improve future revisions.

This design addresses the AutomationEdge review findings without encoding those specific findings as the architecture. It establishes a reusable quality system capable of detecting future playbook failures that have not yet appeared in the current corpus.
