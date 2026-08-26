# MAF Agent Implementation — Verification Report

**Verified against:** `MAF_AGENT_MASTER_PLAN.md` Revision 2
**Tree read:** 2026-08-26, `D:\ContextEdge_pro\ContextEdge\backend\`
**Scope checked:** ContextEdge backend only. SupportCopilot untouched — confirmed, no files modified there.
**Method:** every finding re-checked against the current source, not against the plan's description of it.

---

## 0. Headline

**Substantial and largely faithful.** 6 migrations (0085–0090), 12 new modules, 7 new test files, and the agent host that did not exist before. Of the 17 numbered findings, **11 are properly fixed**, **4 are partially fixed**, and **2 are unfixed despite appearing fixed**.

Three things need attention before this can be called done:

1. **Nothing has been measured.** The golden set is one placeholder row. Every numeric exit criterion in the plan is unverified — the ranking rewrite shipped on reasoning alone.
2. **Two of four recall arms do not work.** R4 is dead (still `plainto_tsquery`); R3 carries the *highest* fusion weight (1.2) but matches `Episode.title ILIKE '%token%'` with no signature lookup and no reviewer-state filter.
3. **An access-control gate was removed.** `resolve_excluded_access_policy_ids` is now called from nowhere in the codebase.

And one subtle trap: **N3 and N4 both look fixed but are gated behind a guard that was left in place** — see GAP-1.

---

## 1. Verified fixed

| # | Finding | Evidence |
|---|---|---|
| N1 | Unpublished version served to agent | `repository._published_versions_for_playbooks` (≈line 850) prefers `current_version_id` **only when `published_at is not None`**, else newest published. Mirrors `runtime._resolve_runtime_published_version`. ✅ |
| N2 | Two risk vocabularies | `_RISK_ORDER` deleted. `hydrators.py:30` imports `risk_within_cap`; line 146 uses it. `risk_policy.PLAYBOOK_RISK_TIERS` now has 6 tiers with `restricted` at rank 5. ✅ |
| N11 | `_risk_cap` vs `_effective_max_risk_tier` | `graph/agent/service.py:28` delegates to `effective_max_risk_tier`; `runtime.py:73` does the same. One vocabulary, one cap. ✅ |
| N5 | No agent host | `api/v1/agent.py` (`POST /diagnose`, flag-gated on `settings.agent_diagnose_enabled`), `integrations/maf/runtime.py` (17.8 KB composition root), `integrations/maf/prompts.py`. Response carries `playbook_version_id`, `applicability`, `grounding_status`, `cited_node_keys`. ✅ |
| N6 | Eval measured wrong pipeline | `evaluation_service.py:10` imports `build_runtime_memory_context`; lines 152-175 pass `domain_id`, `max_risk_tier`, `caller_roles` through to `rank_playbooks`. Adds `recall_at_3`, `recall_at_10`, `mrr`, `abstain_rate`, `keyword_score_zero_rate`. ✅ |
| N7 | Feedback unjoinable | `RuntimeMatchRecord` (0088) with `uq_runtime_match_records_tenant_match`; `retrieval_feedback.playbook_version_id` + indexed `match_id` (0089). ✅ |
| N8 | Tenancy contract | 0087/0088/0090 all set `app.bypass_rls`, `ENABLE`/`FORCE ROW LEVEL SECURITY`, composite `(tenant_id, …)` FKs per 0082. ✅ |
| N10 | Tests locking the bug | `test_hybrid_ranker_negative.py` rewritten (2542 → 2019 B); `test_maf_adapter.py` updated (7412 → 8181 B). Not silently deleted. ✅ |
| G2.4 | Popularity prior dominates | Raw degree removed from `total`. `_batch_precedent_counts` counts `validated_fix` edges; enters at 0.08. `graph` survives only in the shadow linear path and the breakdown. ✅ |
| G2.6 | Freshness double-counted | `recency` weight → 0.0, `freshness` → 0.15 single term. Never-validated → **0.35**. Expired dropped at `hybrid_ranker.py:358` **and** in the gate. ✅ |
| G5.4 | N+1 | `_batch_graph_counts`, `_batch_identity_hits`, `_batch_contradiction_counts`, `_batch_precedent_counts`, `_batch_evidence_link_counts` — all `GROUP BY` over the candidate set. Per-playbook `search_evidence_semantic_for_playbook` gone. ✅ |

**Also landed and working:** version pinning (`playbook_version_id` on `RuntimeMatchResult`, `version_id` query param on `GET /runtime/playbooks/{key}`), `type_reservations={"playbook": 2, "pattern": 2}` in `MAF_V1` with a reservation pass in `selector.py:196-213`, `grounding_status` + `warnings` + `truncation_reasons` injected by the provider, `chosen_playbook_version_id` parsed from the agent's structured tail, RRF fusion, versioned calibration read-only from `ranking_calibration_configs`, and shadow mode (`_shadow_mode()` → serves linear, logs `ranking.shadow` with an agreement flag).

**Backward compatibility held.** All new response fields are optional; `match_score` / `confidence` keep their scale; `confidence_calibrated` and `selection_margin` are additive. `/diagnose` is new surface behind a flag.

---

## 2. Blocking gaps

### GAP-1 — N3 and N4 are gated behind a guard that was not removed

`services/playbook_service.py:311`:

```python
if playbook.embedding is None:
    ...
    await embed_playbook(db, playbook, approved_version)
```

`embed_playbook` itself was improved correctly — it now resolves the **published** version and writes `playbook.lexical_search_text` (`playbook_embedding.py:120-131`). But the call site still fires **only when the embedding is NULL**, and **only on the approve transition**. Nothing re-embeds when a new version is published.

Two findings therefore remain live in normal operation:

- **N3 (staleness):** a playbook embedded at v1.0.0 keeps that vector after v2.0.0 rewrites every step. The R1 arm — the plan's primary recall fix — matches stale content.
- **N4 (lexical ceiling):** `lexical_search_text` is written *by the same call*. For every playbook that already has an embedding, the column stays **NULL forever**, so R2's new `lexical_tsv` branch (`playbook_candidates.py:171-177`) matches nothing. Migration 0086 and the query exist; the data never arrives.

There *is* a repair path — `workers/playbook_tasks.backfill_playbook_embeddings` with `refresh_stale=True` re-embeds from the newest published version, batched and resumable. But it is a **manual `celery call`**, not scheduled and not on the publish path.

**Fix:** drop the `if playbook.embedding is None` guard so publish always re-embeds; or hook `embed_playbook` into `create_playbook_version`'s publish step. Then run the backfill once for history.

---

### GAP-2 — R4 (evidence arm) is dead: `plainto_tsquery` survives in `search_evidence_fts`

`pg_fts.py:54` still reads `tsquery = func.plainto_tsquery("english", query)`. The OR-composed fix was applied to `search_playbooks_fts` (line 119) but **not** to `search_evidence_fts`.

`playbook_candidates._arm_evidence:266` calls it with `frame.symptom_text` — a full symptom prose blob. `plainto_tsquery` ANDs every lexeme, so `fts_match` cannot be satisfied. The two fallbacks do not save it: `title_match` is `EvidenceItem.title.ilike('%<entire symptom text>%')` and `raw_number_match` ILIKEs the same blob against ticket numbers. **All three predicates fail for realistic input, so R4 returns `[]` on essentially every query.**

G1.1 is half-fixed. One of four arms contributes nothing, and `arm_ranks["r4_evidence"]` is permanently empty.

**Fix:** add an OR-composed path to `search_evidence_fts` (keep `plainto_tsquery` for the short-query UI callers — pass a flag or a second function), and pass `frame.lexical_terms` rather than raw prose.

---

### GAP-3 — R3 is not a signature arm, and it carries the highest weight

`DEFAULT_ARM_WEIGHTS["r3_signature"] = 1.2` — the top weight, on the reasoning that a signature→episode→playbook path is confirmed causal precedent. What `_arm_signature` actually does (`playbook_candidates.py:206-260`):

```python
tokens = [t.lower() for t in (frame.identifier_tokens + frame.lexical_terms)[:8]]
episode_q = select(Episode.id).where(
    Episode.tenant_id == tenant_id,
    or_(*(Episode.title.ilike(f"%{token}%") for token in tokens)),
).limit(80)
```

Four problems, compounding:

- **No signature lookup at all.** `IssueSignature` and `ErrorSignature` are never queried. `frame.error_signature_id` / `issue_signature_id` are ignored. The structured diagnostic index the plan built the arm around is untouched.
- **No `reviewer_state` filter.** Unapproved episode drafts can drive playbook selection — the exact discipline `repository.py:344-378` maintains with a separate discounted slot and an `[UNAPPROVED DRAFT]` label.
- **Title only.** Not `root_cause_summary`, not the episode embedding. `repository.py`'s own episode arm uses ANN; this one uses substring.
- **Eight OR'd unanchored `ILIKE`s** on `episodes` — sequential scan, no index. And `lexical_terms` are generic 4+ letter English words, so `%error%` or `%server%` matches a large fraction of titles.

Net: the highest-weighted arm is the least precise and the most expensive, and it can be driven by unreviewed drafts.

**Fix:** query `IssueSignature` / `ErrorSignature` first (reuse the `sig_tsvector` shape at `repository.py:272-302`), traverse `has_signature` → episode → `belongs_to` → pattern → `Playbook.pattern_id`, and add `Episode.reviewer_state == "approved"`.

---

### GAP-4 — Access-control regression: role-based policy exclusion removed

`resolve_excluded_access_policy_ids` is now called from **nowhere**:

```
$ grep -rn "resolve_excluded_access_policy_ids" search/ services/ api/
(no call sites — only the parameter declaration in pg_fts.py)
```

`search/access_control.py` is dead code. `rank_playbooks:303` does `del caller_roles`, so roles never reach candidate generation, and `_arm_evidence` calls `search_evidence_fts(db, tenant_id, query, limit=40)` with **no `exclude_policy_ids`**.

Legal-hold and pending-redaction are still filtered — `_visibility_predicates` applies those unconditionally. But the **role-based `access_policy_id` exclusion is gone**, which is precisely what `pg_fts.py:90-97`'s comment says must not happen: *"Retrieval surfaces return content, so they answer to the same rules."*

Currently masked by GAP-2 (R4 returns nothing anyway). **Fixing GAP-2 without fixing this activates the regression.** Fix both in the same change.

---

### GAP-5 — Nothing has been measured

`evals/datasets/playbook_selection_2026-08-26.jsonl` is 934 bytes: a comment header and **one placeholder case**, whose own `notes` field says *"Replace before treating numbers as a baseline."* The header records the Phase 0 result honestly — *"Phase 0 found 0 retrieval_feedback rows, so the set cannot be mined and must be authored."*

Consequences:

- No baseline exists, so no before/after comparison is possible.
- Every exit criterion is unverified: `recall@10` +15 (Phase 2), `top1_accuracy` +25 (Phase 5), `ECE ≤ 0.08` (Phase 5), `applicability_precision ≥ 0.85` (Phase 4).
- The calibration path is inert — `load_active_calibration` returns `None` with no fitted rows, so `confidence_calibrated` is a pass-through of the fused score and the abstain threshold (GAP-7) has nothing real to act on.
- Shadow mode is built and logs agreement, but with no labels the agreement rate cannot say which path is *better* — only that they differ.

This is the largest single risk. The plan called the golden set "the whole project's foundation"; that foundation has not been laid, so a substantial ranking rewrite is running unmeasured.

**Fix:** author ~120 cases with SMEs (Phase 0 already established they cannot be mined), split 70/30, take the baseline with shadow mode serving linear, then flip.

---

## 3. Correctness defects in the new code

### GAP-6 — Score weights sum to 1.08 and clamp, destroying the margin signal

`hybrid_ranker.py:389-398`:

```
0.55·rrf + 0.15·quality + 0.15·freshness + 0.10·apply + 0.08·precedent + 0.05·identity  =  1.08
total = max(0.0, min(1.0, total))
```

Two strong candidates both saturate at 1.0 → `selection_margin` = 0.0. The margin signal collapses **exactly where it matters most** — two equally-good candidates is the case the plan added margin to catch. `MIN_RECOMMENDATION_SCORE = 0.35` was also calibrated against the old ~1.0-sum scale.

**Fix:** normalise the coefficients to sum to 1.0, or compute the margin on the pre-clamp value.

---

### GAP-7 — Abstain is `AND` where the plan said `OR`, plus a falsy-guard bug

`hybrid_ranker.py:440-450`:

```python
if ranked[0].selection_margin < DEFAULT_MARGIN and ranked[0].confidence_calibrated:
    if ranked[0].confidence_calibrated < 0.55:
        fused_confident = []
```

- **`and ranked[0].confidence_calibrated`** — a calibrated confidence of `0.0` (or `None`) is falsy, so the abstain is **skipped for the lowest-confidence case**. Inverted guard.
- The plan specified `confidence < τ` **OR** `margin < δ`. This requires **both**. A tied pair at high confidence (margin 0.001, confidence 0.9) is served as a confident pick — the failure the margin check exists to prevent.
- Combined with GAP-6, saturated top-2 pairs have margin 0.0 and high confidence, so they always pass.

---

### GAP-8 — `evidence_count` reintroduces a query-independent prior

`_batch_evidence_link_counts` (line 497) counts **all** `PlaybookEvidenceLink` rows on a playbook's published versions — with no relation to the query. It feeds `_quality_score` where `support = min(hits/5, 1.0)`, at weight 0.15.

Any playbook with ≥5 evidence links gets full support **for every query**. That is the same class of defect as G2.4, removed from `graph` and reintroduced in `quality`.

It also **silently changes the meaning of the existing `evidence_count` response field** — previously the count of semantically matching evidence for *this* query, now a static total. That is a semantic change to a field the compatibility contract said would keep its meaning.

**Fix:** count only links whose evidence appears in R4's hit set (once GAP-2 makes R4 return anything).

---

### GAP-9 — `contradicted` is unreachable except via expiry

In `playbook_applicability.evaluate_trigger_conditions`, the only path setting `level="contradicted"` is the expiry check at lines 46-53. No trigger-condition evaluation can produce it — a mismatch yields `unvalidated`.

So the "hard drop on contradicted" only ever drops expired playbooks, which `hybrid_ranker.py:358` also drops independently. **The safety-elimination capability the plan and Gemini both specified does not exist.**

Concretely: a trigger condition requiring `os: windows` against an environment of `os: linux` produces `unvalidated` (weight 0.4, still ranked), not a drop.

**Fix:** add negative-condition handling (`not_applicable_if`, `excludes`, `requires`) and key-aware environment mismatch → `contradicted`.

---

### GAP-10 — The applicability gate rewards under-specified playbooks

Lines 93-100: `matched and not missing` → `exact`; otherwise `strong` / `partial`.

A playbook with **one** vague trigger condition, where any 4+ character token appears anywhere in the haystack, scores `exact` (weight 1.0). A playbook with **ten** precise conditions where nine match scores `strong` (0.85) — ranked *below* the vague one.

Same perverse-incentive class as G2.6's never-validated bonus. Precision in authoring is penalised.

**Fix:** score on match *ratio* with a coverage floor, not on the absence of misses.

---

### GAP-11 — Environment is consumed as an unkeyed bag of values

`playbook_applicability.py:73`: `" ".join(str(v) for v in frame.environment.values() if v is not None)`.

Keys are discarded, so `{"os": "linux"}` and `{"target_os": "linux"}` are indistinguishable, and no key-level comparison is possible. G3.2 is technically closed — `environment` is now read — but it is read as unstructured text, not as the structured applicability input the plan specified.

---

### GAP-12 — CaseFrame never resolves signatures or CIs

`build_case_frame` is **synchronous** and does zero database work. `error_signature_id`, `issue_signature_id` and `ci_entity_ids` are caller-supplied parameters, and **no caller supplies them**:

- `memory_service.py:295` passes only `symptoms`, `entities`, `context`, `domain_id`, `query_text`.
- `hybrid_ranker.py:305` passes `query_text`, `entities`, `environment`, `domain_id`.

`failing_component` / `failure_mode` come only from `environment` dict keys of those exact names. There is no call to `error_signature_service`, `issue_signature_service`, `identity_service`, or `cmdb_topology_service`, and no LLM fallback.

**What did land is real:** the two-representation split works — `symptom_text` is prose from `symptoms + context`, `lexical_terms` are OR-composed tokens, and the entity soup no longer pollutes the embedding. That was the main G1.1 fix and it is correct.

But Stage 0 is a text splitter, not a case frame — which is also *why* GAP-3's R3 arm falls back to title matching. Fixing GAP-12 and GAP-3 together is the single highest-value remaining change.

---

## 4. Coverage and hygiene

| # | Gap | Detail |
|---|---|---|
| GAP-13 | **Migration 0087 has no ORM model and no reader** | `playbook_negative_knowledge` is created with correct RLS and composite tenant FKs, but no class in `models/`, and `grep` finds no writer or reader. The NK signal is still contradiction-edge counting only. Also invisible to `test_tenant_table_coverage.py` (which iterates `Base.metadata.tables`) and may trip `test_review_orm_ddl_drift.py`. |
| GAP-14 | **No `test_playbook_applicability.py`** | The plan named it. The gate is the least-tested new component — and per GAP-9/GAP-10 it has two live logic defects a unit test would have caught. |
| GAP-15 | **ECE / Brier / applicability_precision not computed** | `evaluation_service` adds recall@3/@10, MRR, abstain rate, keyword-zero rate — but not the calibration or applicability metrics. Phase 4 and 5 exit criteria are unmeasurable even once a golden set exists. No `generation_provenance` segmentation either, so the hand-authored population (G3.5) stays hidden inside the average. |
| GAP-16 | **R1/R2 apply `LIMIT` before the domain/risk post-filter** | `_arm_embedding` / `_arm_lexical` take the global top-50, then `_eligible()` filters. For a domain-scoped service token the top-50 may contain zero eligible rows → the arm returns empty. Same class as the tenant post-filter problem `vector_ops.py:24-32` documents. Oversample (≈3×) before post-filtering. |
| GAP-17 | **`node_is_visible` still requires `current_version_id is not None`** | `hydrators.py:141`. A playbook with published versions but a NULL pointer stays invisible to the agent while `/runtime/match` ranks it. One divergence row from the plan's table survives. |
| GAP-18 | **`_batch_graph_counts` runs 2 queries per request for a shadow-only value** | `graph` is no longer in `total`; it appears only in `_legacy_linear_score` and the breakdown. Skip the queries when shadow mode is off. |
| GAP-19 | **`search_playbooks_fts` may now be orphaned** | `hybrid_ranker` no longer imports it. Confirm remaining callers or remove. |

---

## 5. What to do next, in order

1. **GAP-1** — remove the `if playbook.embedding is None` guard; re-embed on every publish. Then run `backfill_playbook_embeddings(refresh_stale=True)` once. *Without this, R1 matches stale text and R2's new lexical column is NULL for the entire existing corpus — two of the plan's headline fixes are inert.*
2. **GAP-2 + GAP-4 together** — OR-composed tsquery for `search_evidence_fts`, and thread `caller_roles` → `exclude_policy_ids` back through candidate generation. Fixing the first without the second activates an access-control hole.
3. **GAP-12 + GAP-3** — make `build_case_frame` async and resolve signatures/CIs; rebuild R3 on `IssueSignature` → `has_signature` → episode (approved only) → pattern → playbook.
4. **GAP-6 + GAP-7** — normalise coefficients to 1.0; change abstain to `OR` and drop the falsy guard.
5. **GAP-9 + GAP-10 + GAP-14** — negative conditions and key-aware environment matching; ratio-based scoring; write the unit tests.
6. **GAP-5** — author the golden set, take the baseline in shadow mode, then flip. Until this exists, none of the above can be confirmed as an improvement rather than a change.
7. **GAP-8, GAP-13, GAP-15..19** — cleanup, in any order.

---

## 6. Summary table

| Finding | Status |
|---|---|
| N1 unpublished version to agent | ✅ Fixed |
| N2 risk vocabularies | ✅ Fixed |
| N3 embedding staleness | ⚠️ **Guard not removed** (GAP-1); manual backfill exists |
| N4 lexical recall ceiling | ⚠️ **Column never populated** (GAP-1) |
| N5 no agent host | ✅ Fixed |
| N6 eval measures wrong pipeline | ✅ Fixed |
| N7 feedback unjoinable | ✅ Fixed |
| N8 tenancy contract | ✅ Fixed (except GAP-13's missing ORM) |
| N10 tests locking bugs | ✅ Fixed |
| N11 risk cap divergence | ✅ Fixed |
| N12 unused version content | ⚠️ Partial — `conflicts` used; `verification_policy`, `generation_provenance` still unused |
| G1.1 term-soup / tsquery | ⚠️ Partial — playbook FTS fixed, **evidence FTS not** (GAP-2) |
| G2.4 popularity prior | ⚠️ Partial — removed from `graph`, **reintroduced in `quality`** (GAP-8) |
| G2.5 negative penalty | ⚠️ Partial — domain-wide constant gone; NK link table unused (GAP-13) |
| G2.6 freshness | ✅ Fixed |
| G2.7 uncalibrated confidence | ⚠️ Partial — plumbing built, **abstain logic wrong** (GAP-7), unfitted (GAP-5) |
| G3.1 trigger conditions | ⚠️ Partial — evaluated, but `contradicted` unreachable (GAP-9) and scoring perverse (GAP-10) |
| G3.2 environment | ⚠️ Partial — read, but unkeyed (GAP-11) |
| G3.3 version pinning | ✅ Fixed |
| G3.4 playbook embedding unused | ✅ Fixed (subject to GAP-1) |
| G3.5 hand-authored unrankable | ✅ Fixed by R1 (subject to GAP-1) |
| G4.1 no type quota | ✅ Fixed |
| G4.2 truncation order | ✅ Fixed |
| G4.3 agent cannot fetch playbook | ✅ Fixed — `get_playbook` with required `version_id` |
| G5.1 silent grounding failure | ✅ Fixed |
| G5.2 false provenance | ✅ Fixed |
| G5.3 loop never closes | ✅ Fixed (inert until GAP-5) |
| G5.4 N+1 | ✅ Fixed |

**11 fixed cleanly · 4 partial · 2 blocked by one un-removed guard · 19 new gaps, 5 blocking.**
