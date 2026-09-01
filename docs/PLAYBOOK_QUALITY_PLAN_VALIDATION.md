# Validation of PLAYBOOK_QUALITY_PERMANENT_FIX_PLAN.md v3.0.0

**Validated against:** ContextEdge backend at `D:\ContextEdge_pro\ContextEdge\backend\src\contextedge`, the 90-row AutomationEdge support review sheet, and `docs/playbook_corpus_remediation/*`
**Date:** 2026-09-01
**Scope:** read-only. No application code, plan, or corpus was modified.

---

## 0. Verdict

**The plan is architecturally sound and its diagnosis of the code is accurate.** Every structural claim it makes about the current implementation checks out against the source (Section 2 below). It is right that title, step, and coherence quality must be independent decisions; right that citation presence is not grounding; right that product rules must be data, not code; right that enforcement paths are incomplete.

**It is not yet safe to execute as written**, for four reasons:

| # | Problem | Severity |
|---|---|---|
| V1 | Its baseline review numbers (§2.1) are stale and understate reviewer disagreement | Blocks Phase 0 |
| V2 | It gates *publication*, but the corpus has never been published — 0 of 420 playbooks are approved, so the gate it builds guards a door nobody uses | Blocks Phase 5 |
| V3 | The reviewed corpus had already been machine-rewritten, with 95 steps known damaged. Labels derived from it attribute remediation defects to the generator | Blocks Phase 0 |
| V4 | 4 of 28 rejections are for actions that evidence *supports* and policy forbids. No gate in the plan can catch them until a policy pack exists, and the plan gives that pack no owner, no seed, and no phase | Blocks Phase 5 |

Nine further gaps (G1–G9) are listed in Section 4. None invalidate the architecture; all change the phase plan.

---

## 1. Method

- Read the plan in full.
- Read the implementation for every module the plan names in §21, plus the runtime retrieval and drift paths: `models/playbook.py`, `services/playbook_service.py`, `services/playbook_editing.py`, `api/v1/playbooks.py`, `ai/generators/playbook_generator.py`, `ai/prompts/playbook.py`, `workers/pattern_tasks.py`, `services/knowledge_retrieval_service.py`, `services/drift_service.py`, `search/playbook_candidates.py`, `ai/extractors/episode_extractor.py`.
- Recomputed the review-sheet statistics from the 90 rows supplied.
- Cross-read `docs/playbook_corpus_remediation/corpus_verification.json`, `apply_summary.json`, `defect_fix_result.json`, and `REMEDIATION_GAP_VALIDATION.md`.

---

## 2. Plan claims that check out against the code

Every one of these was verified in source. The plan earns its diagnosis.

| Plan claim | Verified at |
|---|---|
| §4.1 Title/description live on the shell, steps on the version; a version-level quality record cannot represent shell quality | `models/playbook.py:91-92` (shell) vs `:196-207` (version) |
| §4.1 An approved playbook's title can be edited directly | `api/v1/playbooks.py:492-564` — `PATCH /{id}` requires `knowledge_manager`, checks lifecycle state nowhere, and on a title change does exactly one thing: re-embeds (`:544-551`) |
| §4.2 Citation presence is treated as grounding | `playbook_generator.py:294-330` `classify_step_grounding` — non-empty `source_refs` ⇒ `grounded`. Purely structural. `validate_source_refs` (`:369-430`) proves only that the label was supplied to the prompt |
| §3.1 Structural branch sanitisation exists | `playbook_generator.py:192-291` `sanitize_branching_logic` — drops unresolvable anchors, self-loops, no-op decisions, stranded steps |
| §3.1 Empty-step prevention at transition | `playbook_service.py:254-262` |
| §3.1 Immutable published step content | `playbook_editing.py:1-7` (DB trigger `trg_playbook_versions_steps_immutable`) |
| §4.8 Manual generation is ungoverned | Confirmed and worse than stated — see G1 |
| §4.8 Version fork / rollback carry no quality state | `api/v1/playbooks.py:1192-1229` — rollback copies the payload into a new version and, if the playbook is `approved`, stamps `published_at` immediately. No reassessment, no gate |
| §4.8 Bulk transition applies the same weak check | `api/v1/playbooks.py:607-680` — validates only the state-machine edge; `transition_playbook` then applies only the empty-steps rule |
| §4.9 The result is effectively boolean | There is no quality persistence at all. A repo-wide grep for `quality` returns only `evidence_quality` (a model-authored string in the prompt) and its entry in `PROTECTED_KEYS`. No table, no field, no service |
| §4.10 Drift is a disconnected post-publication alert | `drift_service.py:13-81` — five heuristics (expiry, 90-day staleness, ≥3 negative feedback, pattern-updated-after-playbook). It reads; it never gates |
| §2.3 The episode extractor does not append chunk steps to one episode | `ai/extractors/episode_extractor.py:1-20` states outright that chunks are concatenated as separate episodes and no cross-chunk reduce pass is run. The plan's refusal to build on the old causal theory is correct |
| §12.3 Product-specific rules must not be generic code | Already violated in production — see G4 |

**Two existing strengths the plan under-credits and should name explicitly:**

1. `restricted` is already a lifecycle state (`playbook_service.py:25`) and every runtime arm requires `approved` (`search/playbook_candidates.py:55,166,207,316,399` plus `published_at IS NOT NULL` at `:399-400`). The plan's §20.3 incident response ("restrict from runtime immediately") is therefore implementable today with no new machinery. Say so — it is the one enforcement control that already works.
2. The `conflicts` column is `NULL`-able specifically to distinguish "not assessed" from "assessed, none found" (`models/playbook.py:203-207`). That is exactly the three-valued discipline §5.2 asks for, already established as a house convention. Cite it as the precedent rather than introducing the idea fresh.

---

## 3. The four blockers

### V1 — The plan's baseline numbers are stale and understate disagreement

§2.1 reports 80 rows / 52 approved / 28 rejected / 70 UUIDs / 71 identity groups / 4 conflicts. Recomputing from the sheet supplied now:

| Item | Plan §2.1 | Actual (90 rows) |
|---|---:|---:|
| Review rows | 80 | **90** |
| Approved | 52 | **62** |
| Rejected | 28 | 28 |
| Unique playbook UUIDs | 70 | **77** |
| Rows without a UUID | 1 | 1 (row 14, SharePoint) |
| Effective identity groups | 71 | **78** |
| Identities with conflicting verdicts | 4 | **5** |

Rows 81–90 (Prakash, all Approved) were added after the plan was written. They matter more than their count: **two of them flip a prior rejection.** Row 86 approves `dc6a3e33`, which Priyanka rejected at row 48. Row 88 approves `851d2fd2`, which Priyanka rejected at row 49. The conflicting set is now:

| UUID | Verdicts | Note |
|---|---|---|
| `02c24e93` | Approved (Aniket) / Rejected (Priyanka) | same title |
| `da345261` | Approved (Aniket) / Rejected (Harshal) / Rejected (Srujan) | 2:1 against |
| `851d2fd2` | Rejected (Priyanka) / Approved (Prakash) | |
| `dc6a3e33` | Approved (Aniket) / Rejected (Priyanka) / Approved (Prakash) | **two different titles** |
| `dd39c4cd` | Rejected (Ritesh) / Approved (Priyanka) | **two different titles** |

**Two findings the plan should absorb:**

**(a) The sheet contains direct empirical proof of the plan's own §4.1/§6.3 thesis.** `dc6a3e33` is reviewed as "Application File Upload Size Limit Misconfiguration" and as "AutomationEdge File Upload and Configuration Troubleshooting". `dd39c4cd` is reviewed as "Workflow Processing Bottleneck and Queue Saturation Recovery" and as "Automation Agent Request Processing Inefficiencies Recovery". Same playbook, different titles, opposite verdicts. Title is mutable shell content that changed between reviews while the steps did or did not — nothing in the data can tell us which. This is the strongest available argument for the immutable content revision, and the plan does not use it. Put it in §2.

**(b) Reviewer labels are internally inconsistent within a single reviewer.** Srujan wrote effectively the same comment twice: row 67 "Article is good, But need more details to be added in reference to issue" → **Approved**; row 69 "Article is good, But need more details to be added in reference to issue" → **Rejected**. The binary verdict is not a reliable function of the assessment. This is a second, independent reason the raw sheet cannot be a golden set, and it strengthens §15.2's double-labelling requirement — but it also means **inter-rater agreement must be measured intra-rater as well.** Add that to §15.2 step 6.

**Action:** rewrite §2.1 with the 90-row figures; add (a) and (b); state that the 28 rejections plus 5 conflicts, not the approvals, are the usable signal.

---

### V2 — The plan gates publication; nothing is published

`corpus_verification.json` records the live state:

```
playbooks_total: 440   lifecycle: {candidate: 420, retired: 20}
versions: 863          published_versions: 0
agent_retrievable_playbooks: 0
```

Every one of the 420 active playbooks is in `candidate`. Zero versions have `published_at`. `POST /api/v1/runtime/match` returns nothing for this tenant, as `REMEDIATION_GAP_VALIDATION.md` G1 already established.

The plan's enforcement design (§14.1, §14.4, Phase 5) is built around "approval and publication must require a fresh acceptable assessment" and "runtime does not serve known critical failures". **Both currently guard zero traffic.** Meanwhile the path the support team actually used — `GET /api/v1/playbooks` and the playbook detail page at `:3000/playbooks/{id}` — has no lifecycle filter at all (`api/v1/playbooks.py:170-294`), which is how reviewers are reading and judging `candidate` content.

Two consequences the plan must address:

1. **Approval *is* publication in this code.** `transition_playbook` sets `published_at`/`published_by` on the `approved` transition (`playbook_service.py:266-275`). There is no separate publish action. The plan repeatedly names them as two boundaries with different rules (§14.1, §14.2, §14.4, §19.4). Either reconcile the language to a single `approved` gate, or state that a distinct publish step is being introduced and own that as scope.
2. **The gate that matters first is `candidate → under_review`**, because that is where the support team's attention is being spent today. The plan explicitly says review entry should *not* be blocked (§14.1) — correct as a principle, but it means the assessment must be **visible in the review UI before enforcement exists**, or the entire Phase 4 shadow mode produces findings nobody sees at the moment they are deciding. Move "surface findings in the review queue and detail page" out of Phase 5's dashboards and into **Phase 4 exit criteria**.

**Action:** add a §14.0 that states the current lifecycle reality (0 published), reconciles approve-vs-publish, and names the review-entry boundary as the first enforcement target.

---

### V3 — The reviewed corpus was machine-rewritten before it was reviewed

> **Correction (2026-09-01, after further checking).** An earlier version of this section said the 95-step repair had not been run, and that the remediation had injected a fabricated literal. Both were wrong. `verify_playbook_corpus.py` independently diffs every current step against its pre-remediation original and reports **`residual_defects: 1`** — the repair *was* applied; `defect_fix_result.json` shows nothing left to do because there is nothing left to do. And the `plugin release 4.5 (ticket 219894)` text came from `strip_unverified_version_suffixes.VERIFIED_REPLACEMENTS`, a ticket-checked edit, not a fabrication. The one residual defect is content *loss* — the parenthetical `(such as a plugin sync failure)` was dropped — which the fixer skips because the step is protected as a verified edit, so it needs a decision rather than a re-run. The finding below stands but is narrower than first stated: the threat is **timing**, not damage.

`apply_summary.json` records that the 2026-08-26 remediation applied **233 IMPROVE rewrites and 20 SUPPRESS retirements across 440 playbooks**. `REMEDIATION_GAP_VALIDATION.md` then found that the rewriting function was defective in four ways and damaged **95 steps across 72 playbooks**: 27 whitespace collapses (`and.process-studio`), 51 sentences truncated by the de-hedger (`…passing flags--disable-gpu.`), 15 lost terminal periods, and 2 deleted steps that carried the actual fix. 94 of those 95 have since been repaired.

The support review and the repair both fall in the same window. **Until it is established which reviewed playbooks were read before the repair, a rejection cannot be attributed to the generator.** Candidate cases visible in the comments:

- Row 23 — "ae.property file name not mention" — defect 4 deletes the space before any leading dot, exactly the class that corrupts a filename.
- Row 77 — "The fourth step is not clear and is difficult to understand" — the signature of defect 2, which truncates the remainder of a sentence.
- Row 56 — "One point is useful" — consistent with a playbook whose other steps were damaged.

I am not asserting these are remediation artifacts; I am asserting **the plan cannot label them until each rejected playbook's text is compared against `playbook_original_steps.json` and its review timestamp against its edit timestamp.** Otherwise Phase 0 trains the taxonomy on defects the generator never produced, and Phase 4 calibrates thresholds against them.

**Action:** add to Phase 0, as a blocking prerequisite: (i) establish the review-vs-repair timeline; (ii) diff every reviewed playbook against its pre-remediation original; (iii) tag each of the 28 rejections `generator-defect` / `remediation-defect` / `both` before any labelling; (iv) resolve the one residual defect, which needs a human decision because the step is protected as a ticket-verified edit. Add to §17 Phase 6 that the 233 already-applied IMPROVE edits carry no `human_edited` provenance and were not re-grounded — they need the same assessment treatment as generated content, not a pass.

---

### V4 — The largest single rejection class is invisible to every gate in the plan

Classifying all 28 rejections:

| Class | Rows | Count | Plan coverage |
|---|---|---:|---|
| **Policy-prohibited action** (change the JAR ×3, re-register the Agent) | 21, 24, 27, 76 | 4 | §10.9 only |
| Padding / step reviewers say is unnecessary | 45, 48, 49, 56 | 4 | §11 ✔ |
| Wrong artifact type (info / limitation / feature-planning) | 55, 57, 58, 60 | 4 | §8.2 ✔ |
| Missing required literal (path, property file, VAPT point, config steps) | 14, 23, 37, 59 | 4 | §10.6 + §8.4 ✔ |
| Subject/title wrong, too broad, or not matching steps | 47, 54, 62, 65 | 4 | §10.4 / §10.8 ✔ |
| Pattern over-merge / contradictory content in one playbook | 22, 63, 66 | 3 | §8.3 ✔ (partial — see G3) |
| Cause↔remediation mismatch; wrong knowledge retrieved | 38, 66 | 2 | §10.8 ✔ / §8.1 ✗ (see G3) |
| Incompleteness ("need more details") | 69 | 1 | §10.6 ✔ |
| Ontology / terminology | 71 | 1 | §12.1 ✔ |
| Missing product-defect metadata (fixed-in version) | 18 | 1 | **not covered** (G2) |
| Executability ("fourth step not clear") | 77 | 1 | §10.5 ✔ |

The plan covers 23 of 28 well. The problem is the top row.

"We do not suggest changing the JAR" and "the article should not instruct users to re-register a new AutomationEdge Agent" are **not evidence failures.** The AutomationEdge KB almost certainly *does* document replacing a JAR; episodes almost certainly *did* re-register agents. These steps would pass claim extraction (§10.2), evidence support (§10.3), per-step validation (§10.5), completeness (§10.6), ordering (§10.7), and coherence (§10.8). They are grounded, accurate, complete, and coherent — and the support organisation forbids them.

The only control that catches them is the §10.9 policy pack. The plan describes that pack's *schema* (§12.2) precisely and says nothing operational about it:

- No phase creates it. Phase 2 says "introduce product ontology and policy-pack **interfaces**"; nothing populates it.
- No owner. §12.2 says "source and owner" is a field; §22.5 defers "policy ownership and approval workflow" to a pre-enforcement decision.
- No seeding process. §12.3 says review examples "may seed initial policy proposals" and then forbids them becoming code — correct, but there is no described path from a reviewer's rejection comment to a policy row.
- No way to express *preference* rather than prohibition. "We do not suggest changing the JAR" is not "prohibited"; it is "prefer the alternative remediation, and require justification". §10.9's decision enum (allowed / prohibited / requires-evidence / requires-approval / requires-conditions / requires-rollback / requires-role) has no slot for it.

**This is the single most important missing part of the plan.** Without it, an otherwise perfect quality system publishes the four playbooks the support team most objected to.

**Action:**
1. Add **Phase 2.5 — Policy pack bootstrap** with concrete deliverables: policy-pack table and versioning; a named owner in the AutomationEdge support organisation; a review-UI affordance that converts a rejection comment into a proposed policy row; an initial pack seeded by adjudicating the 28 rejections; and an agreed review cadence.
2. Add `discouraged` (prefer-alternative, requires documented justification) to the §10.9 decision enum, with the alternative action named on the policy row.
3. Add to §22 the question the sheet raises directly: **when the KB documents an action the support organisation will not perform, which wins, and who records that?** That is a governance decision, not a validator decision, and it is unanswered today.

---

## 4. Further gaps (G1–G9)

### G1 — `POST /playbooks/generate` diverges from the worker far more than §4.8 states

The plan says manual generation "should use the same orchestration as automatic generation". Correct — but the reader will underestimate the delta. Comparing `api/v1/playbooks.py:1236-1411` against `workers/pattern_tasks.py:417-668`, the manual endpoint is missing **all five** of the worker's guards:

| Guard | Worker | Manual endpoint |
|---|---|---|
| Existing-playbook dedup | `pattern_tasks.py:430-440` | absent |
| Pattern-confidence floor | `:457-470` | absent |
| Empty-steps rejection | `:560-579` | **absent** — persists a stepless playbook |
| `_effective_risk_tier` normalisation | `:77`, `:581` | absent — takes the model's `risk_tier` verbatim, defaults `medium` (`:1362`) |
| Explicit `lifecycle_state` | `:594` | relies on the column default |

Note the empty-steps case is *partly* recovered downstream by `transition_playbook`, but only when someone tries to move it — the row is already in the corpus and the reviewer's queue by then.

**Action:** replace §4.8's one-line bullet with this table. It converts "should share orchestration" from a preference into a defect list, which is what gets it prioritised.

### G2 — The quality contract has no product-defect shape

Row 18: *"The article does not clearly mention the proper error, cause, solution, or the product version in which this issue is fixed."* Row 65 and row 59 point the same way. Several AutomationEdge issues are **product defects**, where the required content is not a remediation procedure but: defect identity, affected versions, fixed-in version, and interim workaround.

§7.1's contract schema has 18 elements and none of them is defect identity or fixed-in version. §8.2's artifact-type list ("informational, limitation, planning, procedural, diagnostic, change, communication") has no defect-notice type either.

**Action:** add `defect_record` to the §8.2 artifact types, and add defect identity / affected versions / fixed-in version / workaround-validity to §7.1.

### G3 — Nothing gates retrieval *precision*

Row 66: *"The issue is more related to external integration; the details referring in KB is more related to GUI automation or Web-Automation."* Row 38: *"The solution provided does not appear to be appropriate for the reported issue."*

§8.1 checks that retrieval *succeeded* and that failure is distinguishable from "no relevant knowledge". It does not check that what came back is **about this issue**. This matters more than it appears, because §10.3 then validates claims *against those sources* — if the retrieved KB is off-topic, an off-topic step can be entailed by it and score as grounded. Off-topic retrieval defeats evidence grounding rather than being caught by it.

Retrieval is embedding-nearest with a stored-version re-rank (`knowledge_retrieval_service.py:305-500`), and the prompt is instructed to use whatever arrives as a binding coverage checklist (v7 rules 13-14, `ai/prompts/playbook.py:433-453`). Nothing between those two points asks whether the article is about the same failure.

**Action:** add to §8.4 an explicit *source-relevance* gate — each retrieved document must be judged applicable to the contract's failure mode and component before it becomes an obligation; an inapplicable document is dropped from the checklist rather than covered.

### G4 — A product-specific keyword rule is already in the generic layer, feeding the prompt as an obligation

§12.3 says product-specific rules must not become generic code. That has already happened, upstream of generation. `knowledge_retrieval_service.py:532-612` labels every KB section ACTION / PREREQUISITE / VALIDATION / ROLLBACK / context by **substring match on a hardcoded keyword list** — `"required"`, `"test"`, `"confirm"`, `"run "`, `"click "`, `"applies to"` — with a `_COMMANDISH_RE` regex fallback. Playbook prompt v7 then makes those labels binding: *"Treat labelled KB sections as a coverage checklist… If any required item is missing, add it or record a conflict."*

So a paragraph containing the word "required" becomes a PREREQUISITE the generator must include, and a descriptive paragraph containing "test" becomes a VALIDATION obligation. This is a plausible mechanical source of two rejection classes at once: the padding rows (45, 48, 49) and the irrelevant-points rows (63, 69).

**Action:** name this in §4.5. The plan currently frames the KB-coverage problem as "too broad a definition of obligation"; the sharper statement is that **obligations are currently derived by keyword match and then made binding by prompt**. §7's contract must replace this labelling, not sit beside it — and §17 should say when the keyword labeller is retired.

### G5 — The prompt currently *mandates* the step class reviewers reject

Prompt v5 rule 9 (`ai/prompts/playbook.py:326-347`) instructs the generator to add best-practice steps for: *"prerequisite validation, backup/rollback preparation, checksum or antivirus verification, security validation, version compatibility checks, file integrity verification, logging and audit documentation, customer communication checkpoints, post-deployment validation, health checks, risk mitigation, cleanup, documentation updates, lessons learned."*

Now read the rejections:
- Row 45: *"Agent auto-update fails during JAR deletion — for this **no need to check agent state**"* → prerequisite validation
- Row 48: *"**no need to check size** and check the installed AutomationEdge version to verify… (8.1.0 or higher)"* → version compatibility check
- Row 49: *"Errors regarding Log4j or conflicting plugin JARs — **no need to check log4j file**"* → file integrity verification

The generator is doing exactly what v5 rule 9 tells it to, and the support team rejects the result. `classify_step_grounding` then force-tags these steps `best_practice` (`playbook_generator.py:294-330`), so they are already perfectly identifiable in the data.

The plan's §11 correctly designs utility-based padding detection — but it never says the **prompt's own mandate must change**. Building a detector for output the prompt requires is fighting yourself.

**Action:** add to Phase 3 (or a new Phase 2.6): revise the generation prompt so best-practice steps are *permitted where policy or contract requires them* rather than enumerated as a checklist, and measure the change as a prompt A/B. The `evals/playbook_prompt_ab.py` harness already exists for exactly this. Separately, note in §11.2 that `grounding_status == "non_grounded"` is a ready-made, already-persisted, zero-cost first signal for the padding detector — 4 of 28 rejections are entirely within that population.

### G6 — Editing a grounded step keeps its citations

`playbook_editing.py:54-69` lists `source_refs`, `grounding_status`, `evidence_quality`, and `step_classification` as `PROTECTED_KEYS` — never copied from a client patch. The intent is right (a typed round-trip must not strip provenance). The effect is that a reviewer can rewrite a grounded step's instruction text and the step keeps its old `source_refs` and its `grounded` status. `human_edited`/`edited_by`/`edited_at` are set (`:192-198`), so the fact is recorded — but nothing acts on it, and the step still reads as evidenced.

This is §14.2's "draft step edit → invalidate affected dimensions" made concrete, and it deserves naming because it is the one place where the existing code actively *preserves* a stale grounding claim.

**Action:** name it in §14.2 as the worked example; state that `human_edited == true` on a `grounded` step must force that step's evidence dimension to `stale` until re-entailed.

### G7 — Duplicate detection at generation is exact-title equality, and the corpus shows the cost

`pattern_tasks.py:430-440` skips generation when a playbook exists with the same `pattern_id` **or** `lower(title)` equal to the pattern title. §4.7 correctly warns against retiring on title equality; the live defect is the mirror image — **admitting** on title inequality.

The sheet shows the result. Browser/driver compatibility alone has nine distinct playbooks under review: `892b0cee`, `2b39038a`, `e413c9b3`, `bd0d6a45`, `17d0c7d2`, `61aac0a3`, `8f77b4a5`, `a8d8c1fc`, `207407ab`. Five of the nine are rejected, two of them (rows 24, 27) with the *identical* comment "We not suggest to change JAR". One policy decision, five near-identical playbooks, five separate rejections.

§10.10 classifies duplicates for new candidates. Nothing in the plan runs consolidation across the **existing** corpus except Phase 6's per-revision routing, which judges revisions one at a time and will therefore route all nine of these independently.

**Action:** add to Phase 6 a corpus-level clustering pass that groups by subject/component/failure-mode *before* per-revision routing, so a family is adjudicated once. The review effort saving is the argument that will get it funded: nine reviews become one.

### G8 — Episode fragmentation is the untested upstream cause of the over-merge rejections

Row 22 is the most diagnostic comment in the sheet: *"This Article is mix solution, we didn't understand exact issue: 1st symptom is request raised in tab but agent not pickup, and 5th symptom is system shows agent utilizes 90% memory. It means agent is running and working on older requests…"* — two mutually exclusive symptoms presented as one issue. Row 62 is the same shape: *"it merges GUI-AutomationEdge and Web-GUI plugin"*.

§8.3's pattern-coherence gate is the right control. But the extractor explicitly concatenates chunk outputs as **separate episodes** with no cross-chunk reduce (`episode_extractor.py:1-20`), and the module comment names the exact risk: *"If cross-chunk duplication shows up as a real problem (same incident split across two episodes from adjacent chunks), add a reduce pass."* Two fragments of one incident, or two adjacent incidents, can then cluster into one pattern and produce one playbook with a contradictory symptom set.

The plan is right to refuse to build on the unproven historical stacked-timeline theory (§2.3). But this is a *different*, currently-documented mechanism, and it is cheap to test: check whether the patterns behind `dd39c4cd` and `da345261` draw episodes from adjacent chunks of the same correlation cluster.

**Action:** add that check to Phase 0's baselining. If it holds, §8.3's gate should be complemented by an episode-level containment check upstream, not only a pattern-level compatibility check downstream.

### G9 — A cheap, high-precision artifact-suitability pre-filter is already computable

`corpus_verification.json` F_step_shape lists 17 active playbooks with ≤2 steps. Four of them appear in the review sheet:

| Playbook | Steps | Verdict |
|---|---:|---|
| Audit Log Purging Failure Due to Product Defect | 2 | Rejected (18) |
| Planned Cloud Patching Impact Communication | 2 | Rejected (57) |
| New Feature Planning and Guidance | 2 | Rejected (60) |
| Dormant Account Activation | 2 | Approved (4) |

Three of four rejected, and all three rejection comments say the same thing — "this is only info", "info and upgrade details only", "the details should be more accurate". The same file records that only **154 of 420** playbooks have a verification step and only **17 of 420** have an escalation step.

`step_count ≤ 2 AND no verification step AND no escalation step` is a deterministic, zero-cost, no-LLM signal for "this is probably not a procedure". It will not be the artifact-suitability classifier — but it is an excellent **Phase 4 calibration anchor** and a sanity check on whatever the classifier produces.

**Action:** add it to §15.4's test categories and to Phase 4 as a precision baseline the semantic classifier must beat.

---

## 5. Smaller corrections

| § | Correction |
|---|---|
| §2 | The plan says the investigation drew on "existing corpus-remediation reports and verification scripts" but does not carry forward a single one of `REMEDIATION_GAP_VALIDATION.md`'s twelve open items. G1 (nothing published), G7 (48 agreed-worthless playbooks still active), G8 (109 KEEP-vs-CRITICAL-GAP conflicts unadjudicated), and G12 (four concreteness scorers, none reproducing the audit) all bear directly on Phases 0 and 6. Reference them explicitly or state why they are out of scope |
| §4.4 | The concreteness critique is right and should name the artefact: `scripts/remediate_playbook_corpus.py :: remaining_quality`, scoring on `extract_coords or AE_PRODUCT`. Say whether it is retired or demoted to dashboard-only, and which of the four existing scorers becomes the one of record |
| §5.2 | Cite `PlaybookVersion.conflicts` (`models/playbook.py:203-207`) as the existing precedent for NULL-means-not-assessed. It makes the six-state model read as an extension of house convention rather than a new abstraction |
| §14.2 | The table's "Runtime retrieval — exclude failed or critically stale revisions" is already satisfied structurally by the `approved` + `published_at` filters. What is *not* covered is `GET /api/v1/playbooks`, which has no lifecycle filter at all and is what reviewers actually read. Add a row |
| §17 Phase 1 | "No title-only edit can retain a stale pass" is the right exit criterion and is directly testable today: `PATCH /playbooks/{id}` with only `title` set (`api/v1/playbooks.py:492-564`). Name the endpoint in the criterion |
| §19.3 | 420 playbooks × ~4.4 steps = ~1,850 steps. At the plan's cascade, a full corpus reassessment is bounded and cheap. Put the number in — it removes the main objection to Phase 6 |
| §21 | Add `services/knowledge_retrieval_service.py` section labelling (G4) and `ai/prompts/playbook.py` v5 rule 9 (G5) to the affected-areas table. Both are currently absent and both must change |

---

## 6. Recommended amendments, in order

1. **Rewrite §2.1** with the 90-row figures, the five conflicts, the two-titles-one-UUID evidence, and Srujan's intra-rater contradiction. *(V1)*
2. **Add §14.0 — current lifecycle reality:** 0 published, approve == publish, review-entry is the first meaningful boundary. *(V2)*
3. **Add to Phase 0, as blocking:** run the 95-step repair; diff every reviewed playbook against `playbook_original_steps.json`; tag each rejection generator-defect vs remediation-defect. *(V3)*
4. **Add Phase 2.5 — Policy pack bootstrap**, with owner, seeding path from rejection comments, and a `discouraged` decision type. *(V4)*
5. **Add Phase 2.6 / extend Phase 3** — revise prompt v5 rule 9 so best-practice steps are permitted, not enumerated; A/B it on the existing harness. *(G5)*
6. **Extend §8.4** with a source-relevance gate. *(G3)*
7. **Extend Phase 6** with corpus-level family clustering before per-revision routing. *(G7)*
8. Fold in G1, G2, G4, G6, G8, G9 and Section 5 as textual amendments.

---

## 7. What this validation did not check

- Did not run any code, migration, or query against the live database. All corpus figures are read from `corpus_verification.json` (2026-08-26) and may have moved since.
- Did not read the frontend review-queue components, so §20.1's reviewer-experience requirements are unvalidated against what the UI can show.
- Did not open the 90 reviewed playbooks' actual step content, so the rejection classification in §V4 is derived from reviewer comments alone. Confirming it — and testing the V3 remediation-damage hypothesis — requires that diff.
- Did not verify the contradiction service's scan scope in detail (`services/contradiction_service.py` was read only for entry points).
