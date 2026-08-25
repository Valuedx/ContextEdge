# Logic & Systems Audit — Similarity, Normalization, Classification, Pattern & Playbook Pipelines

## Authoritative Coder Implementation Runbook

This is the **single tracking source** for remediation. Do not create another
tracking document. Execute rows strictly from top to bottom and work on only one
row at a time. After changing a row:

1. Re-open every listed source location and verify the intended code is present.
2. Add or update the focused regression test named by the proof gate.
3. Run that focused test before changing the next row.
4. Replace `Pending` with exactly `Fixed`, `Already OK`, or
   `Skipped-with-reason`, and record the actual change in this table.
5. Do not mark a row `Fixed` when only one sub-path was repaired.

Section 6 is authoritative whenever its validated remedy differs from the
original proposal in Section 2. Findings A/B, R1-R6, and M15 are included because
they are findings in this file even though the initial coder prompt named only
#1-#16 and M1-M14. Nothing in this runbook authorizes unrelated refactoring.

### Ordered Tracking Table

| Order | ID | Primary file(s) | Status | Correct root-cause change and proof gate |
|---:|---|---|---|---|
| 1 | #1 | `hybrid_ranker.py`; `knowledge_retrieval_service.py`; `correlation_suggestion_service.py`; `graph/agent/repository.py`; `chunk_rollup.py` | **Already OK** | Section 6 found no shared-score invariant: consumer-specific mappings are deliberate. Do not apply the regressive global `1 - distance` change. Proof: retain mapping-specific tests and record no production diff. |
| 2 | #2 | `ai/embeddings.py`; decision/artifact/evidence embedding callers; related schemas/tests | Pending | Replace empty-input zero sentinels with a nullable embedding contract. Propagate `None` through every caller, leave database embedding columns NULL, and do not raise on soft artifact paths. Proof: empty evidence/decision/artifact input writes no vector; ANN/MMR populations contain no zero sentinel; nonempty paths remain 3,072-dimensional. |
| 3 | #3 | Evidence normalization; version create/approval embedding callers | **Already OK** | Section 6 proved both stated traces are blocked: content changes create a new evidence row and version edits re-embed. Do not add the proposed ad-hoc hash field. The real rollback/published-version problem is handled only under #13. |
| 4 | #4 | `search/hybrid_ranker.py`; FTS calibration tests | Pending — calibration required | Remove request-population `rank / max_rank`. Introduce one monotonic, bounded, fixed-across-requests, **versioned and empirically calibrated** `calibrated_fts_score`; recalibrate `MIN_RECOMMENDATION_SCORE` with fixtures. Never substitute the audit's unsupported `/0.1`. If no approved calibration dataset/threshold exists, mark `Skipped-with-reason` rather than inventing one. Proof: the same row's keyword score is unchanged when stronger/weaker sibling hits are added. |
| 5 | #5 | `ai/classifiers/relevance.py`; both extraction worker paths | Pending | Centralize canonical label and finite confidence parsing. Allowed labels are `operational`, `possibly_relevant`, and `not_relevant`; missing/unknown becomes `unclassified`; booleans/non-finite values become confidence 0; finite values clamp to `[0,1]`. Both workers must store and compare the returned canonical label. Keep `unclassified` on the extraction path. Proof: missing label does not skip; `not relevant` and `not_relevant` behave identically; unknown/NaN/bool inputs are safe. |
| 6 | #6 | `ai/extractors/pattern_extractor.py`; `workers/pattern_tasks.py` | Pending | Parse `is_match` only when its exact type is `bool`; parse finite bounded confidence; any malformed value/exception returns a non-match. Caller requires `is_match is True` and confidence `>= 0.70`. Candidate SQL must aggregate minimum distance per pattern and order nearest-first before `LIMIT 1`. Proof: `{}`, string `"false"`, NaN confidence, and timeout never merge; nearest valid candidate is deterministic. |
| 7 | #7 | `services/pattern_service.py`; automated pattern/playbook dedup path | Pending | Remove normalized title as identity from creation and housekeeping. Equal titles create distinct clusters; title collisions become review proposals/flags only. Do not add a title unique index. Proof: two disjoint clusters with the same title remain separate through creation and deduplication. |
| 8 | #8 | `workers/pattern_tasks.py`; `services/pattern_service.py`; membership model/migration if required | Pending | Preserve valid `0.0` with one finite-unit parser; invalid confidence leaves episodes unassigned. Eliminate single-link chaining with a centroid or bounded all-member vector rule plus #6 validation. Do not fabricate confidence updates: status cannot be `Fixed` until member verdicts are stored and a documented aggregation rule recomputes pattern confidence, or that sub-claim is recorded `Skipped-with-reason`. Proof: bridge chains do not merge; zero remains zero; invalid/NaN does not become 0.8; aggregation has deterministic fixtures. |
| 9 | #9 | Pattern worker/API orchestration; `pattern_evidence_links` model and Alembic migration | Pending | Acquire a tenant/domain advisory lock **before synthesis** in worker and manual discovery. Add partial unique index `uq_pattern_episode_once` on non-null `episode_id`; handle uniqueness conflicts as idempotent skips. Do not add title uniqueness. Proof: concurrent worker/API discovery performs one synthesis/pattern membership and loser exits cleanly. |
| 10 | #10 | New/shared playbook-generation service; worker and `api/v1/playbooks.py` wrappers | Pending | Extract one async service used by both worker and API. In order: active pattern check, existing-playbook/confidence guards before retrieval/LLM, knowledge retrieval, ID-bearing episode summaries, generation, empty-step rejection, deterministic risk floor, and atomic persistence. Map API skips to 409/422. Do not call the nested Celery `work()` and do not duplicate guards. Proof: worker/API yield identical artifacts and skip reasons; duplicate/low-confidence/empty-step requests make no persisted shell and no unnecessary LLM call. |
| 11 | #11 | Central playbook-version/step validation; schema; `execution_service.py` | Pending | Validate all raw and typed step dictionaries centrally. Reject unknown safety classes. Require explicit safety class when `action_name` or `tool_ref` declares an executable binding; preserve existing unbound/manual prose steps. Repeat the bound-action check at execution as defense in depth. Proof: unknown class and bound action without class fail before persistence; valid unbound/read-only and valid bound steps retain behavior. |
| 12 | #12 | Shared service from #10; pattern growth worker; playbook API/UI lifecycle | Pending | Add an explicit update-version operation on the existing playbook. Worker must no longer silently skip pattern growth, while ordinary create must return 409 when a playbook exists. For approved playbooks, preserve the published executable version, create a reviewable draft, apply governance transition rules, keep the published embedding active, and invalidate pattern/playbook/detail queries. Proof: growth produces one playbook with a draft version, never PB2; rank/execute continue using the prior published version until approval. |
| 13 | #13 | `api/v1/playbooks.py`; `models/playbook.py`; Alembic; embedding service; rank/execute/agent selectors | Pending | Delete the largest-pattern lineage fallback. Add explicit `embedding_version_id` (or equivalent version-keyed storage); approved semantic seeds may be updated only from the selected published version and must write vector+version atomically. Use one shared published-version selector across rank, execute, agent seed, approval, and rollback. Rollback must re-embed its newly published version. Proof: orphan references return null lineage; draft creation cannot alter approved retrieval; rollback/rank/execute agree on version ID. |
| 14 | #14 | Knowledge retrieval; evidence/decision/artifact/episode embedding callers; playbook task staging | Pending | Reuse one attributed query embedding through document and section retrieval. Pass tenant/db through every budgeted embedding caller. Batch episode repair in bounded chunks while validating every vector and preserving per-item failure isolation. Whole-task replay may be reduced only with persisted/idempotent stage state; do not merely move `self.retry`. Include sibling unattributed attachment, decision, and semantic-fallback calls. Proof: one query embed per retrieval, budget attribution on all paid calls, bounded chunk retries, and no completed stage repeats after retry. |
| 15 | #15 | Playbook list/detail API and UI; transition mutation | Pending | Select an explicit current/latest published version for confidence; remove every `0.8` fallback; render confidence only when non-null. Invalidate the playbook list after transitions if immediate cross-page freshness is required. **Do not** invalidate/rebuild historical runtime match snapshots. Proof: missing confidence renders no badge/em dash, ordering is deterministic, list refreshes after transition, and T0 explain remains unchanged. |
| 16 | #16 | `search/vector_search.py`; `search/chunk_rollup.py` | Pending | Add SQL tie-break `EvidenceChunk.id` after distance. Replace set iteration with ordered indexes and explicit key `(score, -distance, chunk_id)`, resetting `best_key` each loop. Proof: exact ties return identical IDs across repeated runs and candidate input orders permitted by SQL. |
| 17 | A | `ai/provider.py`; `services/evidence_chunk_service.py` | Pending | For batch embeddings, require response count to equal input count and validate **every** vector's type/dimension before returning any. Do not allow `zip` to hide cardinality mismatch. Proof: short, long, and mixed-dimension responses fail atomically; no chunk is partially stamped. |
| 18 | B | `ai/classifiers/message_function.py`; ticket correction path | Pending | Reject bool/non-finite confidence, then clamp finite values to `[0,1]`. Keep invalid confidence at zero so the trust-floor comparison fails closed. Proof: `"NaN"`, infinities, booleans, malformed types, zero, and exact trust-floor boundaries. |
| 19 | R1 | `search/hybrid_ranker.py`; ranked/runtime response schemas | Pending | Preserve intentional fail-soft ranking only if degradation is observable: log the exception and propagate structured degraded-signal metadata (embedding failure vs semantic-search failure) to runtime/evaluation callers, or fail the request explicitly. Do not return an indistinguishable normal recommendation. Proof: injected failures either produce the declared error or a response marked degraded; successful path is unmarked. |
| 20 | R2 | `api/v1/runtime.py`; runtime snapshot storage | Pending | Do not return an explanation handle unless its initial snapshot was persisted. Prefer a durable fallback store or fail the match request with an explicit availability error when `setex` fails. Do not silently `pass`. Proof: Redis write failure cannot yield a 200 response containing an unresolvable `match_id`. |
| 21 | R3 | `services/knowledge_retrieval_service.py`; generated provenance/schema | Pending | Make section-selection fallback observable. Either fail generation when semantic section ranking is required, or mark every fallback document/version with selection mode and degradation reason; log the exception. Do not silently present chunk-order selection as semantic grounding. Proof: embedding failure produces explicit fallback metadata or an error; semantic success remains unchanged. |
| 22 | R4 | Scoped frontend queries: patterns, playbooks, suggestions, approvals, runtime lists, version/reference queries | Pending | Destructure/query `error` or `isError` and render a distinct failure state with retry; reserve empty-state copy for successful empty arrays. Apply consistently to every scoped query named in R4. Proof: mocked 401/500/network failure never renders “no records/pending”; successful `[]` still does. |
| 23 | R5 | `frontend/.../runtime/page.tsx` | Pending | Clear dependent match/explain/playbook result state at the start of a new request and on failure where appropriate; never display request A beside request B inputs/error. Proof: success A followed by failed B removes or explicitly labels A as stale. |
| 24 | R6 | `frontend/.../playbooks/page.tsx` | Pending | Reset pagination whenever search text is changed/applied/cleared, following the evidence-page pattern. Proof: from page > 0, new/cleared search requests offset 0. |
| 25 | M1 | `api/v1/playbooks.py` | Pending | Validate the parent playbook with ID+tenant before listing versions; return 404 for foreign/missing parent. Proof: own versions succeed, foreign UUID returns 404, compiled query/parent guard contains tenant. |
| 26 | M2 | `api/v1/patterns.py`; patterns page | Pending | Require the chosen privileged backend role (consistent with destructive pattern operations) before deduplication and hide/disable the UI control for unauthorized roles. Proof: ordinary authenticated user receives 403 and cannot see/trigger the button; authorized role retains behavior. |
| 27 | M3 | Pattern-link schema/API; manual playbook generation | Pending | Require exactly one of episode/evidence ID. Validate target existence, tenant, and compatible domain before insert; add tenant predicate again when loading linked episodes for generation. Proof: both/neither IDs fail; missing/foreign/cross-domain targets fail; own valid target succeeds. |
| 28 | M4 | Runtime feedback API; drift service | Pending | Validate feedback playbook ownership and, when present, match ownership/membership. Restrict feedback type to the supported enum. Add feedback tenant predicate to drift aggregation. Proof: arbitrary/foreign playbook or mismatched match fails; tenant A rows never affect tenant B drift. |
| 29 | M5 | Evidence API and `search/pg_fts.py` | Pending | Pass/apply `source_id`, `domain_id`, and `offset` through the FTS branch. Proof: query+facet SQL contains both predicates and query page two differs from page one; blank-query behavior is unchanged. |
| 30 | M6 | Playbook create/update/generated write validation; `search/risk_policy.py` | Pending | Enforce the canonical risk-tier enum at every write boundary, including raw generated artifacts. Unknown legacy values fail closed during cap evaluation rather than mapping to medium. Proof: unknown create/update/generation values reject or normalize per explicit rule; `risk_within_cap(unknown, medium)` is false. |
| 31 | M7 | `services/correlation_suggestion_service.py`; concurrency tests | Pending | Under a tenant-scoped transaction/advisory lock, reserve only `cap - pending_count` slots before inserts; zero/negative remaining returns capped. Replace unordered 5,000-row learning sample with SQL aggregates or an explicitly ordered documented window. Proof: 499 can add at most one; concurrent workers never exceed 500; >5,000 decisions produce deterministic stats. |
| 32 | M8 | `api/v1/patterns.py` | Pending | Normalize requested episode IDs and require the found tenant-owned ID set to match exactly before synthesis. Proof: empty, duplicate, missing, mixed-valid/foreign, and all-valid fixtures have explicit outcomes; partial input never proceeds. |
| 33 | M9 | Playbook create API | Pending | Validate supplied domain and pattern against caller tenant; when both exist, require pattern/domain consistency. Proof: foreign domain, foreign pattern, and mismatched own domain/pattern fail; valid/null combinations preserve behavior. |
| 34 | M10 | `services/playbook_service.py`; provenance tests | Pending | Make knowledge provenance win over generic evidence for the same ID by ordering or upgrading the link. Extend the overlap test to assert `based_on_kb`, not only count. Proof: shared ID yields exactly one specific knowledge link. |
| 35 | M11 | Pattern detail frontend | Pending | Parse weight once and default only for non-finite input; preserve numeric zero. Proof: `0`, `0.0`, positive, empty, NaN-like input, and backend negative rejection. |
| 36 | M12 | Playbook generator and worker persistence | Pending | Return explicit source-usage metadata from the selected prompt. Build citation maps and persist `knowledge_ids`/knowledge applicability only when knowledge was actually inserted into that prompt. Proof: v1/v2 pin records no normative grounding; v3+ records exactly supplied documents. |
| 37 | M13 | `search/hybrid_ranker.py`; validation timestamp writers | Pending | Clamp freshness to `[0,1]` and reject materially future validation timestamps at write boundaries. Proof: past, now, +clock-tolerance, beyond-tolerance, expiry, and missing timestamps. |
| 38 | M14 | Pattern list/detail API; duplicate invariant from #9/#12 | Pending | Prevent duplicate playbooks transactionally via #12. Until/for legacy duplicates, use one documented deterministic canonical selector in both list and detail (lifecycle/published version/update/ID ordering). Proof: both endpoints return the same ID/status across repeated calls with duplicates. |
| 39 | M15 | Skill registration/model; remediation rollback lookup | Pending | Validate `rollback_skill_id` by ID+tenant during registration and repeat tenant-scoped lookup in rollback planning. Proof: foreign/missing rollback fails; own rollback succeeds; plan never copies foreign metadata. |

### Mandatory Final Gates

After order 39 is resolved:

1. Search this table for `Pending`; none may remain.
2. Re-open every modified production file and confirm the intended guard/branch is
   present at the actual location.
3. Run the full backend test suite. Tests blocked by an environment dependency
   are not “passed”: install the declared dependency or record the exact blocker.
4. Run frontend unit tests, lint, TypeScript/build validation, and the focused
   role/error/state tests added above.
5. Run migration upgrade tests for #9/#13 and concurrency tests for #9/M7.
6. Report every row individually in the final coder summary. Do not group rows,
   and explicitly list any `Already OK` or `Skipped-with-reason` disposition.

## 0. Scope Note

Reviewed backend first, then frontend, at the current workspace tree under `ContextEdge/`. This is a targeted deep-dive into five pipelines the prior audit (`LOGIC_SYSTEMS_AUDIT.md`) only touched lightly: cosine/distance matching, score/vector normalization, classification, pattern creation, and playbook creation — including how those outputs feed ranking and execution.

**In scope**

- Backend: `search/vector_ops.py`, `search/chunk_rollup.py`, `search/vector_search.py`, `search/hybrid_ranker.py`, `search/pg_fts.py`, `ai/embeddings.py`, `ai/provider.py` (`generate_embedding*`), `ai/classifiers/relevance.py`, `ai/classifiers/message_function.py`, `ai/extractors/pattern_extractor.py`, `ai/generators/playbook_generator.py`, `workers/pattern_tasks.py`, `workers/extraction_tasks.py`, `services/pattern_service.py`, `services/playbook_service.py`, `services/playbook_embedding.py`, `services/knowledge_retrieval_service.py`, `services/correlation_suggestion_service.py`, `services/episode_service.py` (similar-episode pass), `services/execution_service.py` (creation vs execution constraints / version pin), `graph/agent/repository.py` (semantic seed conversion), `api/v1/playbooks.py`, `api/v1/patterns.py`, `api/v1/runtime.py`.
- Frontend: patterns list/generate, playbooks list/detail, runtime match sandbox, suggestions similarity display, React Query defaults, execution approvals polling.

**Out of scope (already covered by the prior audit; not re-litigated here)**

- Approval-gate bypass (Issue #1), title-based *evidence* deletion (Issue #2), terminal execution overwrite (Issue #3), ad-hoc idempotency collision (Issue #4), paused-sync stranded raw IDs (Issue #5), closed graph edges in ranking (Issue #6), expired-playbook recommend-then-reject (Issue #7), identity-reconciliation NaN confidence (Issue #8), and the validator’s body-only `content_hash` identity finding (Validation 5.3).

**Cross-reference:** several findings here *compound* Issues #6 and #7 (inflated semantic scores make the expiry leak easier to trip; creation-time safety/version gaps are the same ranking-vs-execution class of bug). See §4.

**Limits on confidence:** pgvector’s cosine distance on a stored zero vector was not executed against a live Postgres in this pass; IEEE/`NaN` comparison behaviour is inferred from the formula pgvector implements (`1 - (a·b)/(|a||b|)`). Celery worker cardinality for queue `pattern` is not pinned in application code, so concurrent pattern creation is confirmed at the schema/TOCTOU layer and only suspected as a live race if more than one `pattern` consumer is deployed.

No production code was changed by this audit.

---

## 1. Logic Gap Matrix

| # | Area | Severity | Confidence | Location | Failure Type | Accuracy Impact | Cost Impact | Impact Summary |
|---|---|---|---|---|---|---|---|---|
| 1 | A | P1 | Confirmed | `hybrid_ranker.py:45-54`; `knowledge_retrieval_service.py:584-586`; `correlation_suggestion_service.py:189`; `graph/agent/repository.py:401-429`; `chunk_rollup.py:45-47` | Distance/similarity confusion | Silent FP (over-trigger ranking) | none | Same cosine distance is mapped three incompatible ways; ranker inflates semantic score vs knowledge/correlation/agent. |
| 2 | A | P1 | Confirmed | `ai/embeddings.py:33-34, 61-63`; `chunk_rollup.py:72-74`; `extraction_tasks.py:65-69` | Zero-vector / stale vector | Silent FN/FP in ANN | redundant-call (empty text still stored as a “real” embedding) | Empty text persists a 3072-zero vector; MMR rewrites a 0-norm to 1; search treats the row as embedded. |
| 3 | A | P1 | Confirmed | `extraction_tasks.py:65-69`; `playbook_service.py:307-317`; `playbook_embedding.py:86-98` | Stale embedding vs source text | Silent FP/FN | none (re-embed on every edit would *increase* cost — see fix) | Evidence is never re-embedded after first write; approved playbooks keep a candidate-era fingerprint unless the column is NULL. |
| 4 | B | P1 | Confirmed | `hybrid_ranker.py:264-269, 174-180` | Population min-max | Silent FP | none | FTS keyword score is `rank / max(rank in this result set)`. A lone hit is always 1.0. |
| 5 | C | P1 | Confirmed | `classifiers/relevance.py:70-72`; `extraction_tasks.py:436-478, 648-679` | Unvalidated label; `>`/`==` mismatch | Silent FN (skip) and FP (weird labels) | redundant-call on fail-open | Relevance labels are not enum-checked. Skip uses exact `"not_relevant"` *before* space-normalization; missing label + high confidence skips extraction. |
| 6 | D | P1 | Confirmed | `pattern_extractor.py:102-112`; `pattern_tasks.py:211-234` | Fail-open classification | Silent FP (over-merge) | retry-storm on LLM failure (fallback still merges) | Pattern-match LLM defaults `is_match=True`; any exception returns a vector-similarity “match”. Confidence is not gated. |
| 7 | D | P1 | Confirmed | `pattern_service.py:81-106, 369-379` | Identity collision (Issue #2 lens) | Silent FP | none | Pattern *creation* merges into an existing row on normalized title. Distinct incidents with a generic title become one pattern. |
| 8 | D | P1 | Confirmed | `pattern_tasks.py:201-274, 327`; `pattern_service.py:200-251` | Single-linkage + frozen confidence | Silent FP/FN | unbounded LLM-per-candidate | Join threshold is cosine *distance* `< 0.35` to *any* member (`LIMIT 1`, no `ORDER BY`). Confidence is frozen at create; `0.0 or 0.8` stores 0.8. |
| 9 | D | P1 | Confirmed | `models/pattern.py:23-57`; `pattern_service.py:88-126` | Race / no uniqueness | Silent FP (duplicate patterns) | redundant-call (two syntheses) | No unique constraint on `(tenant, domain, title)` or episode membership. Title pre-check then insert is TOCTOU. |
| 10 | E | P1 | Confirmed | `api/v1/playbooks.py:654-735`; `pattern_tasks.py:416-576` | Creation vs worker constraints | Silent FP (duplicate/hollow playbooks) | redundant-call | Manual `/playbooks/generate` does not apply the worker’s existing-playbook skip, confidence floor, empty-steps fail, knowledge retrieval, risk floor, or episode ids. |
| 11 | E | P1 | Confirmed | `execution_service.py:776-804`; `pattern_tasks.py:47-54`; `schemas/playbook.py:64-68` | Creation vs execution safety | Silent FP then loud exec fail | none | Missing `safety_class` → `read_only` (fail-open). Unknown class is persisted by generators (no Pydantic path) and then rejected at `start_execution` — Issue #7’s class of bug. |
| 12 | E | P1 | Confirmed | `pattern_tasks.py:416-426`; `pattern_service.py:244-247`; `api/v1/patterns.py:79-84` | Stale playbook / duplicate create | Silent FN then FP | redundant-call | Adding episodes enqueues generation; worker skips because a playbook exists. UI `review_needed` then hits the API and creates a *second* playbook. |
| 13 | E | P1 | Confirmed | `api/v1/playbooks.py:281-296`; `playbook_embedding.py:86-98`; `playbook_service.py:422`; `execution_service.py:669-685` | Version / lineage drift | Silent FP | none | Orphan playbooks are shown as derived from the tenant’s largest pattern. Draft-version embeddings can disagree with the published version execution actually runs. |
| 14 | G | P1 | Confirmed | `vector_search.py:218`; `knowledge_retrieval_service.py:305-307, 468-469`; `pattern_tasks.py:146-155`; `extraction_tasks.py:68`; `pattern_tasks.py:680-684` | Unattributed / sequential / full-retry | n/a (spend) | redundant-call + retry-storm | Knowledge retrieval embeds twice (once unattributed). Episode repair embeds one-at-a-time. Celery retries replay the entire playbook pipeline. Evidence embed bypasses the budget gate. |
| 15 | Frontend | P1 | Confirmed | `api/v1/playbooks.py:179-200`; `playbooks/[id]/page.tsx:897-900`; `providers.tsx:13`; `runtime.py:29, 232-236` | Stale / defaulted scores | Silent FP in UI | none | List paints confidence `0.8` when missing and picks an unordered version. Detail falls back to `0.8`. React Query `staleTime` 30s; runtime match cache 3600s, flushed only on lifecycle transition. |
| 16 | A | P2 | Confirmed | `chunk_rollup.py:98-107` | Nondeterministic tie-break | Silent rank jitter | none | MMR iterates a `set` of remaining indexes; equal scores keep first-seen, which is hash-order. |

---

## 2. Detailed Findings

### Issue #1: Cosine distance is converted three incompatible ways

Location: `backend/src/contextedge/search/hybrid_ranker.py:45-54`; `backend/src/contextedge/search/chunk_rollup.py:45-47`; `backend/src/contextedge/services/knowledge_retrieval_service.py:584-586`; `backend/src/contextedge/services/correlation_suggestion_service.py:189`; `backend/src/contextedge/graph/agent/repository.py:401-429`

Original code (verbatim quote):

```python
def _semantic_corpus_score(rows: list) -> tuple[float, int]:
    """Map best semantic distance to [0,1]; cosine distance typically in [0, 2]."""
    if not rows:
        return 0.0, 0
    distances = [float(r[1]) for r in rows if r[1] is not None]
    if not distances:
        return 0.0, len(rows)
    best = min(distances)
    score = max(0.0, 1.0 - (best / 2.0))
    return score, len(rows)
```

```python
    def relevance(self) -> float:
        # Cosine distance lives in [0, 2] → [0, 1] relevance.
        return 1.0 - min(max(self.distance, 0.0), 2.0) / 2.0
```

```python
        similarity = 1.0 - min(max(float(document.best_distance), 0.0), 1.0)
        if similarity < KNOWLEDGE_LINK_MIN_SIMILARITY:
            continue
```

```python
            similarity = 1.0 - float(dist)
            if similarity > best.get(other_id, 0.0):
                best[other_id] = similarity
```

```python
                similarity = 1.0 - min(max(float(episode_distance), 0.0), 1.0)
                if similarity < 0.5:
                    continue  # unrelated history is noise, not context
```

pgvector’s `<=>` / `.cosine_distance` is `1 - cosine_similarity`, range `[0, 2]` for L2-normalized embeddings. OpenAI/Gemini embeddings are typically L2-normalized, so a “quite similar” pair sits near distance `0.20` (cosine `0.80`).

**Is the vector L2-normalized before a Python dot product?** In MMR, yes: `_normalized_matrix` L2-normalizes then does `matrix @ matrix.T` (true cosine). Ranking/search/knowledge do **not** use a raw Python dot product; they use pgvector cosine *distance* and then disagree on how to turn it into a similarity/score.

Flawed logic: the ranker and MMR map `d` through `/ 2.0` (full `[0, 2]` range). Knowledge links, correlation suggestions, and agent seeds treat `d` as already on `[0, 1]` via `1 - d`. The same stored distance therefore means different things downstream.

Concrete failure trace:

- Given input: query vs playbook-linked chunk, pgvector `distance = 0.30` (cosine similarity `0.70`).
- Step 1: Ranker `_semantic_corpus_score` → `1.0 - (0.30 / 2.0) = 0.85`.
- Step 2: `persist_knowledge_links` → `1.0 - 0.30 = 0.70`, which is `< 0.75`, so no `supported_by` edge.
- Step 3: Correlation `_semantic_candidates` → `0.70`, which meets `SIMILARITY_FLOOR = 0.7`, so a reviewer suggestion is written.
- Step 4: Agent seed → `0.70 >= 0.5`, episode/playbook is seeded.
- Resulting fault: ranking treats the pair as a strong semantic hit (and can clear `MIN_RECOMMENDATION_SCORE = 0.35` even more easily, compounding Issue #7). Knowledge graph refuses to assert the same pair. Correlation bothers a reviewer. Silent, not an exception.

**Accuracy impact:** Silent false positives in ranking (over-trigger recommendations); silent false negatives on knowledge edges for the same distance band `~0.26–0.35`. Direction: ranker over-triggers relative to the rest of the pipeline. No fabricated corpus percentage; the `0.30` fixture is the identity `1 - cosine_sim`.

**Cost impact:** none (pure arithmetic).

Corrected logic (pick one mapping and use it everywhere; `1 - d` matches how knowledge thresholds were measured):

```diff
-    score = max(0.0, 1.0 - (best / 2.0))
+    score = max(0.0, 1.0 - min(max(best, 0.0), 1.0))
```

```diff
-        return 1.0 - min(max(self.distance, 0.0), 2.0) / 2.0
+        return max(0.0, 1.0 - min(max(self.distance, 0.0), 1.0))
```

Clamp correlation the same way (`max(0.0, 1.0 - min(max(dist, 0.0), 1.0))`) so distances `> 1` cannot go negative and then sort oddly.

Also Confirmed (not a separate issue): `generate_embedding` **does** fail loud on dimension mismatch (`provider.py:787-793`). MMR **does not** truncate/pad: a dim mismatch raises `ValueError` and `_normalized_matrix` returns `None`, degrading to distance sort (`chunk_rollup.py:75-76, 91-92`). Zero-norm in MMR is Issue #2.

---

### Issue #2: Empty text is stored as a 3072-zero “embedding”

Location: `backend/src/contextedge/ai/embeddings.py:19-35, 61-63`; `backend/src/contextedge/search/chunk_rollup.py:72-74`; `backend/src/contextedge/workers/extraction_tasks.py:65-69`

Original code (verbatim quote):

```python
    text = "\n\n".join(text_parts) if text_parts else ""
    if not text:
        return [0.0] * 3072
    return await generate_embedding(text, tenant_id=tenant_id, db=db)
```

```python
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms
```

```python
async def _ensure_embedding(db: AsyncSession, evidence: EvidenceItem) -> bool:
    if evidence.embedding is not None:
        return False
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
```

Flawed logic: a missing body is encoded as an all-zero vector so `embedding IS NOT NULL` gates treat the row as embedded. Cosine distance is `1 - (a·b)/(|a||b|)`. `|a| = 0` is division by zero (NaN in pgvector). MMR avoids NaN by rewriting a 0-norm to `1.0`, leaving the zero vector unchanged; `0 · v = 0` for every neighbour, so MMR thinks the chunk is orthogonal to everything, including another zero vector.

Concrete failure trace:

- Given input: evidence with `title=None`, `body_text=""` (or only stripped-to-empty content).
- Step 1: `embed_evidence` returns `[0.0] * 3072` **without** calling the provider.
- Step 2: `_ensure_embedding` stores it; `embedded: true`.
- Step 3: Semantic search includes the row (`embedding.is_not(None)`).
- Step 4: SQL `cosine_distance` vs a real query vector is NaN; `NaN < 0.35` is not true, so pattern clustering would not join it, but ANN `ORDER BY` of NaN is not a meaningful rank.
- Step 5: If another empty item exists, MMR similarity between them is `0.0`, not `1.0`.
- Resulting fault: empty rows pollute the “has embedding” population and can occupy oversample slots. Silent.

**Accuracy impact:** Silent false positives (empty items look indexed) and false negatives (they never genuinely match). Direction: retrieval quality degrades for tenants with quote-stripped or marker-only bodies (`QUOTED_ONLY_MARKER` still has text; truly empty title+body is the trigger).

**Cost impact:** the zero path skips the paid call (cost-safe already). The bug is accuracy, not spend.

Corrected logic (fail closed: leave NULL, do not pretend it is a vector):

```diff
     text = "\n\n".join(text_parts) if text_parts else ""
     if not text:
-        return [0.0] * 3072
+        raise ValueError("refusing to persist a zero embedding for empty text")
```

Callers already treat embed failure as soft (`embedding_failed` log) and leave `embedding` NULL — which search already excludes. Same change for `embed_decision`. Do **not** “fix” this by calling the embedding API on empty strings.

---

### Issue #3: Embeddings are write-once relative to source text

Location: `backend/src/contextedge/workers/extraction_tasks.py:65-69`; `backend/src/contextedge/services/playbook_service.py:307-317`

Original code (verbatim quote):

```python
async def _ensure_embedding(db: AsyncSession, evidence: EvidenceItem) -> bool:
    if evidence.embedding is not None:
        return False
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
```

```python
        if playbook.embedding is None:
            # "approved" is exactly the state the agent seed resolver's
            # semantic layer filters on (migration 0035), so repair the
            # fingerprint here: pre-0035 rows and playbooks whose embed
            # failed transiently at version creation become semantically
            # matchable the moment a reviewer approves them. Best-effort —
            from contextedge.services.playbook_embedding import embed_playbook

            await embed_playbook(db, playbook, approved_version)
```

Flawed logic: evidence embeddings are never recomputed when title/body change (attachment merge in `artifact_extraction_service.py:510` *does* overwrite — that path is fine). Playbook approval skips re-embed if a candidate-era vector already exists, so a playbook edited between candidate and approve keeps the old fingerprint. Agent seeds (`graph/agent/repository.py:315-323`) filter `lifecycle_state == "approved"` and rank by that column.

Concrete failure trace:

- Given input: ticket body replaced on re-normalize after a connector update; `evidence.embedding` already set from the first body.
- Step 1: `_ensure_embedding` returns `False`.
- Step 2: Chunk tasks skip chunks with `embedding is not None` (`chunk_tasks.py:157`) — parent stale; new chunks may still embed if IDs are new.
- Resulting fault: parent-pass search (`vector_search.py:230-241`) matches the old meaning. Silent.

Playbook path:

- Given input: candidate generated from pattern v1 text; reviewer edits steps; then approves.
- Step 1: `create_playbook_version` already wrote `playbook.embedding` at candidate time (worker `pattern_tasks.py:655-657`).
- Step 2: `transition_playbook` sees `embedding is not None`, skips.
- Step 3: Agent semantic seed retrieves the playbook for queries that match the *old* candidate text, not the approved steps.
- Resulting fault: silent mismatch between what the agent retrieves and what execution will run (see also Issue #13).

**Accuracy impact:** Silent FN (updated text not found) and FP (old text still matches). Compounds into ranking and agent seeds.

**Cost impact:** none as written (it *avoids* re-embed). A cost-safe fix is a content-hash key, not unconditional re-embed:

```diff
 async def _ensure_embedding(db: AsyncSession, evidence: EvidenceItem) -> bool:
-    if evidence.embedding is not None:
-        return False
-    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
+    material = f"{evidence.title or ''}\n\n{(evidence.body_text or '')[:8000]}"
+    digest = hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
+    if evidence.embedding is not None and (evidence.attributes or {}).get("embedding_input_sha256") == digest:
+        return False
+    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
+    # stamp digest on a cheap JSON/column; skip the API when text is unchanged
```

Same hash-gate for `embed_playbook` on approve (recompute only if version text changed).

---

### Issue #4: Keyword score is min-max normalized against the in-request FTS population

Location: `backend/src/contextedge/search/hybrid_ranker.py:264-269, 174-180`

Original code (verbatim quote):

```python
        fts_results = await search_playbooks_fts(db, tenant_id, query_text, limit=50)
        max_rank = max((r for _, r in fts_results), default=1.0) or 1.0
        for playbook, rank in fts_results:
            fts_scores[playbook.id] = float(rank) / max_rank
```

```python
def _quality_score(playbook_confidence: float, evidence_hits: int) -> float:
    support = min(evidence_hits / 5.0, 1.0)
    return min(max(0.6 * playbook_confidence + 0.4 * support, 0.0), 1.0)
```

L2 normalization exists only inside MMR (`chunk_rollup.py`). There is no z-score path. Quality/graph use **fixed caps** (`/ 5.0`), which are consistent across requests. FTS does not.

Zero-range: `max(...) or 1.0` turns an all-zero `ts_rank` into divisor `1.0`, so scores become `0`. Single-item: divisor equals that item’s rank, so the score is **always 1.0** regardless of how weak the match is.

Concrete failure trace:

- Given input: query `"vpn"`. Only one approved playbook’s tsvector matches, with `ts_rank = 0.004`.
- Step 1: `max_rank = 0.004`.
- Step 2: `keyword_score = 1.0`.
- Step 3: Weighted keyword contribution `0.25 * 1.0 = 0.25`. With a middling semantic `0.85` from Issue #1, `total ≈ 0.25 + 0.30*0.85 + …` clears `0.35` even if graph/identity are 0.
- Contrast: the same playbook in a tenant with 20 FTS hits and `max_rank = 0.08` would get `keyword_score = 0.05`.
- Resulting fault: **the same playbook + same query scores differently depending on who else matched in this call.** Silent. Single-item population is the worst case (always 1.0).

**Accuracy impact:** Silent false positives when the FTS result set is small (cold-start tenants, narrow domains). Direction: over-triggering of recommendations. This makes Issue #7 easier to hit: expiry only zeroes freshness/recency (`0.15` of weight); an inflated keyword+semantic remainder still passes the gate.

**Cost impact:** none.

Corrected logic (fixed mapping, not batch-relative):

```diff
-        max_rank = max((r for _, r in fts_results), default=1.0) or 1.0
-        for playbook, rank in fts_results:
-            fts_scores[playbook.id] = float(rank) / max_rank
+        for playbook, rank in fts_results:
+            fts_scores[playbook.id] = min(float(rank) / 0.1, 1.0)  # cap vs a fixed ts_rank scale
```

(The constant should be calibrated once from `ts_rank` histograms, then frozen — not recomputed per request.)

---

### Issue #5: Relevance classification is not enum-validated and the skip gate uses a different string than storage

Location: `backend/src/contextedge/ai/classifiers/relevance.py:70-72`; `backend/src/contextedge/workers/extraction_tasks.py:436-478, 648-679`; `backend/src/contextedge/ai/prompts/relevance.py:18-21`

Original code (verbatim quote):

```python
    return {
        "classification": result.get("classification", "not_relevant"),
        "confidence": float(result.get("confidence", 0.5)),
```

```python
        classification_label = cls.get("classification", "not_relevant")
        classification_confidence = float(cls.get("confidence", 0.0))
        ev.relevance_state = classification_label.replace(" ", "_")
        ev.relevance_score = classification_confidence
...
    skip_extraction = (
        classification_label == "not_relevant"
        and classification_confidence is not None
        and classification_confidence >= 0.75
    )
```

```python
    label = out.get("classification", "not_relevant")
    ev.relevance_state = label.replace(" ", "_")
...
        "needs_fanout": (
            ev.relevance_state in ("operational", "possibly_relevant")
```

The prompt allows only `operational | possibly_relevant | not_relevant`. The classifier does not check that. Contrast `classify_message_function`, which *does* fail closed to `unclassified` (`message_function.py:58-60`).

Operator notes:

- Skip uses `>= 0.75` (inclusive). Conservative vs missing extraction, as commented.
- Skip compares the **raw** label to `"not_relevant"`. Storage lowercases spaces to underscores. `"not relevant"` is stored as `not_relevant` but does **not** skip.
- Missing `classification` defaults to `"not_relevant"`. Combined with a present high `confidence` (the model filled confidence but dropped the label), skip fires.
- Classifier exception: label stays `None`, skip is false, `relevance_state` remains `unclassified` — fail-open to the expensive path (cost), not a fake `"operational"` (accuracy-safer).
- Downstream episode reconstruction keeps only `operational` / `possibly_relevant` (`api/v1/episodes.py:329`). Unknown stored labels are silently dropped from episodes.

Concrete failure trace A (false negative / skip):

- Given input: LLM JSON `{"confidence": 0.92}` (no classification key).
- Step 1: `classification` defaults to `"not_relevant"`.
- Step 2: `skip_extraction` is true (`0.92 >= 0.75`).
- Resulting fault: a real incident is never embedded, chunked, or identity-extracted. Silent. Direction: under-trigger of the memory pipeline.

Concrete failure trace B (label drift):

- Given input: `{"classification": "not relevant", "confidence": 0.9}`.
- Step 1: skip is false (string mismatch).
- Step 2: `relevance_state = "not_relevant"`.
- Step 3: UI shows not-relevant; extraction still ran; later episode filter excludes it.
- Resulting fault: paid extraction on an item the UI claims is noise; still never becomes an episode. Silent inconsistency.

**Accuracy impact:** Silent FN on skip path; silent drop from episodes for non-enum labels. Message-function classifier: **no issue found** (enum-validated, malformed confidence clamped).

**Cost impact:** exception path fail-opens to embed+extract+chunk (redundant relative to a fail-closed unclassified). Trigger: provider 5xx during classify. Cost-safe fix: persist `unclassified` and skip fan-out until a bounded reclassify task runs — do not add retries here.

Corrected logic:

```diff
-        "classification": result.get("classification", "not_relevant"),
-        "confidence": float(result.get("confidence", 0.5)),
+    ALLOWED = {"operational", "possibly_relevant", "not_relevant"}
+    raw = result.get("classification")
+    label = raw.replace(" ", "_") if isinstance(raw, str) else None
+    if label not in ALLOWED:
+        label = "unclassified"
+    try:
+        confidence = float(result.get("confidence"))
+    except (TypeError, ValueError):
+        confidence = 0.0
+    ...
+    skip_extraction = (
+        label == "not_relevant" and math.isfinite(confidence) and confidence >= 0.75
+    )
```

Use the same `label` for both persist and skip. Do not default missing classification to `not_relevant`.

---

### Issue #6: Pattern-match validation fails open to `is_match=True`

Location: `backend/src/contextedge/ai/extractors/pattern_extractor.py:94-112`; `backend/src/contextedge/workers/pattern_tasks.py:211-234`

Original code (verbatim quote):

```python
        if isinstance(res, dict):
            return {
                "is_match": bool(res.get("is_match", True)),
                "confidence": float(res.get("confidence", 0.8)),
                "reason": str(res.get("reason", "AI match evaluation")),
            }
    except Exception:
        pass

    # Safe fallback if LLM is unavailable
    return {"is_match": True, "confidence": 0.75, "reason": "Vector similarity fallback"}
```

```python
                    Episode.embedding.cosine_distance(ep.embedding) < 0.35,
...
                    if ai_val.get("is_match"):
                        await add_episode_to_pattern(db, tid, matched_pattern_id, ep.id)
```

Flawed logic: the comment says “Safe fallback”. For membership in an operational pattern, fail-open is the unsafe direction. `bool(res.get("is_match", True))` treats a missing key as match; `bool("false")` is `True`. `float("high")` raises, hits `except`, and still returns match. Returned `confidence` is logged and **never compared to a floor**. Vector pre-filter is cosine *distance* `< 0.35` (cosine `> ~0.65`), much looser than episode-merge’s `0.85` (`episode_service.py:619`), and is `LIMIT 1` with **no `ORDER BY`** — any member within 0.35 of a *single* episode pulls the candidate in (single-linkage chaining).

Concrete failure trace:

- Given input: episode E about “VPN cert expired”; pattern P’s nearest member is a generic “network down” episode at distance `0.34`. LLM times out / returns `{}`.
- Step 1: SQL `LIMIT 1` returns P (whichever row the planner produced first).
- Step 2: `validate_pattern_match` excepts or sees missing `is_match`.
- Step 3: `{"is_match": True, "confidence": 0.75, ...}`.
- Step 4: `add_episode_to_pattern` links E to P, bumps `episode_count`, enqueues playbook generation (Issue #12).
- Resulting fault: unrelated incidents share a pattern. Playbook synthesis then averages them. Silent FP. Compounds into ranking (playbook evidence links) and execution candidates.

**Accuracy impact:** Silent false positives (over-merge). Direction: over-trigger pattern membership. The live episode-dedup comments (`episode_service.py:607-618`) already measured distinct incidents up to cosine `0.578`; distance `0.35` is cosine `0.65`, above that sample’s false pairs but far below the `0.85` they refused to use alone — and then the LLM gate is a no-op on failure.

**Cost impact:** one LLM call per unlinked candidate that has any neighbour `< 0.35`. On LLM failure the call is wasted and the merge still happens. Trigger: provider outage during clustering. Cost-safe fix: fail closed (`is_match=False`) so the worker does not retry validation in a loop; leave the episode for the next cluster pass’s “new pattern” path instead of merging.

Corrected logic:

```diff
         if isinstance(res, dict):
+            raw_match = res.get("is_match")
+            if not isinstance(raw_match, bool):
+                return {"is_match": False, "confidence": 0.0, "reason": "invalid_is_match"}
             return {
-                "is_match": bool(res.get("is_match", True)),
+                "is_match": raw_match,
...
-    return {"is_match": True, "confidence": 0.75, "reason": "Vector similarity fallback"}
+    return {"is_match": False, "confidence": 0.0, "reason": "validator_unavailable"}
```

And require `confidence >= 0.7` (or similar) before `add_episode_to_pattern`. Order matches by distance and take the nearest pattern, not `LIMIT 1` unordered.

---

### Issue #7: Pattern creation merges on normalized title (Issue #2 lens, at create time)

Location: `backend/src/contextedge/services/pattern_service.py:81-106`; title sweep at `369-379`

Original code (verbatim quote):

```python
    # Preventive Deduplication: merge into an existing pattern when the
    # title matches — scoped to the SAME domain ...
    clean_title = title.strip()
    existing_pattern = None
    try:
        existing_pattern_res = await db.execute(
            select(Pattern).where(
                Pattern.tenant_id == tenant_id,
                Pattern.domain_id == domain_id,
                Pattern.active_flag.is_(True),
                func.lower(Pattern.title) == clean_title.lower(),
            ).limit(1)
        )
        existing_pattern = existing_pattern_res.scalar_one_or_none()
```

```python
        key = p.title.strip().lower()
        grouped_patterns.setdefault(key, []).append(p)
```

The prior audit’s Issue #2 is automated *evidence* deletion on `(title, evidence_type)`. This is the same identity-collision applied at **pattern creation**: LLM-synthesized titles like `"Database unavailable"` or `"Auto: VPN issue"` collapse distinct clusters. The later housekeeping sweep repeats the same key.

Concrete failure trace:

- Given input: cluster A (Oracle tablespace) synthesizes title `"Database unavailable"`; cluster B (SQL Server log full) synthesizes the same title in the same domain.
- Step 1: A inserts a new `Pattern`.
- Step 2: B’s `create_pattern_from_episodes` finds A by `lower(title)`, calls `add_episode_to_pattern` for B’s episodes, returns A.
- Step 3: Playbook generation (if any) sees a mixed episode set.
- Resulting fault: two incidents become one pattern without an embedding-distance or content-hash check. Silent FP. Does **not** delete evidence (unlike Issue #2) but does fuse operational knowledge.

**Accuracy impact:** Silent false positives on “same pattern”. Direction: over-merge. Generic titles are common in this corpus (Issue #2’s `"Database unavailable"` fixture applies directly).

**Cost impact:** none extra (avoids a second synthesis persist); accuracy cost is the merge.

Corrected logic: stop using title as identity. Require a new pattern row unless vector distance to the existing pattern’s members is below the cluster threshold **and** Issue #6’s validator passes. Title collision should be a review flag, not a merge.

---

### Issue #8: “Same pattern” is single-linkage distance, hardcoded, and confidence never updates

Location: `backend/src/contextedge/workers/pattern_tasks.py:201-274, 327`; `backend/src/contextedge/services/pattern_service.py:223-247`

Original code (verbatim quote):

```python
            # First, check if candidate episode is close (cosine distance < 0.35)
            # to an EXISTING pattern
...
                    Episode.embedding.cosine_distance(ep.embedding) < 0.35,
...
            # Find similar episodes using vector distance (threshold 0.20)
...
                    Episode.embedding.cosine_distance(ep.embedding) < 0.20
```

```python
                    confidence=float(synthesis.get("confidence") or 0.8),
```

```python
        pattern.episode_count += 1
        await db.flush()
...
            generate_playbook_candidate.delay(str(pattern.id), str(tenant_id))
```

`add_episode_to_pattern` increments `episode_count` and does not touch `pattern.confidence`. `0.0 or 0.8` is `0.8` in Python; `float("nan")` is truthy so NaN is stored (same family as prior Issue #8).

Thresholds are literals, not derived from the current embedding model’s score distribution. They also disagree with episode supersession (`SIMILAR_EPISODE_MIN_COSINE = 0.85` ⇒ distance `0.15`). Clustering uses `Vector.cosine_distance`, not `halfvec_cosine_distance`, so it does not use the 0032 HNSW expression (full scan + fp32 vs search’s fp16).

Concrete failure trace (chaining):

- Given input: episodes A–D in a line with adjacent distances `0.33`, none of A vs D closer than `0.55`.
- Step 1: A+B form a pattern (pair `< 0.20`).
- Step 2: C is `< 0.35` from B, `LIMIT 1` hits the pattern, fail-open validator (Issue #6) joins C.
- Step 3: D joins via C.
- Resulting fault: A and D share a pattern though they would not cluster as a pair. Silent FP. Model-version change that shifts typical cosine by `0.05` would over- or under-merge globally (threshold sensitivity).

Concrete failure trace (confidence):

- Given input: synthesis `{"confidence": 0.0, "title": "Agent unknown state"}`.
- Step 1: `0.0 or 0.8` → stored `0.8`.
- Step 2: Worker playbook gate `0.8 >= 0.5` generates a playbook from a model that reported zero confidence.
- Resulting fault: silent FP into playbook creation.

**Accuracy impact:** Silent over-merge (chaining, loose 0.35) and over-generation (0.0→0.8, frozen high confidence). New contrary evidence never lowers confidence, so the playbook floor cannot later fail closed.

**Cost impact:** `validate_pattern_match` LLM per candidate (Issue #6) plus `synthesize_pattern` per new cluster, with all episode steps in the prompt (`pattern_extractor.py:26-35`, 5 steps × unbounded cluster). Burst of N similar tickets → up to N validation calls + one large synthesis. Cost-safe: cap cluster size and skip the LLM validator when distance `< 0.20` (already the “same cluster” band); fail closed on validator errors instead of retrying.

Corrected logic (minimum):

```diff
-                    confidence=float(synthesis.get("confidence") or 0.8),
+                    confidence=_finite_unit_interval(synthesis.get("confidence"), default=None)
+# if confidence is None: do not create; leave episodes unassigned
```

```diff
+        # recompute confidence from member count / validator scores; never OR-away 0.0
         pattern.episode_count += 1
```

Use nearest-centroid or require distance `< 0.20` to *all* sampled members, not `< 0.35` to one unordered member.

---

### Issue #9: Concurrent pattern creation has no uniqueness constraint

Location: `backend/src/contextedge/models/pattern.py:23-57` (Pattern table: title is non-unique); `backend/src/contextedge/services/pattern_service.py:88-126`; `backend/src/contextedge/workers/celery_app.py:271`

Original code (verbatim quote):

```python
    title: Mapped[str] = mapped_column(String(500), nullable=False)
```

```python
        existing_pattern = existing_pattern_res.scalar_one_or_none()
    except Exception:  # noqa: BLE001
        existing_pattern = None

    if existing_pattern:
        ...
        return existing_pattern

    pattern = Pattern(
```

```python
        "pattern.*": {"queue": "pattern"},
```

`PatternEvidenceLink` has no unique `(pattern_id, episode_id)` either. The title `SELECT` then `INSERT` is TOCTOU. Routing to a named queue does **not** set `worker_concurrency=1` in this repo. Comments in `pattern_tasks.py:352-356` admit the API-triggered dedup sweep can overlap clustering.

Concrete failure trace:

- Given input: two overlapping candidate sets processed by two `pattern` workers (or cluster + `POST /patterns/discover`).
- Step 1: Both title lookups miss.
- Step 2: Both insert `Pattern` rows and `PatternEvidenceLink` rows for overlapping episodes.
- Step 3: `_linked_episode_ids` is computed once at the start of `_cluster`, so in-flight inserts are invisible to the other worker’s candidate list.
- Resulting fault: duplicate patterns. Later title-dedup (Issue #7) may fuse them — or not, if titles differ. Silent FP. Two `synthesize_pattern` + two `generate_playbook_candidate` LLM calls.

**Accuracy impact:** Silent duplicate patterns/playbooks. Direction: over-create.

**Cost impact:** redundant synthesis/generation. Trigger: concurrent cluster/discover. Cost-safe fix: `pg_advisory_lock` per tenant+domain (the reconstruct path already uses this pattern in `extraction_tasks.py:1048`) plus a unique index on active `(tenant_id, domain_id, lower(title))` **only if** title stops being a merge key — better: unique episode membership (`UNIQUE(episode_id) WHERE episode_id IS NOT NULL`) so an episode cannot join two patterns.

Confidence: **Confirmed** absence of constraints and TOCTOU. Live double-insert is **Suspected** unless more than one `pattern` consumer runs.

---

### Issue #10: Manual playbook generation does not re-check the worker’s creation constraints

Location: `backend/src/contextedge/api/v1/playbooks.py:654-735`; worker gates at `backend/src/contextedge/workers/pattern_tasks.py:411-576`

Original code (verbatim quote):

```python
        existing = await db.execute(
            select(Playbook).where(
                Playbook.tenant_id == tid,
                or_(
                    Playbook.pattern_id == pid,
                    func.lower(Playbook.title) == pattern.title.strip().lower(),
                ),
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "skipped", "reason": "playbook_already_exists"}
...
        if pattern_confidence < PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE:
            ...
            return {"status": "skipped", "reason": "pattern_confidence_below_floor", ...}
...
        if not steps:
            ...
            return {"status": "failed", "reason": "no_steps_generated"}
```

API path (no equivalents):

```python
        ep_summaries = []
        for ep in episodes:
            ep_summaries.append({
                "title": ep.title,
                "root_cause": ep.root_cause_summary,
                "outcome": ep.final_outcome
            })
...
        candidate = await generate_playbook_candidate(
            pattern.title,
            pattern.description or "",
            len(episodes),
            ep_summaries,
            negative_knowledge,
            tenant_id=user.tenant_id,
            db=db,
        )
...
            risk_tier=candidate.get("risk_tier", "medium"),
...
        await create_playbook_version(db, playbook, candidate)
```

Gaps vs worker (and vs execution):

| Check | Worker | API `/playbooks/generate` | `start_execution` |
|---|---|---|---|
| Pattern still exists | skip | 404 if gone; **no `active_flag` check** | n/a |
| Playbook already exists | skip | **creates another** | n/a |
| Confidence floor 0.5 | skip | **none** | n/a |
| Knowledge retrieval | yes | **not passed** (`knowledge_sources` omitted) | n/a |
| Empty steps | fail | **persists** | approve path later rejects empty (`playbook_service.py:251-258`) |
| Episode ids on summaries | yes | **omitted** → citations cannot resolve | n/a |
| `_effective_risk_tier` | yes | raw LLM `risk_tier` or `"medium"` | n/a |
| Expiry / published version | n/a | n/a | enforced (Issue #7) |
| Step `safety_class` enum | not validated | not validated | **raises** on unknown |

`generate_playbook_candidate` (generator) still runs `validate_source_refs` / `classify_step_grounding`. Without episode `id`s, every `[ep-N]` citation is dropped and steps are forced to `best_practice` (`playbook_generator.py:129-134`). Silent accuracy loss: a playbook generated from real incidents looks ungrounded.

Concrete failure trace:

- Given input: pattern with `confidence=0.4`, existing candidate playbook, UI `review_needed` (Issue #12). User clicks Generate.
- Step 1: API does not skip on existing playbook or low confidence.
- Step 2: LLM is called **without** KB/SOP context the worker would have fetched.
- Step 3: New `Playbook` row, `lifecycle_state` default `candidate`, possibly empty or ungrounded steps.
- Step 4: Reviewer later approves; ranking can surface it; execution may then refuse empty/unknown-safety steps.
- Resulting fault: duplicate, weaker playbook. Silent at create; loud later. Same ranking-vs-execution class as Issue #7.

**Accuracy impact:** Silent FP (duplicate / ungrounded / low-confidence playbooks). FN on knowledge-backed steps.

**Cost impact:** full playbook LLM without the worker skip. Trigger: UI generate on a pattern that already has a playbook, or on a low-confidence pattern. Cost-safe fix: reuse the worker function (or its gates) so the API cannot take the expensive path the worker already refused; do not “fix” by generating a second, better playbook automatically.

Corrected logic: call the same `work()` the Celery task runs, including skip reasons mapped to HTTP 409/422.

---

### Issue #11: Missing `safety_class` is fail-open `read_only`; unknown class is ranking-ok / execution-fail

Location: `backend/src/contextedge/services/execution_service.py:776-804`; `backend/src/contextedge/workers/pattern_tasks.py:47-54`; `backend/src/contextedge/schemas/playbook.py:64-68`; `backend/src/contextedge/services/playbook_service.py:372, 396`

Original code (verbatim quote):

```python
            step_safety = step_data.get("safety_class", "read_only")
            needs_approval = bool(step_data.get("requires_approval", False))
...
        if _safety_class_rank(step_safety) > _safety_class_rank(effective_safety_class):
            needs_approval = True
...
            safety_class=step_safety,
```

```python
        step_floor = _SAFETY_CLASS_RISK_FLOOR.get(
            str(step.get("safety_class") or "read_only"), "high"
        )
```

```python
    def _validate_safety_class(cls, v: str | None) -> str | None:
        if v is not None and v not in SAFETY_CLASSES:
            raise ValueError(f"safety_class must be one of {SAFETY_CLASSES}")
        return v
```

`PlaybookVersionCreate.steps: list[PlaybookStep]` validates enum **only** on `POST /playbooks/{id}/versions`. Worker and `POST /playbooks/generate` pass a raw dict into `create_playbook_version` (`steps=version_data.get("steps", [])`) and never run that validator. `_safety_class_rank` fail-closes on **unknown** strings (`execution_service.py:51-60`) — after the playbook is already `approved` and rankable.

Concrete failure trace (missing class, fail-open):

- Given input: generated step `{"title": "Restart database cluster"}` with no `safety_class`.
- Step 1: Worker risk floor treats it as `read_only` → playbook `risk_tier` may stay `medium`.
- Step 2: Reviewer approves (steps non-empty).
- Step 3: Ranker includes it (approved, Issue #7 expiry still applies separately).
- Step 4: `start_execution` stores `safety_class="read_only"`. Approval is not forced by safety class.
- Resulting fault: a destructive instruction executes under read-only governance. Silent at create; materially wrong at execution if tools are wired. P1 here because this repo still has no in-process tool executor (prior Issue #1 validation), but the recorded step class is wrong.

Concrete failure trace (unknown class, Issue #7-shaped):

- Given input: step `{"safety_class": "write"}`.
- Step 1: Persisted on generated version.
- Step 2: Ranker recommends the approved playbook.
- Step 3: `start_execution` raises `Unknown safety class 'write'`.
- Resulting fault: recommend-then-refuse, same user-visible class as Issue #7.

**Accuracy impact:** Silent FP (under-classified steps) and loud FN (unknown class). Creation-time validation does **not** match execution.

**Cost impact:** none.

Corrected logic: validate steps with `PlaybookStep` inside `create_playbook_version` (all callers). Treat missing `safety_class` as a creation error, not `read_only`. Do not default.

---

### Issue #12: Pattern growth cannot refresh a playbook; the UI then creates a duplicate

Location: `backend/src/contextedge/workers/pattern_tasks.py:416-426`; `backend/src/contextedge/services/pattern_service.py:244-247`; `backend/src/contextedge/api/v1/patterns.py:79-84`; `frontend/src/app/(dashboard)/patterns/page.tsx:35-75`

Original code (verbatim quote):

```python
        if existing.scalar_one_or_none():
            return {"status": "skipped", "reason": "playbook_already_exists"}
```

```python
            generate_playbook_candidate.delay(str(pattern.id), str(tenant_id))
```

```python
            if (
                pb_updated_at
                and pat.updated_at
                and (pat.updated_at - pb_updated_at).total_seconds() > 5
            ):
                resp.playbook_status = "review_needed"
```

```typescript
    mutationFn: () => api.post("/playbooks/generate", { pattern_id: pattern.id }),
...
              title="New episodes added — click to update Playbook"
```

`Pattern.updated_at` has `onupdate=func.now()` (`models/base.py:17-18`). Adding an episode bumps it; playbook `updated_at` does not. UI shows `review_needed` and POSTs `/playbooks/generate`, which **creates** (Issue #10), while the auto worker **skips**. Toast says “Playbook candidate updated!” — it was not an update.

The pattern→playbook link is not re-validated for deleted/merged/superseded patterns on the worker beyond `pattern_not_found`. Hard delete of a pattern that still has playbooks hits the FK (`Playbook.pattern_id` has no `ondelete`) — loud. Inactive patterns: generate does not check `active_flag`.

Concrete failure trace:

- Given input: pattern P with playbook PB; new episode linked; `updated_at(P) > updated_at(PB) + 5s`.
- Step 1: Worker enqueue from `add_episode_to_pattern` → skip `playbook_already_exists`. PB steps still describe the old cluster.
- Step 2: UI Generate → second playbook PB2, same `pattern_id`.
- Step 3: `pb_map = {row[1]: (row[0], row[2])}` last-write-wins (`patterns.py:70`) — which playbook the list shows is unordered.
- Step 4: Ranker can surface both once approved. Dedup sweep may later merge on `pattern_id` (`pattern_service.py:487`), deleting versions.
- Resulting fault: stale procedure, or duplicates, or a destructive title-merge of playbooks. Silent then possibly destructive (playbook row delete in dedup).

**Accuracy impact:** Silent FN (playbook not regenerated) then silent FP (second playbook). Compounds Issue #7 if the stale one is still approved/unexpired.

**Cost impact:** extra full generation LLM. Trigger: clicking the review_needed control. Cost-safe: API should attach a new *version* to the existing playbook or 409; never start a second generation when `Playbook.pattern_id` already exists.

---

### Issue #13: Lineage fallback and draft embeddings disagree with the executed version

Location: `backend/src/contextedge/api/v1/playbooks.py:281-296`; `backend/src/contextedge/services/playbook_service.py:422`; `backend/src/contextedge/services/playbook_embedding.py:86-98`; `backend/src/contextedge/api/v1/playbooks.py:538-540`; `backend/src/contextedge/services/execution_service.py:669-685`; `backend/src/contextedge/search/hybrid_ranker.py:183-210`

Original code (verbatim quote):

```python
    if not pattern_info:
        pat_match = await db.execute(
            select(Pattern).where(
                Pattern.tenant_id == user.tenant_id,
            ).order_by(Pattern.episode_count.desc()).limit(1)
        )
        pat = pat_match.scalar_one_or_none()
        if pat:
            target_pattern_id = pat.id
            pattern_info = {
                "id": str(pat.id),
                "title": pat.title,
                "confidence": pat.confidence,
                "episode_count": pat.episode_count,
            }
```

```python
                playbook.current_version_id = version.id
```

```python
    if version is None and playbook.current_version_id is not None:
        version = await db.get(PlaybookVersion, playbook.current_version_id)
...
        playbook.embedding = await generate_embedding(
            text, tenant_id=playbook.tenant_id, db=db
        )
```

```python
        result = await db.execute(
            select(PlaybookVersion)
            .where(
                PlaybookVersion.playbook_id == playbook.id,
                PlaybookVersion.published_at.is_not(None),
            )
            .order_by(PlaybookVersion.published_at.desc())
            .limit(1)
        )
```

In-flight executions pin `playbook_version_id` at start (`execution_service.py:725`) — consistent for that run. Ranking uses newest **published**. `POST .../versions` embeds the **new unpublished** version and points `current_version_id` at it. Agent seeds use `Playbook.embedding` (`repository.py:315-323`).

Concrete failure trace (lineage):

- Given input: hand-authored playbook with `pattern_id=NULL`; tenant’s largest pattern is “Agent Unknown State” (44 episodes).
- Step 1: Detail “references” API misses pattern_id.
- Step 2: Fallback selects that largest pattern.
- Step 3: UI shows that pattern’s confidence/episodes as lineage; generate-from-pattern mental model is wrong.
- Resulting fault: silent false provenance. A reviewer can “update” the wrong pattern’s playbook (Issue #12) from this screen’s pattern chip if they follow it.

Concrete failure trace (version):

- Given input: approved published `v1.0.0` (restart VPN); new unpublished `v1.0.1` (wipe endpoint) created via API; `embed_playbook` runs on v1.0.1.
- Step 1: Agent query “wipe endpoint” matches `Playbook.embedding`, lifecycle still `approved`.
- Step 2: Ranker scores using v1.0.0 published evidence links / FTS title.
- Step 3: `start_execution` without `playbook_version_id` loads v1.0.0.
- Resulting fault: retrieved as the wipe procedure, executes the restart procedure. Silent until runtime. In-flight runs that already pinned v1.0.0 stay on v1.0.0 (correct); new runs disagree with the agent seed.

**Accuracy impact:** Silent FP on lineage and on agent retrieval vs execution version.

**Cost impact:** none.

Corrected logic: delete the largest-pattern fallback (return `pattern: null`). Embed published version only (or store `embedding_version_id`). Rank/execute/embed must key off the same version selector.

---

### Issue #14: Paid embedding/LLM paths re-do work that is already cached or batched elsewhere

Location: `backend/src/contextedge/search/vector_search.py:218`; `backend/src/contextedge/services/knowledge_retrieval_service.py:305-307, 466-469`; `backend/src/contextedge/workers/pattern_tasks.py:146-155, 680-684`; `backend/src/contextedge/workers/extraction_tasks.py:68`; `backend/src/contextedge/ai/embeddings.py:67-78`

Original code (verbatim quote):

```python
    emb = query_embedding if query_embedding is not None else await generate_embedding(query_text)
```

(no `tenant_id`/`db` — budget gate skipped, `/admin/cost` unattributed)

```python
    rows = await search_evidence_semantic(
        db, tenant_id, query, limit=max(limit * 6, 30)
    )
...
        embedding = await generate_embedding(query, tenant_id=tenant_id, db=db)
```

(`_retrieve` then `_attach_sections` embed the same query twice; first call unattributed)

```python
        for ep in episodes_needing_embedding:
            ...
                ep.embedding = await generate_embedding(
                    emb_text, tenant_id=tid, db=db,
                )
```

(`embed_evidence_batch` / `generate_embeddings_batch` exist and are documented “Currently uncalled”)

```python
    except Exception as exc:
        logger.exception("playbook.generate_failed", pattern_id=pattern_id, error=str(exc))
        raise self.retry(exc=exc) from exc
```

(`max_retries=2` plus `litellm.num_retries` default 2 — each Celery retry replays knowledge retrieval + playbook LLM)

```python
    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
```

(ingest parent embed: no `tenant_id`/`db`)

Unbounded-ish fan-out: `synthesize_pattern` concatenates every cluster episode (cap 100 candidates, no cap on cluster size after the `< 0.20` query) with 5 steps each. Playbook prompt caps summaries at 10 (`playbook_generator.py:41`) but still retrieves knowledge and negative knowledge first.

Concrete failure traces:

1. **Trigger:** playbook auto-generation. `_retrieve` embeds `query`; `_attach_sections` embeds `query` again. Two billed embedding calls per generation. Cost-safe fix: pass `query_embedding` into `search_evidence_semantic` and reuse it in `_attach_sections`.
2. **Trigger:** first `cluster_episodes` after a backlog of approved episodes with `embedding IS NULL`. N sequential `generate_embedding` calls. Cost-safe fix: `generate_embeddings_batch` (already bounded by `embedding_max_batch_size`).
3. **Trigger:** playbook LLM 5xx. Celery retries the whole `work()` including `retrieve_knowledge_for_pattern` (another embed+ANN) and `generate_playbook_candidate`. Cost-safe fix: retry only the failed LLM completion; persist knowledge retrieval on the pattern row.
4. **Trigger:** normalize of evidence with a body. Parent embed bypasses tenant budget (`embed_evidence` without kwargs) while chunk batch embed does not (`chunk_tasks.py:169-171`). Cost-safe fix: pass `tenant_id`/`db` through `_ensure_embedding` (attribution + gate; does not add calls).

`rank_playbooks` already reuses one query embedding across playbooks (`hybrid_ranker.py:277-307`) — no issue found on that sub-path. Correlation suggestions reuse stored chunk vectors (`correlation_suggestion_service.py:142-152`) — no extra embed. Message-function is skipped when relevance skip fires — good.

Classification is one LLM per evidence item; the provider has no batch-classify API in-repo, so “batch the classifier” is not a cost-safe recommendation without a cheaper model. Do not add a second pass.

Over-fetch: knowledge sections are capped (`MAX_SECTIONS_PER_DOC = 6`, 800 chars) — no issue found. Pattern synthesis is the over-fetch risk (full cluster). Cost-safe: cap `ep_data` to N episodes (e.g. 12, matching playbook summaries).

---

### Issue #15: Frontend/API paint a default 80% confidence and cache ranking for an hour

Location: `backend/src/contextedge/api/v1/playbooks.py:179-200`; `frontend/src/app/(dashboard)/playbooks/[id]/page.tsx:897-900`; `frontend/src/components/providers.tsx:13`; `backend/src/contextedge/api/v1/runtime.py:29, 232-236`; `backend/src/contextedge/services/playbook_service.py:319-326`

Original code (verbatim quote):

```python
        select(PlaybookVersion.playbook_id, PlaybookVersion.playbook_confidence).where(
            PlaybookVersion.playbook_id.in_(pb_ids)
        )
    )
    ver_map = {row[0]: row[1] for row in ver_result.all() if row[1] is not None}
...
        conf = ver_map.get(pb.id)
        if conf is None and pb.pattern_id:
            conf = pat_map.get(pb.pattern_id)
        r.confidence = float(conf) if conf is not None else 0.8
```

```tsx
                Score: {(((latest?.playbook_confidence ?? playbook.confidence ?? 0.8)) * 100).toFixed(0)}%
```

```tsx
          queries: { staleTime: 30_000, retry: 1 },
```

```python
MATCH_CACHE_TTL_SEC = 3600
...
        await redis.setex(
            f"runtime:match:{match_id}",
            MATCH_CACHE_TTL_SEC,
            json.dumps(payload),
        )
```

List query has **no `ORDER BY`**, so with multiple versions `ver_map` last-write-wins. That value is **not** the ranker’s hybrid score and **not** necessarily the published version’s `playbook_confidence`. Frontend does not reimplement `MIN_RECOMMENDATION_SCORE` (runtime just renders backend `score`) — **no threshold-divergence issue found** on the sandbox. Suggestions render `similarity * 100` from the backend’s `1 - distance` (Issue #1’s correlation mapping), not `/2`.

Runtime cache is invalidated on playbook **lifecycle transition** only, not on version create, embedding refresh, or pattern reclassification.

Concrete failure trace:

- Given input: versions `0.1.0` confidence `0.4` and `0.2.0` confidence `0.9`; unordered SELECT returns `0.4` last.
- Step 1: List shows `40%` (or `90%`) nondeterministically.
- Step 2: If both `playbook_confidence` were NULL, list shows `80%`.
- Step 3: Detail `GET /playbooks/{id}` does **not** apply the 0.8 default (`get_playbook` returns the ORM; `confidence` stays `null`); the header still shows `80%` via `?? 0.8` if version confidence is missing.
- Step 4: User matches at T0; Redis holds results 3600s. Playbook is edited (not transitioned). Explain still shows T0 scores. React Query can serve 30s-stale `lifecycle_state` on the playbooks list; execution page polls 30s (`execution/page.tsx:124`) so approvals are fresher than playbook state.
- Resulting fault: operator acts on a score/class that looks valid. Silent.

Failed classification: normalize failure leaves `unclassified` (visible badge), not a fake `operational`. **No silent last-known classification on that path.** `_classify` errors propagate (loud).

**Accuracy impact:** Silent FP in the UI (inflated/stale confidence). Does not by itself execute, but it is the number next to Transition / Generate.

**Cost impact:** none. Do not “fix” staleness by refetching `/runtime/match` on an interval (that would re-embed every time — Issue #14). Cost-safe: invalidate `runtime:match:*` on version create (already have the SCAN helper); drop the `0.8` default and show “—”.

Corrected logic:

```diff
-        r.confidence = float(conf) if conf is not None else 0.8
+        r.confidence = float(conf) if conf is not None else None
```

```diff
-    ver_map = {row[0]: row[1] for row in ver_result.all() if row[1] is not None}
+    # take current_version_id or latest published only, with ORDER BY
```

```diff
-                Score: {(((latest?.playbook_confidence ?? playbook.confidence ?? 0.8)) * 100).toFixed(0)}%
+                Score: {latest?.playbook_confidence != null ? … : "—"}
```

---

### Issue #16: MMR tie-break depends on `set` iteration order

Location: `backend/src/contextedge/search/chunk_rollup.py:98-107`

Original code (verbatim quote):

```python
    remaining = set(range(len(candidates)))
    while remaining and len(selected) < select_n:
        best_index, best_score = None, None
        for index in remaining:
            max_sim = float(similarity[index, selected].max()) if selected else 0.0
            score = lambda_ * float(relevance[index]) - (1.0 - lambda_) * max_sim
            if best_score is None or score > best_score:
                best_index, best_score = index, score
```

Equal MMR scores keep the first index visited. `set` iteration order is hash-randomized per process. Ranker then re-sorts rolled parents by distance (`chunk_rollup.py:121`), so **final parent order** is stable when distances differ. Ties in both MMR selection set *and* distance still jitter which duplicate parent survives oversample.

Concrete failure trace: two chunks, same distance, same embedding; `select_n=1`; which chunk’s snippet is the breadcrumb depends on set order. Silent rank jitter. P2.

Corrected logic: `remaining = list(range(len(candidates)))` and break ties on `(score, -distance, chunk_id)`.

---

## 3. Edge Case Test Matrix

| Fixture | Concrete input | Expected corrected result |
|---|---|---|
| Identical unit vectors | `a = b = L2([1,0,…])`, distance `0` | Ranker semantic `1.0`; knowledge similarity `1.0`; correlation `1.0`; MMR self-sim `1.0`. All four agree. |
| Zero vector | `embed_evidence(None, None)` | `embedding` stays NULL; not in ANN; no NaN distance. |
| Two zero vectors | historically stored `[0]*3072` vs same | Must not count as cosine `1.0`; exclude from search. |
| Distance `0.30` | one shared mapping | Ranker, knowledge (`>= 0.75`?), correlation (`>= 0.7`), agent (`>= 0.5`) all use `1-d = 0.70`. |
| Distance `1.2` (obtuse) | clamp | Similarity `0`, never negative, never `/2 → 0.40` “relevant”. |
| Dim mismatch | embed returns 768 | Loud `ValueError` at generate (already). MMR degrades to distance sort (already). No pad/truncate. |
| FTS single hit | `ts_rank=0.004`, only one approved match | Keyword score is **not** `1.0`; uses a fixed scale. |
| FTS all-zero ranks | `max_rank=0` | Scores `0` (already via `or 1.0`); keep. |
| Quality `evidence_hits=0` | `_quality_score(0.9, 0)` | `0.6*0.9 = 0.54` (already defined). |
| Tied classification | n/a (single LLM label) | Reject non-enum; do not default `not_relevant`. |
| Missing classification + `confidence=0.92` | skip must **not** fire | Persist `unclassified`; extract or bounded requeue. |
| `"not relevant"` label | skip and store agree | Both treat as `not_relevant`. |
| Tied `is_match` / missing | validator `{}` or timeout | `is_match=False`; episode remains unlinked. |
| Title collision | two clusters titled `"Database unavailable"` | Two patterns unless vector+validator agree. |
| Single-linkage chain | A–B–C–D adjacent `d=0.33` | D not in A’s pattern without centroid/all-pair check. |
| `confidence: 0.0` synthesis | must not store `0.8` | Skip playbook generation (`< 0.5`). |
| Concurrent create | two workers, overlapping episodes | One pattern; second hits unique membership. |
| Burst N similar tickets | N=50 within 0.20 | Embeddings batched; at most one synthesis; validator LLM capped; cost sub-linear. |
| Playbook generate with existing PB | UI review_needed | 409 skip, **not** PB2; optional new version. |
| Empty steps from API generate | `steps=[]` | Fail like the worker; no row. |
| Missing `safety_class` | restart step | Creation rejected (or explicit class required). |
| Unknown `safety_class` `"write"` | generation | Reject at `create_playbook_version`, never rankable. |
| Unpublished v1.0.1 embed | agent vs execution | Both use last **published** version. |
| Orphan playbook references | `pattern_id=NULL` | `pattern: null`, not max-episode pattern. |
| Retry on playbook LLM 5xx | Celery retry | Knowledge retrieval not re-billed; only the completion retries; max 2. |
| Runtime cache after version edit | match at T0, edit at T0+1m | Explain 404 or rebuilt; not T0 scores. |
| Frontend default confidence | no version confidence | Display “—”, not `80%`. |
| Stale list `lifecycle_state` | transitioned 10s ago | `staleTime` 30s may show old state — invalidate on transition (already for runtime Redis; add query invalidation). |
| Failed relevance LLM | exception in `_normalize` | `unclassified` visible; no `operational` default (already). |

---

## 4. Cross-Reference to Prior Audit

| Prior issue | Interaction |
|---|---|
| **#2** title-based evidence deletion | **Extended:** the same title-identity mistake happens at **pattern creation** (this Issue #7) and in the pattern/playbook title sweep. Fixing only evidence grouping does not stop pattern over-merge. |
| **#6** closed graph edges in ranking | Still open. Inflated semantic+keyword scores (this #1, #4) mean a stale contradiction penalty of `0.015` is even less likely to save you — and a stale *positive* graph boost is more likely to push a weak match over `0.35`. |
| **#7** expired playbooks remain recommendable | **Worsened.** Expiry only zeroes 0.15 of hybrid weight. This audit shows the remaining mass is systematically inflated (FTS min-max, `/2` semantic). Creation-time gaps (this #10–#13) are the same *recommend vs execute* family: missing/unknown `safety_class`, unpublished-vs-published version, duplicate/stale playbooks. |
| **#8** NaN LLM confidence | **Repeated** on pattern `confidence=float(... or 0.8)` and playbook `playbook_confidence`. `0.0` is additionally OR-away to a passing score. Apply the same finite/`[0,1]` parser. |
| Validation **5.3** body-only `content_hash` | Not re-opened. Pattern identity here is title/vector, not that hash. Do not use body hash as the pattern key either. |
| **#1, #3, #4, #5** | No new interaction beyond “wrong playbooks still shouldn’t execute without approval.” This pass did not re-verify those paths. |

---

## 5. Self-Check

Re-read findings against quoted lines. **Dropped four** candidates that did not survive:

1. Domain-wide negative-knowledge count as a per-playbook penalty — same additive term for every playbook in a domain; does not invert in-domain ranking (only a ≤0.05 absolute shift vs the 0.35 gate). Not a demonstrated fault.
2. Pattern clustering using `Vector.cosine_distance` instead of `halfvec_cosine_distance` — sequential scan / fp32 vs fp16, but no traced ranking inversion.
3. Trigram `similarity > 0.3` vs `>=` in identity resolution — outside the five target pipelines.
4. Re-stating prior Issue #2 (evidence deletion) and #6/#7 (graph expiry / playbook expiry) as new items.

**Also explicitly “no issue found”**

- **A dimension truncate/pad:** not present; generate fails loud; MMR degrades.
- **A Python raw-dot-as-cosine (MMR):** MMR L2-normalizes first. The bug is the *post-pgvector* mapping (Issue #1), not an unnormalized dot inside MMR.
- **C message-function classifier:** enum-validated; bad confidence clamped.
- **C `classify_support`:** deterministic, `>=` contested ratio, silence≠failure — no gap for this audit’s bar.
- **G correlation suggestions:** reuse stored embeddings; queue cap 500; no extra provider calls.
- **Frontend threshold reimplementation:** runtime does not locally re-gate `0.35`; suggestions do not locally re-gate `0.7`.

Final report contains **16 findings**, each tied to quoted line ranges. No production code was changed.

---

## 6. Independent Final Validation (2026-08-17)

### 0. Validation Scope Note

Validated the report against branch `feat/graph-quality-hardening`, commit
`233b643ba8be014e64e13fc70b150fe88470f5bd`, in
`D:\ContextEdge_pro\ContextEdge`. Both audit Markdown files are untracked;
`git status --short` shows no production-code modification.

Every cited source path and range was opened. I also inspected the affected
callers, migrations/models, worker startup configuration, the prior audit and
its appended independent validation. The live local PostgreSQL 17 database was
queried read-only: pgvector is `0.8.1`, and both
`'[0,0]'::vector <=> '[1,0]'::vector` and
`'[0,0]'::vector <=> '[0,0]'::vector` returned `NaN`.

A focused regression run covered decision embeddings, chunk rollup, message
classification, negative ranking, knowledge support, playbook lifecycle and
embedding behavior, and Celery routing: **76 passed, 2 failed**. Both failures
are an existing test-mock import-resolution problem in
`test_decision_embedding.py` (`contextedge.services` lacks the eagerly loaded
`review_queue_service` attribute at patch time); they do not refute any trace
below. Direct isolated reproductions were also run for malformed relevance
output, pattern-validator fallback, `NaN` message confidence, risk defaults,
semantic mapping, and MMR hash seeds.

The report's “verbatim” excerpts were compared as contiguous source substrings,
normalizing only CRLF/LF. Exact-block counts were:

| Issue | Exact quoted blocks | Quote-integrity result |
|---|---:|---|
| 1 | 5/5 | Matched |
| 2 | 3/3 | Matched |
| 3 | 1/2 | Second block omits source comments while claiming a verbatim contiguous quote |
| 4 | 1/2 | `_quality_score` block omits its docstring |
| 5 | 1/3 | Two blocks contain literal `...` not present in source |
| 6 | 1/2 | Worker block contains literal `...` |
| 7 | 1/2 | Creation block reconstructs a comment with `...` |
| 8 | 1/3 | Two blocks contain literal `...` |
| 9 | 2/3 | Service block contains literal `...` |
| 10 | 0/2 | Both aggregate non-contiguous ranges with `...` |
| 11 | 2/3 | Execution block contains literal `...` |
| 12 | 3/4 | Frontend block contains literal `...` |
| 13 | 3/4 | Embedding block contains literal `...` |
| 14 | 3/5 | Two blocks contain literal `...` |
| 15 | 2/4 | API and runtime blocks contain literal `...` |
| 16 | 1/1 | Matched |

For a real issue with a drifted aggregate quote, the quote claim is marked
**UNVERIFIABLE** while the underlying logic is validated from the real source.
This maps to `PARTIALLY CONFIRMED`, because the required verdict vocabulary has
no standalone `UNVERIFIABLE` outcome.

### 1. Validation Matrix

| # | Original Verdict | Your Verdict | Confidence | Notes |
|---|---|---|---|---|
| 1 | P1 Confirmed | **NOT CONFIRMED** | High | The arithmetic differs, but the source explicitly defines different, valid consumer-specific scales; no shared-score invariant is shown. |
| 2 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Live pgvector confirms `NaN`, but the normal evidence path supplies a fallback title and the proposed exception is not soft for every caller. |
| 3 | P1 Confirmed | **NOT CONFIRMED** | High | Both stated traces are blocked: changed normalized content creates a new row, and the version-edit API re-embeds the edited playbook. |
| 4 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Population dependence is real; the claimed false-positive calibration and arbitrary `0.1` replacement are not established. Severity is P2. |
| 5 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Missing/invalid labels reproduce, but two quotes drift and the proposed fix is incomplete and internally inconsistent about `unclassified`. |
| 6 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Fail-open merge reproduces; the report's Celery “retry storm” cost label is false and the fix does not fully validate confidence/order. |
| 7 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Generic-title over-merge is real; the primary quote is reconstructed and the proposed remedy is not a drop-in fix. |
| 8 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | `0.0 -> 0.8`, single-link chaining, and frozen confidence reproduce; cost is bounded at 100 candidates and the fix closes only one sub-defect. |
| 9 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Live schema and Docker worker defaults make the race realistic; the suggested constraints/lock do not prevent pre-lock duplicate LLM spend as written. |
| 10 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | API/worker rule divergence reproduces; neither quote is verbatim and the nested worker `work()` cannot be called from the API as proposed. |
| 11 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Unknown classes persist then fail at execution; missing class is recorded as read-only, but no in-repo executor performs the claimed destructive action. |
| 12 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Growth -> worker skip -> UI duplicate is reachable; a new version alone is incomplete without lifecycle/version/cache handling. |
| 13 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | False lineage and draft-embedding/published-execution drift both reproduce; one quote drifts and rollback is an additional affected caller. |
| 14 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Duplicate/unattributed calls and whole-task replay are real; “storm/unbounded” is overstated and batch conversion can regress per-item failure isolation. |
| 15 | P1 Confirmed | **PARTIALLY CONFIRMED** | High | Unordered/defaulted UI confidence is real; a match-id explanation is an intentional T0 snapshot, not cached ranking served to later matches. Severity is P2. |
| 16 | P2 Confirmed | **PARTIALLY CONFIRMED** | High | `set` order is unspecified, but integer hashes are not process-randomized and the stated cross-seed jitter did not reproduce; SQL ties also need a tie-break. |

### 2. Per-Issue Validation Detail

#### Issue #1 Validation: Cosine distance is converted three incompatible ways

- **Quote check:** All five source snippets matched exactly.
- **Trace re-run:** At distance `0.30`, `_semantic_corpus_score` returns `0.85`,
  while knowledge/correlation/agent code computes `0.70`. The arithmetic is
  reachable. It does not prove a fault: `chunk_rollup.py:45-47` and
  `hybrid_ranker.py:45-54` deliberately map the full cosine-distance interval
  `[0,2]` to relevance `[0,1]`; the other consumers clamp raw cosine similarity
  to `[0,1]` and apply their own independently stated floors.
- **Accuracy-impact re-check:** “Incompatible” assumes a cross-consumer score
  identity that is not present in code or tests. A negative cosine can validly
  become a weak non-negative relevance under the affine map. The claimed silent
  false positive is therefore not established.
- **Cost-impact re-check:** No call-count change exists.
- **Fix review:** The proposed global `1 - distance` replacement is regressive
  unless all ranker weights, MMR behavior, and thresholds are recalibrated. It
  can introduce false negatives by turning every obtuse vector into zero.
  No production diff is justified by this finding.
- **Cross-file impact:** All five mappings and their callers were checked; no
  shared score object or downstream equality comparison requires them to match.
- **Verdict:** **NOT CONFIRMED**, High confidence.

#### Issue #2 Validation: Empty text is stored as a 3072-zero embedding

- **Quote check:** All three blocks matched exactly.
- **Trace re-run:** The helper returns 3,072 zeros. The live pgvector query
  returned `NaN` for zero/non-zero and zero/zero cosine distance. MMR normalizes
  the zero row to itself and computes zero dot similarity. However, normal
  evidence creation calls `evidence_title_from_payload`, whose final return is
  `"Untitled Evidence"`; the stated newly normalized evidence fixture therefore
  does not reach the zero branch. It remains reachable for nullable legacy/direct
  evidence rows and for empty-string `DecisionCreate` fields, which have no
  minimum-length validation.
- **Accuracy-impact re-check:** The stored sentinel is semantically invalid and
  can contaminate `embedding IS NOT NULL` populations. The report overstates
  reachability for ordinary connector normalization and does not prove that a
  `NaN` row occupies an ANN oversample slot; cosine indexes may omit zero vectors.
- **Cost-impact re-check:** The empty branch makes no paid call. The report's
  matrix calling it a redundant call is incorrect.
- **Fix review:** Raising is not a universally soft change. The artifact
  extraction caller directly assigns the result without a local catch, and
  `test_embed_decision_empty_returns_zero_vector` explicitly requires the old
  contract. A complete, cost-neutral shape is nullable:

```diff
-) -> list[float]:
+) -> list[float] | None:
     ...
     if not text:
-        return [0.0] * 3072
+        return None

 async def _ensure_embedding(db, evidence):
     if evidence.embedding is not None:
         return False
-    evidence.embedding = await embed_evidence(evidence.title, evidence.body_text)
+    candidate = await embed_evidence(
+        evidence.title, evidence.body_text,
+        tenant_id=evidence.tenant_id, db=db,
+    )
+    if candidate is None:
+        return False
+    evidence.embedding = candidate
```

  Apply the nullable contract to decision and batch helpers, update their callers,
  and never persist a zero sentinel. This adds no provider call.
- **Cross-file impact:** Checked `_ensure_embedding`, attachment extraction,
  decision creation, chunk batching, tests, and all direct helper callers.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #3 Validation: Embeddings are write-once relative to source text

- **Quote check:** The `_ensure_embedding` block matched. The approval block is
  **UNVERIFIABLE as quoted** because the source contains two additional comment
  lines before the import. The real operative source is:

```python
        if playbook.embedding is None:
            # "approved" is exactly the state the agent seed resolver's
            # semantic layer filters on (migration 0035), so repair the
            # fingerprint here: pre-0035 rows and playbooks whose embed
            # failed transiently at version creation become semantically
            # matchable the moment a reviewer approves them. Best-effort —
            # a provider failure leaves FTS-only matching, never blocks
            # the transition.
            from contextedge.services.playbook_embedding import embed_playbook

            await embed_playbook(db, playbook, approved_version)
```

- **Trace re-run:** A changed title/body changes `content_hash`; normalization
  does not overwrite the existing row, it inserts a new `EvidenceItem`. On the
  unchanged-hash branch only facets/lifecycle fields refresh. Attachment merging,
  the only shown in-place body update, explicitly recomputes the parent embedding.
  On the playbook path, `POST /{id}/versions` calls `embed_playbook(db, playbook,
  version)` immediately after `create_playbook_version`. Thus both stated traces
  are stopped by code outside the quote.
- **Accuracy-impact re-check:** The report's evidence and reviewer-edit FP/FN
  claims do not occur on those paths. A related real path exists: rollback creates
  and possibly publishes a version without re-embedding; that is covered under
  Issue #13 below.
- **Cost-impact re-check:** The proposed hash field/migration adds storage and
  complexity but no demonstrated benefit on the stated evidence path.
- **Fix review:** The diff is not drop-in: it references `hashlib`, an absent
  `EvidenceItem.attributes` member in the shown model, and never writes the digest.
  Do not apply it. Version identity should be fixed under Issue #13 with an
  explicit `embedding_version_id` (or equivalent), not an ad hoc JSON key.
- **Cross-file impact:** Checked every assignment to evidence title/body/embedding,
  all playbook embedding callers, version creation, rollback, retention, and the
  embedding backfill task.
- **Verdict:** **NOT CONFIRMED**, High confidence.

#### Issue #4 Validation: Keyword score is normalized against the request population

- **Quote check:** The FTS block matched. The `_quality_score` quote is
  **UNVERIFIABLE as a contiguous quote** because its docstring was removed. The
  actual function includes the docstring at `174-178` before the two quoted lines.
- **Trace re-run:** A lone non-zero `ts_rank` is divided by itself and becomes
  `1.0`; adding a stronger matching row changes the first row's score. No guard
  prevents this.
- **Accuracy-impact re-check:** Population dependence is confirmed and is fragile
  beside an absolute `0.35` final gate. The specific `0.004` false-positive claim
  is not calibrated: the code and tests contain no evidence that `0.004` is weak,
  and top-relative normalization can be an intentional ranking feature.
- **Cost-impact re-check:** None.
- **Fix review:** Dividing by hard-coded `0.1` merely substitutes an unsupported
  calibration constant. A safe correction requires an observed, versioned fixed
  transform and threshold tests, for example:

```diff
-        max_rank = max((r for _, r in fts_results), default=1.0) or 1.0
         for playbook, rank in fts_results:
-            fts_scores[playbook.id] = float(rank) / max_rank
+            fts_scores[playbook.id] = calibrated_fts_score(float(rank))
```

  `calibrated_fts_score` must be monotonic, bounded, fixed across requests, and
  calibrated together with `MIN_RECOMMENDATION_SCORE`; the audit's `0.1` cannot
  be shipped as-is.
- **Cross-file impact:** `search_playbooks_fts` returns raw `ts_rank`; no other
  caller normalizes it into the hybrid threshold.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence; severity corrected to P2.

#### Issue #5 Validation: Relevance classification is not enum-validated

- **Quote check:** Classifier lines `70-72` matched. Both worker aggregates are
  **UNVERIFIABLE as verbatim** because they contain literal `...`. The real core is:

```python
        classification_label = cls.get("classification", "not_relevant")
        classification_confidence = float(cls.get("confidence", 0.0))
        ev.relevance_state = classification_label.replace(" ", "_")
        ev.relevance_score = classification_confidence
```

```python
    skip_extraction = (
        classification_label == "not_relevant"
        and classification_confidence is not None
        and classification_confidence >= 0.75
    )
```

- **Trace re-run:** Mocked JSON `{"confidence": 0.92}` returned
  `classification="not_relevant"`; skip becomes true. JSON label
  `"not relevant"` is stored as `not_relevant` but does not skip. Unknown labels
  persist and are excluded by the episode filter. No schema validator upstream
  blocks these shapes.
- **Accuracy-impact re-check:** Missing-label false negative and unknown-label
  drop are silent and confirmed. The classifier-exception path is accuracy-safe:
  it leaves `unclassified` and runs the expensive path.
- **Cost-impact re-check:** Label drift causes an unnecessary downstream pass.
  Provider failure does not create a retry storm; it deliberately pays the
  existing downstream cost. Skipping all `unclassified` items, as the report
  suggests elsewhere, would convert an outage into systematic false negatives.
- **Fix review:** The shown diff does not place `ALLOWED` in valid context, omits
  `math`, does not clamp finite confidence, and does not show the returned label.
  It also conflicts with the prose about whether `unclassified` is extracted.
  A complete cost-neutral parser is:

```diff
+import math

+ALLOWED_RELEVANCE = {"operational", "possibly_relevant", "not_relevant"}
 ...
-    return {
-        "classification": result.get("classification", "not_relevant"),
-        "confidence": float(result.get("confidence", 0.5)),
+    raw_label = result.get("classification")
+    label = raw_label.strip().lower().replace(" ", "_") if isinstance(raw_label, str) else ""
+    if label not in ALLOWED_RELEVANCE:
+        label = "unclassified"
+    raw_confidence = result.get("confidence")
+    try:
+        confidence = float(raw_confidence) if not isinstance(raw_confidence, bool) else 0.0
+    except (TypeError, ValueError):
+        confidence = 0.0
+    confidence = min(max(confidence, 0.0), 1.0) if math.isfinite(confidence) else 0.0
+    return {
+        "classification": label,
+        "confidence": confidence,
```

  Both worker paths should persist and compare that returned canonical label.
  `unclassified` should retain the current accuracy-safe extraction behavior
  unless a real bounded reclassification queue is implemented.
- **Cross-file impact:** Checked inline normalization, manual `_classify`, episode
  reconstruction, relevance prompt versions, and message-function comparison.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #6 Validation: Pattern-match validation fails open

- **Quote check:** Validator block matched. Worker aggregate is **UNVERIFIABLE as
  verbatim** because of literal `...`. The actual deciding line is:

```python
                    if ai_val.get("is_match"):
                        await add_episode_to_pattern(db, tid, matched_pattern_id, ep.id)
```

- **Trace re-run:** A mocked `{}` returned `is_match=True, confidence=0.8`; a
  timeout returned `is_match=True, confidence=0.75`. A string `"false"` is also
  truthy under `bool(...)`. Once the vector prefilter finds any member below
  distance `0.35`, the episode is linked with no confidence floor.
- **Accuracy-impact re-check:** Silent over-merge is confirmed. The unordered
  `LIMIT 1` can choose any qualifying pattern member.
- **Cost-impact re-check:** The matrix's “retry storm” is false. This function
  catches the final provider exception and returns a match, so that exception
  does not reach Celery's retry handler. LiteLLM may perform its configured
  bounded retries, but the Celery task does not replay because of this failure.
- **Fix review:** Fail-closed boolean validation is correct, but confidence must
  also be finite/bounded and the caller must enforce it. The candidate query also
  needs nearest-pattern ordering. A complete direction is:

```diff
-                "is_match": bool(res.get("is_match", True)),
+                "is_match": res["is_match"],
```

  only after checking `type(res.get("is_match")) is bool` and parsing a finite
  `[0,1]` confidence; otherwise return false. At the caller:

```diff
-                    if ai_val.get("is_match"):
+                    if ai_val.get("is_match") is True and ai_val["confidence"] >= 0.70:
```

  The SQL must aggregate the minimum distance per pattern and order by that
  distance before `LIMIT 1`. These changes add no provider call.
- **Cross-file impact:** `pattern_tasks.py` is the only caller of
  `validate_pattern_match`.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #7 Validation: Pattern creation merges on normalized title

- **Quote check:** The later grouping block matched. The creation block is
  **UNVERIFIABLE as verbatim** because the report replaced several real comment
  lines with `...`. The actual predicate is:

```python
            select(Pattern).where(
                Pattern.tenant_id == tenant_id,
                Pattern.domain_id == domain_id,
                Pattern.active_flag.is_(True),
                func.lower(Pattern.title) == clean_title.lower(),
            ).limit(1)
```

- **Trace re-run:** Two same-tenant/domain clusters with the same normalized
  title cause the second call to add every episode to the first pattern and
  return it. There is no content, member, or semantic identity check.
- **Accuracy-impact re-check:** Silent fusion of distinct clusters is confirmed.
- **Cost-impact re-check:** The second synthesis has already been paid before
  this service sees its title. Merging avoids a second row but can enqueue one
  playbook task per newly added episode; those tasks normally skip before an
  LLM call once a playbook exists.
- **Fix review:** The prose remedy is directionally right but not drop-in. To
  close the trace, remove title-as-identity from both creation and automated
  housekeeping; record collisions for review instead:

```diff
-    clean_title = title.strip()
-    existing_pattern = ... lower(Pattern.title) == clean_title.lower() ...
-    if existing_pattern:
-        for ep_id in episode_ids:
-            await add_episode_to_pattern(...)
-        return existing_pattern
+    # A title is display text, never identity. Persist the new cluster.
+    # A separate review proposal may flag equal normalized titles.
```

  This adds no LLM or embedding call; both clusters were already synthesized.
- **Cross-file impact:** Checked worker clustering, manual `/patterns/discover`,
  `create_pattern_from_episodes`, and the later pattern/playbook dedup sweep.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #8 Validation: Single-linkage matching and frozen confidence

- **Quote check:** Only the single confidence line matched. The two aggregate
  blocks are **UNVERIFIABLE as verbatim** because they contain `...`.
- **Trace re-run:** The existing-pattern query joins every pattern member and
  accepts any one member below `0.35`. After each link, that new member can serve
  as the next bridge. `float(synthesis.get("confidence") or 0.8)` converts a real
  `0.0` to `0.8`; `add_episode_to_pattern` increments only `episode_count` and
  never changes confidence. All three behaviors are reachable.
- **Accuracy-impact re-check:** Chaining and zero-confidence inflation are
  confirmed silent false positives. “Confidence should decrease on contrary
  evidence” is not implementable from the shown data because no membership
  score is persisted; the frozen-value observation is valid, but its desired
  aggregation rule is unspecified.
- **Cost-impact re-check:** The candidate query is capped at 100. A burst into
  an existing pattern can cause up to one bounded validation call per candidate;
  a new 50-episode all-within-`0.20` cluster normally causes one synthesis, not
  50. The matrix's “unbounded” label is inaccurate.
- **Fix review:** The proposed finite parser closes only `0.0`/`NaN`; it does not
  close chaining or frozen confidence. Minimum safe confidence parsing is:

```diff
+def _finite_unit(value):
+    if isinstance(value, bool):
+        return None
+    try:
+        parsed = float(value)
+    except (TypeError, ValueError):
+        return None
+    return parsed if math.isfinite(parsed) and 0.0 <= parsed <= 1.0 else None
 ...
-                    confidence=float(synthesis.get("confidence") or 0.8),
+                    confidence=_finite_unit(synthesis.get("confidence")),
```

  If `None`, leave the episodes unassigned. A valid `0.0` pattern may be stored
  but will fail the existing `0.5` playbook gate. Chaining needs a centroid or
  bounded all-member vector check plus the corrected validator; no additional
  LLM call is necessary. Confidence recomputation needs stored member verdicts,
  not a fabricated increment formula.
- **Cross-file impact:** Checked the only membership service, both clustering
  branches, worker generation floor, and episode supersession threshold.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #9 Validation: Concurrent pattern creation has no uniqueness constraint

- **Quote check:** Model-title and queue-route blocks matched. The service block
  is **UNVERIFIABLE as verbatim** because it includes `...`.
- **Trace re-run:** ORM and live-database constraint inspection found only PK/FK
  constraints on `patterns` and `pattern_evidence_links`; there is no title or
  episode-membership uniqueness. Two transactions can both miss the pre-check
  and insert. `_linked_episode_ids` is read once before the cluster loop.
- **Accuracy-impact re-check:** Duplicate rows/links are a realistic silent race.
- **Cost-impact re-check:** Duplicate synthesis/generation is possible because
  locking only inside `create_pattern_from_episodes` would occur after synthesis.
- **Fix review:** The deployment caveat is resolved: `docker-compose.dev.yml`
  starts a Linux Celery worker without `--concurrency`, so Celery defaults to a
  multi-process pool on multi-core hosts; API discovery can also overlap. A
  complete fix must acquire a tenant/domain advisory lock **before synthesis**
  in worker/API orchestration and enforce the intended one-pattern-per-episode
  invariant in the database:

```sql
CREATE UNIQUE INDEX uq_pattern_episode_once
ON pattern_evidence_links (episode_id)
WHERE episode_id IS NOT NULL;
```

  This constraint is consistent with `_linked_episode_ids`, but should be
  approved as a business invariant because it prevents manual multi-pattern
  membership too. The transaction must handle the resulting uniqueness error
  as an idempotent skip. A title unique index must not be added while Issue #7
  correctly stops treating titles as identity.
- **Cross-file impact:** Checked Docker/Windows worker startup, Celery routing,
  three cluster enqueue APIs, manual discovery, service creation, and live DDL.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #10 Validation: Manual playbook generation bypasses worker constraints

- **Quote check:** Both report blocks are **UNVERIFIABLE as verbatim**; they
  splice non-contiguous ranges with literal `...`. The real API creation core is:

```python
        candidate = await generate_playbook_candidate(
            pattern.title,
            pattern.description or "",
            len(episodes),
            ep_summaries,
            negative_knowledge,
            tenant_id=user.tenant_id,
            db=db,
        )
```

```python
        playbook = Playbook(
            tenant_id=user.tenant_id,
            domain_id=pattern.domain_id,
            stable_key=stable_key,
            title=candidate.get("title", f"Fix: {pattern.title}"),
            description=candidate.get("description", pattern.description),
            risk_tier=candidate.get("risk_tier", "medium"),
            automation_mode="suggest_only",
            owner_user_id=user.user_id,
            pattern_id=pattern.id,
        )
        db.add(playbook)
        await db.flush()
        await create_playbook_version(db, playbook, candidate)
```

- **Trace re-run:** The API checks existence of the pattern and episode links,
  then calls the LLM. It does not check `active_flag`, existing playbooks,
  confidence, empty steps, deterministic risk floor, or knowledge sources. Its
  episode summaries omit IDs, so generated `[ep-N]` references cannot resolve.
  The stated duplicate/low-confidence path is reachable.
- **Accuracy-impact re-check:** Duplicate/hollow/ungrounded candidates are
  persisted silently; empty versions cannot later enter review because the
  transition guard raises. Unknown safety classes may fail at execution.
- **Cost-impact re-check:** A full playbook call occurs on inputs the worker would
  skip. This is a confirmed redundant call.
- **Fix review:** The worker's `work()` is nested inside a Celery task and cannot
  be imported/called by the API. The correct change is to extract one async
  service used by both wrappers. That service must perform, in order, the
  existing-playbook and confidence checks before retrieval/LLM, then shared
  knowledge retrieval, ID-bearing summaries, empty-step validation, risk floor,
  and persistence. API skip reasons should map to 409/422. Duplicating just the
  guards in the route would compile but reintroduce future drift, so the audit's
  proposed direct call is not shippable as written.
- **Cross-file impact:** The generator has exactly two production callers: this
  route and the worker. `create_playbook_version` has additional hand-authored,
  rollback, and test callers and therefore cannot silently assume generated data.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #11 Validation: Missing/unknown safety classes diverge at execution

- **Quote check:** Risk-floor and Pydantic blocks matched. The execution block is
  **UNVERIFIABLE as verbatim** because of `...`. The operative source is:

```python
            step_safety = step_data.get("safety_class", "read_only")
            needs_approval = bool(step_data.get("requires_approval", False))
```

```python
        if _safety_class_rank(step_safety) > _safety_class_rank(effective_safety_class):
            needs_approval = True
```

- **Trace re-run:** Generated raw dictionaries bypass `PlaybookStep`; unknown
  `"write"` persists and `_safety_class_rank` later raises. A missing class is
  treated as read-only by both risk-floor and execution-row creation. The schema
  validator rejects unknown values only on the typed version endpoint and
  explicitly allows `None`.
- **Accuracy-impact re-check:** Recommend-then-refuse for unknown class is
  confirmed. For the report's missing-class fixture, `start_execution` creates a
  pending `ExecutionStepRun`; no in-repo executor performs “Restart database”.
  The metadata is under-classified, but actual destructive execution is an
  assumption about an external tool runner and cannot be stated as confirmed.
- **Cost-impact re-check:** None.
- **Fix review:** “Validate with `PlaybookStep`” rejects unknown strings but does
  **not** reject a missing class, so it does not close the stated trace. Requiring
  a class for every prose-only instruction would also break existing valid
  unbound/read-only steps. A preserving rule is: validate every step centrally;
  require an explicit safety class whenever `action_name` or `tool_ref` declares
  an executable binding; reject all unknown classes; repeat the bound-action
  guard at execution as defense in depth.
- **Cross-file impact:** Checked all `create_playbook_version` callers, generated
  paths, typed schemas, risk calculation, binding validation, start-execution,
  and the prior audit's finding that no tool executor is in scope.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #12 Validation: Pattern growth cannot refresh a playbook

- **Quote check:** The three backend snippets matched. The frontend aggregate is
  **UNVERIFIABLE as verbatim** because it contains `...`. Actual mutation source:

```typescript
  const generateMutation = useMutation({
    mutationFn: () => api.post("/playbooks/generate", { pattern_id: pattern.id }),
    onSuccess: () => {
      toast.success("Playbook candidate updated!");
```

- **Trace re-run:** Adding a new member increments `Pattern.episode_count`, which
  emits an update and advances `updated_at`; it enqueues generation. The worker
  finds an existing playbook and exits. The list marks `review_needed`; the UI
  calls the manual route, which inserts another playbook. `pb_map` then chooses
  one duplicate by unordered last-write-wins.
- **Accuracy-impact re-check:** Stale original plus duplicate candidate is
  confirmed. The later dedup sweep can delete/merge rows, but whether it runs is
  separate from this trace.
- **Cost-impact re-check:** Worker skip occurs before retrieval/LLM. The UI click
  pays one unnecessary full generation because it creates PB2 rather than a new
  version of PB1.
- **Fix review:** “Attach a new version” is necessary but incomplete. For an
  approved playbook, the update flow must preserve the published executable
  version, create a reviewable draft on the same playbook, keep agent embedding
  keyed to the published version, transition governance appropriately, and
  invalidate relevant UI queries. Otherwise it simply recreates Issue #13 inside
  one row. The shared generation service from Issue #10 should return 409 unless
  the caller explicitly selects an update-version operation.
- **Cross-file impact:** Checked pattern growth, worker generation, patterns list
  mapping, UI mutation, manual generation, ranker, and dedup sweep.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #13 Validation: Lineage and version embedding disagree with execution

- **Quote check:** Lineage, `current_version_id`, and published-version blocks
  matched. The embedding block is **UNVERIFIABLE as verbatim** because it uses
  `...`; real lines `89-98` load current version, build text, and overwrite the
  row-level embedding.
- **Trace re-run:** A `pattern_id=NULL` playbook is assigned the tenant's largest
  pattern in `/references`, with no identity relation. For an approved playbook,
  creating an unpublished version repoints `current_version_id` and embeds that
  draft while lifecycle remains approved. Agent seed search reads the row-level
  draft embedding; ranker and `start_execution` independently select the newest
  published version. Both stated mismatches are reachable.
- **Accuracy-impact re-check:** False provenance and retrieval-vs-execution drift
  are confirmed silent inconsistencies. In-flight runs correctly remain pinned.
- **Cost-impact re-check:** None beyond the already intended one embed per new
  version.
- **Fix review:** Removing the arbitrary lineage fallback is correct. Version
  identity also needs to be stored, not inferred:

```diff
 class Playbook(...):
     embedding = mapped_column(Vector(3072), nullable=True)
+    embedding_version_id = mapped_column(UUID(as_uuid=True), nullable=True)
```

  Only a published/approved version should update the embedding used by approved
  agent seeds, and the write must set both fields atomically. Draft creation may
  have a separate draft embedding but must not overwrite the published seed.
  Rank, execute, and semantic seed selection must use the same version-selector
  helper. Rollback at `playbooks.py:643-647` is another affected caller: it can
  publish a new version without calling `embed_playbook`.
- **Cross-file impact:** Checked create version, approve, rollback, ranker,
  execution, agent repository, detail references, worker/API embedding callers.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #14 Validation: Paid paths repeat or bypass attributed work

- **Quote check:** Vector search, Celery retry, and evidence embed lines matched.
  Knowledge and episode-loop aggregates are **UNVERIFIABLE as verbatim** because
  of `...`.
- **Trace re-run:** `_retrieve` calls `search_evidence_semantic` without a supplied
  vector, causing one unattributed embed; `_attach_sections` embeds the identical
  query again with tenant context. Episode repair loops sequentially. Any exception
  escaping playbook `work()` invokes a whole-task retry (`max_retries=2`), replaying
  retrieval and generation after rollback. `_ensure_embedding` omits tenant/db.
- **Accuracy-impact re-check:** This issue is primarily cost/observability. A
  blocked tenant can bypass its budget on unattributed calls. Batch conversion
  must not turn per-item soft failure into whole-batch data loss.
- **Cost-impact re-check:** Knowledge generation pays exactly two embedding calls
  when documents survive to `_attach_sections`; zero documents skip the second.
  A task can run at most three Celery attempts, and LiteLLM itself has bounded
  `num_retries=2`; this is expensive replay, not an unbounded storm. Clustering is
  capped at 100 candidates. Pattern synthesis can still send all members of that
  bounded cluster.
- **Fix review:** Reuse one attributed query embedding through both search and
  section attachment. Pass tenant/db through `_ensure_embedding`. Batch episode
  repair in configured chunks, but preserve failure isolation by validating each
  returned vector and retrying only failed chunks/items. Separating retrieval from
  completion retry requires persisted/idempotent stage state; simply moving
  `self.retry` inside the LLM call is not enough if the transaction later fails.
  Sibling unattributed calls also exist in attachment evidence embedding,
  decision creation/query embedding, and the generic semantic-search fallback;
  those must be included in any budget-gate fix.
- **Cross-file impact:** Checked every `generate_embedding*`, `embed_evidence`,
  `embed_decision`, and `embed_playbook` production caller, plus chunk stamping
  and provider retry configuration.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

#### Issue #15 Validation: UI confidence defaults and runtime cache

- **Quote check:** Frontend confidence and React Query blocks matched. API and
  runtime aggregates are **UNVERIFIABLE as verbatim** because of `...`.
- **Trace re-run:** `ver_map` consumes all versions without ordering, so the last
  database row wins. Missing values fall back to `0.8`. Detail returns
  `confidence=null`; because the JSX condition tests only `!== undefined`, null
  still renders and the `?? 0.8` chain displays 80%. These paths reproduce.
  `/runtime/match` does **not** read this Redis entry on later matches. It creates
  a fresh random `match_id`, recomputes ranking, and stores the T0 payload only for
  `/runtime/explain/{match_id}`.
- **Accuracy-impact re-check:** Defaulted/unordered UI confidence is a silent
  presentation error. The cached explanation retaining T0 scores after an edit is
  correct decision-lineage behavior, not stale ranking served to a new request.
  A 30-second React Query stale window is real but bounded and the detail mutation
  invalidates the detail query; transition currently omits invalidating the list.
- **Cost-impact re-check:** None. Periodic refetching would add paid embedding
  calls and should not be introduced.
- **Fix review:** Drop the `0.8` defaults and select an explicit current/latest
  published version. In JSX, compute one nullable confidence and render the badge
  only when non-null. Do **not** invalidate or rebuild historical match-id
  explanations on version edits; that proposed fix is regressive and destroys the
  reason the explanation exists. Add `invalidateQueries({queryKey:["playbooks"]})`
  after a transition if immediate list freshness is required.
- **Cross-file impact:** Checked list/detail/version APIs, types, React Query
  mutations, runtime match/explain, ranker, and Redis invalidation helper.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence; severity corrected to P2.

#### Issue #16 Validation: MMR tie-break uses an unordered set

- **Quote check:** Matched exactly.
- **Trace re-run:** Equal scores retain the first index visited from
  `set(range(n))`. Across `PYTHONHASHSEED` values `0,1,2,7,42,99,123456`, a 20-way
  exact tie selected `0,1,2,3,4` every time. CPython integer hashes are stable,
  so the report's “hash-randomized per process” mechanism is false. Set order is
  nevertheless unspecified by the language, making this a portability/maintenance
  risk rather than a demonstrated current-process jitter.
- **Accuracy-impact re-check:** No per-process silent rank jitter reproduced.
  Different Python implementations/versions may choose another equal-score item.
  A separate SQL issue remains: `_chunk_candidates` orders only by distance, so
  equal-distance rows at the oversample boundary can enter in database-dependent
  order before MMR.
- **Cost-impact re-check:** None.
- **Fix review:** Converting to a list is deterministic only if the input ordering
  is deterministic. Close both layers:

```diff
-        .order_by(distance)
+        .order_by(distance, EvidenceChunk.id)
```

```diff
-    remaining = set(range(len(candidates)))
+    remaining = list(range(len(candidates)))
 ...
-            if best_score is None or score > best_score:
-                best_index, best_score = index, score
+            key = (score, -candidates[index].distance, str(candidates[index].chunk_id))
+            if best_key is None or key > best_key:
+                best_index, best_score, best_key = index, score, key
 ...
-        remaining.discard(best_index)
+        remaining.remove(best_index)
```

  Initialize `best_key = None` each loop. This adds no provider/LLM cost.
- **Cross-file impact:** Checked both semantic-search entry points, candidate SQL,
  MMR tests, rollup's existing `(distance, chunk_id)` sort, and hash-seed behavior.
- **Verdict:** **PARTIALLY CONFIRMED**, High confidence.

### 3. Uncertainty Admissions - Resolved

| Admission | Resolution |
|---|---|
| pgvector zero-vector behavior was inferred | **Resolved.** Live pgvector 0.8.1 returned Python/SQL `NaN` for zero-vs-real and zero-vs-zero cosine distance. The numerical premise is confirmed; ANN slot occupancy was not separately demonstrated. |
| Pattern queue cardinality/race was unknown | **Resolved as realistically reachable.** Live schema has no relevant uniqueness. Docker starts Celery without `--concurrency`, which defaults to a multi-process pool on a multi-core Linux host; API/manual discovery can overlap too. Windows `dev.py` defaults to `solo`, but that is not the only configured deployment. |

### 4. Self-Check Re-Audit

#### Four dropped candidates

1. **Domain-wide negative knowledge penalty - correctly dropped at this audit's
   proof bar.** It applies the same bounded score to every playbook in one domain;
   tests explicitly encode that behavior. It can shift an absolute threshold by
   at most `0.05`, but no shown invariant ties a negative item to one playbook.
2. **`Vector.cosine_distance` vs halfvec - correctly dropped as a logic issue.**
   Precision/index-plan differences alone do not prove a ranking inversion.
3. **Trigram `>` vs `>=` - correctly dropped.** Exact `0.3` is excluded, but the
   code does not state that the threshold is inclusive, and this pipeline report
   did not establish the business boundary.
4. **Restating prior findings - correctly dropped.** Cross-reference is the right
   treatment; they are not new pipeline findings.

#### Explicit “no issue found” claims

- **Dimension truncate/pad - partially correct, with a missed output-shape gap.**
  No padding/truncation exists, and a single vector of wrong dimension fails loud.
  Batch generation validates only `embeddings[0]`; it does not verify every vector
  or response cardinality. See New Finding A below.
- **MMR raw dot as cosine - correctly cleared for non-zero valid vectors.** Rows
  are L2-normalized before the dot product. Zero-vector semantics remain Issue #2.
- **Message-function classifier - incorrectly cleared.** Enum validation is good,
  but `min(max(float("NaN"), 0), 1)` remains NaN. See New Finding B.
- **`classify_support` - correctly cleared.** The function is deterministic,
  checks contested first with inclusive `>=`, and its production caller supplies
  non-negative aggregate counts.
- **Correlation suggestions - correctly cleared.** It reuses up to six stored
  chunk vectors, has no provider call, and stops adding work at 500 pending rows.
- **Frontend threshold reimplementation - correctly cleared.** Runtime renders
  backend results and does not locally apply `0.35`; suggestions do not locally
  reapply the backend's `0.7` gate. Review-page color bands are presentation, not
  filtering.

### 5. Cross-Reference Table Check

| Prior audit row | Validator result |
|---|---|
| Prior #2 title-based evidence deletion | **Confirmed conceptually, with wording caution.** Pattern Issue #7 repeats title-as-identity but fuses pattern membership rather than deleting evidence. The interaction is real; the failure effects differ. |
| Prior #6 closed graph edges | **Partially supported.** Closed positive/negative edges still influence ranking, as the prior validation confirmed. This report's dependency on “inflated” Issue #1 is not supported, and Issue #4's scale is uncalibrated. The stale-edge defect stands without those claims. |
| Prior #7 expired playbooks | **Partially supported.** Expiry zeros only freshness/recency and the prior validator confirmed recommend-then-reject. FTS population dependence can affect the remaining score, but `/2` semantic inflation is not established. Issues #11/#13 share a recommend-vs-execute inconsistency; duplicates alone do not necessarily do so. |
| Prior #8 NaN confidence | **Confirmed.** Pattern and playbook confidence use `float(value or default)`: valid zero is replaced and `NaN` remains non-finite. The same finite `[0,1]` parser family is required. |
| Validation 5.3 body-only `content_hash` | **Confirmed as not re-opened.** Pattern identity does not use that hash, and a pattern should not adopt it as its sole key. |
| Prior #1/#3/#4/#5 no new interaction | **Reasonable.** No direct new dependency was demonstrated. Issue #11's claimed destructive action remains limited by prior #1's finding that execution is externally recorded rather than performed in-process. |

### 6. New Findings Surfaced During Validation

These were found only while checking the report's explicit negative claims and
affected callers; no broader audit was performed.

#### New Finding A: Batch embedding validates only the first vector and not response count

**Severity:** P1  
**Confidence:** Confirmed  
**Location:** `backend/src/contextedge/ai/provider.py:883-896`;
`backend/src/contextedge/services/evidence_chunk_service.py:196-213`

Original code (verbatim):

```python
        embeddings = [item["embedding"] for item in response.data]
        if embeddings and len(embeddings[0]) != 3072:
            outcome = "error"
            raise ValueError(
                f"Embedding model '{model}' returned {len(embeddings[0])} dimensions, "
                f"but 3072 are required. Use a model that supports 3072 dims "
                f"(e.g. vertex_ai/gemini-embedding-004 or text-embedding-3-large)."
            )
        return embeddings
```

```python
    for ch, emb in zip(chunks, embeddings):
        ch.embedding = emb
        written += 1
```

**Trace:** Given two requested texts and provider data containing one 3,072-vector
plus one 768-vector, the first-vector check passes; the second is assigned and the
database rejects it later, aborting the batch. Given only one returned vector for
two texts, `zip` silently stamps one chunk and leaves the other NULL while reporting
one write; the provider helper never reports the cardinality violation.

**Fault:** Loud transaction failure for mixed dimensions; silent incomplete
embedding for short provider responses.

Corrected logic:

```diff
         embeddings = [item["embedding"] for item in response.data]
-        if embeddings and len(embeddings[0]) != 3072:
+        if len(embeddings) != len(texts):
+            outcome = "error"
+            raise ValueError(
+                f"Embedding provider returned {len(embeddings)} vectors for {len(texts)} inputs"
+            )
+        bad_dimensions = [
+            index for index, embedding in enumerate(embeddings)
+            if not isinstance(embedding, (list, tuple)) or len(embedding) != 3072
+        ]
+        if bad_dimensions:
             outcome = "error"
             raise ValueError(
-                f"Embedding model '{model}' returned {len(embeddings[0])} dimensions, "
-                f"but 3072 are required. Use a model that supports 3072 dims "
-                f"(e.g. vertex_ai/gemini-embedding-004 or text-embedding-3-large)."
+                f"Embedding model '{model}' returned invalid dimensions at indexes "
+                f"{bad_dimensions[:10]}; every vector must have 3072 dimensions"
             )
```

This adds no provider call and makes the third-party output contract atomic.

#### New Finding B: Message-function `NaN` confidence bypasses the low-confidence fallback

**Severity:** P1  
**Confidence:** Confirmed  
**Location:** `backend/src/contextedge/ai/classifiers/message_function.py:58-64`;
`backend/src/contextedge/services/ticket_bridge_service.py:797-804, 816-818, 953-956`

Original code (verbatim):

```python
    function = result.get("function")
    if function not in MESSAGE_FUNCTIONS:
        function = "unclassified"
    try:
        confidence = min(max(float(result.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {"function": function, "confidence": confidence}
```

```python
    confidence = getattr(evidence, "message_function_confidence", None) or 0.0
    if label != "correction" or confidence < CLASSIFIER_TRUST_FLOOR:
        return counts
```

**Trace:** Mocked model output
`{"function":"correction","confidence":"NaN"}` returned a valid `correction`
label with `math.isnan(confidence) == True`. NaN is truthy, so `or 0.0` does not
replace it. `NaN < CLASSIFIER_TRUST_FLOOR` is false; the correction path proceeds
as if trusted and may supersede existing ticket membership.

**Fault:** Silent false-positive correction/dissociation behavior from malformed
model confidence.

Corrected logic:

```diff
+import math
 ...
     try:
-        confidence = min(max(float(result.get("confidence", 0.0)), 0.0), 1.0)
+        raw_confidence = result.get("confidence", 0.0)
+        confidence = float(raw_confidence) if not isinstance(raw_confidence, bool) else 0.0
     except (TypeError, ValueError):
         confidence = 0.0
+    if not math.isfinite(confidence):
+        confidence = 0.0
+    confidence = min(max(confidence, 0.0), 1.0)
```

This adds no call and restores the documented deterministic low-confidence fallback.

### 7. Edge Case and Overall Assessment

#### Edge Case Matrix validation

| Fixture group | Validation of the proposed corrected result |
|---|---|
| Boundary cosine `0`, `0.30`, `1.2` | Identical-vector result is already consistent. The shared `1-d` expectations at `0.30/1.2` follow the proposed arithmetic but are not established business requirements; do not use them as acceptance tests for Issue #1. |
| Zero/two-zero vectors | Nullable-return correction excludes them and avoids NaN. The report's raising fix does not guarantee “stays NULL” for every caller and can retry/fail attachment processing. |
| Dimension mismatch | Single-vector 768 fails loud. Batch mixed dimensions/cardinality do not; New Finding A supplies the missing assertion. |
| FTS single/all-zero | Current single result becomes 1.0 and all-zero remains 0. A fixed transform would satisfy the desired population independence only after calibration; `/0.1` is not validated. |
| Quality zero evidence | `_quality_score(0.9, 0) == 0.54` reproduced; already correct. |
| Tied/missing classification | Missing label currently defaults to not-relevant and skips at 0.92. The corrected canonical parser yields `unclassified` and no skip. `"not relevant"` canonicalizes consistently. |
| Missing pattern verdict | Fail-closed parser produces false, but “episode remains unlinked” is too strong: the worker can still create a new pattern from the subsequent cluster branch. The important result is “not merged into that existing pattern.” |
| Title collision / linkage chain | Desired results require the additional service/query changes described above; the report does not provide executable diffs that achieve them. |
| Confidence `0.0` | A finite parser preserves zero. Existing playbook generation then skips because `0.0 < 0.5`; the pattern itself need not be discarded unless that is a separate rule. |
| Concurrent/burst N=50 | Unique membership plus a pre-synthesis advisory lock can produce one pattern. A service-only post-synthesis lock does not prevent duplicate LLM cost. With no existing pattern, 50 mutually close tickets already cause one bounded synthesis; with an existing pattern they can cause up to 50 validation calls. |
| Existing playbook / empty steps | A shared pre-LLM guard can return 409 without spend. Empty-step validation must occur before the playbook shell is flushed/committed. |
| Safety classes | Unknown is rejectable centrally. Validating with current `PlaybookStep` alone does not reject missing values because the field is optional; an explicit bound-action rule is required. |
| Published/unpublished versions and lineage | Removing lineage fallback works. Version-correct behavior requires a stored embedding-version identity and coverage of create, approve, rollback, rank, agent seed, and execute. |
| Celery LLM 5xx | The proposed “only completion retries” result needs staged idempotent state. Current whole task can run three times, each with bounded provider retries. |
| Runtime cache/edit | “Explain 404 or rebuilt” is the wrong expected result. Explain should preserve the T0 decision snapshot; a new match should recompute, which it already does. |
| Frontend missing confidence | Rendering an em dash/nonexistent badge is correct. Transition should invalidate the list query if immediate cross-page freshness is required. |

#### Overall Assessment

The report contains substantial real defects, especially malformed classifier
defaults, fail-open pattern matching, title-based pattern fusion, zero-confidence
inflation, missing concurrency constraints, worker/API playbook divergence,
pattern-growth duplication, version/lineage drift, and repeated/unattributed paid
calls. It is not safe to apply the proposed fixes as-is. Issue #1 is not a
demonstrated bug; Issue #3's stated traces are blocked; Issue #15's runtime-cache
remedy would destroy historical explanation semantics; and Issue #16's claimed
per-process mechanism does not reproduce. The remaining findings require the
quote corrections and fix changes above, particularly centralized parsing,
pre-synthesis locking, one shared playbook-generation service, explicit
version-keyed embeddings, and atomic validation of every batch vector. No
production code was modified during this validation.

## 8. Residual Backend and Frontend Assurance Review (2026-08-17)

### 8.0 Scope and Outcome

This pass checked the logic within the backend and frontend scope named in
Section 0, excluding defects already recorded in Sections 1-7. It does **not**
support a blanket conclusion that all remaining logic is correct: six additional
logic gaps were found. Everything else is classified below as either confirmed
within explicit tested boundaries or unverified.

Repository state reviewed:

- Branch: `feat/graph-quality-hardening`
- Commit: `233b643ba8be014e64e13fc70b150fe88470f5bd`
- Backend: 291 selected tests passed and the scoped backend modules compiled.
- Three additional policy tests did not reach their assertions because the local
  environment is missing `rfc8785`. Runtime/execution test collection was blocked
  by the same missing dependency.
- Frontend: 42 tests across 7 files passed, ESLint passed, and the Next.js
  production build and TypeScript validation completed successfully. The first
  sandboxed build attempt could not download Google Fonts; the build passed when
  network access was allowed.
- Not exercised: browser E2E, live LLM/connector behavior, Redis outage recovery,
  Celery concurrency, production-scale ANN recall, and complete execution flows.
- No production code was modified.

### 8.1 Residual Gap Matrix

| # | Severity | Confidence | Location | Failure type | Impact summary |
|---|---|---|---|---|---|
| R1 | P1 | Confirmed | `search/hybrid_ranker.py:271-281, 295-311` | Silent signal loss | Ranking can return recommendations after embedding or semantic retrieval fails, without reporting degraded scoring. |
| R2 | P1 | Confirmed | `api/v1/runtime.py:230-238` | Lifecycle / silent failure | `/runtime/match` can return a `match_id` that was never cached and therefore cannot be explained. |
| R3 | P1 | Confirmed | `services/knowledge_retrieval_service.py:468-484` | Silent relevance degradation | An embedding failure changes section selection from semantic relevance to document order without reporting the change. |
| R4 | P1 | Confirmed | Frontend scoped list/queue queries, representative `suggestions/page.tsx:34-39, 114-120` | Error/empty-state conflation | API failures are presented as legitimate empty queues or tables. |
| R5 | P1 | Confirmed | `runtime/page.tsx:161-229` | Stale component state | A failed second request leaves a prior successful match, explanation, or playbook visible beside the new inputs. |
| R6 | P1 | Confirmed | `playbooks/page.tsx:61-72, 92-101`; `use-pagination.ts:15-25` | Pagination state | Changing or clearing a search retains the old page offset and can hide valid page-one matches. |

### 8.2 Detailed Residual Findings

#### Residual R1: Ranking silently continues without semantic evidence

Location: `backend/src/contextedge/search/hybrid_ranker.py:271-281, 295-311`

Original code:

```python
query_embedding: list[float] | None = None
if query_text.strip():
    try:
        # Cost hardening: attributed + budget-gated — this call runs on
        # every ranking query and every eval case; unattributed spend
        # here was invisible to /admin/cost and the tenant budget.
        query_embedding = await generate_embedding(
            query_text, tenant_id=tenant_id, db=db
        )
    except Exception:
        query_embedding = None
```

```python
sem_rows: list = []
semantic_evidence_ids: set[uuid.UUID] = set()
if query_text.strip() and query_embedding is not None:
    try:
        sem_rows = await search_evidence_semantic_for_playbook(
            db,
            tenant_id,
            pb.id,
            pv_id,
            query_text,
            limit=10,
            query_embedding=query_embedding,
            exclude_policy_ids=excluded_policy_ids,
        )
        semantic_evidence_ids = {row[0].id for row in sem_rows if row[0] is not None}
    except Exception:
        sem_rows = []
```

Failure trace:

1. `generate_embedding` or `search_evidence_semantic_for_playbook` raises.
2. The exception is swallowed without a log, error response, or degraded-result flag.
3. `sem_rows` stays empty and semantic score becomes zero.
4. Keyword, graph, quality, identity, recency, and freshness signals are still
   calculated and can exceed `MIN_RECOMMENDATION_SCORE` without semantic support.
5. Runtime, evaluation, and `runtime_service.py` callers receive an ordinary
   recommendation and cannot distinguish it from a fully evaluated result.

Resulting fault: silent partial-signal recommendation. Fail-soft ranking may be a
valid availability decision, but returning an indistinguishable normal result is
not a verified business rule.

#### Residual R2: Runtime returns match IDs that may not be explainable

Location: `backend/src/contextedge/api/v1/runtime.py:230-246`

Original code:

```python
try:
    redis = request.app.state.redis
    await redis.setex(
        f"runtime:match:{match_id}",
        MATCH_CACHE_TTL_SEC,
        json.dumps(payload),
    )
except Exception:
    pass

return RuntimeMatchResponse(
    match_id=match_id,
    session_id=body.session_id,
    results=results,
    fallback_guidance=fallback,
    filters_applied=filters_applied,
)
```

Failure trace:

1. Ranking succeeds and constructs `match_id = M`.
2. Redis `setex` raises because Redis is unavailable or rejects the write.
3. The exception is discarded and the response still returns `M`.
4. A later `/runtime/explain/M` lookup cannot find the snapshot and returns 404
   once Redis recovers, or fails while the outage continues.

Resulting fault: a successful response advertises an explanation handle that was
never persisted. This is separate from the existing cache-TTL discussion: the
problem occurs at initial snapshot creation.

#### Residual R3: Knowledge section selection silently changes algorithms

Location: `backend/src/contextedge/services/knowledge_retrieval_service.py:468-484`

Original code:

```python
try:
    embedding = await generate_embedding(query, tenant_id=tenant_id, db=db)
except Exception:  # noqa: BLE001
    embedding = None

for document in documents:
    stmt = select(EvidenceChunk).where(
        EvidenceChunk.tenant_id == tenant_id,
        EvidenceChunk.evidence_id == document.evidence_id,
    )
    if embedding is not None:
        from contextedge.search.vector_ops import halfvec_cosine_distance

        distance = halfvec_cosine_distance(EvidenceChunk.embedding, embedding)
        stmt = stmt.where(EvidenceChunk.embedding.is_not(None)).order_by(distance)
    else:
        stmt = stmt.order_by(EvidenceChunk.chunk_index)
```

Failure trace:

1. Document retrieval succeeds, but the section-query embedding call raises.
2. The handled exception does not reach the outer logged retrieval-failure path.
3. Each document returns its first chunks rather than its closest semantic chunks.
4. Playbook generation consumes those sections as normal knowledge input, without
   a marker that section relevance was degraded.

Resulting fault: silently different grounding selection. It is unverified whether
document order is an acceptable fallback for the business contract.

#### Residual R4: Frontend fetch errors are shown as valid empty state

Representative location:
`frontend/src/app/(dashboard)/suggestions/page.tsx:34-39, 114-120`

Original code:

```tsx
function SemanticQueue() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery<SemanticSuggestion[]>({
    queryKey: ["suggestions", "pending"],
    queryFn: () => api.get("/correlations/suggestions", { status: "pending" }),
  });
```

```tsx
if (isLoading) return <DataTableSkeleton columns={5} />;
if (data.length === 0)
  return (
    <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
      No pending semantic suggestions.
    </div>
  );
```

Failure trace:

1. The API returns an authorization error, server error, or network failure.
2. React Query exhausts its configured retry.
3. The component ignores `error`; destructuring leaves `data = []`.
4. After loading completes, the component renders “No pending semantic
   suggestions,” which is indistinguishable from a successful empty response.

The same failure/empty conflation exists in the scoped patterns list, playbooks
list, fleet suggestions, identity review queue, execution approvals, runtime
feedback/sessions/domains, and playbook version/reference queries.

Resulting fault: silent false-empty operational state.

#### Residual R5: Runtime sandbox retains successful data after a failed rerun

Location: `frontend/src/app/(dashboard)/runtime/page.tsx:161-229`

Original code:

```tsx
const matchMut = useMutation({
  mutationFn: async () => {
    setFormError(null);
```

```tsx
  onSuccess: (data) => {
    setMatch(data);
    setExplain(null);
  },
  onError: (e: Error) => setFormError(e.message),
});
```

```tsx
const fetchPlaybookMut = useMutation({
  mutationFn: async () => {
    setPbError(null);
```

```tsx
  onSuccess: (data) => setPlaybookVersion(data),
  onError: (e: Error) => setPbError(e.message),
});
```

Failure trace:

1. Match A succeeds and populates `match`; explanation or playbook data may also
   be populated.
2. The user changes the form and submits request B.
3. B fails validation or the API call fails.
4. `onError` records only an error string. It does not clear `match`, `explain`,
   or `playbookVersion`.
5. The screen can display B's current inputs and error beside A's old result,
   without identifying the result as stale.

Resulting fault: stale-result state is presented as current context after a
failed lifecycle transition.

#### Residual R6: Playbook search does not reset pagination

Locations:

- `frontend/src/app/(dashboard)/playbooks/page.tsx:61-72, 92-101`
- `frontend/src/lib/hooks/use-pagination.ts:15-25`

Original code:

```tsx
const pg = usePagination(50);
const [searchQuery, setSearchQuery] = useState("");

const params: Record<string, string> = { ...pg.params };
if (searchQuery.trim()) {
  params.q = searchQuery.trim();
}

const { data = [], isLoading } = useQuery<Playbook[]>({
  queryKey: ["playbooks", pg.page, searchQuery],
  queryFn: () => api.get("/playbooks", params),
});
```

```tsx
<Input
  placeholder="Search playbooks by issue, description, title, or ticket # (e.g. 408801)..."
  value={searchQuery}
  onChange={(e) => setSearchQuery(e.target.value)}
  className="pl-9 pr-9"
/>
```

```ts
export function usePagination(pageSize = 50): PaginationState & PaginationActions & { params: Record<string, string> } {
  const [page, setPage] = useState(0);

  return {
    page,
    pageSize,
    offset: page * pageSize,
    params: { limit: String(pageSize), offset: String(page * pageSize) },
    nextPage: () => setPage((p) => p + 1),
    prevPage: () => setPage((p) => Math.max(0, p - 1)),
    reset: () => setPage(0),
```

Failure trace:

1. The user advances to page 2 (`offset=100` for a 50-row page size).
2. The user enters a search having 20 matching records.
3. The query includes the new `q` but retains `offset=100`.
4. The API correctly returns an empty page even though matches exist at offset 0.

Resulting fault: false-empty search results until the user manually navigates
back to the first page. Clearing the search has the same stale-offset behavior.

### 8.3 Backend Area-by-Area Verdict

| Area | Verdict | Checks and limitations |
|---|---|---|
| Vector primitives and chunk rollup | **Confirmed within tested boundaries** | Empty candidate lists, one candidate, malformed dimensions, nonzero normalization, selection limits, and distance ordering passed. Existing zero-vector, batch-response, and tie findings remain excluded. |
| Embedding provider contract | **Unverified end-to-end** | Single-vector dimension mismatch fails loudly. Live provider response shape, batch cardinality, rate limiting, and external fallback behavior were not exercised. |
| Semantic evidence search and access control | **Confirmed within unit scope** | Tenant, domain, role/access-policy, published-version, and empty-result filtering passed. Production HNSW recall and query plans remain unverified. |
| Hybrid ranking and FTS | **Not correct** | Existing score findings remain, and R1 prevents confirmation. Normal tenant/risk/lifecycle filters and inclusive threshold behavior passed. |
| Relevance and message classifiers | **Confirmed only for finite, schema-compatible inputs** | Ordinary malformed confidence types and finite boundaries are handled. Existing enum and NaN findings remain. Live model compliance is unverified. |
| Extraction and normalization workers | **Unverified end-to-end** | Static branches compiled, but connector payload diversity, Celery retry ordering, rollback, and concurrent normalization were not executed together. |
| Pattern construction and clustering | **Partially confirmed** | Domain-safe membership, existing-link avoidance, and tested cluster branches passed. Existing identity, fail-open, confidence, and concurrency issues prevent full confirmation. |
| Playbook generation and grounding | **Partially confirmed** | Citation validation, grounding classification, empty-step rejection, risk derivation, and version-schema tests passed. Live model output and graph-link failure behavior are unverified. |
| Playbook lifecycle and versioning | **Partially confirmed** | Transition allowlists, step requirements, version-collision handling, and published-version selection passed available tests. Three policy tests were blocked by `rfc8785`. |
| Knowledge retrieval | **Not correct** | Lifecycle, applicability, and supersession tests passed, but R3 remains. Returning `[]` after a logged retrieval failure and continuing generation also needs an explicit product contract. |
| Correlation suggestions | **Confirmed within unit scope** | Pair identity, thresholds, corroborator gates, learned floors, nested-transaction duplicate handling, and caps passed. Production-corpus recall remains unverified. |
| Similar-episode handling | **Partially confirmed** | Occurrence scoping and similarity dedup tests passed. The superseded-filter API test was blocked by the missing dependency. |
| Execution and approval lifecycle | **Unverified** | Static guards exist, but runtime/execution tests could not load. Concurrent decisions, idempotent delivery, artifact signing, and complete transitions are not confirmed. |
| Agent graph semantic seeds | **Unverified end-to-end** | Tenant/domain/reviewer filters were inspected, but live graph hydration, partial database failure, and seed-to-traversal behavior were not executed. |
| Backend API routes | **Partially confirmed** | Patterns and most playbook service behavior passed available tests. R2 affects runtime; execution-backed endpoints remain unverified. |

### 8.4 Frontend Area-by-Area Verdict

| Area | Verdict | Checks and limitations |
|---|---|---|
| Patterns list and generation | **Not fully correct** | Pending guards and query invalidations are present, but request failures collapse into empty data under R4. Generation also depends on previously flagged backend divergence. |
| Playbooks list | **Not correct** | Missing-confidence display is safe, but R4 and R6 affect failure and search behavior. |
| Playbook detail and versions | **Partially confirmed** | Step sorting, empty/malformed step arrays, citations, and optional fields are covered by tests. Version/reference query failures are not surfaced; lifecycle mutations lack browser/API E2E. |
| Runtime sandbox | **Not correct** | JSON-object, UUID, empty-value, and `top_k` boundaries are checked. R2 and R5 leave explanation and stale-state gaps. |
| Suggestions | **Not correct** | Mutation pending guards and invalidation are present. R4 renders query failure as an empty queue; malformed upstream fields have no runtime validation. |
| React Query defaults | **Confirmed as configured, not behaviorally sufficient** | `staleTime: 30_000` and `retry: 1` are active. Existing staleness concerns remain, and retry exhaustion is hidden by R4. |
| Execution approval polling | **Unverified** | The query polls every 30 seconds and submission is disabled while pending. Concurrent reviewer decisions and API conflict handling were not exercised. |

### 8.5 Self-Check and Final Assessment

All six residual findings were re-checked against the current source at the cited
locations. None were removed during the final self-check. R1-R6 are distinct from
the existing numbered findings: they concern silent semantic degradation,
initial runtime snapshot loss, section-ranking fallback, frontend error/empty
conflation, stale local mutation state, and search pagination state.

The non-flagged happy paths have meaningful automated support, but this codebase
cannot yet be represented as having no missed gaps or unhandled edge cases. The
six residual findings should be incorporated into remediation planning, while
execution, live integration, concurrency, and browser behavior must remain
explicitly unverified until their blocked or absent test paths are exercised.

## 9. Second-Pass Completeness Review (2026-08-17)

### 9.0 Scope and Conclusion

This pass re-checked the backend and frontend areas declared in Section 0,
specifically looking for gaps omitted by Sections 1-8. Repository state remained
`feat/graph-quality-hardening` at commit
`233b643ba8be014e64e13fc70b150fe88470f5bd`. No production code was modified.

The answer to "are all logic-related gaps covered?" is **no**. Fifteen missed
gaps were found. Three are realistic P0 paths. Section 8 was correct to avoid a
blanket assurance, but its residual inventory was incomplete, and its statement
that correlation-suggestion caps passed is disproved below.

Focused reproductions confirmed that:

- the version-list query returns a simulated foreign-tenant version and contains
  no tenant predicate;
- arbitrary feedback playbook identifiers are persisted;
- `PlaybookCreate` accepts an unknown risk tier and that tier passes a medium cap;
- a future validation timestamp produces freshness `1.167`;
- a shared evidence/knowledge identifier is materialized as
  `derived_from_evidence`, not `based_on_kb`;
- the FTS query contains neither a redaction predicate nor an offset; and
- the frontend expression for an explicit weight of zero produces `1`;
- a foreign `rollback_skill_id` is accepted without any lookup; and
- rollback planning dereferences that foreign skill globally and copies its tool
  reference and safety class into the caller tenant's plan.

The earlier 291-backend-test and 42-frontend-test results remain useful regression
evidence, but their assertions do not cover the paths below. The same previously
listed integration limitations remain: no live LLM/connectors, browser E2E,
Redis-outage recovery, Celery concurrency, production ANN, or complete execution
flow.

### 9.1 Missed Gap Matrix

| # | Severity | Confidence | Location | Failure type | Impact summary |
|---|---|---|---|---|---|
| M1 | P0 | Confirmed | `api/v1/playbooks.py:505-512` | Tenant invariant | Any authenticated tenant can read another tenant's playbook versions when the UUID is known. |
| M2 | P0 | Confirmed | `api/v1/patterns.py:31-38`; `services/pattern_service.py:528-540`; `patterns/page.tsx:193-220` | Authorization / destructive lifecycle | Any authenticated user can invoke deduplication that deletes or merges tenant artifacts. |
| M3 | P0 | Confirmed | `api/v1/patterns.py:216-235`; `schemas/review.py:174-178`; `api/v1/playbooks.py:680-685` | Tenant invariant / lineage | A foreign-tenant episode can be linked to a local pattern and sent into playbook generation. |
| M4 | P1 | Confirmed | `api/v1/runtime.py:357-368`; `services/drift_service.py:39-47` | Reconciliation / tenant integrity | Feedback submitted by one tenant can contribute to another tenant's drift alert. |
| M5 | P1 | Confirmed | `api/v1/evidence.py:29-59`; `search/pg_fts.py:12-22, 64-81` | Filter contract / pagination | Query search silently ignores `source_id`, `domain_id`, and `offset`. |
| M6 | P1 | Confirmed | `schemas/playbook.py:210-222`; `search/risk_policy.py:12-20` | Confidence/risk gate | Unknown risk tiers are accepted and treated as medium. |
| M7 | P1 | Confirmed | `services/correlation_suggestion_service.py:303-322, 383-411`; `:101-119` | Boundary / concurrency | The hard queue cap can reach 504 sequentially and higher concurrently; learned statistics use an unordered partial population. |
| M8 | P1 | Confirmed | `api/v1/patterns.py:319-330` | Partial-input acceptance | Missing or foreign requested episodes are silently dropped when at least one valid episode remains. |
| M9 | P1 | Confirmed | `api/v1/playbooks.py:206-224`; `models/playbook.py:63-74, 115-119` | Cross-tenant association | A tenant playbook can reference another tenant's domain or pattern. |
| M10 | P1 | Confirmed | `services/playbook_service.py:152-164, 180-196` | Provenance precedence | Generic evidence provenance wins over the more specific knowledge provenance. |
| M11 | P1 | Confirmed | `patterns/[id]/page.tsx:38-44` | Exact-boundary conversion | A requested link weight of `0` is submitted as `1.0`. |
| M12 | P1 | Confirmed (configuration-dependent) | `ai/generators/playbook_generator.py:68-75`; `workers/pattern_tasks.py:604-635` | Prompt/provenance contract | A tenant pinned to prompt v1/v2 records knowledge provenance for documents not supplied to that prompt. |
| M13 | P2 | Confirmed | `search/hybrid_ranker.py:382-389` | Score bound | Future validation timestamps produce freshness above `1.0`. |
| M14 | P1 | Confirmed | `api/v1/patterns.py:64-70, 106-112` | Ordering / duplicate reconciliation | When duplicate playbooks exist, database row order chooses the playbook shown for a pattern. |
| M15 | P1 | Confirmed | `services/skill_registry_service.py:184-235`; `models/skill.py:191-193`; `services/remediation_service.py:48-61` | Tenant invariant / remediation plan | A tenant can bind a foreign rollback skill; its metadata is later copied into the local rollback plan. |

### 9.2 Detailed Missed Findings

#### M1: Cross-tenant playbook-version disclosure

Location: `backend/src/contextedge/api/v1/playbooks.py:505-512`

Original code:

```python
@router.get("/{playbook_id}/versions", response_model=list[PlaybookVersionResponse])
async def list_versions(playbook_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(PlaybookVersion)
        .where(PlaybookVersion.playbook_id == playbook_id)
        .order_by(PlaybookVersion.created_at.desc())
    )
    return result.scalars().all()
```

Flawed logic: `user` is unused. Unlike the adjacent version create, diff, and
rollback routes, the query never establishes that the parent playbook belongs to
`user.tenant_id`.

Concrete failure trace:

1. Tenant A authenticates and supplies a tenant B playbook UUID.
2. The only predicate is `PlaybookVersion.playbook_id == playbook_id`.
3. Every version for B's playbook is returned, including steps, triggers,
   conflicts, and evidence references.

Resulting fault: cross-tenant information disclosure.

Corrected logic:

```diff
+    playbook = (
+        await db.execute(
+            select(Playbook).where(
+                Playbook.id == playbook_id,
+                Playbook.tenant_id == user.tenant_id,
+            )
+        )
+    ).scalar_one_or_none()
+    if playbook is None:
+        raise HTTPException(status_code=404, detail="Playbook not found")
     result = await db.execute(
         select(PlaybookVersion)
         .where(PlaybookVersion.playbook_id == playbook_id)
```

#### M2: Destructive deduplication has no role guard

Locations: `backend/src/contextedge/api/v1/patterns.py:31-38`,
`backend/src/contextedge/services/pattern_service.py:528-540`, and
`frontend/src/app/(dashboard)/patterns/page.tsx:193-220`

Original code:

```python
@router.post("/deduplicate")
async def deduplicate_patterns_endpoint(db: DbSession, user: AuthUser):
    """Scan and merge duplicate patterns and playbooks for the user's tenant."""
    from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks

    result = await deduplicate_patterns_and_playbooks(db, user.tenant_id)
    await db.commit()
    return {"status": "success", "data": result}
```

```python
                else:
                    await db.execute(
                        delete(PlaybookEvidenceLink).where(
                            PlaybookEvidenceLink.playbook_version_id == v.id
                        )
                    )
                    await db.execute(
                        delete(PlaybookVersion).where(PlaybookVersion.id == v.id)
                    )

            await db.execute(
                delete(Playbook).where(Playbook.id == dup_pb.id)
            )
```

```tsx
const dedupMutation = useMutation({
  mutationFn: () => api.post<DeduplicateResult>("/patterns/deduplicate", {}),
```

```tsx
<Button
  variant="outline"
  size="sm"
  className="gap-2 text-xs border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10"
  onClick={() => dedupMutation.mutate()}
  disabled={dedupMutation.isPending}
>
```

Flawed logic: this operation deletes and merges knowledge artifacts, but the
backend requires no administrative role and the frontend exposes it to every
authenticated user. This is not only a presentation issue: the route itself is
unguarded.

Concrete failure trace:

1. A non-privileged authenticated tenant user opens the patterns page.
2. The user clicks `Clean & Deduplicate`.
3. The backend accepts the request and invokes the destructive service.
4. Same-version duplicates and duplicate playbooks can be deleted.

Resulting fault: unauthorized tenant data deletion or destructive merge.

Corrected logic:

```diff
 async def deduplicate_patterns_endpoint(db: DbSession, user: AuthUser):
+    user.require_role("knowledge_manager")
```

The frontend should apply the same visibility rule, but frontend gating is not a
replacement for the backend guard.

#### M3: Pattern links allow cross-tenant episode ingestion

Locations: `backend/src/contextedge/api/v1/patterns.py:216-235`,
`backend/src/contextedge/schemas/review.py:174-178`, and
`backend/src/contextedge/api/v1/playbooks.py:680-685`

Original code:

```python
    user.require_role("knowledge_manager")
    pattern = (
        await db.execute(
            select(Pattern).where(Pattern.id == pattern_id, Pattern.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")
    if body.evidence_id is None and body.episode_id is None:
        raise HTTPException(status_code=400, detail="episode_id or evidence_id is required")

    link = PatternEvidenceLink(
        pattern_id=pattern_id,
        episode_id=body.episode_id,
        evidence_id=body.evidence_id,
        link_type=body.link_type,
        weight=body.weight,
    )
```

```python
class PatternEvidenceLinkCreate(BaseModel):
    episode_id: UUID | None = None
    evidence_id: UUID | None = None
    link_type: str = Field(..., min_length=1, max_length=50)
    weight: float = Field(1.0, ge=0.0)
```

```python
episode_ids = [link.episode_id for link in pattern.evidence_links if link.episode_id]
if not episode_ids:
    raise HTTPException(status_code=400, detail="Pattern has no associated episodes to analyze")

res = await db.execute(select(Episode).where(Episode.id.in_(episode_ids)))
episodes = res.scalars().all()
```

Flawed logic: the route checks the pattern tenant but not the linked entity's
existence, tenant, or domain. It also accepts both identifiers at once. Manual
generation then loads linked episodes without a tenant predicate.

Concrete failure trace:

1. Tenant A obtains a tenant B episode UUID.
2. A knowledge manager links it to an A-owned pattern.
3. The global episode foreign key accepts B's episode.
4. `/playbooks/generate` loads it without `Episode.tenant_id`.
5. B's episode title, root cause, and outcome enter A's LLM request and generated
   artifact.

Resulting fault: cross-tenant content exposure and lineage contamination.

Corrected logic: enforce exactly one of `episode_id`/`evidence_id`, query that
entity with `tenant_id == user.tenant_id`, validate compatible domain membership,
and retain the tenant predicate in the generation query as defense in depth.

#### M4: Runtime feedback can poison another tenant's drift state

Locations: `backend/src/contextedge/api/v1/runtime.py:357-368` and
`backend/src/contextedge/services/drift_service.py:39-47`

Original code:

```python
async def submit_feedback(body: FeedbackSubmission, db: DbSession, user: AuthUser):
    """Submit structured feedback on a runtime match result."""
    feedback = RetrievalFeedback(
        tenant_id=user.tenant_id,
        match_id=body.match_id,
        playbook_id=body.playbook_id,
        feedback_type=body.feedback_type,
        details=body.details,
        submitted_by=user.user_id,
    )
    db.add(feedback)
    await db.flush()
```

```python
negative_feedback = await db.execute(
    select(func.count()).where(
        RetrievalFeedback.playbook_id == pb.id,
        RetrievalFeedback.feedback_type.in_(
            ["wrong_match", "step_ineffective", "expired_workaround"]
        ),
        RetrievalFeedback.created_at >= now - timedelta(days=30),
    )
)
```

Flawed logic: submission establishes neither playbook ownership nor match
membership. Drift counting then omits the feedback tenant.

Concrete failure trace:

1. Tenant A submits three `wrong_match` rows using tenant B's playbook UUID.
2. The rows persist under A's tenant because `playbook_id` is a bare UUID.
3. B's next drift scan counts every row matching B's playbook UUID.
4. B receives a false high-negative-feedback alert.

Resulting fault: cross-tenant integrity violation and false drift state.

Corrected logic: validate the playbook tenant and, when a match ID is supplied,
that the cached/stored match belongs to the tenant and contained the playbook.
Also add `RetrievalFeedback.tenant_id == pb.tenant_id` to drift counting.

#### M5: Full-text evidence search silently drops filters

Locations: `backend/src/contextedge/api/v1/evidence.py:29-59` and
`backend/src/contextedge/search/pg_fts.py:12-22, 64-81`

Original code:

```python
async def search_evidence(
    db: DbSession,
    user: AuthUser,
    query: str | None = None,
    source_id: UUID | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    source_type: str | None = None,
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
```

```python
        fts_results = await search_evidence_fts(
            db,
            user.tenant_id,
            query.strip(),
            limit=limit,
            exclude_policy_ids=excluded_policy_ids,
            relevance_state=relevance_state,
            evidence_type=evidence_type,
            source_type=source_type,
        )
```

```python
async def search_evidence_fts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 50,
    *,
    exclude_policy_ids: list[uuid.UUID] | None = None,
    relevance_state: str | None = None,
    evidence_type: str | None = None,
    source_type: str | None = None,
) -> list[tuple]:
```

```python
    stmt = (
        select(EvidenceItem, rank.label("rank"))
        .where(
            *base_filters,
            or_(fts_match, raw_number_match, title_match),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
```

Flawed logic: `source_id`, `domain_id`, and `offset` are valid route parameters
but disappear whenever a nonblank query selects the FTS branch.

Concrete failure trace:

1. Request `?query=vpn&domain_id=D&source_id=S&offset=50`.
2. The route calls FTS without any of those three values.
3. The FTS statement searches all tenant domains/sources and has no offset.
4. Page two repeats page one and facets do not constrain results.

Resulting fault: silently incorrect filtering and pagination.

Corrected logic: extend `search_evidence_fts` with `source_id`, `domain_id`, and
`offset`, add their predicates, and apply `.offset(offset)`.

Whether the human FTS endpoint must also exclude legal-hold/pending-redaction
records remains **unverified**, not confirmed. Vector search treats them as content
fences, but the ordinary human list also permits them; the explicit existing
invariant only covers content sent to LLMs.

#### M6: Unknown risk tiers fail open as medium

Locations: `backend/src/contextedge/schemas/playbook.py:210-222` and
`backend/src/contextedge/search/risk_policy.py:12-20`

Original code:

```python
class PlaybookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    domain_id: UUID | None = None
    risk_tier: str = "medium"
    automation_mode: str = "suggest_only"
    pattern_id: UUID | None = None
    approval_policy_id: UUID | None = None

    @field_validator("automation_mode")
    @classmethod
    def _check_automation_mode(cls, value: str) -> str:
        return _validate_automation_mode(value)
```

```python
def playbook_risk_rank(tier: str | None) -> int:
    return RISK_RANK.get((tier or "medium").lower().strip(), 2)


def risk_within_cap(tier: str | None, max_tier: str | None) -> bool:
    """If max_tier is None, all tiers allowed; otherwise tier must be at or below the cap."""
    if max_tier is None:
        return True
    return playbook_risk_rank(tier) <= playbook_risk_rank(max_tier)
```

Flawed logic: write schemas validate `automation_mode` but not `risk_tier`.
Unknown persisted values are then assigned rank 2, which is medium rather than a
fail-closed rank.

Concrete failure trace:

1. Create `PlaybookCreate(title="x", risk_tier="catastrophic")`.
2. Schema validation accepts the string.
3. `playbook_risk_rank("catastrophic")` returns `2`.
4. `risk_within_cap("catastrophic", "medium")` returns `True`.

Resulting fault: malformed or newly introduced risk labels bypass a medium risk
cap.

Corrected logic:

```diff
+    @field_validator("risk_tier")
+    @classmethod
+    def _check_risk_tier(cls, value: str) -> str:
+        normalized = value.lower().strip()
+        if normalized not in RISK_RANK:
+            raise ValueError("invalid risk tier")
+        return normalized
```

Unknown already-persisted values should fail closed during retrieval instead of
being treated as medium.

#### M7: The correlation hard cap is not a hard cap

Locations: `backend/src/contextedge/services/correlation_suggestion_service.py:
303-322, 383-411` and `:101-119`

Original code:

```python
pending_count = (
    await db.execute(
        select(func.count(CorrelationSuggestion.id)).where(
            CorrelationSuggestion.tenant_id == tenant_id,
            CorrelationSuggestion.status == "pending",
        )
    )
).scalar_one()
if pending_count >= SUGGESTION_QUEUE_CAP:
    counts["queue_capped"] = True
    logger.info(
        "correlation_suggestions.queue_capped",
        tenant_id=str(tenant_id),
        pending=pending_count,
    )
    return counts
```

```python
ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
for other_id, similarity in ranked:
    if counts["suggested"] >= MAX_SUGGESTIONS_PER_RUN:
        break
```

Flawed logic: the guard checks only whether the queue is already at 500. It does
not restrict the current run to the remaining slots and is not atomic with the
inserts.

Concrete failure trace:

1. The tenant has 499 pending suggestions.
2. `499 >= 500` is false.
3. The run writes up to `MAX_SUGGESTIONS_PER_RUN == 5` rows.
4. The queue finishes at 504 without any concurrency. Concurrent workers can
   exceed it further.

Resulting fault: the promised hard review-backlog bound is violated.

The learned-review input is also an unordered subset:

```python
rows = (
    await db.execute(
        select(
            CorrelationSuggestion.status,
            CorrelationSuggestion.corroborators,
            src_low.source_type,
            src_high.source_type,
        )
        .join(ev_low, ev_low.id == CorrelationSuggestion.evidence_id_low)
        .join(ev_high, ev_high.id == CorrelationSuggestion.evidence_id_high)
        .outerjoin(src_low, src_low.id == ev_low.source_id)
        .outerjoin(src_high, src_high.id == ev_high.source_id)
        .where(
            CorrelationSuggestion.tenant_id == tenant_id,
            CorrelationSuggestion.status.in_(("accepted", "rejected")),
        )
        .limit(5000)
    )
).all()
```

Once more than 5,000 decided rows exist, database row order determines the
sample and therefore the learned acceptance floor.

Corrected logic: atomically reserve `SUGGESTION_QUEUE_CAP - pending_count` slots
and cap the loop to that value. Use a tenant-level lock or database-enforced
reservation for concurrent workers. Define an ordered recent window or compute
aggregates in SQL instead of applying `.limit(5000)` to an unordered row set.

#### M8: Pattern discovery silently accepts a partial episode set

Location: `backend/src/contextedge/api/v1/patterns.py:319-330`

Original code:

```python
res = await db.execute(
    select(Episode)
    .where(
        Episode.id.in_(body.episode_ids),
        Episode.tenant_id == user.tenant_id
    )
    .options(selectinload(Episode.steps))
)
episodes = res.scalars().all()
if not episodes:
    raise HTTPException(status_code=400, detail="No episodes found to analyze")
```

Flawed logic: only the all-missing case is rejected. It does not require every
requested unique UUID to resolve within the tenant.

Concrete failure trace:

1. Submit `[own_episode_id, missing_or_foreign_episode_id]`.
2. The query returns only `own_episode_id`.
3. `if not episodes` is false.
4. Pattern synthesis proceeds and reports success for a different input set than
   the caller requested.

Resulting fault: silent partial execution and false lineage.

Corrected logic:

```diff
+    requested_ids = set(body.episode_ids)
     episodes = res.scalars().all()
-    if not episodes:
-        raise HTTPException(status_code=400, detail="No episodes found to analyze")
+    found_ids = {episode.id for episode in episodes}
+    if found_ids != requested_ids:
+        raise HTTPException(status_code=400, detail="One or more episodes were not found")
```

#### M9: Playbook creation accepts cross-tenant associations

Locations: `backend/src/contextedge/api/v1/playbooks.py:206-224` and
`backend/src/contextedge/models/playbook.py:63-74, 115-119`

Original code:

```python
async def create_playbook(body: PlaybookCreate, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    await assert_policy_assignment(db, user.tenant_id, body.approval_policy_id, "approval")
    stable_key = f"pb-{uuid_mod.uuid4().hex[:12]}"
    playbook = Playbook(
        tenant_id=user.tenant_id,
        domain_id=body.domain_id,
        stable_key=stable_key,
        title=body.title,
        description=body.description,
        risk_tier=body.risk_tier,
        automation_mode=body.automation_mode,
        approval_policy_id=body.approval_policy_id,
        owner_user_id=user.user_id,
        pattern_id=body.pattern_id,
    )
```

```python
domain_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("domains.id"),
    nullable=True,
)
```

```python
pattern_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("patterns.id"),
    nullable=True,
)
```

Flawed logic: approval policy ownership is validated, but the supplied domain
and pattern are copied without tenant checks. The database foreign keys establish
global existence only.

Concrete failure trace:

1. Tenant A supplies a tenant B domain or pattern UUID.
2. Both global foreign keys resolve.
3. An A-owned playbook is persisted with a B-owned association.

Resulting fault: cross-tenant referential corruption and incorrect domain/
provenance filtering.

Corrected logic: query every supplied association with both its ID and
`tenant_id == user.tenant_id`; when both domain and pattern are supplied, require
their domains to agree.

#### M10: Specific knowledge provenance loses to generic evidence provenance

Location: `backend/src/contextedge/services/playbook_service.py:152-164, 180-196`

Original code:

```python
for raw_id in evidence_ids[:MAX_EVIDENCE_LINKS]:
    parsed = _coerce_uuid(raw_id)
    if parsed is None or ("e", str(parsed)) in seen:
        continue
    seen.add(("e", str(parsed)))
    db.add(
        PlaybookEvidenceLink(
            playbook_version_id=version.id,
            evidence_id=parsed,
            link_type=EVIDENCE_LINK_TYPE,
        )
    )
```

```python
for raw_id in knowledge_ids[:MAX_EVIDENCE_LINKS]:
    parsed = _coerce_uuid(raw_id)
    # Deduped against the evidence namespace: a KB article is an
    # EvidenceItem, so the same id could arrive on both lists. When
    # it does, the knowledge link is the more specific claim and the
    # first write wins.
    if parsed is None or ("e", str(parsed)) in seen:
        continue
    seen.add(("e", str(parsed)))
    db.add(
        PlaybookEvidenceLink(
            playbook_version_id=version.id,
            evidence_id=parsed,
            link_type=KNOWLEDGE_LINK_TYPE,
        )
    )
```

Flawed logic: the comment says the knowledge link is the more specific claim,
but evidence is processed first. A shared ID enters `seen` as generic evidence,
so the later knowledge row is skipped.

Concrete failure trace:

1. `evidence_ids` and `knowledge_ids` contain the same KB evidence UUID.
2. The evidence loop writes `derived_from_evidence` and marks the ID seen.
3. The knowledge loop skips it.
4. Queries for `based_on_kb` cannot find this normative dependency.

Resulting fault: silent loss of specific provenance and incomplete knowledge-
drift impact analysis.

Corrected logic: process knowledge IDs before generic evidence IDs, or upgrade an
existing generic row when a later knowledge occurrence is encountered. Extend the
overlap test to assert the surviving link type, not only row count.

#### M11: The frontend changes an explicit zero weight to one

Location: `frontend/src/app/(dashboard)/patterns/[id]/page.tsx:38-44`

Original code:

```tsx
mutationFn: () =>
  api.post(`/patterns/${patternId}/evidence-links`, {
    evidence_id: evidenceId.trim() || undefined,
    link_type: linkType,
    weight: parseFloat(weight) || 1.0,
  }),
```

Flawed logic: JavaScript treats numeric zero as falsy. The backend explicitly
permits `weight >= 0.0`, but `parseFloat("0") || 1.0` evaluates to `1.0`.

Concrete failure trace:

1. Reviewer enters weight `0` to neutralize a link.
2. `parseFloat("0")` returns numeric zero.
3. `0 || 1.0` returns `1.0`.
4. The backend persists a full-strength link.

Resulting fault: exact-boundary mutation and incorrect graph weighting.

Corrected logic:

```diff
-    weight: parseFloat(weight) || 1.0,
+    weight: Number.isFinite(Number.parseFloat(weight))
+      ? Number.parseFloat(weight)
+      : 1.0,
```

#### M12: Old prompt variants can record knowledge grounding never shown to the model

Locations: `backend/src/contextedge/ai/generators/playbook_generator.py:68-75`
and `backend/src/contextedge/workers/pattern_tasks.py:604-635`

Original code:

```python
# Older prompt versions have no knowledge slot; a tenant pinned to v1
# or v2 via variant routing must keep working rather than raising on
# an unexpected format key.
if "{knowledge_sources}" in prompt.user_template:
    format_kwargs["knowledge_sources"] = knowledge_text

user = prompt.format_user(**format_kwargs)
ref_map = _build_ref_map(knowledge_sources or [], episode_summaries)
```

```python
"evidence_refs": {
    "evidence_ids": evidence_ref_ids,
    "episode_ids": [str(eid) for eid in ep_ids],
    "pattern_id": str(pattern.id),
    # Knowledge is recorded separately from the episode
    # evidence it was generated alongside. It grounds the
    # playbook normatively, not empirically, and a reviewer
    # asking "which SOP does this implement" needs that
    # distinction preserved rather than flattened into one
    # evidence list.
    "knowledge_ids": [str(k.evidence_id) for k in knowledge],
    # The applicability verdict as it stood when this version
    # was generated. Persisted rather than recomputed on
    # read, for two reasons: the estate moves (an article
    # that matched production's release stops matching after
    # an upgrade), and a reviewer auditing THIS version needs
    # what the generator was actually told, not what the
    # comparison would say today.
```

Flawed logic: v1/v2 omit the knowledge block, but the citation allowlist and
persisted version provenance still include retrieved knowledge. The comments
state that the persisted data describes what the generator was told, which is
false on this configured branch.

Concrete failure trace (configuration-dependent):

1. Tenant prompt routing pins `playbook` to v1 or v2.
2. Knowledge retrieval returns approved documents.
3. The generator omits those documents because the selected prompt has no
   `{knowledge_sources}` slot.
4. The worker still persists their IDs as normative grounding.

Resulting fault: false provenance. The branch is confirmed; operational
reachability depends on at least one tenant being pinned to v1/v2.

Corrected logic: explicitly return which source classes the selected prompt
received. Build citation maps and persist normative provenance only for sources
actually supplied to that prompt.

#### M13: Future validation dates inflate freshness above one

Location: `backend/src/contextedge/search/hybrid_ranker.py:382-389`

Original code:

```python
def _compute_freshness(playbook: Playbook, now: datetime) -> float:
    """Compute freshness score based on last validation and expiry."""
    if playbook.expiry_at and playbook.expiry_at < now:
        return 0.0
    if playbook.last_validated_at:
        days_since = (now - playbook.last_validated_at).days
        return max(0.0, 1.0 - (days_since / 180))
    return 0.5
```

Flawed logic: only the lower bound is clamped. A future timestamp makes
`days_since` negative and the score greater than one.

Concrete failure trace:

1. `last_validated_at = now + 30 days` because of clock skew, manual data, or a
   bad migration.
2. `days_since == -30`.
3. Freshness becomes `1 - (-30 / 180) == 1.1667`.
4. The playbook receives more score than the freshness signal's designed
   maximum.

Resulting fault: invalid score aggregation and ranking inflation.

Corrected logic:

```diff
-        return max(0.0, 1.0 - (days_since / 180))
+        return min(1.0, max(0.0, 1.0 - (days_since / 180)))
```

Write paths should also reject materially future validation timestamps.

#### M14: Duplicate playbooks produce nondeterministic pattern links

Location: `backend/src/contextedge/api/v1/patterns.py:64-70, 106-112`

Original code:

```python
pb_result = await db.execute(
    select(Playbook.id, Playbook.pattern_id, Playbook.updated_at).where(
        Playbook.tenant_id == user.tenant_id,
        Playbook.pattern_id.in_(pat_ids),
    )
)
pb_map = {row[1]: (row[0], row[2]) for row in pb_result.all()}
```

```python
pb_result = await db.execute(
    select(Playbook.id, Playbook.updated_at).where(
        Playbook.tenant_id == user.tenant_id,
        Playbook.pattern_id == pattern.id,
    ).limit(1)
)
pb_row = pb_result.first()
```

Flawed logic: existing Issue #12 establishes that multiple playbooks per pattern
are reachable. Neither query defines which duplicate is canonical. The list's
dict comprehension is last-row-wins over an unordered result, while detail takes
an unordered first row.

Concrete failure trace:

1. A pattern has two playbooks after the manual/worker generation race.
2. The database returns them in an unspecified order.
3. The list and detail endpoints can select different IDs or change selection
   across executions.
4. The UI links to an arbitrary duplicate and computes review status against its
   timestamp.

Resulting fault: nondeterministic navigation and stale/generated status.

Corrected logic: prevent duplicate creation transactionally. Until that invariant
exists, select one deterministic canonical playbook using explicit lifecycle,
published-version, `updated_at`, and ID ordering in both endpoints.

#### M15: Rollback skills are not tenant-validated and are dereferenced globally

Locations: `backend/src/contextedge/services/skill_registry_service.py:184-235`,
`backend/src/contextedge/models/skill.py:191-193`, and
`backend/src/contextedge/services/remediation_service.py:48-61`

Original code:

```python
async def register_skill(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    skill_key: str,
    name: str,
    interface_type: str,
    safety_class: str,
    version: str = "1.0.0",
    description: str | None = None,
    action_type: str | None = None,
    endpoint_or_tool: str | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    reversible: bool = False,
    rollback_skill_id: uuid.UUID | None = None,
    allowed_principal_roles: list[str] | None = None,
    execution_contract_id: uuid.UUID | None = None,
    status: str = "draft",
    created_by: uuid.UUID | None = None,
) -> Skill:
    """Register a skill, or raise if it may not be registered as described."""
    contract: ExecutionContract | None = None
    if execution_contract_id is not None:
        contract = await db.get(ExecutionContract, execution_contract_id)
        if contract is None or contract.tenant_id != tenant_id:
            raise SkillRegistryError(
                f"execution contract {execution_contract_id} not found for this tenant"
            )

    skill = Skill(
        tenant_id=tenant_id,
        skill_key=skill_key.strip(),
        version=version.strip(),
        name=name.strip(),
        description=description,
        action_type=action_type,
        interface_type=interface_type,
        endpoint_or_tool=endpoint_or_tool,
        input_schema=input_schema,
        output_schema=output_schema,
        reversible=reversible,
        rollback_skill_id=rollback_skill_id,
```

```python
rollback_skill_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
)
```

```python
        try:
            skill = await resolve_skill(db, tenant_id, tool_ref)
        except UnresolvedSkillReference:
            skill = None
        if skill is not None and skill.rollback_skill_id is not None:
            rollback_skill = await db.get(Skill, skill.rollback_skill_id)
            if rollback_skill is not None:
                return {
                    "step_index": step.step_index,
                    "reverses": step.step_title,
                    "method": "skill",
                    "tool_ref": f"{rollback_skill.skill_key}@{rollback_skill.version}",
                    "safety_class": rollback_skill.safety_class,
                }
```

Flawed logic: registration correctly validates `execution_contract_id` against
the tenant but copies `rollback_skill_id` without looking it up. The database
foreign key proves only global existence. Rollback planning later uses `db.get`,
again without tenant scoping, and copies the foreign skill's key, version, and
safety class into a tenant-local rollback plan.

Concrete failure trace:

1. Tenant A's administrator registers a reversible skill and supplies tenant B's
   skill UUID as `rollback_skill_id`.
2. No rollback-skill lookup occurs; the global foreign key accepts the UUID.
3. An A execution later requires a rollback plan.
4. The forward skill resolves under A, but `db.get(Skill, rollback_skill_id)`
   loads B's skill globally.
5. B's tool reference and safety class are persisted in A's rollback plan. If A
   has a same-key/version skill, later authoring can also resolve the plan text to
   a different local operation than the foreign skill used to derive it.

Resulting fault: cross-tenant metadata exposure, invalid rollback lineage, and a
potentially incorrect remediation plan.

The focused reproduction printed:

```text
foreign_rollback_id_persisted= True
registration_lookup_count= 0
foreign_skill_dereferenced= True
foreign_tool_ref_exposed= foreign_restore@1.0.0
foreign_safety_class_exposed= destructive
```

Corrected logic:

```diff
     contract: ExecutionContract | None = None
+    rollback_skill: Skill | None = None
     if execution_contract_id is not None:
         contract = await db.get(ExecutionContract, execution_contract_id)
         if contract is None or contract.tenant_id != tenant_id:
             raise SkillRegistryError(
                 f"execution contract {execution_contract_id} not found for this tenant"
             )
+    if rollback_skill_id is not None:
+        rollback_skill = (
+            await db.execute(
+                select(Skill).where(
+                    Skill.id == rollback_skill_id,
+                    Skill.tenant_id == tenant_id,
+                )
+            )
+        ).scalar_one_or_none()
+        if rollback_skill is None:
+            raise SkillRegistryError(
+                f"rollback skill {rollback_skill_id} not found for this tenant"
+            )
```

Rollback planning must independently retain the tenant predicate rather than
trusting the stored association:

```diff
-            rollback_skill = await db.get(Skill, skill.rollback_skill_id)
+            rollback_skill = (
+                await db.execute(
+                    select(Skill).where(
+                        Skill.id == skill.rollback_skill_id,
+                        Skill.tenant_id == tenant_id,
+                    )
+                )
+            ).scalar_one_or_none()
```

### 9.3 Corrected Backend Area-by-Area Verdict

| Area | Verdict | Checks and limitations |
|---|---|---|
| Vector primitives and chunk rollup | **Confirmed within unit boundaries** | Empty/single inputs, dimensions, limits, and distance ordering were covered. Existing zero-vector, batch-cardinality, and tie findings remain excluded. |
| Embedding provider contract | **Unverified end-to-end** | Live provider cardinality, malformed remote responses, rate limiting, and fallback behavior were not exercised. |
| Semantic vector retrieval | **Confirmed within unit boundaries** | Tenant, domain, access-policy, and published-version predicates were checked. Production ANN recall remains unverified. |
| Hybrid ranking and FTS | **Not correct** | Existing score findings remain; M5 drops search filters, M6 fails open on unknown risk, and M13 exceeds the freshness bound. Normal tenant/lifecycle filters remain correct. |
| Relevance and message classifiers | **Partially confirmed** | Finite schema-compatible paths are covered. Existing enum, boolean, and NaN defects remain; live model compliance is unverified. |
| Extraction and normalization workers | **Unverified end-to-end** | Connector diversity, retries, rollback, and concurrent normalization were not executed together. |
| Pattern construction and clustering | **Not correct** | Existing identity/concurrency defects remain, with M2, M3, M8, and M14 adding authorization, tenant, partial-input, and ordering failures. |
| Playbook generation and grounding | **Not correct** | Normal current-prompt citation cleanup and empty-step rejection work, but M3, M6, and M12 remain. Live model behavior is unverified. |
| Playbook lifecycle and versioning | **Not correct** | Transition guards, version collision handling, and ordinary published selection passed, but M1 is a P0 tenant-boundary failure. |
| Knowledge retrieval and provenance | **Not correct** | Lifecycle/applicability tests pass; R3, M10, and M12 prevent confirmation. |
| Correlation suggestions | **Not correct** | Pair identity and ordinary corroborator/threshold gates work, but M7 disproves the strict-cap assurance and shows nondeterministic learned statistics at scale. |
| Similar-episode handling | **Partially confirmed** | Existing occurrence and similarity-dedup tests passed. The dependency-blocked API path and production behavior remain unverified. |
| Runtime feedback and matching | **Not correct** | Tenant-scoped feedback reads work, but M4 permits cross-tenant drift influence; R1/R2 remain. |
| Execution and approval lifecycle | **Unverified** | Static guards exist, but missing `rfc8785` blocked complete tests. Concurrent decisions, retries, artifact signing, and full transitions remain unconfirmed. |
| Skill registry and rollback planning | **Not correct** | Contract ownership and ordinary skill resolution are tenant-scoped, but M15 accepts and globally dereferences a foreign rollback skill. |
| Agent graph semantic seeds | **Unverified end-to-end** | Static tenant/domain filters were inspected. Live hydration and partial-failure behavior were not executed. |
| Backend API routes | **Not correct** | M1-M5, M8, and M9 establish route-level tenant, authorization, filter, and partial-execution defects. |

### 9.4 Corrected Frontend Area-by-Area Verdict

| Area | Verdict | Checks and limitations |
|---|---|---|
| Patterns list and generation | **Not correct** | Existing error/empty conflation remains; M2 exposes destructive deduplication, M11 mutates zero weight, and M14 makes the linked playbook nondeterministic. Other privileged actions are displayed without role gating, although their guarded backend routes reject unauthorized calls. |
| Playbooks list | **Not correct** | Existing R4/R6 and confidence/version defects remain. |
| Playbook detail and versions | **Not correct** | Step/citation rendering works for tested data, but the page relies on M1's unsafe version endpoint and still hides version-query failures. |
| Runtime sandbox | **Not correct** | UUID, JSON-object, empty-value, and `top_k` boundaries are checked. R2/R5 remain, and M4 affects feedback integrity. |
| Suggestions | **Not correct** | Mutation pending guards and invalidation work. Query failures still render as empty queues, and backend queue behavior fails M7. |
| React Query defaults | **Correct as configured, not behaviorally sufficient** | `staleTime` and retry settings are active, but do not repair page-level state/error defects. |
| Execution approval polling | **Unverified** | Polling and pending-button guards exist; multi-reviewer conflicts and API conflict handling were not exercised. |
| Authenticated browser behavior | **Unverified** | No multi-role browser E2E was run, so route visibility, navigation after authorization failures, and concurrent UI state cannot be confirmed. |

### 9.5 Self-Check and Final Assessment

All fifteen findings were re-read against the current source and retained. One
initial candidate was removed from the confirmed list: "FTS exposes legal-hold
evidence." The FTS query clearly lacks the vector content fences, but the existing
explicit invariant only forbids legal-hold content from reaching LLMs, while this
route serves authenticated human reviewers and the non-query list also permits
held records. That behavior remains an **unverified product-policy question**, not
a confirmed security bug.

The corrected conclusion is that this audit is substantial but still cannot
certify the scoped pipelines as free of missed logic gaps. The immediate priority
is M1-M3: cross-tenant version disclosure, unauthorized destructive
deduplication, and foreign-episode generation. M4-M12 and M14-M15 require remediation before
the affected reconciliation, filtering, risk, provenance, and frontend paths can
be described as correct. M13 is a latent score-bound defect. Execution, live
providers, concurrent Celery behavior, production ANN, Redis outages, and
authenticated browser flows remain explicitly unverified.

## 10. Independent Validation of the Final Completeness Pass (2026-08-17)

### 10.0 Validation Scope

The independent review supplied after Section 9 was checked against the same
repository, branch, and commit. Every M1-M14 location was reopened. The described
branch behavior and proposed corrections match the current code. M12 is upgraded
from suspected to **confirmed, configuration-dependent**: per-tenant prompt
variant routing is a supported code path, although whether any current deployed
tenant is pinned to v1/v2 was not established.

The independent review also expanded beyond Section 9's original file list into
the skill registry, rollback planning, source deletion, and the evidence explorer.
That expansion produced one additional confirmed issue, M15. Two other observations
were not new confirmed findings, as detailed below.

### 10.1 Independent Validation Matrix

| Item | Final verdict | Confidence | Independent result |
|---|---|---|---|
| M1 | **CONFIRMED** | High | Version-list quote matches; no tenant/RLS substitute was found; the foreign-version trace reaches the response. |
| M2 | **CONFIRMED** | High | Endpoint requires authentication but no privileged role; the invoked service performs hard deletes. |
| M3 | **CONFIRMED** | High | Link write and later episode load both omit target-entity tenant validation; foreign episode fields enter generation. |
| M4 | **CONFIRMED** | High | Arbitrary playbook IDs persist in feedback and tenant is omitted from drift aggregation. |
| M5 | **CONFIRMED** | High | `source_id`, `domain_id`, and `offset` disappear only on the nonblank-query branch. |
| M6 | **CONFIRMED** | High | Unknown schema value is accepted and maps to medium rank. |
| M7 | **CONFIRMED** | High | A queue of 499 can accept five rows sequentially; concurrent overshoot remains an additional realistic path. |
| M8 | **CONFIRMED** | High | Only the zero-row case is rejected; a nonempty subset proceeds. |
| M9 | **CONFIRMED** | High | Domain and pattern foreign keys prove global existence, not tenant ownership. |
| M10 | **CONFIRMED** | High | Direct reproduction retained `derived_from_evidence` instead of `based_on_kb`. |
| M11 | **CONFIRMED** | High | Direct JavaScript evaluation of `parseFloat("0") || 1.0` produced `1`. |
| M12 | **CONFIRMED (configuration-dependent)** | Medium | The supported v1/v2 variant branch omits knowledge text while retaining its validation/persistence provenance. Trigger requires a tenant pin to v1/v2. |
| M13 | **CONFIRMED** | High | Direct calculation produced freshness `1.167` for a date 30 days in the future. |
| M14 | **CONFIRMED** | High | Both selection queries lack ordering; existing duplicate-playbook reachability makes the nondeterminism concrete. |
| M15 | **CONFIRMED** | High | Direct reproduction persisted a foreign rollback UUID, dereferenced the foreign skill globally, and exposed its key/version/safety class. |

### 10.2 Corrections to the Independent Review's Wording

The following naming/wording differences do not invalidate its findings, but are
corrected here so the final report stays traceable to actual symbols:

- M2 is not an unauthenticated endpoint. `AuthUser` is required. It is an
  **authenticated-but-unauthorized-role** destructive endpoint.
- The M3 vulnerable API function is `generate_playbook`; it calls
  `generate_playbook_candidate` after the untenant-scoped episode query.
- M4's containing service function is `check_playbook_drift`, not
  `scan_for_drift`.
- M8's route function is `discover_pattern`, not
  `synthesize_pattern_endpoint`; `synthesize_pattern` is the extractor it calls.
- M10's materialization function is `_materialize_evidence_links`, not
  `record_playbook_evidence_links`.

### 10.3 Source-Deletion Observation: Not Confirmed as a New Bug

Location: `backend/src/contextedge/api/v1/sources.py:569-590, 612-615`

Original code:

```python
@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: UUID, db: DbSession, user: AuthUser):
    """Permanently delete a source and all its associated evidence/logs."""
    user.require_role("domain_admin")

    source = (
        await db.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    from sqlalchemy import delete, or_

    from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
    from contextedge.models.source import SourceObject, SyncRun

    # 1. Resolve Evidence IDs to delete dependencies
    evidence_ids_q = await db.execute(
        select(EvidenceItem.id).where(EvidenceItem.source_id == source_id)
    )
```

```python
    # 4. Delete Evidence Items
    await db.execute(
        delete(EvidenceItem).where(EvidenceItem.source_id == source_id)
    )
```

Validation result: **NOT CONFIRMED as a reachable tenant-isolation bug**. The
parent `Source` is first validated against the caller tenant, and `Source.id` is a
globally unique primary key. Therefore `source_id == source_id` identifies that
exact already-authorized source. A foreign evidence row would be deleted only if
some earlier path had already violated the separate invariant that an evidence
item's tenant must match its source tenant. The supplied review did not identify
such a writer in this trace.

Adding `EvidenceItem.tenant_id == user.tenant_id` would be reasonable defense in
depth and would preserve corrupt foreign rows for investigation, but the absent
redundant predicate alone is not sufficient evidence for another confirmed bug.

### 10.4 Evidence-Explorer Observation: Confirmed Impact of M5, Not a New Gap

Location: `frontend/src/app/(dashboard)/evidence/page.tsx:206-216`

Original code:

```tsx
const { data = [], isLoading, isFetching } = useQuery<EvidenceItem[]>({
  queryKey: ["evidence", appliedQuery, evidenceTypeFilter, relevanceFilter, sourceTypeFilter, pg.page],
  queryFn: () => {
    const params: Record<string, string> = { ...pg.params };
    if (appliedQuery.trim()) params.query = appliedQuery.trim();
    if (evidenceTypeFilter !== "all") params.evidence_type = evidenceTypeFilter;
    if (relevanceFilter !== "all") params.relevance_state = relevanceFilter;
    if (sourceTypeFilter !== "all") params.source_type = sourceTypeFilter;
    return api.get("/evidence", params);
  },
});
```

Validation result: **CONFIRMED downstream manifestation of M5**, not an
independent finding. The frontend correctly sends `pg.params` and resets the page
when search/filter state changes. The backend query branch drops `offset`, so the
visible page counter can advance while page-one results repeat. Fixing M5 closes
this trace without a frontend change.

### 10.5 Final Assessment

The independent review validates M1-M14, subject to the corrected symbol names
and M12's explicit configuration trigger. It also surfaces the confirmed M15
rollback-skill tenant violation. It does **not** establish the source-delete
observation as another reachable bug, and its evidence-page observation is an
impact trace for M5 rather than a separate defect.

The final confirmed inventory from the completeness passes is therefore M1-M15:
three P0 issues, eleven P1 issues, and one P2 issue. This count does not include
the earlier numbered and residual findings in Sections 1-8. The file remains a
bounded audit, not a universal proof: live providers, Redis failure recovery,
Celery concurrency, production ANN, complete execution/approval flows, and
authenticated browser E2E remain unverified.
