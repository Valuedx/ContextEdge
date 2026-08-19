# Evaluation, drift, and retrieval feedback

## Summary

You will understand how ContextEdge measures retrieval quality via **offline evaluation runs** against gold datasets, how **drift detection** finds approved playbooks that have gone stale, outlived their expiry, or accumulated negative feedback, and how **retrieval feedback** from live callers closes the quality loop. You will also see the two offline eval toolkits that enforce the repo's measure-first discipline, and where the AI episode-review sweep lives in this same worker file.

One thing to carry in from [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md): on 2026-08-19 the episode corpus these measurements run against was **materially changed** — knowledge-derived episodes were refused at synthesis and the existing ones migrated out to `knowledge_cases`. Any accuracy number recorded before that date was measured against a different population. This page also covers a third notion of drift the same commits made *possible* but did not wire up: knowledge drift, where a documented resolution stops working while its article stays approved upstream.

## Business picture

Playbooks that were accurate six months ago may not match how incidents present today. Software versions change, workarounds expire, and field teams discover better steps. Without continuous quality checks, outdated guidance quietly degrades every recommendation the system makes.

ContextEdge measures retrieval quality, detects when playbooks go stale, and feeds real-world outcome signals back into the system — so operational knowledge stays current without manual monitoring. Four capabilities work together:

- **Offline evaluation** replays curated cases against the current retrieval pipeline to catch accuracy regressions before they reach production. If a recent change causes the wrong playbook to rank first, the evaluation run surfaces it. A second dataset kind grades episode extraction itself: are the steps the model writes actually supported by the evidence it cites?
- **Drift detection** periodically scans approved playbooks for signs of staleness — an expiry date that has passed, a long gap since the last human validation, a pattern of negative feedback from field teams, or a source pattern that kept growing after the playbook was generated. Expired playbooks are automatically removed from runtime results; the rest are flagged for review.
- **Retrieval feedback** closes the loop: when a team member reports that a recommended playbook was wrong or a step no longer works, that signal feeds directly into the next drift scan. Operators do not need to manually flag each affected playbook.
- **Measure-first evals** settle model and prompt arguments with data instead of opinion: A/B harnesses run the real generation path twice with one variable swapped, and golden regression sets catch a prompt edit that silently degraded a classifier.

A quality loop is only as honest as the things it counts. That is why the 2026-08-19 split matters here rather than only in the episode chapter: if a KB article can be reconstructed as an episode, then a document's claim is counted as an observed outcome, and every number on this page — top-1 accuracy over a corpus, an AI reviewer's approval rate, a pattern's apparent strength — silently includes evidence of something that never happened. Removing that contamination is a measurement fix as much as a modelling one, and it means older figures and newer ones are not comparable.

## Technical walkthrough

### Evaluation datasets and runs

- `models/evaluation.py` defines the tables (backend/src/contextedge/models/evaluation.py:11-39):
  - `EvaluationDataset` — a named, tenant-scoped JSONB array of `cases`, plus `name` and `description`.
  - `EvaluationRun` — one execution of a dataset; stores `config` (JSONB), `status` (`pending` → `running` → `completed` / `failed`), `results` (JSONB), `started_at`, `completed_at`.

- The HTTP surface is `api/v1/evaluations.py`, mounted at `/api/v1/evaluations` (backend/src/contextedge/api/v1/__init__.py:56). `POST /datasets` and `POST /runs` require the `knowledge_manager` role; creating a run flushes the row and immediately dispatches the `evaluation.run_evaluation` Celery task (backend/src/contextedge/api/v1/evaluations.py:86-100). `GET /runs/{run_id}` polls status and results.

- `evaluation_service.execute_evaluation_run` drives a run (backend/src/contextedge/services/evaluation_service.py:26-54):
  1. Loads the `EvaluationRun` and its `EvaluationDataset`; validates tenant ownership.
  2. Sets `status="running"`, stamps `started_at`, and flushes.
  3. Calls `_execute_evaluation_core` (evaluation_service.py:100-192). Retrieval cases assemble a query string from `symptoms` + `entities` + `context`, call `rank_playbooks` — **the same hybrid ranker runtime uses** (backend/src/contextedge/search/hybrid_ranker.py:213), though without the caller-scoped filters the HTTP match adds on top (see Design decisions) — with `top_k=5`, and record whether the top result's `stable_key` matches `expected_playbook_stable_key` (`expected_stable_key` is accepted as an alias, evaluation_service.py:132).
  4. Writes `results = {case_count, top1_accuracy, cases}` where each retrieval case carries `expected_stable_key`, `top_stable_key`, `top1_hit`, and the scored `top_k` list (evaluation_service.py:146-161), then sets `status="completed"`. Read the two numbers separately: `case_count` counts both kinds of case, but `top1_accuracy` divides only by the retrieval cases, so a citation-only dataset reports `0.0` — which means "no retrieval cases ran", not "everything missed" (evaluation_service.py:156-159).
  5. On any exception, sets `status="failed"` and stores the error string in `results` (evaluation_service.py:48-54).

- **Episode-citation cases (the C5 dataset kind).** A case with `"kind": "episode_citation"` is not a retrieval replay — it runs a real LLM episode reconstruction over the case's `evidence_items` (optionally pinned to a specific prompt version via `run.config["episode_prompt_version"]`) and grades the steps against per-step gold citations: `unsupported_step_rate` (steps citing nothing) and `wrong_attribution_rate` (steps whose citations are not a subset of that step's gold set) (`_run_citation_case`, evaluation_service.py:57-97). Because each case is a paid reconstruction, runs cap LLM cases at `config["max_llm_cases"]` (default 100); truncation is reported in `results.citation.cases_skipped_by_llm_cap`, never silent (evaluation_service.py:113-124, 173-187). This is what makes episode prompt v2-vs-v3 comparisons a dataset away instead of an argument.

  **It calls the extractor, not the worker.** `_run_citation_case` imports `reconstruct_episode` from `ai/extractors/episode_extractor.py` and calls it on the case's `evidence_items` directly (evaluation_service.py:70-79). It does not go through `workers/extraction_tasks._reconstruct`, so **none of the synthesis gates apply** — not the min-cluster floor, not the debounce, not the growth gate, and not the observational-source gate added on 2026-08-19. That is the right design for what this dataset kind measures (does the model cite the evidence it was given, per step?), and it has one consequence worth stating plainly: a citation case whose `evidence_items` are all KB articles will still be reconstructed and graded here, even though the same evidence would be refused in production. A citation dataset is therefore a probe of the extractor's grounding behaviour, never evidence that a cluster would have become an episode.

- The `evaluation.run_evaluation` Celery task (backend/src/contextedge/workers/evaluation_tasks.py:14-34) wraps the service in the async runner with up to 2 retries and a 120-second delay, so evaluation jobs survive transient database or provider hiccups. It has no beat entry — runs are created on demand through the API.

### Drift detection

`services/drift_service.py` implements three functions used together:

**`list_drift_alerts`** (read-only, safe for HTTP GET; backend/src/contextedge/services/drift_service.py:13-81) scans all `approved` playbooks for a tenant and checks four signals per playbook:

- `expiry_at` at or past the current time → issue `"past_expiry"` (drift_service.py:31-32).
- `last_validated_at` more than 90 days ago → issue `"not_validated_in_N_days"`, with N the actual day count (drift_service.py:34-37). The clock resets on every approval: `transition_playbook` stamps `last_validated_at` when a playbook is approved (backend/src/contextedge/services/playbook_service.py:268).
- Three or more `RetrievalFeedback` rows of type `wrong_match`, `step_ineffective`, or `expired_workaround` in the past 30 days → issue `"high_negative_feedback_N"` (drift_service.py:39-50).
- The source pattern's `updated_at` more than 5 seconds newer than the playbook's → issue `"pattern_nodes_added_drift"` — new episodes joined the pattern after the playbook was generated, so the procedure may no longer reflect everything the pattern knows (drift_service.py:52-66).

Each alert dict carries `playbook_id`, `pattern_id`, `title`, `issues`, and `severity`. Severity is **three-valued**: `"high"` when past expiry, `"medium"` when the pattern grew after generation, otherwise `"low"` — so a feedback-only or staleness-only alert is a low-severity nudge, not an alarm (drift_service.py:68-79).

**`apply_expired_playbook_transitions`** (mutating, for batch/Celery; drift_service.py:84-101) bulk-updates playbooks from `approved` → `expired` where `expiry_at <= now`, and returns the row count. Runtime removal follows for free: `rank_playbooks` only considers `lifecycle_state == "approved"` (backend/src/contextedge/search/hybrid_ranker.py:238-241), so an `expired` playbook stops matching the moment this commits. The lifecycle map allows `expired → under_review` if a human wants to refresh and re-approve it (backend/src/contextedge/services/playbook_service.py:22-30).

**`check_playbook_drift`** combines both (drift_service.py:104-118): snapshot alerts first — so `past_expiry` items are captured *before* they transition — then run the transition, then return `{alerts, expired_transition_count, alert_count}`. This is what the `detect_drift` Celery task calls.

### Drift task and scan schedule

`workers/evaluation_tasks.py` — `evaluation.detect_drift` (backend/src/contextedge/workers/evaluation_tasks.py:37-81):
- Accepts a tenant id or the literal string `"all"` to scan every tenant in sequence.
- Calls `check_playbook_drift` per tenant and aggregates `alerts`, `alert_count`, and `expired_transition_count` across tenants.
- Retries once with a 300-second delay.
- Scheduled by beat as `detect-drift-every-6h` — every 21,600 seconds with args `("all",)` (backend/src/contextedge/workers/celery_app.py:282-286).

`evaluation.scan_contradictions_task` (evaluation_tasks.py:84-122) follows the same `"all"` fan-out pattern for the playbook-vs-KB contradiction scanner (`services/contradiction_service.scan_contradictions`, backend/src/contextedge/services/contradiction_service.py:318), aggregating `playbooks_scanned`, `kb_items_scanned`, `candidate_pairs_scanned`, `contradictions_created`, `contradictions_updated`. Beat runs it as `scan-contradictions-every-12h` (celery_app.py:287-291).

This worker file also hosts **`evaluation.ai_review_episodes`** (evaluation_tasks.py:125-358) — the hourly AI first-pass review of pending episode drafts (modes `off`/`advisory`/`auto_approve`, downgrade-only override, per-episode commits, crash-recovery re-dispatch of issue signatures, per-domain clustering dispatch after auto-approvals). It is scheduled unconditionally (`ai-review-episodes-hourly`, celery_app.py:379-383) and returns `{"status": "disabled"}` instantly while the setting is off and no override was passed (evaluation_tasks.py:171-173). The full mechanism and the auto-approve floors live in [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md).

### Knowledge drift: a third notion, designed but not running

There are now **three** distinct "drift" mechanisms in the repo and it is worth keeping them apart, because they answer different questions and only two of them run:

| Question | Mechanism | Status |
| --- | --- | --- |
| Has this approved playbook gone stale? | `drift_service.list_drift_alerts` — expiry, 90-day validation clock, negative feedback, pattern growth | Running, beat every 6 h |
| Does this playbook contradict an approved KB article? | `contradiction_service.scan_contradictions` via `evaluation.scan_contradictions_task` | Running, beat every 12 h |
| Has a *documented resolution* stopped working while its article stays approved upstream? | `pattern_evidence` rows with `support_role='contradicts_resolution'`, read back by `knowledge_case_service.pattern_support` | **Schema only — nothing writes those rows and nothing reads the result** |

The third one is what the 2026-08-19 evidence ledger was designed to make possible (backend/src/contextedge/models/pattern.py:107-110). `pattern_support` (backend/src/contextedge/services/knowledge_case_service.py:246) groups `pattern_evidence` by class, role and outcome and returns `documented` / `prescriptive` / `empirical` counts, an `empirical_success` and `empirical_failure` split, a `contradicts` count, and a derived state of `empirically_supported` / `documented_only` / `unsupported`. That is precisely the shape a reviewer needs in order to decide how much to trust a playbook: three KB articles and nineteen resolved incidents are not the same pattern, and `episode_count` alone cannot tell them apart.

None of it is live yet, and the reasons are worth being specific about rather than describing the capability as though it existed:

- **Nothing writes `contradicts_resolution`.** The only writer of `PatternEvidence` anywhere in `backend/src/contextedge` is `knowledge_case_service._record` (knowledge_case_service.py:116), and it always writes `support_role="supports_resolution"` with `evidence_class="documented"`. A search for `PatternEvidence` across the package finds no other construction site.
- **Nothing writes empirical rows on an ongoing basis either.** `create_pattern_from_episodes` and `add_episode_to_pattern` write `pattern_evidence_links` (membership) and not `pattern_evidence` (footing), so the empirical rows that exist came from a one-off backfill over the then-current episode links (1,416 of them, operator-reported — the figure is not in the repo) and do not grow as new episodes join patterns. The ledger is a snapshot dated 2026-08-20, and it will drift away from `pattern_evidence_links` from here.
- **Nothing reads `pattern_support`.** It has no HTTP route and no UI surface — the only callers in the tree are `backend/tests/test_knowledge_case_attachment.py`. So `documented_only` is a state the database can express and a reviewer cannot see.

One knock-on for the drift scanner that *does* run: `list_drift_alerts` scans **approved playbooks** only (the filter is at drift_service.py:18-24), and a documented-only pattern produces no playbook at all — confidence 0.4 sits below the 0.5 generation floor and it has zero episode links, so `generate_playbook_candidate` skips it twice over. About 55% of the patterns created by the knowledge backfill are that kind (75 seeded of 135 cases — operator-reported, like every other figure from that run; no line in the repo records it). They are therefore entirely outside the drift surface: not stale, not fresh, simply unmeasured. That is the correct behaviour for a pattern nothing has happened to, but it means "how many patterns are we monitoring for drift" and "how many patterns exist" have quietly become different numbers.

### What the measurements now run against

Two of the 2026-08-19/20 commits changed the population under the microscope, so figures either side of that date are not comparable:

- **Knowledge-derived episodes are gone.** Migration 0073 moved them into `knowledge_cases` and deleted the originals into `episodes_knowledge_migrated_backup` / `episode_steps_knowledge_migrated_backup` (backend/alembic/versions/0073_migrate_knowledge_episodes_to_cases.py:86-102, 229-234). Any retrieval or citation dataset whose expected answers reference one of those episodes will now miss, and that is a corpus change rather than a regression. A handful of episodes stayed behind as `invalidated` because they resolved to no knowledge source at all — the migration refuses to delete an episode it did not represent, and counts and prints the leftovers at run time rather than hardcoding them (0073:216-220, 244-252). The docstring's own 296-of-299 arithmetic implies three, but that pairing describes the corpus the migration was written against. However many there are, they are out of review, clustering and the agent, yet they are still live rows — so a count of "episodes" includes them while every quality signal excludes them.
- **The AI review sweep has less to do, for a good reason.** `evaluation.ai_review_episodes` selects pending drafts with `ai_review IS NULL` (evaluation_tasks.py:131). Knowledge-only clusters no longer become drafts at all, so the sweep no longer spends an LLM call per draft holding or approving reconstructions of documents. The design decision on the sweep's yield still stands unmeasured — no calibration run for that stage is recorded anywhere in the repo — and it is now doubly unmeasurable across the boundary, because the input population changed at the same time.

Also worth stating for anyone building a dataset here: this deployment has **only `zoho_desk` connected**. The evidence types present are `thread_message`, `ticket` and `kb_article`; there are zero change records, zero monitoring alerts, zero CI or service entities (all 849 entities are `topic` or `knowledge_category`) and zero topology edges ([INCIDENT_DIAGNOSIS_ROADMAP.md](./INCIDENT_DIAGNOSIS_ROADMAP.md), H4-H6). A gold set cannot exercise change-correlation or alert-driven retrieval, because there is nothing of those kinds to retrieve. The `operational_situations` tables added by migration 0074 are empty and have no writer, so there is nothing to evaluate there either — and no dataset kind grades them.

### Retrieval feedback

`models/evaluation.py` — `RetrievalFeedback` (backend/src/contextedge/models/evaluation.py:42-55):
- Written by `POST /api/v1/runtime/feedback` after a playbook match was used; the row records `match_id`, `playbook_id`, `feedback_type`, freeform `details` JSONB, and `submitted_by` (the authenticated user) (backend/src/contextedge/api/v1/runtime.py:352-369). `GET /api/v1/runtime/feedback` lists rows filterable by playbook, type, or match id (runtime.py:372-391).
- `feedback_type` is a free string at the API (`FeedbackSubmission`, backend/src/contextedge/schemas/playbook.py:317-321); only `wrong_match`, `step_ineffective`, and `expired_workaround` count toward drift's negative-feedback signal (drift_service.py:42-44). Other values are stored and listable but do not move alerts.

This means a pattern of bad runtime outcomes surfaces in the next scheduled drift pass — at most six hours later — without anyone manually flagging the playbook.

### Decision analytics — calibration and pattern mining

`workers/decision_tasks.py` registers two `evaluation.*`-named tasks (routed to the `evaluation` queue) that mine the first-class `Decision` / `DecisionOutcome` rows (see [16-decision-traces.md](./16-decision-traces.md)) for reviewer-visible quality signals:

- **`evaluation.calibrate_decision_confidence`** (backend/src/contextedge/workers/decision_tasks.py:126-216) joins decisions with non-null `confidence` to their outcomes, buckets by predicted confidence at 0.1 granularity, and computes the observed success rate per bucket. A well-calibrated extractor has bucket `0.9 → 0.9 ± 0.1` observed success; a miscalibrated one shows "high confidence, low success" buckets that deserve prompt tuning. Results persist as `decision.confidence_calibrated` operational events the admin dashboard can chart over time.
- **`evaluation.mine_decision_patterns`** (decision_tasks.py:30-123) groups **completed** decisions by `(decision_type, execution_result)` with count ≥ 3, computes a failure rate for failing groups, and emits `decision.patterns_mined` — so recurring mistakes ("restart is ineffective for network-share failures") are visible without a human eyeballing every run. Mining aggregates tenant-wide deliberately: it emits counts into operational events, not synthesized content, so the domain-scoping rules that constrain pattern mining do not apply.

Both accept the `"all"` sentinel with isolated per-tenant exception handling — one broken tenant doesn't kill the beat for the rest (decision_tasks.py:97-113, 192-206) — and both retry twice at 300 seconds. Beat schedules them daily (`calibrate-decision-confidence-daily`, `mine-decision-patterns-daily`, celery_app.py:309-318).

### Offline evals: golden regression and A/B harnesses

Two toolkits, two locations, one discipline (CLAUDE.md's measure-first rule: model-facing changes ship only with a before/after measurement).

**Golden regression** (`backend/evals/`): one hand-curated `golden.jsonl` per extractor — one case per line with `id`, inputs, `expected_classification`, optional `min_confidence`. `backend/evals/run_regression.py` is a CLI (`python -m evals.run_regression relevance`) that runs the set through the live classifier, prints per-case pass/fail plus a confusion matrix, and exits non-zero on any failure so it can gate CI (backend/evals/run_regression.py:86-116). Today there is exactly one registered runner, `relevance` (run_regression.py:81-83), and its golden set holds 5 cases — a smoke test by design, not a benchmark; the module docstring says to switch to a real eval runner past ~50 cases (run_regression.py:15-18). Ship path: run manually on model or prompt changes. The weekly beat entry is **deliberately deferred** until the customer signs off on what "regression" means — absolute accuracy bar or week-over-week delta (backend/evals/README.md:53-55; tracked in [KNOWN_GAPS.md](./KNOWN_GAPS.md) under "Scheduled jobs that need wiring").

**A/B and stability harnesses** (`backend/src/contextedge/evals/`): scripts that run the *real* generation path with one variable isolated, persist nothing, and write verdict snapshots to `datasets/`:
- `playbook_model_ab.py` — two models, same inputs, same prompt. The verdict on record (2026-08-17, snapshot `datasets/playbook_model_ab_2026-08-17.json`): gemini-3.7-flash beat 2.5-flash on grounded-step share 0.70 → 0.81 with latency halved, which is why `playbook_model` defaults to 3.7-flash while the unmeasured pattern lane stays on 2.5-flash.
- `playbook_prompt_ab.py` — two prompt versions, same model; structural scoring reused from `playbook_model_ab` plus a blind LLM judge for the axes structure cannot see (causal sequencing, redundant steps, language quality — the judge sees one playbook at a time and never which prompt wrote it). This is the gate playbook prompt **v6** passed on 2026-08-19, which is why v6 is now the registered default: over 6 patterns it took steps 6.3 → 5.5 while holding citations at 62 → 61, grounded share 0.79 → 0.94, and language grade 4.67 → 5.0, with rollback notes 6/6 and latency unchanged (snapshot `datasets/playbook_prompt_ab_2026-08-19.json`; verdict copied into the prompt module at backend/src/contextedge/ai/prompts/playbook.py:371-375). Two negative results were recorded so nobody re-litigates them: the judge's `logic_flaws` count reversed on a re-run of the same pattern and is too noisy to decide on, and prompting did **not** fix branch validity — that is enforced by `sanitize_branching_logic` in code (playbook_prompt_ab.py:20-33).
- `extraction_eval.py` — entity-extraction precision/recall/stability against `datasets/entity_extraction.jsonl`; stability (running each case several times) is the metric that separates "dropped a real entity" from "is noisy".
- `adjudication_thinking_eval.py` — measures whether capping the identity adjudicator's thinking budget preserves confidence *ordering*, because a threshold only cares about rank.

## Example: Acme VPN data at this stage

**Input — evaluation dataset curated by Acme's reviewer** (`POST /api/v1/evaluations/datasets`)

```json
{
  "name": "VPN domain gold set - Q3 2026",
  "cases": [
    {
      "symptoms": ["VPN authentication failure", "AUTH_CERT_EXPIRED"],
      "entities": ["vpn-gw-east-01"],
      "context": "Reported in INC0010427",
      "expected_playbook_stable_key": "pb-4f8a2c9d01e7"
    },
    {
      "symptoms": ["VPN slow connection", "high latency"],
      "entities": ["vpn-gw-west-02"],
      "context": "No recent changes",
      "expected_playbook_stable_key": "pb-77c3ab120f44"
    }
  ]
}
```

**Output — `run.results` after `evaluation.run_evaluation` completes**

```json
{
  "case_count": 2,
  "top1_accuracy": 0.5,
  "cases": [
    {
      "expected_stable_key": "pb-4f8a2c9d01e7",
      "top_stable_key": "pb-4f8a2c9d01e7",
      "top1_hit": true,
      "top_k": [{ "stable_key": "pb-4f8a2c9d01e7", "score": 0.81 }, { "stable_key": "pb-77c3ab120f44", "score": 0.44 }]
    },
    {
      "expected_stable_key": "pb-77c3ab120f44",
      "top_stable_key": "pb-4f8a2c9d01e7",
      "top1_hit": false,
      "top_k": [{ "stable_key": "pb-4f8a2c9d01e7", "score": 0.52 }, { "stable_key": "pb-77c3ab120f44", "score": 0.49 }]
    }
  ]
}
```

Case 2 is a miss: the certificate-rotation playbook is ranking too broadly — it wins even for a capacity complaint. That tells the reviewer to narrow its trigger conditions or strengthen the capacity playbook's evidence links. (A dataset can also mix in `"kind": "episode_citation"` cases; those add a `results.citation` block with mean unsupported-step and wrong-attribution rates.)

**Output — drift alerts from the scheduled `detect_drift` pass**

```json
{
  "tenants": 1,
  "alert_count": 2,
  "expired_transition_count": 1,
  "alerts": [
    {
      "playbook_id": "pb-r1s2t3",
      "pattern_id": "pat-m1n2o3",
      "title": "VPN Gateway Certificate Rotation",
      "issues": ["high_negative_feedback_3", "pattern_nodes_added_drift"],
      "severity": "medium"
    },
    {
      "playbook_id": "pb-old-vpn",
      "pattern_id": null,
      "title": "Legacy VPN Reconnect Steps",
      "issues": ["past_expiry", "not_validated_in_184_days"],
      "severity": "high"
    }
  ]
}
```

The legacy playbook was snapshotted as an alert and then transitioned `approved` → `expired` in the same pass, which removes it from runtime results immediately. The certificate playbook stays approved — its issues are medium severity (three `wrong_match` reports in 30 days, plus new episodes on its source pattern since generation) — and appears in the knowledge manager's queue. Had it carried the feedback issue alone, the alert would be severity `"low"`.

## Design decisions

- **Reuse `rank_playbooks` in evaluation** — *Why:* the offline run scores through the same ranker production uses (backend/src/contextedge/services/evaluation_service.py:9, 134-140); evaluation results degrade alongside real retrieval quality, not as an approximation. *Tradeoff:* evaluation accuracy depends on dataset cases staying representative as evidence and playbooks evolve; eval-time embedding calls are real, budget-gated spend; and the eval call passes only `tenant_id`, `query_text`, `entities`, and `top_k` — not the `domain_id`, `max_risk_tier`, `allowed_domain_ids`, or `caller_roles` that `POST /api/v1/runtime/match` supplies (backend/src/contextedge/api/v1/runtime.py:130-140). So a run measures the scorer, not the caller-scoped filtering layered on top of it.

- **Snapshot alerts before transitioning** — *Why:* `check_playbook_drift` runs `list_drift_alerts` first, so a playbook that expired since the last pass appears in the alert list *and* gets transitioned in the same call — operators see why something disappeared from runtime, not just that it did (drift_service.py:104-118). *Tradeoff:* the alert list and the post-transition state describe two adjacent moments; a consumer must not assume every alerted playbook is still `approved`.

- **Cap LLM cases per evaluation run** — *Why:* each `episode_citation` case is a full paid reconstruction, so `max_llm_cases` (default 100) stops a huge dataset from burning unbounded tokens on one trigger; skipped cases are counted in the results, never silent (evaluation_service.py:113-124). *Tradeoff:* a large citation dataset needs multiple runs or an explicit higher cap in `run.config`.

- **`"all"` fan-out in scheduled tasks** — *Why:* a single beat entry covers all tenants without per-tenant cron entries; simpler operations for tenant onboarding. *Tradeoff:* sequential fan-out adds latency at scale — and unlike the decision-analytics tasks, `detect_drift`'s loop has no per-tenant exception isolation, so one failing tenant aborts the pass into the task-level retry (evaluation_tasks.py:52-81).

- **Soft expiry transition (approved → expired)** — *Why:* stays inside `playbook_service.VALID_TRANSITIONS`; runtime retrieval already filters to `approved`, so the transition removes stale playbooks from results with no second mechanism, and `expired → under_review` remains open for a refresh (playbook_service.py:22-30). *Tradeoff:* the 6-hour cadence bounds how long an expired playbook can keep serving runtime traffic.

- **Feedback drives drift, not immediate suppression** — *Why:* a single negative feedback event should not instantly suppress a playbook — noise and misuse would cause false suppressions; the 30-day, 3-event threshold gives a stable signal, and even then the alert is severity `"low"` on its own. *Tradeoff:* a seriously wrong playbook may serve for up to one drift cycle before the alert fires, and acting on the alert is still a human decision.

- **Citation cases bypass the synthesis gates on purpose** — *Why:* the `episode_citation` dataset kind exists to grade one thing — whether the model's steps cite the evidence they were given — and routing it through `_reconstruct` would mean the min-cluster floor, the debounce, the growth gate and the observational-source gate could all silently refuse a case, turning a measurement into a no-op. Calling `reconstruct_episode` directly (evaluation_service.py:70-79) keeps the harness measuring the extractor. *Tradeoff:* the eval path and the production path have diverged in a way a reader can easily miss. A gold set can contain evidence production would never synthesize — an all-KB case, a two-item cluster — and score perfectly, so a citation run says nothing about whether those clusters would ever become episodes. The same caveat the retrieval cases already carry (they measure the scorer, not the caller-scoped filtering) now applies to the citation cases about the worker's gates.

- **The evidence ledger is a measurement surface with no writer yet** — *Why:* landing the table, the CHECK constraint and the read function together fixes the *vocabulary* — `empirical` versus `documented`, outcome only on episodes — before any producer can encode a different one; the constraint is unaddable later once wrong rows exist. *Tradeoff:* a ledger nobody writes to is worse than no ledger if it is mistaken for one, because `pattern_support` will happily return a confident-looking snapshot forever. Until clustering writes an empirical row when an episode joins a pattern, and until something writes `contradicts_resolution` when a recent episode fails a documented resolution, this page should be read as describing a designed capability. Treat any support figure as dated 2026-08-20.

- **Weekly golden-eval beat deliberately deferred** — *Why:* wiring a schedule before the customer defines the pass bar produces alerts nobody has agreed to act on; the scaffolding makes it a one-line beat addition once criteria exist (backend/evals/README.md:53-55). *Tradeoff:* until then, regression coverage depends on engineers remembering to run it on prompt or model changes.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evaluation execution | `backend/src/contextedge/services/evaluation_service.py` | `execute_evaluation_run` (26), `_execute_evaluation_core` (100), `_run_citation_case` (57) | Celery task |
| Evaluation models | `backend/src/contextedge/models/evaluation.py` | `EvaluationDataset` (11), `EvaluationRun` (25), `RetrievalFeedback` (42) | ORM |
| Evaluation API | `backend/src/contextedge/api/v1/evaluations.py` | `create_dataset` (61), `create_run` (87, dispatches the task) | HTTP, `knowledge_manager` |
| Drift alerts (read) | `backend/src/contextedge/services/drift_service.py` | `list_drift_alerts` (13) | HTTP GET / inside the beat task |
| Drift transition (mutate) | `backend/src/contextedge/services/drift_service.py` | `apply_expired_playbook_transitions` (84), `check_playbook_drift` (104) | Celery beat, 6h |
| Evaluation & drift tasks | `backend/src/contextedge/workers/evaluation_tasks.py` | `run_evaluation` (20), `detect_drift` (43), `scan_contradictions_task` (90), `ai_review_episodes` (131) | On demand / beat 6h / beat 12h / beat hourly |
| Beat schedule | `backend/src/contextedge/workers/celery_app.py` | `detect-drift-every-6h` (282), `scan-contradictions-every-12h` (287), daily decision analytics (309-318), `ai-review-episodes-hourly` (379) | Celery beat |
| Feedback API | `backend/src/contextedge/api/v1/runtime.py` | `submit_feedback` (357), `list_feedback` (373) | HTTP |
| Decision analytics tasks | `backend/src/contextedge/workers/decision_tasks.py` | `mine_decision_patterns` (36), `calibrate_decision_confidence` (132) | Celery beat (daily) |
| Golden evals | `backend/evals/` | `relevance/golden.jsonl`, `run_regression.py` CLI (main at 119) | Manual / CI on prompt or model change |
| A/B + stability harnesses | `backend/src/contextedge/evals/` | `playbook_model_ab.py`, `playbook_prompt_ab.py`, `extraction_eval.py`, `adjudication_thinking_eval.py` | Manual, before default changes |
| Hybrid ranker (shared) | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks` (213) | Eval + runtime |
| Freshness clock | `backend/src/contextedge/services/playbook_service.py` | `last_validated_at` stamped on approval (268) | Playbook approval |
| Evidence ledger (support state) | `backend/src/contextedge/services/knowledge_case_service.py` | `pattern_support` (246) | **Nothing calls it** — tests only |
| Ledger model + invariant | `backend/src/contextedge/models/pattern.py` | `PatternEvidence` (87), `ck_pattern_evidence_empirical_is_episode` (177), knowledge-drift rationale (107-110) | ORM (migration 0072) |
| Corpus change under the measurements | `backend/alembic/versions/0073_migrate_knowledge_episodes_to_cases.py` | tombstone tables (86-102), deletion (229-234), reported counts (244-252) | One-time, 2026-08-19 |

## Acme VPN incident (this layer)

After the VPN playbook is published, Acme's team submits `wrong_match` feedback through `POST /api/v1/runtime/feedback` when the playbook returns for a DNS misconfiguration incident it does not cover. After the third such event within a month, the next `detect_drift` pass (at most six hours later) includes a `high_negative_feedback_3` issue for the playbook; because its source pattern also gained new episodes since generation, the alert lands at `medium` severity, prompting a knowledge manager to narrow the trigger conditions. Meanwhile, an `EvaluationDataset` seeded with the INC0010427 case runs through `run_evaluation` and shows `top1_accuracy` dropping after a ranking change — with the per-case `top_k` scores pointing at exactly which competing playbook stole the top slot. When the certificate playbook is re-approved after its revision, `last_validated_at` resets and the 90-day staleness clock starts over.

What Acme cannot yet see is knowledge drift, the third mechanism above. Their cert-rotation SOP is attached to the same pattern as a `documented` ledger row, so if the next three VPN certificate episodes were to fail the SOP's procedure, those would be `contradicts_resolution` rows and `pattern_support` would return `contradicts: 3` beside `documented: 1` — the KB article is stale even though nobody retired it upstream. Every part of that sentence exists except the writer and the reader: no code path records a contradiction, and no route surfaces the result. Today the same situation shows up only indirectly, as `step_ineffective` feedback on the playbook.

## Further reading

- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — playbook lifecycle states, the AI episode-review sweep this file's worker hosts, and (section 6) the observational-source gate, knowledge cases and the evidence ledger whose measurement consequences this page records
- [INCIDENT_DIAGNOSIS_ROADMAP.md](./INCIDENT_DIAGNOSIS_ROADMAP.md) — why change, alert and topology evidence cannot appear in a gold set on this deployment
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — `rank_playbooks` internals
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — the contradiction scanner behind `scan_contradictions_task`
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — queue topology; `evaluation.*` routes to the `evaluation` queue
- [18-cost-observability-and-containment.md](./18-cost-observability-and-containment.md) — the measure-first precedent the A/B harnesses implement
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the deferred weekly golden-eval beat, and evaluation-as-release-gate (P1-7) roadmap status
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — operating scheduled evaluation and drift jobs
