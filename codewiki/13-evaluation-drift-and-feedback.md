# Evaluation, drift, and retrieval feedback

## Summary

You will understand how ContextEdge measures retrieval quality via **offline evaluation runs** against gold datasets, how **drift detection** finds approved playbooks that have gone stale or accumulated negative feedback, and how **retrieval feedback** from live callers closes the quality loop back into both systems.

## Business picture

Playbooks that were accurate six months ago may no longer match the way incidents present today—software versions change, workarounds expire, and field teams learn better steps. ContextEdge tracks this in two complementary loops. **Offline evaluation** replays historical cases against the current retrieval pipeline to catch regressions before they reach production. **Drift detection** continuously monitors approved playbooks for signs of staleness (past expiry, lack of validation, or a spike in negative runtime feedback). When runtime callers report that a match was wrong or a step was ineffective, that **retrieval feedback** feeds directly into drift scoring—closing the gap between offline benchmarks and live performance.

## Technical walkthrough

### Evaluation datasets and runs

- `models/evaluation.py` defines two tables:
  - `EvaluationDataset` — a named, tenant-scoped JSONB array of `cases`. Each case carries `symptoms`, `entities`, an optional `context` string, and `expected_playbook_stable_key` (the ground-truth playbook that should rank first).
  - `EvaluationRun` — one execution of a dataset against the live pipeline; stores `status` (`pending` → `running` → `completed` / `failed`), `results` (JSONB), `started_at`, and `completed_at`.

- `evaluation_service.execute_evaluation_run` drives a run:
  1. Loads the `EvaluationRun` and its `EvaluationDataset`; validates tenant ownership.
  2. Sets `status="running"` and flushes.
  3. Calls `_execute_evaluation_core`: for each case, assembles a query string from symptoms + entities + context, calls `rank_playbooks` (the same hybrid ranker used at runtime), and checks whether the top result's `stable_key` matches `expected_playbook_stable_key`.
  4. Writes `top1_accuracy`, per-case hit/miss detail, and `completed` into `run.results`.
  5. On any exception, sets `status="failed"` and stores the error in `results`.

- The `run_evaluation` Celery task in `workers/evaluation_tasks.py` wraps this in an async runner with up to 2 retries and a 120-second delay, so evaluation jobs survive transient DB or provider hiccups.

### Drift detection

`services/drift_service.py` implements two functions used together:

**`list_drift_alerts`** (read-only, safe for HTTP GET) scans all `approved` playbooks for a tenant and checks:
- `expiry_at` past the current time → issue `"past_expiry"`.
- `last_validated_at` more than 90 days ago → issue `"not_validated_in_N_days"`.
- Three or more `RetrievalFeedback` rows with type `wrong_match`, `step_ineffective`, or `expired_workaround` in the past 30 days → issue `"high_negative_feedback_N"`.

Returns a list of alert dicts with `playbook_id`, `title`, `issues`, and `severity` (`"high"` if past expiry, otherwise `"medium"`).

**`apply_expired_playbook_transitions`** (mutating, for batch/Celery) bulk-updates playbooks from `approved` → `expired` where `expiry_at <= now`. Returns the row count.

**`check_playbook_drift`** combines both: snapshot alerts (which include past-expiry items before they transition), run the transition, then return a merged result dict. This is what `detect_drift` Celery task calls.

### Drift task and scan schedule

`workers/evaluation_tasks.py` — `detect_drift` task:
- Accepts `tenant_id` or the literal string `"all"` to scan every tenant in sequence.
- Calls `check_playbook_drift` per tenant and aggregates `alerts` and `expired_transition_count`.
- Retries once with a 300-second delay.

`scan_contradictions_task` (also in this file, originally from `09-graph-and-correlation.md`) follows the same pattern—`"all"` fan-out or single tenant—so both scheduled tasks can share Beat configuration.

Both tasks are wired into Celery Beat via `celery_app.beat_schedule`; see [08-workers-celery-queues.md](./08-workers-celery-queues.md) for queue names and intervals.

### Retrieval feedback

`models/evaluation.py` — `RetrievalFeedback`:
- Created by runtime API callers submitting outcome signals after a playbook was used.
- Stores `playbook_id`, `feedback_type` (`wrong_match`, `step_ineffective`, `expired_workaround`, and others), and freeform `details` JSONB.
- `drift_service.list_drift_alerts` queries this table's `feedback_type` and recency when scoring negative feedback signals.

This means a pattern of bad runtime outcomes surfaces in the next scheduled drift pass—operators do not need to manually flag each affected playbook.

## Design decisions

- **Reuse `rank_playbooks` in evaluation** — *Why:* the offline run tests exactly the code path that production uses; evaluation results degrade alongside real retrieval quality, not as an approximation. *Tradeoff:* evaluation accuracy depends on `EvaluationDataset` cases staying representative as evidence and playbooks evolve.

- **`"all"` fan-out in drift and contradiction tasks** — *Why:* a single Beat schedule entry covers all tenants without per-tenant cron entries; simpler operations for new tenant onboarding. *Tradeoff:* for large deployments, sequential fan-out adds latency; consider sharding tenant IDs across multiple task invocations if tenant count grows large.

- **Soft expiry transition (approved → expired)** — *Why:* keeps `drift_service` aligned with `playbook_service.VALID_TRANSITIONS`; runtime retrieval already filters by `lifecycle_state`, so transitioning to `expired` naturally removes stale playbooks from results. *Tradeoff:* the batch job must run frequently enough that expired playbooks do not serve runtime traffic for long.

- **Feedback drives drift, not immediate suppression** — *Why:* a single negative feedback event should not instantly suppress a playbook—noise and misuse would cause false suppressions. The 30-day, 3-event threshold gives a stable signal. *Tradeoff:* seriously incorrect playbooks may serve for up to one drift cycle before the alert fires.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evaluation execution | `backend/src/contextedge/services/evaluation_service.py` | `execute_evaluation_run`, `_execute_evaluation_core` | Celery task |
| Evaluation models | `backend/src/contextedge/models/evaluation.py` | `EvaluationDataset`, `EvaluationRun`, `RetrievalFeedback` | ORM |
| Drift alerts (read) | `backend/src/contextedge/services/drift_service.py` | `list_drift_alerts` | HTTP GET / beat task |
| Drift transition (mutate) | `backend/src/contextedge/services/drift_service.py` | `apply_expired_playbook_transitions`, `check_playbook_drift` | Celery beat |
| Evaluation & drift tasks | `backend/src/contextedge/workers/evaluation_tasks.py` | `run_evaluation`, `detect_drift`, `scan_contradictions_task` | Celery beat |
| Hybrid ranker (shared) | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks` | Eval + runtime |

## Acme VPN incident (this layer)

After the VPN playbook is published, Acme's team submits `wrong_match` feedback when the playbook returns for a DNS misconfiguration incident it does not cover. After three such events within a month, the next `detect_drift` pass includes a `high_negative_feedback_3` alert for the VPN playbook—prompting a Knowledge Manager to narrow the playbook's trigger conditions. Meanwhile, an `EvaluationDataset` curated by Acme's reviewer runs `run_evaluation`, confirming top-1 accuracy dropped from 91% to 78% after a recent patch; the regression guides which evidence gaps to fill before the next playbook revision.

## Further reading

- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — playbook lifecycle states (`approved`, `expired`, `retired`)
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — `rank_playbooks` internals
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — `scan_contradictions_task` details
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — beat schedule and queue topology
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — scheduling evaluation and drift jobs in production
