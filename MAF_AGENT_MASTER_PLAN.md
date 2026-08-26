# MAF Agent — Logical Gap Analysis & Master Creation/Implementation Plan

**Revision 2** — second pass, after deep-reading `hydrators.py`, `repository.py` (hydration path), `evaluation_service.py`, `vector_ops.py`, the migration chain, and the test suite. Revision 1's §2 findings survive; three claims were **corrected** and **twelve new findings** were added, two of which change the shape of the plan.

**Scope:** `D:\ContextEdge_pro\ContextEdge\backend\src\contextedge\` only.
**Explicitly out of scope:** `D:\ContextEdge_pro\SupportCopilot\` — no code changes. Every existing API contract stays backward compatible (additive fields only).
**Reference input:** `playbook_selection_analysis.md` (Gemini). Validated in §1 — partially agreed, partially corrected.
**Analysis date:** 2026-08-26. Line numbers are against the tree as read on that date. Latest migration on disk: **0084**.

---

## 0. Executive summary

The accuracy problem is not one bug. It is **three separate failures stacked on each other:**

1. **Arithmetic.** The ranker's semantic signal is compressed into a ~0.1-wide band, its lexical signal is structurally zero for realistic queries, and a query-*independent* popularity prior carries 15% of the weight. (§2, G1–G2)
2. **Divergence.** Two retrieval systems answer the same question with different rules — different tsquery semantics, different risk vocabularies, different version-resolution, different filters. The agent reads one; selection happens in the other. (§2 table, N1, N2, N11)
3. **Absence.** There is **no MAF agent host in the repository at all.** `ContextGraphMAFPlugin` is a library nothing instantiates outside its own tests; `api/v1/copilot/` is an empty directory. (§3, N5)

**The two findings that most change what to build:**

- **N1 — the graph projection serves steps and trigger conditions from an *unpublished* playbook version.** `repository.py:904` resolves `current_version_id` with no `published_at` check, and `create_playbook_version` repoints that pointer before review. This is both the concrete mechanism behind "incorrect details are being retrieved" and a governance hole, in a codebase that is otherwise scrupulous about exactly this (it refuses pending AI decisions so "agent output launders itself into agent input" — `hydrators.py:176-180`).
- **N5 — the agent you want to improve does not exist yet.** Gemini's diagram labels a box `maf_runtime.py / agent.run`; that file is not in the tree. The plan therefore needs a phase that builds the composition root and its endpoint, and an honest statement that this is **new surface** SupportCopilot adopts later.

**Cheapest large wins, in order:** N2 (one dict), N3 (one call site), N1 (one WHERE clause), G1.1 (one tsquery function). Roughly forty lines, all four ahead of any architectural work — see **Phase 0.5**.

### The two systems, side by side

| | System A — `/api/v1/runtime/match` | System B — `/api/v1/graph/agent-subsets` |
|---|---|---|
| Entry point | `api/v1/runtime.py:89` | `graph/agent/service.py:108` |
| Ranking | `search/hybrid_ranker.py::rank_playbooks` | `graph/agent/selector.py::select` |
| Lexical query | `plainto_tsquery` — **ANDs every term** | `websearch_to_tsquery` — OR-composed ✅ |
| Uses `Playbook.embedding` | ❌ never | ✅ `repository.py:389-405` |
| Version resolved | newest **published** | **`current_version_id`, unpublished included** ❌ |
| Risk vocabulary | `RISK_RANK` (5 tiers incl. `minimal`/`critical`) | `_RISK_ORDER` (4 tiers incl. `restricted`) ❌ |
| Admin risk cap | `None` (uncapped) | `"high"` ❌ |
| Expired playbooks | scored 0.0, **still returned** | excluded ✅ |
| `current_version_id IS NULL` | rankable | **invisible** ❌ |
| Uses `trigger_conditions` | ❌ never | rendered as text only, never evaluated |
| Uses `environment` | ❌ accepted then discarded | ❌ not passed |
| Consumed by MAF agent | ❌ **not wired at all** | ✅ `integrations/maf/provider.py` |

Nine rows disagree. Each disagreement is a case where the agent's context and the selection endpoint tell a different story about the same playbook.

---

## 1. Validation of the Gemini analysis

**Right:**

- ✅ No playbook retrieval tool exists. `plugin.py:71-86` registers `query_context_graph`, `cmdb_topology`, `assess_change_risk`, `assess_fix_applicability`, `get_cohort_shared_attributes`, `propose_dependency` — nothing that fetches a playbook version.
- ✅ `trigger_conditions` is never *evaluated*. (Revision 1 said it was never *read*; corrected — it is rendered into node facts. See C1.)
- ✅ A flat arithmetic threshold on `match_score` is the wrong gate — and worse than described (G2.7).
- ✅ Decision provenance is weak, and actively harmful (G5.2).

**Wrong, and it matters:**

1. **"Graph retrieval and playbook matching must NOT live outside MAF."** Moving *ranking* into the LLM loop trades a deterministic, testable, auditable function for a non-reproducible one — no regression tests, no calibrated confidence, no explanation to an auditor. The correct split is **deterministic retrieval + deterministic applicability gate + agent adjudication over a small candidate set.** The agent resolves genuine ambiguity and explains; it does not compute scores.

2. **`insights.py` is in SupportCopilot**, which we are not changing. The fix lands server-side, behind existing endpoints. That constraint shapes the whole plan.

3. **The comparison table blames architecture; the cause is mostly arithmetic.** `1 - distance/2` maps every real embedding distance into `[0.70, 0.97]` (G2.1). Re-hosting that function inside MAF changes nothing.

4. **It assumes a runtime that does not exist.** The `maf_runtime.py / agent.run` box in the diagram has no counterpart in the tree (N5). Everything downstream of that box is a design for software nobody has written yet — which is fine, but it needs to be planned as construction, not as refactoring.

5. **Dynamic traversal is offered as the fix for ambiguity.** It is Phase 5, not Phase 1. An agent traversing a graph that already truncated the playbook out (G4.1) explores harder and still cannot see the answer.

**Net:** adopt Gemini's component inventory. Reject its placement of ranking. Invert its sequence.

---

## 2. Logical gaps — evidence and consequence

> Revision 1 findings **G1–G5** are unchanged except where a **[CORRECTED]** tag appears. New findings from the second pass are **N1–N12**.

### G1 — Query construction

**G1.1 — `query_text` is a term-soup, then ANDed.**
`services/memory_service.py:230-240` builds one flat string from `symptoms + entities + context + session.symptoms + session.entities + session.notes + identity.canonical_name`. It goes to two places:

- `search/pg_fts.py:95` → `plainto_tsquery("english", query)`, which **ANDs every lexeme**. A 10–60 token query is satisfiable by essentially no playbook, so `fts_scores` (`hybrid_ranker.py:264-269`) is **empty for realistic input** and `keyword_score` is 0.0 for every candidate.
- `hybrid_ranker.py:277` → one embedding of that soup. Hostnames, ticket numbers and person names dominate the vector norm; the symptom semantics dilute.

Already found and fixed on the graph side. `graph/agent/repository.py:190-207` carries the comment: *"plainto_tsquery over the raw window would AND every lexeme of a multi-message conversation, which no playbook can ever satisfy."* Never back-ported to the ranker.

**G1.2 — The MAF graph query is the tail of the transcript.**
`integrations/maf/provider.py:59-71` builds the query from `"\n".join(last 4 messages)` truncated to the **last** 4,000 chars. `get_messages(include_input=True)` includes assistant turns, so as the conversation grows retrieval re-anchors on the model's own previous answer. A wrong first pick becomes self-reinforcing.

**G1.3 — The provider discards every structured input it has.**
`provider.py:41`: `AgentGraphRequest(query=query, profile="maf.v1")` — no `seeds`, `entities`, `session_id`, `budget`, or `max_depth`. So Layer C identifier matching fires only on regex hits in prose; session seeding (`repository.py:178-185`) never fires; and the budget defaults to `max_nodes=24, max_depth=2, max_characters=12_000` (`contracts.py:26-30`) while `MAF_V1` permits `60 / 3 / 30_000` (`profiles.py:196-201`). **The MAF path runs at 40% of its own available budget.**

### G2 — Scoring arithmetic

**G2.1 — The semantic signal has no dynamic range. (Primary arithmetic defect.)**
`hybrid_ranker.py:45-54`:

```python
best = min(distances)
score = max(0.0, 1.0 - (best / 2.0))
```

`/2.0` assumes cosine distance spans `[0, 2]`. Real sentence embeddings over same-domain operational text land in roughly `[0.05, 0.6]`. Every candidate — correct or not — scores **0.70 to 0.97**. The correct playbook and an unrelated one differ by ~0.1 on the signal carrying 30% of the weight, which is below the noise from G2.4.

**G2.2 — Semantic is then multiplied by a signal that is always zero.**
`hybrid_ranker.py:330`: `semantic_score = min(1.0, semantic_pb * (0.6 + 0.4 * keyword_score))`. With `keyword_score = 0` from G1.1, this multiplies every candidate by a uniform 0.6 — no discrimination, just shrinkage of the one signal still working.

**G2.3 — Lexical score is relative, with no absolute floor.**
`hybrid_ranker.py:266-269`: `fts_scores[id] = rank / max_rank`. The best FTS hit always gets 1.0 however weak. On the rare query where FTS fires on one stemmed word, that playbook collects the full 0.25 weight on a single shared token.

**G2.4 — The graph signal is a query-independent popularity prior.**
`hybrid_ranker.py:65-77` counts *all* `GraphEdge` rows touching the playbook: `graph_count_score = min(1.0, n / 5.0)`. Any playbook with ≥5 edges scores 1.0 **for every query ever issued.** At weight 0.15, with semantic flattened by G2.1/G2.2, **graph hub-count becomes the dominant discriminator.** This is the direct mechanism behind "the same wrong playbooks keep getting selected."

**G2.5 — The negative penalty is a domain-wide constant that inverts the intended bias.**
`hybrid_ranker.py:155-163` counts every `NegativeKnowledgeItem` row in the domain, with **no join to the playbook**. Three failures:

- Constant per domain → cannot discriminate.
- Called at line 325-327 with `pb.domain_id`, not the request's `domain_id` (which the other two helpers receive). **Tenant-wide playbooks (`domain_id IS NULL`) always score penalty 0; domain-scoped playbooks are penalised up to 1.0.** With ≥10 negative items in a domain, every domain-specific playbook loses the full 0.05 and generic tenant-wide ones lose nothing. **The safety code produces a systematic bias toward generic playbooks.**
- `min(1.0, contradiction_count * 0.3 + nk_count * 0.1)` saturates at 10 items, which any mature domain exceeds.

**G2.6 — Freshness is double-counted and rewards never-validated playbooks.**
`hybrid_ranker.py:333-334`: `recency_score = freshness`, both weighted (`0.10 + 0.05`) — 15% of the score is one quantity counted twice. `_compute_freshness` (382-389) returns **0.5 for a never-validated playbook** and **0.0 for one validated 181 days ago.** A freshly imported, never-used playbook beats a proven-but-stale one by 0.075 absolute. Expired playbooks return 0.0 and stay in the result set.

**G2.7 — `confidence` is an uncalibrated sum, and the abstain threshold sits inside the noise band.**
`hybrid_ranker.py:349-353` sets `confidence = total`, surfaced to SupportCopilot as both `match_score` and `confidence` (`runtime.py:150-151`). Two absolute cut-offs act on it: `MIN_RECOMMENDATION_SCORE = 0.35` (line 171) and `runtime.py:163`'s `< 0.3`. Given the compression above, realistic totals cluster around **0.30–0.55** — the thresholds sit *inside* the cluster, so results flip between "confident" and "abstain" on noise. No margin check either: two candidates 0.001 apart are reported as a confident pick.

### G3 — Grounding and applicability

**G3.1 — `trigger_conditions` is never evaluated.** Defined at `models/playbook.py:147`, used for embedding text (`playbook_embedding.py:66`), rendered as flattened strings into node facts (`hydrators.py:249-251`) — and **evaluated by nothing.** No code asks whether the selected playbook applies to this ticket.

**G3.2 — `environment` is accepted and silently discarded.** `schemas/playbook.py:279` declares it; `runtime.py:224` echoes it into the event payload and Redis; it is **never passed to `rank_playbooks`.** The one structured applicability input the API accepts is dropped.

**G3.3 — Version divergence.** See **N1**, which supersedes and sharpens this with a third, worse rule.

**G3.4 — `Playbook.embedding` is maintained and unused by the ranker.** `services/playbook_embedding.py` composes it from title + description + trigger conditions + step titles — exactly the symptom vocabulary engineers type. `repository.py:389-405` uses it for graph seeding. `rank_playbooks` **never touches it.** (And see **N3** — it is also stale.)

**G3.5 — The ranker's semantic signal is unavailable for hand-authored playbooks.** `hybrid_ranker.py:299` calls `search_evidence_semantic_for_playbook`, which joins through `PlaybookEvidenceLink`. Those rows are written **only** by `_materialize_evidence_links` from AI-generated `evidence_refs` at version creation (`playbook_service.py:416-424`). Hand-authored, imported or seeded playbooks have zero links → `semantic = 0`, `evidence_hits = 0`, `quality = 0.6 × confidence`. `vector_search.py:120-138` already logs this exact condition. **Hand-written playbooks are structurally unrankable.**

### G4 — Projection budget

**G4.1 — No per-type quota; playbooks lose the budget race by construction.**
`selector.py:159-192` admits nodes by descending relevance, requiring each node's whole ancestor chain to fit (`chain_for`, 149-157). A playbook at 2 hops (`episode → pattern → playbook`) needs **3 slots** at ~0.56–0.67 relevance even with the deliberate `belongs_to` / `derived_from` 1.2 boosts (`profiles.py:213-220`). Single-hop evidence nodes sit at ~0.70–0.90 and cost **1 slot**. At the provider's default `max_nodes = 24` (G1.3), evidence and episodes fill the budget and **the playbook is truncated out.**

**The author believed a quota existed.** `hydrators.py:196` reads: *"the playbook budget in maf.v1 is 2 nodes, so worst case is ~2 bounded step lists per projection."* **No such per-type budget exists in the code** — grep across `graph/agent/` finds nothing. So `playbook_version_facts`' character sizing (15 steps × 200 chars ≈ 3,200 chars per playbook node) was calibrated against a cap that was never implemented. At the provider's 12,000-char default, four playbook nodes would consume the entire budget; in practice higher-ranked evidence takes it first. Either way the behaviour is unpredictable and nobody intended it.

**G4.2 — Truncation does not preserve order.** `selector.py:186-189`: a node whose chain exceeds the character budget is skipped with `continue` and the loop proceeds, so *lower*-relevance smaller nodes are admitted after a *higher*-relevance one was rejected. The emitted set is not the top-N by relevance, and nothing records that this happened.

**G4.3 [CORRECTED] — The agent does get steps, but capped, flattened, and from the wrong version.**
Revision 1 claimed playbook content never reaches the agent. **That was wrong.** `hydrators.playbook_version_facts` (193-253), called from `repository.py:917-937`, renders `semantic_version`, `steps_total`, up to **15** step labels at **200** chars each, `trigger_conditions` flattened to a **600**-char budget, and `rollback_notes` at **300** chars into the node's `facts`.

The real defects are narrower and still serious:

- **Wrong version** — see N1. This is the important one.
- **Silent truncation** — a 40-step runbook shows 15 steps and `steps_total: 40`. The agent can see the count mismatch, but has **no tool to fetch the rest** (`plugin.py:71-86`).
- **Labels, not steps** — `step.get("title") or ("text") or ("action") or ("instruction")`. `inputs`, `outputs_schema`, `safety_class`, `requires_approval`, `reversible`, `rollback_hint`, `verification`, `tool_ref` (all defined on `PlaybookStep`, `schemas/playbook.py:47-77`) are dropped. The agent cannot tell an approval-gated destructive step from a read-only check.
- **`trigger_conditions` arrive as flattened prose**, order-dependent and truncated at 600 chars — usable as a hint, not as a specification the agent can verify against.

So the tool gap is real, just different from what Revision 1 said: the agent needs a way to fetch the **full, correct, structured** version — not a way to see a playbook at all.

### G5 — Silence, provenance, cost

**G5.1 — Retrieval failure is silent, and the warnings are stripped.** `provider.py:81-82`: `if not subset.nodes: return`. Zero grounding is indistinguishable from "no provider configured." And `provider.py:92-98` explicitly excludes `warnings` and `truncation_reasons` from the injected payload — so `"No authorized graph seeds were resolved."` (`selector.py:238`) and `max_nodes` truncation **never reach the model.** The agent cannot know it is ungrounded.

**G5.2 — The decision record asserts provenance that did not happen.** `provider.py:85-89` stores the projection's first 40 nodes as `cited_nodes`; `after_run` (150-160) writes each as an `evidence_ref` described as *"cited in the projection that informed this run."* Those are nodes that were **offered**, not **used**. The flywheel trains on noise, and audit answers "what informed this diagnosis?" with a mostly-wrong list.

**G5.3 — The loop never closes.** `after_run` records a rationale string, not which playbook was selected, its version, or the outcome. `RetrievalFeedback` (`runtime.py:357`) is the only labelled signal and **nothing consumes it.** (And see **N7** — it also cannot be joined to what was shown.)

**G5.4 — Cost hides the accuracy problem.** `rank_playbooks` loops over **every approved playbook** (line 288), and per playbook issues `search_evidence_semantic_for_playbook` (its own `tune_ann_recall` + an ANN oversample of 80–240 chunks), `_graph_score_for_playbook` (1–2 queries), `_identity_score_for_playbook`, and `_negative_penalty_for_playbook`. At 200 approved playbooks: **~800 round trips and 200 ANN scans per ticket.** Operators respond by keeping `top_k` low, suppressing recall — so the latency problem manufactures an accuracy problem on top of the arithmetic one.

---

### N — Second-pass findings

**N1 — The graph projection serves an UNPUBLISHED playbook version. (Governance hole + the concrete "wrong details" mechanism.)**

`graph/agent/repository.py:904-905`:

```python
version_ids = [row.current_version_id for row in rows if row.current_version_id]
```

No `published_at.is_not(None)` filter. `node_is_visible` (`hydrators.py:143-149`) checks `lifecycle_state == "approved"` on the **Playbook** row and `current_version_id is not None` — never the version's publish state. `create_playbook_version` repoints `current_version_id` **immediately, before review** (documented in `playbook_embedding.py`'s module docstring and `models/playbook.py`'s `embedding` comment).

**So an approved playbook carrying a fresh, unreviewed draft projects that draft's steps and trigger conditions straight to the agent.**

This is precisely the discipline applied everywhere else and missed here. `hydrators.py:176-180` refuses pending AI-authored decisions *"otherwise agent output launders itself into agent input."* `repository.py:344-378` gives unapproved episode drafts a separate, smaller, relevance-discounted slot and labels every one `[UNAPPROVED DRAFT]`. Playbook versions got neither guard.

**Three different version-resolution rules in three places:**

| Surface | Version resolved | Publish-state check |
|---|---|---|
| `rank_playbooks` (`hybrid_ranker.py:183-210`) | newest **published** | ✅ |
| `GET /runtime/playbooks/{key}` (`runtime.py:55-73`) | `current_version_id` if published, else newest published | ✅ |
| Graph projection (`repository.py:904`) | **`current_version_id`, whatever its state** | ❌ |

The score comes from one row, the chat context from another, and the fetched steps from a third.

**N2 — Two incompatible risk vocabularies silently hide whole tiers from the agent.**

- `hydrators.py:56` — `_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "restricted": 3}`
- `search/risk_policy.py` — `RISK_RANK = {"minimal": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}`

Different members **and** different ordering. `hydrators.py:148` uses `_RISK_ORDER.get(obj.risk_tier, 99)`, so a playbook whose `risk_tier` is `"critical"` **or** `"minimal"` scores **99** and is **excluded from every graph projection, for every principal, silently.** `risk_within_cap` handles both fine, so `/runtime/match` returns them.

Compounding: `scope.playbook_risk_cap` maxes at `"high"` (=2) because `service.py::_risk_cap` returns only `"high"` or `"medium"`, so `"restricted"` (3) is also unreachable. **Net: the MAF agent can only ever see low/medium/high playbooks.** `Playbook.risk_tier` is a bare `String(20)` with no enum or CHECK constraint, so nothing prevents the other values from existing.

**N3 — Playbook embeddings are written once and never refreshed.**

`embed_playbook` has exactly two references in the tree: its definition, and `playbook_service.py:319-321` — which runs **only `if playbook.embedding is None`**, on the approve transition, as an explicit repair path for pre-0035 rows. It is **not called from `create_playbook_version`.**

So a playbook embedded at v1.0.0 keeps that vector forever, even after v2.0.0 rewrites every step and trigger condition. The graph semantic seed layer (`repository.py:389-405`) and the R1 arm this plan adds both match **stale content**. `models/playbook.py`'s own comment describes the embedding as the *latest-created* version's content — a behaviour the code does not implement.

**N4 — `Playbook.search_tsvector` structurally cannot contain step or trigger text.**

It is `Computed("to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,''))", persisted=True)`. Migration 0035 documents why it was not extended: *"generated columns cannot reference other tables, and trigger conditions live on playbook_versions."*

So fixing the tsquery (Phase 1) raises **precision** but leaves a hard **recall ceiling** — no lexical query can ever reach a playbook through its steps. A denormalized, maintained lexical column is required, populated by the same refresh hook that fixes N3.

**N5 — There is no MAF agent host. Anywhere.**

`grep -rn "AgentSession|ChatAgent|agent\.run|maf_runtime"` across `src/` and `tests/` returns **only the plugin class and `tests/test_maf_adapter.py`**. `api/v1/copilot/` is an **empty directory**. `api/v1/__init__.py` registers 35 routers; none runs an agent turn. `ContextGraphMAFPlugin` is a library that nothing in the repository instantiates outside its own tests.

Gemini's diagram labels a box `maf_runtime.py / agent.run`. **That file does not exist.** Today the only live consumer of ContextEdge's retrieval is SupportCopilot's own Vertex AI chat, calling the HTTP endpoints directly — and SupportCopilot is out of scope.

**Consequence for the plan:** a phase must build the composition root and its endpoint (**Phase 5A**), and the plan must state plainly that this is **new surface shipped behind a new endpoint**, adopted by SupportCopilot in a later, separately-scoped piece of work.

**N6 — The existing eval measures a different pipeline than production.**

`services/evaluation_service.py:131`:

```python
query = " ".join(symptoms + entities + ([ctx] if ctx else []))
```

It reconstructs the term-soup itself and **bypasses `build_runtime_memory_context` entirely.** It also passes no `domain_id`, no `max_risk_tier`, and no `caller_roles` to `rank_playbooks`, so it never exercises the RBAC and domain filters `/runtime/match` applies at `runtime.py:119-140`.

Two consequences: the harness **cannot detect G1.1**, because it contains the same bug; and a playbook production would filter out can win in the eval. **Any baseline taken with it today measures a pipeline no user ever hits.** Fixing the harness is a prerequisite for every numeric gate in this plan.

**N7 — Feedback cannot be joined to what was shown.**

`RetrievalFeedback` (`models/evaluation.py:42-55`) has `match_id: String(255)`, nullable, **no FK, no index**, and `playbook_id` but **no `playbook_version_id`**. The only record of what a `match_id` returned lives in Redis at `runtime:match:{id}` with `MATCH_CACHE_TTL_SEC = 3600` (`runtime.py:29`) — and `playbook_service._invalidate_runtime_match_cache` **SCAN-deletes those keys on any lifecycle transition.**

So feedback submitted more than an hour later, or after any playbook approval, points at a match whose content is gone. **Phase 6's flywheel is impossible without durable match persistence** — an assumption Revision 1 left silent.

**N8 — New tables must satisfy an enforced tenancy contract.**

Migrations 0077–0084 are tenant-isolation and RLS work. `tests/test_tenant_table_coverage.py` asserts that **every** mapped table (except `tenants`, `role_nav_access`, `entity_classes`) has a **non-nullable `tenant_id` with an FK to `tenants` ON DELETE CASCADE**. 0078/0079/0082 add RLS policies, composite tenant FKs and a trigger. `test_orm_migration_column_parity.py` and `test_review_orm_ddl_drift.py` guard ORM↔DDL drift.

Every new table this plan proposes must comply or CI fails. **Next migration number: 0085.**

**N9 — ANN queries have a mandatory shape.**

`search/vector_ops.py`: 3072-dim vectors cannot use a plain HNSW index (pgvector's limit is 2000), so 0032/0035 build **expression** indexes over `(embedding::halfvec(3072))`. Therefore every new ANN query **must** order by `halfvec_cosine_distance(...)` — a raw `.cosine_distance()` is, in the module's own words, *"a guaranteed sequential scan"* — and **must** call `tune_ann_recall(db)` first, because the indexes are global across tenants and the default `ef_search=40` can return zero rows for a small tenant after the tenant post-filter.

Good news: `ix_playbooks_embedding_halfvec_hnsw` exists (0035), so the R1 arm is indexed from day one.

**N10 — Tests that lock the current behaviour will break, deliberately.**

`tests/test_hybrid_ranker_negative.py` contains five tests pinning the **exact buggy semantics** — `test_negative_penalty_caps_at_one` asserts 10 domain-wide NK items → penalty 1.0; `test_negative_penalty_no_domain_skips_nk` asserts the tenant-wide skip and `await_count == 1`. These are regression locks **on the defect**.

`tests/test_maf_adapter.py` has ten, including `test_provider_injects_attributed_context`, `test_provider_truncates_long_conversations_instead_of_dropping_context`, and `test_provider_fences_untrusted_graph_content`. The provider rewrite must **preserve** the fencing and attribution these lock, while replacing the query-construction the truncation test asserts.

Any plan that does not name these ends with someone deleting a test to make CI green.

**N11 — `_effective_max_risk_tier` and `_risk_cap` disagree.**
`runtime.py:42-52` returns `None` (uncapped) for platform/tenant/domain admins; `graph/agent/service.py:27-36` returns `"high"` for the same principals. Combined with N2, an admin can be recommended a `critical`-tier playbook by `/match` that their agent context can never contain.

**N12 — Rich version content nothing consumes.**
`PlaybookVersion` carries `conflicts` (0050 — where approved KB/SOP and observed practice disagree; NULL means *not assessed*, deliberately distinct from empty), `verification_policy`, `execution_confidence_guidance`, `branching_logic`, `inputs`/`outputs`, and `generation_provenance` (NULL on hand-authored). None reaches the agent or the ranker.

Two are immediately useful: **`conflicts`** is a ready-made applicability and safety signal for Stage 2, and **`generation_provenance`** is exactly the field needed to segment eval recall by AI-generated vs hand-authored — the G3.5 population.

---

## 3. Target architecture

Deterministic where determinism is cheap and testable; agent where judgement is genuinely required.

```
Ticket / conversation
        │
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 0  CASE FRAME  (deterministic + 1 cheap LLM)    │
   │  error_signature · failing_component · failure_mode   │
   │  CIs · environment · symptom_text · identifier_tokens │
   └────┬──────────────────────────────────────────────────┘
        │  two representations, never mixed:
        │  lexical_terms (OR-composed)  |  symptom_text (embedded alone)
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 1  CANDIDATE GENERATION  (4 recall arms, union) │
   │  R1 Playbook.embedding ANN      R3 signature→episode  │
   │  R2 websearch_to_tsquery        R4 evidence→link      │
   │  → ≤60 candidates; ALL later signals computed BATCHED │
   └────┬──────────────────────────────────────────────────┘
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 2  APPLICABILITY GATE  (deterministic, hard)    │
   │  trigger_conditions × case_frame → verdict            │
   │  + version.conflicts (N12)                            │
   │  contradicted → DROP · expired → DROP · else flag     │
   └────┬──────────────────────────────────────────────────┘
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 3  RRF FUSION + CALIBRATION                     │
   │  rank-based fusion (scale-free) → calibrated conf.    │
   │  abstain on low confidence OR low top1−top2 margin    │
   └────┬──────────────────────────────────────────────────┘
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 4  AGENT HOST  ← NEW SURFACE (N5)               │
   │  composition root + POST /api/v1/agent/diagnose       │
   │  provider: case-frame request, full budget,           │
   │            grounding_status ALWAYS injected           │
   │  tools:    match_playbooks · get_playbook (version-   │
   │            pinned, full, structured) ·                │
   │            check_trigger_conditions ·                 │
   │            get_negative_knowledge                     │
   │  after_run: agent-DECLARED citations + chosen version │
   └────┬──────────────────────────────────────────────────┘
   ┌────▼──────────────────────────────────────────────────┐
   │ Stage 5  EVAL + SHADOW  (harness fixed FIRST)         │
   │  Recall@k · MRR · top-1 · applicability precision ·   │
   │  abstain rate · ECE / Brier · shadow-mode diff        │
   └───────────────────────────────────────────────────────┘
```

**Why RRF rather than tuning the linear weights.** Reciprocal Rank Fusion combines *rank positions*, not scores. It is immune to the entire class of bug in G2.1–G2.4 — a signal with a compressed range or a wrong scale contributes its ordering and can no longer flatten or dominate the sum. Weight-tuning the current function would need re-tuning every time a signal's distribution shifts; RRF does not.

---

## 4. Implementation plan

### Phase 0 — Data readiness audit (est. 2 days) — **GO/NO-GO GATE**

Read-only. Nothing else starts first. Several later phases are only worth building if the data supports them, and Revision 1 buried this in a risk bullet.

| Question | Query against | Go threshold |
|---|---|---|
| Do published versions have usable `trigger_conditions`? | `playbook_versions` where `published_at NOT NULL` | ≥30% non-empty → build Stage 2. Below → **defer Phase 3**, invest in authoring tooling instead |
| How many playbooks have zero `PlaybookEvidenceLink` rows? | `playbooks` ⟕ `playbook_evidence_links` | quantifies the G3.5 population; also sizes R4's coverage |
| How stale are embeddings? | `playbooks.embedding NOT NULL` vs newest published version's `created_at` | any drift → N3 confirmed on live data; sizes the backfill |
| What `risk_tier` values actually exist? | `SELECT DISTINCT risk_tier, count(*) FROM playbooks` | any `minimal` / `critical` / `restricted` → N2 is live, not theoretical |
| How many approved playbooks have an **unpublished** `current_version_id`? | `playbooks` ⟕ `playbook_versions` | sizes N1's blast radius |
| How many have `current_version_id IS NULL`? | `playbooks` | these are invisible to the agent today |
| Is `NegativeKnowledgeItem` linkable to playbooks at all? | `negative_knowledge_items.evidence_refs`, `step_text` | decides Phase 2.3's approach |
| How much labelled feedback exists? | `retrieval_feedback` joined to anything | decides whether the golden set can be mined or must be authored |

**Deliverable:** a one-page audit with counts, and an explicit go/no-go on Phases 3 and 6.

---

### Phase 0.5 — Correctness patches (est. 2–3 days) — **highest value per line in the entire plan**

Four independent fixes, each small, each with an immediate accuracy or governance effect. Ship them individually so each can be reverted alone.

| # | Fix | File | Size |
|---|---|---|---|
| 1 | **N2** — replace `_RISK_ORDER` with `risk_policy.playbook_risk_rank`; make `_risk_cap` and `_effective_max_risk_tier` agree (N11) | `graph/agent/hydrators.py:56,148`; `graph/agent/service.py:27-36` | ~10 lines |
| 2 | **N1** — filter version hydration to `published_at IS NOT NULL`; fall back to newest published, mirroring `runtime.py:55-73` | `graph/agent/repository.py:900-916` | ~8 lines |
| 3 | **N3** — call `embed_playbook` on publish, not only when `embedding IS NULL`; embed the **published** version | `services/playbook_service.py:319-321`; `services/playbook_embedding.py:79-95` | ~6 lines + backfill task |
| 4 | **G1.1** — `plainto_tsquery` → OR-composed `websearch_to_tsquery`, reusing the proven shape at `repository.py:196-207` | `search/pg_fts.py:88-109` | ~12 lines |

Add a `CHECK` constraint or enum on `Playbook.risk_tier` (migration **0085**) so N2 cannot recur. Backfill for fix 3: a one-off Celery task re-embedding every playbook whose embedding predates its newest published version — batched, budget-attributed, resumable.

**Exit criterion:** N1/N2 no longer reproducible on a seeded fixture; embedding backfill complete; `keyword_score == 0` rate measurably down (measured properly after Phase 1's harness fix — see below).

---

### Phase 1 — Fix the eval harness, then measure (est. 3–4 days)

Revision 1 put measurement first, which was right, but proposed a **new** file-based harness. **Corrected:** a DB-backed playbook-ranking eval already exists and must be extended — `models/evaluation.py` (`EvaluationDataset`, `EvaluationRun`), `services/evaluation_service.py`, `workers/evaluation_tasks.py`, `api/v1/evaluations.py`. The `evals/` directory stays what its `__init__.py` says it is: *"a tool for deciding what to ship, not part of what ships"* — calibration datasets live there; the replay harness stays in `evaluation_service`.

| Action | File |
|---|---|
| Edit | `services/evaluation_service.py::_execute_evaluation_core` |
| New | `evals/datasets/playbook_selection_<date>.jsonl` |
| New | `backend/tests/test_playbook_selection_eval.py` |

**1.1 Make the harness measure production (N6).** Replace line 131's hand-rolled query with a real `build_runtime_memory_context` call, and pass `domain_id`, `max_risk_tier` and `caller_roles` through to `rank_playbooks` so RBAC and domain filters are exercised. Until this lands, no number from this harness means anything.

**1.2 Add the metrics that matter.** Today: `top1_accuracy` only. Add `recall@3`, `recall@10`, `MRR`, `applicability_precision`, `abstain_rate`, `ECE`, `Brier`, and **segmentation by `generation_provenance`** (N12) so the hand-authored population (G3.5) is visible separately rather than averaged away.

**1.3 Build the golden set.** ≥120 real resolved tickets with the human-confirmed correct playbook, mined from `RetrievalFeedback` plus resolved `Episode → Pattern → Playbook` chains (`Playbook.pattern_id` gives the direct join). **Hold out 30% for calibration and never tune on it.**

**Deliverable: a committed baseline report**, taken *after* Phase 0.5 so the trivially-fixed bugs are not counted as the ranker's failure. Expect `top1_accuracy` in the 0.2–0.4 band. If `keyword_score == 0` is not still high on >70% of cases before the Phase 0.5 fix-4, re-check G1.1 against your data before continuing.

**Exit criterion:** the harness reproduces `/runtime/match` end-to-end; baseline numbers committed and reproducible.

---

### Phase 2 — Query construction (est. 4–5 days)

| Action | File |
|---|---|
| New | `services/case_frame_service.py` |
| New | migration **0086** — `playbooks.lexical_search_text` + GIN index |
| Edit | `services/playbook_embedding.py` — populate lexical text alongside the embedding |
| Edit | `search/hybrid_ranker.py` — add the playbook-embedding arm |
| Edit | `services/memory_service.py` — emit the frame alongside `query_text` |

**2.1 `CaseFrame`** — `services/case_frame_service.py`:

```python
@dataclass(frozen=True, slots=True)
class CaseFrame:
    symptom_text: str            # prose ONLY — this is what gets embedded
    lexical_terms: list[str]     # OR-composed, deduped, ≤24
    identifier_tokens: list[str] # MG22, INC0010427, vpn-gw-east-01
    error_signature_id: UUID | None
    issue_signature_id: UUID | None
    failing_component: str | None
    failure_mode: str | None
    ci_entity_ids: list[UUID]
    environment: dict            # ← finally consumed (fixes G3.2)
    domain_id: UUID | None
```

Reuse, do not reimplement: `repository.extract_identifier_tokens` (`repository.py:66`), `services/error_signature_service.py`, `identity_service.resolve_identity_ids_for_terms`, `cmdb_topology_service.resolve_ci_entity`. The LLM call is a **fallback only**, for `failing_component` / `failure_mode` when the deterministic path yields nothing — budget-gated through `ai/provider` like every other call, and cached per session so it does not run per keystroke.

**2.2 Break the lexical recall ceiling (N4).** Migration **0086** adds a **maintained** (not generated) `playbooks.lexical_search_text TEXT` + `tsvector` + GIN index, populated from title + description + the **published** version's trigger conditions and step labels — by the same hook that Phase 0.5 fix-3 uses for the embedding, so the two can never drift. Migration 0035's note explains why a generated column cannot do this. Keep `search_tsvector` in place and query both, so a rollback is a one-line revert.

**2.3 Add the playbook-embedding recall arm (G3.4).** Embed `frame.symptom_text` **alone** and ANN over `Playbook.embedding`, mirroring `repository.py:389-405`. Must use `halfvec_cosine_distance` and `tune_ann_recall` (N9). This is the change most likely to fix hand-authored playbooks (G3.5), because it does not depend on `PlaybookEvidenceLink` at all.

**Exit criterion:** `recall@10` improves ≥15 points over the Phase 1 baseline; `recall@10` on the hand-authored segment improves at least as much as on the AI-generated one.

---

### Phase 3 — Candidate generation and batch scoring (est. 5–7 days)

| Action | File |
|---|---|
| New | `search/playbook_candidates.py` |
| Rewrite | `search/hybrid_ranker.py` |
| Edit | `api/v1/runtime.py` — pass the frame through |
| Rewrite | `backend/tests/test_hybrid_ranker_negative.py` (N10) |

**3.1 Four recall arms → one union:**

| Arm | Source | Cap |
|---|---|---|
| R1 | `Playbook.embedding` ANN on `symptom_text` | 50 |
| R2 | OR-composed `websearch_to_tsquery` over title + description + **new lexical text** | 50 |
| R3 | `error_signature` / `issue_signature` → `episode` → `pattern` → `playbook` (`Playbook.pattern_id` is a direct FK; also reuse `graph/queries.py`) | 30 |
| R4 | semantic evidence hits → `PlaybookEvidenceLink` **reverse** lookup, one query | 30 |

Union, dedupe, cap at 60. Each arm returns a **rank list**, retained for Phase 5 fusion. Oversample per N9 so the tenant post-filter does not starve small tenants.

**3.2 Kill the N+1 (G5.4).** Every remaining signal is computed **once, batched over the ≤60 candidates**:

- graph edge counts → one `GROUP BY` over candidate ids
- identity hits → one `GROUP BY`
- negative knowledge → one join (see 3.3)
- published versions → `_latest_published_versions` already batches; keep it
- **delete the per-playbook `search_evidence_semantic_for_playbook` call** — R4 supplies that signal for the whole set in one query

Target: **≤12 queries per match request, independent of tenant playbook count.**

**3.3 The negative-knowledge join, honestly [CORRECTED].** Revision 1 proposed joining "via the pattern → playbook derivation edge or an explicit `playbook_id` column." **Neither exists.** `NegativeKnowledgeItem` (`models/pattern.py:189-204`) has only `step_text`, `failure_reason`, `status`, `evidence_refs`, `domain_id` — no `playbook_id`, no `pattern_id`. Options, in order of preference:

- **(a)** New link table `playbook_negative_knowledge` (migration **0087**) written when a negative item is recorded against a playbook step. Must satisfy the N8 tenancy contract: non-nullable `tenant_id`, FK to `tenants` ON DELETE CASCADE, RLS policy, composite tenant FK per 0082.
- **(b)** Match `step_text` against the version's step labels — cheap, fuzzy, and honest about being a heuristic.
- **(c)** Until (a) or (b) ships, **set the weight to zero.** A signal that cannot discriminate is worse than a missing one, and today's version actively inverts the intended bias (G2.5).

**3.4 Delete the double-count (G2.6).** Remove `recency_score`; keep one `freshness` term. Never-validated → **0.35** (below recently-validated, above expired). Expired → excluded in Phase 4's gate, not scored 0.

**3.5 Rewrite the tests that lock the bug (N10).** `test_hybrid_ranker_negative.py`'s five tests assert the current domain-wide-count semantics. Replace them with tests of the new per-playbook signal, and add one that asserts the **old** behaviour is gone — `test_negative_penalty_is_not_domain_wide_constant`. Do not delete them silently.

**Exit criterion:** p95 `/runtime/match` latency < 800 ms at 200+ approved playbooks; `recall@10` non-regressive.

---

### Phase 4 — Applicability gate and version pinning (est. 4–5 days)
*Gated on Phase 0's `trigger_conditions` coverage ≥30%.*

| Action | File |
|---|---|
| New | `services/playbook_applicability.py` |
| Edit | `schemas/playbook.py` — additive response fields |
| Edit | `api/v1/runtime.py` — version pinning |
| New | `backend/tests/test_playbook_applicability.py` |

**4.1 Evaluate `trigger_conditions` (G3.1):**

```python
def evaluate_trigger_conditions(
    version: PlaybookVersion, frame: CaseFrame
) -> ApplicabilityVerdict
```

Levels: `exact` / `strong` / `partial` / `unvalidated` / `contradicted`. These are playbook-oriented and deliberately **not** the same enum as `fix_applicability_service`'s CI-oriented ladder (`exact_ci` / `same_model_and_configuration` / … / `semantic_only`) — but the **result shape must match it**: explicit level, `matched_factors`, `differences`, `review_required`. Same shape, different vocabulary, so the two read alike in a trace without conflating CI applicability with trigger-condition fit.

- `contradicted` → **hard drop**, reason recorded
- `expiry_at < now` → **hard drop** (closes G2.6's tail; the graph path already does this at `hydrators.py:146-147`)
- `unvalidated` → admitted, flagged, confidence-capped
- **Fold in `version.conflicts` (N12).** A version whose approved KB/SOP disagrees with observed practice cannot be `exact`. Respect the field's three-state semantics: `NULL` means *not assessed*, which is not the same as an empty list.

`trigger_conditions` is model-authored JSONB of uncertain shape. The evaluator must be **tolerant like `_materialize_evidence_links`**: unknown keys ignored, malformed values downgrade to `unvalidated`, never raise. `playbook_version_facts` already models this defensive posture (`hydrators.py:210-212`).

**4.2 Pin the version (N1 / G3.3).** Two contract changes, large consequences:

- `RuntimeMatchResult` gains `playbook_version_id` and `semantic_version` — **the version actually scored.**
- `GET /runtime/playbooks/{stable_key}` gains an optional `version_id: UUID | None` query parameter. When supplied it returns **that exact published version**, bypassing the `current_version_id` preference at `runtime.py:55-73`.

Both additive. SupportCopilot keeps working unchanged; the Phase 6 tool always passes `version_id`, so the agent can never fetch a version other than the one it scored. Together with Phase 0.5 fix-2, all three version-resolution rules converge on **newest published**.

**4.3 Additive response fields** on `RuntimeMatchResult` — all optional, nothing renamed:

```python
playbook_version_id: UUID | None = None
semantic_version: str | None = None
applicability: str | None = None            # exact|strong|partial|unvalidated
applicability_factors: list[str] | None = None
applicability_differences: list[str] | None = None
confidence_calibrated: float | None = None  # NEW — see 5.2
selection_margin: float | None = None       # top1 − top2
```

**Exit criterion:** `applicability_precision` ≥ 0.85 on the golden set; zero version-mismatch cases in an end-to-end match→fetch→project test that exercises all three surfaces.

---

### Phase 5 — Fusion and calibration (est. 4–5 days)

| Action | File |
|---|---|
| New | `search/fusion.py` |
| Edit | `search/hybrid_ranker.py` |
| New | `services/score_calibration.py` |

**5.1 RRF over the four arms (fixes G2.1–G2.4 as a class):**

```python
rrf_score(pb) = Σ_arms  weight_arm / (K + rank_arm(pb))     # K = 60
```

Starting arm weights R1 1.0 / R2 0.8 / R3 1.2 / R4 0.6 — R3 highest because a signature→episode→playbook path is confirmed causal precedent, not similarity. Tune **only** against the training split.

Then a small linear layer over gate + quality features (applicability level, `playbook_confidence`, freshness, precedent count). **Precedent count replaces `graph_count_score`** — count *resolved* episodes whose signature matches the case frame, not raw edge degree. That one substitution turns G2.4's popularity prior into a query-conditional signal.

**5.2 Calibrate, and keep the raw score's meaning intact.** Fit isotonic regression on the held-out 30% mapping fused score → P(correct). Expose it as a **new** field `confidence_calibrated`; leave `confidence` and `match_score` on their current scale.

This matters because SupportCopilot is out of scope. Any client-side `match_score >= 0.8` threshold keeps behaving as it does today; the calibrated number is opt-in. Note in the handover that SupportCopilot's eventual upgrade is to read `confidence_calibrated`, `selection_margin` and `applicability`, and to pass `playbook_version_id` when fetching steps.

**5.3 Abstain on margin, not only on score (G2.7).** Two near-identical candidates are the honest "I don't know" case, and today's code reports them as confident. Abstain when `confidence_calibrated < τ` **or** `selection_margin < δ`; pick τ and δ from the precision/recall curve on the training split, not by intuition.

**5.4 Shadow mode before cutover.** Behind a config flag, compute the new ranking on live traffic, **log it, and serve the old one.** Compare agreement rate, margin distribution and latency for at least a week. Offline eval on 120 cases is not sufficient evidence to flip a live retrieval path. This is the rollback story for every phase above: dual-path, flag-gated, revert is a config change.

**Exit criterion:** ECE ≤ 0.08; `top1_accuracy` ≥ baseline + 25 points; false-confident rate (high confidence, wrong pick) ≤ 5%; shadow agreement understood and explained where it diverges.

---

### Phase 6 — The agent host (est. 5–7 days) — **NEW SURFACE (N5)**

Nothing in the repository runs an agent today. This phase builds the thing the rest of the plan feeds.

| Action | File |
|---|---|
| New | `integrations/maf/runtime.py` — composition root |
| New | `api/v1/agent.py` — `POST /api/v1/agent/diagnose` |
| Edit | `api/v1/__init__.py` — register the router |
| New | `integrations/maf/prompts.py` — the agent's system prompt |

**6.1 Composition root** — `integrations/maf/runtime.py`. Builds, per request: the scope via the existing `build_agent_graph_scope` (`graph/agent/service.py:39`), the in-process clients (`InProcessContextGraphClient`, `InProcessCmdbTopologyClient`, `InProcessChangeRiskClient`, `InProcessFixApplicabilityClient`, `InProcessCohortClient`, `InProcessEdgeProposalClient`, `InProcessDecisionWritebackClient` — all already written in `client.py`), the plugin, and the agent. Model selection goes through the existing LiteLLM/`ai/provider` path so cost attribution and tenant budgets apply — the same hardening `hybrid_ranker.py:275-279` documents for embeddings.

**6.2 `POST /api/v1/agent/diagnose`** — accepts a ticket context, returns a structured decision: chosen `playbook_id` + `playbook_version_id`, applicability verdict, cited node keys, rationale, and `grounding_status`. Auth, tenancy and domain scoping reuse `deps.AuthUser` / `DbSession` exactly as `runtime.py` does.

**6.3 Scope honesty.** SupportCopilot is out of scope, so **nothing consumes this endpoint on day one.** It ships behind a flag, is exercised by the eval harness and integration tests, and SupportCopilot adopts it in a separately-scoped piece of work. Anyone reading this plan should not expect an end-user-visible change from Phase 6 alone.

**Exit criterion:** the endpoint runs a full turn against a seeded tenant in an integration test, with tool calls and write-back asserted.

---

### Phase 7 — Agent tools and provider rewrite (est. 6–8 days)

| Action | File |
|---|---|
| New | `integrations/maf/playbook_client.py` |
| New | `integrations/maf/playbook_tools.py` |
| Edit | `integrations/maf/plugin.py` |
| Edit | `integrations/maf/provider.py` |
| Edit | `graph/agent/selector.py`, `graph/agent/profiles.py` |
| Edit | `backend/tests/test_maf_adapter.py` (N10) |

**7.1 `PlaybookRetrievalClient`** — mirror the `ContextGraphClient` pattern in `client.py` exactly: `Protocol` + `InProcess…` (own session per call, commit/rollback) + `Http…` (https-only token hygiene with the `allow_insecure_http` escape hatch).

**7.2 Four tools (fixes G4.3):**

| Tool | Returns |
|---|---|
| `match_playbooks(symptoms, entities, environment, top_k)` | Phase 3–5 result: candidates with `playbook_version_id`, applicability verdict, breakdown, margin |
| `get_playbook(playbook_id, version_id)` | **full structured steps** — not labels: `safety_class`, `requires_approval`, `reversible`, `rollback_hint`, `verification`, `tool_ref`, `inputs` — plus complete `trigger_conditions`, `rollback_notes`, `verification_policy`, `conflicts`, `semantic_version`. `version_id` **required**, so the agent can only read the version it scored |
| `check_trigger_conditions(playbook_version_id, environment, symptoms)` | the deterministic verdict — the agent *verifies*, it does not judge applicability itself |
| `get_negative_knowledge(playbook_version_id)` | what NOT to do, with sources |

`get_playbook` is what closes G4.3's truncation gap: the projection shows 15 of 40 steps and `steps_total: 40`, and this is the tool that fetches the rest.

Tool-description discipline follows the bar already set in `tools.py` — **state what an empty result means.** `assess_fix_applicability`'s description is the model: *"An empty 'applicable' list means no validated precedent — do NOT stretch a fix across unvalidated preconditions."* Errors return `_tool_error(code, message)` dicts, never tracebacks; model-supplied arguments are clamped and validated exactly as `query_context_graph` does at `tools.py:60-96`.

**7.3 Provider rewrite (`provider.py`) — four changes:**

- **Query from the case frame, not the transcript tail** (G1.2): `query=frame.symptom_text`, `entities=frame.lexical_terms`, `seeds=[signature/CI refs]`, `session_id=...`.
- **Request the budget the profile allows** (G1.3): `AgentGraphBudget(max_nodes=60, max_relationships=120, max_depth=3, max_characters=30_000)`.
- **Never be silent** (G5.1): always inject a `grounding_status` block — `grounded` / `weak` / `no_precedent` — plus `subset.warnings` and `subset.truncation_reasons`, which lines 92-98 currently strip. On `no_precedent`, the instruction is explicit: *no operational precedent was retrieved; say so rather than proposing steps.*
- **Cite what was used, not what was offered** (G5.2): replace the top-40 heuristic. Read the actual tool-call results from the session context and require a structured tail naming `chosen_playbook_version_id` and `cited_node_keys`. `after_run` records **those**, plus the applicability verdict and selection margin.

**Preserve what the existing tests lock (N10):** `test_provider_injects_attributed_context` and `test_provider_fences_untrusted_graph_content` guard the `source_id` attribution and the `<untrusted-data>` fencing at `provider.py:100-112`. Both behaviours stay. `test_provider_truncates_long_conversations_instead_of_dropping_context` asserts the query-construction being replaced — rewrite it against the case-frame path, and keep its intent: **a long conversation must degrade, never lose graph context entirely.**

**7.4 Selector per-type quotas (`selector.py`, fixes G4.1).** Add a reservation pass before the relevance-ordered admission loop: reserve slots for the highest-relevance N `playbook` and M `pattern` nodes, admit those with their ancestor chains, then run the existing loop over the remainder. Express it on `AgentGraphProjectionProfile` as `type_reservations: dict[str, int]` so it is server-controlled and per-profile, consistent with the rest of that dataclass.

**Set the playbook reservation to 2**, matching the intent `hydrators.py:196` already documents — and add a comment pointing at that line, so the character-budget maths there finally describes something real.

While in `selector.py`, fix G4.2: when a node is rejected for the character budget, either stop admitting or record the skip in `truncation_reasons`. Silent reordering under truncation is the harder bug to debug later.

**Exit criterion:** on the golden set the agent's chosen playbook matches the human-confirmed one at ≥ Phase 5's `top1_accuracy` − 3 points. If the agent *loses* accuracy relative to deterministic ranking, the tool descriptions or the abstain instruction are wrong — fix those, do not widen the agent's latitude. Zero cases of steps returned for a version other than the matched one.

---

### Phase 8 — Close the flywheel (est. 4–5 days)
*Gated on Phase 0's feedback-volume audit.*

| Action | File |
|---|---|
| New | migration **0088** — durable `runtime_match_records` |
| New | migration **0089** — `retrieval_feedback.playbook_version_id` + index on `match_id` |
| New | `services/retrieval_feedback_service.py` |
| New | `workers/ranking_calibration_tasks.py` |

**8.1 Make matches durable (N7).** Feedback today points at a Redis key with a 3600 s TTL that any lifecycle transition SCAN-deletes. Add `runtime_match_records` persisting `match_id`, `tenant_id`, query frame, ranked results with `playbook_version_id`, filters applied, and calibrated confidence. Redis stays the hot cache for `/runtime/explain`; the table is the durable record.

**N8 contract, mandatory:** non-nullable `tenant_id`, FK to `tenants` ON DELETE CASCADE, RLS policy per 0078/0079, composite tenant FK per 0082, ORM↔DDL parity per `test_orm_migration_column_parity.py`. `test_tenant_table_coverage.py` will fail the build otherwise.

**8.2 Make feedback joinable.** Add `retrieval_feedback.playbook_version_id` and an index on `match_id`, so "the playbook was right but v3 broke it" becomes expressible — today it is not.

**8.3 Nightly recalibration.** A Celery task (registered in `workers/celery_app.py` alongside the existing `evaluation_tasks`) recomputes the isotonic calibration and RRF arm weights from accumulated feedback, and **writes them to a versioned config row — never straight into the ranker.** A ranking function that silently retunes itself is not auditable. Bound the per-run weight delta and alert when it is hit.

**8.4 Feed confirmed selections back** as `validated_fix` edges, so Phase 5's precedent count strengthens with use. `partially_validated_fix` already exists as a distinct type (`profiles.py:186`, weighted 1.05 at line 209) precisely so half-fixes cannot masquerade as full validation — respect that distinction here.

**Exit criterion:** calibration runs green for 7 consecutive days with weight deltas inside the envelope; a feedback row submitted 24 h after its match still joins to what was shown.

---

## 5. Sequencing, risk, effort

| Phase | Effort | Risk | Gate |
|---|---|---|---|
| 0 · Data readiness audit | 2 d | None | **go/no-go for 4 and 8** |
| 0.5 · Correctness patches | 2–3 d | Low | — |
| 1 · Fix harness + baseline | 3–4 d | Low | **blocks every numeric gate** |
| 2 · Query construction | 4–5 d | Low | — |
| 3 · Candidates + batch | 5–7 d | Medium | — |
| 4 · Applicability + pinning | 4–5 d | Medium | needs Phase 0 ≥30% |
| 5 · Fusion + calibration | 4–5 d | Medium | shadow mode before cutover |
| 6 · Agent host | 5–7 d | High | new surface, no user impact alone |
| 7 · Tools + provider | 6–8 d | High | — |
| 8 · Flywheel | 4–5 d | Low | needs feedback volume |

**Total: ~40–51 working days.** Phases 0.5–5 are the accuracy work (~23–29 d). Phases 6–7 are the architecture Gemini describes (~11–15 d). Doing 6–7 first re-hosts a broken ranking function inside a nicer runtime — and, per N5, would have nothing to run against until the host exists anyway.

**Risks, owned:**

- **`trigger_conditions` may be too sparse to gate on.** Phase 0 decides. Below 30% coverage, Phase 4 degrades to a pass-through and the effort belongs in authoring tooling. Do not build the gate on faith.
- **The golden set is the whole project's foundation.** 120 labelled cases is a floor. If `RetrievalFeedback` is thin (Phase 0 tells you), budget explicit SME labelling — cheaper than shipping an unmeasurable rewrite.
- **RRF arm weights can overfit a small golden set.** Keep the held-out split sealed; report both splits every run.
- **Phase 0.5 fix-2 and Phase 4.2 change which rows are returned** where `current_version_id` and newest-published diverge. That is the intended fix, and Phase 0 sizes the blast radius, but it *will* look like a behaviour change. Call it out in the release note.
- **Phase 0.5 fix-1 makes `minimal` / `critical` / `restricted` playbooks visible to the agent for the first time.** If those tiers were being used as a de-facto hiding mechanism, review them before shipping — this is a policy question, not a code question.
- **Re-embedding every playbook (fix-3 backfill) costs real tokens.** Batch it, attribute it to the tenant budget, make it resumable.
- **Phase 6 delivers no user-visible change.** Say so to stakeholders up front, or it reads as five days with nothing to show.

**Observability, every phase.** Named `structlog` events per stage (`playbook_candidates.generated`, `playbook_applicability.verdict`, `ranking.fused`, `ranking.abstained` — the last already exists at `hybrid_ranker.py:372`), with arm counts, verdict distribution, margin and latency. A regression must be visible in production logs, not only in an eval run someone remembers to trigger.

---

## 6. Backward-compatibility contract (SupportCopilot is not modified)

| Surface | Change | Client impact |
|---|---|---|
| `POST /runtime/match` request | none — `environment` is finally *read* | none |
| `RuntimeMatchResult` | **additive optional fields only** | none |
| `match_score`, `confidence` | **scale preserved**; calibrated value in the new `confidence_calibrated` | none |
| `GET /runtime/playbooks/{stable_key}` | optional `version_id` query param | none when omitted |
| `POST /graph/agent-subsets` | `AgentGraphSubset` unchanged; version, risk-tier and quota behaviour corrected | **content changes** — see below |
| `POST /agent/diagnose` | new endpoint | none (nothing calls it yet) |

**One honest caveat.** `/graph/agent-subsets` keeps its schema, but Phase 0.5 changes **what it returns**: unpublished version content disappears (N1), and `minimal` / `critical` / `restricted` playbooks appear (N2). Any consumer that had adapted to the old content will see different — correct — data. That is the fix, not a regression, but it belongs in the release note rather than being discovered.

No field is renamed, removed, or rescaled. SupportCopilot's existing thresholds keep their current behaviour.

---

## 7. First five commits

1. **Phase 0 audit** — read-only queries, one-page result. Decides whether Phases 4 and 8 are worth building.
2. **N2** — `hydrators._RISK_ORDER` → `risk_policy.playbook_risk_rank`; reconcile `_risk_cap` with `_effective_max_risk_tier`; add the `risk_tier` CHECK constraint (0085). Entire tiers of playbooks become visible to the agent.
3. **N1** — `repository.py:904` filters to published versions. The agent stops being shown unreviewed drafts.
4. **N3** — `embed_playbook` on every publish, from the published version, plus the backfill task. Semantic matching stops pointing at v1.0.0 forever.
5. **N6 + G1.1** — fix `evaluation_service` to measure production, then flip `plainto_tsquery` → OR-composed `websearch_to_tsquery`. **In that order** — otherwise the harness cannot see what the tsquery fix did.

Re-run the eval after each of 2–5. If commits 2–5 do not move `recall@10` by double digits, stop and re-validate §2's assumptions against your tenant's actual data before continuing. The analysis here is read from source, but the magnitude of each defect depends on your data distribution — which is exactly what commit 1 measures.
