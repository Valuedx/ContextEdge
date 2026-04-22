# Evaluation, drift, and retrieval feedback

## Summary

You will understand how ContextEdge measures retrieval quality via **offline evaluation runs** against gold datasets, how **drift detection** finds approved playbooks that have gone stale or accumulated negative feedback, and how **retrieval feedback** from live callers closes the quality loop back into both systems.

## Business picture

Playbooks that were accurate six months ago may not match how incidents present today. Software versions change, workarounds expire, and field teams discover better steps. Without continuous quality checks, outdated guidance quietly degrades every recommendation the system makes.

ContextEdge measures retrieval quality, detects when playbooks go stale, and feeds real-world outcome signals back into the system — so your operational knowledge stays current without manual monitoring. Three capabilities work together:

- **Offline evaluation** replays historical cases against the current retrieval pipeline to catch accuracy regressions before they reach production. If a recent change causes the wrong playbook to rank first, the evaluation run surfaces it.
- **Drift detection** continuously monitors approved playbooks for signs of staleness — an expiry date that has passed, a long gap since the last review, or a pattern of negative feedback from field teams. Stale playbooks are flagged for review or automatically removed from results.
- **Retrieval feedback** closes the loop: when a team member reports that a recommended playbook was wrong or a step no longer works, that signal feeds directly into drift scoring. Operators do not need to manually flag each affected playbook — the system connects the dots on the next scheduled scan.

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

- The `run_evaluation` Celery task in `workers/evaluation_tasks.py` wraps this in an async runner with up to 2 retries and a 120-second delay, so evaluation jobs survive transient database or provider hiccups.

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

### Decision analytics — calibration and pattern mining

`workers/decision_tasks.py` registers two evaluation-queue tasks that mine the first-class `Decision` / `DecisionOutcome` rows (see [16-decision-traces.md](./16-decision-traces.md)) for reviewer-visible quality signals:

- **`evaluation.calibrate_decision_confidence`** buckets completed decisions by their predicted `confidence` (0.1-granularity) and computes the observed success rate per bucket. A well-calibrated extractor has bucket `0.9 → 0.9 ± 0.1` observed success; a miscalibrated one shows "high confidence, low success" buckets that deserve prompt tuning. Results persist as `decision.confidence_calibrated` operational events the admin dashboard can chart over time.
- **`evaluation.mine_decision_patterns`** groups `(decision_type, execution_result)` pairs with count ≥ 3 and surfaces failure rates so recurring mistakes ("restart is ineffective for network-share failures") are visible without a human eyeballing every run. Emits `decision.patterns_mined`.

Both tasks accept the `"all"` sentinel for per-tenant fan-out with isolated exception handling — one broken tenant doesn't kill the beat for the rest. They're scheduled daily via Celery Beat (`calibrate-decision-confidence-daily`, `mine-decision-patterns-daily`).

### Golden eval scaffolding

`backend/evals/` holds hand-curated `golden.jsonl` files per extractor (one case per line: `id`, inputs, `expected_classification`, optional `min_confidence`). `backend/evals/run_regression.py` is a CLI that runs a golden set through the live extractor, prints per-case pass/fail + a confusion matrix, and exits non-zero on any failure so it can be wired into CI. Ship paths today: run manually on model or prompt changes. The weekly-beat automation is deferred until customer pass-bar criteria are signed off — see `backend/evals/README.md`.

## Example: Acme VPN data at this stage

**Input — evaluation dataset curated by Acme's reviewer**

```json
{
  "dataset_id": "eval-ds-001",
  "tenant_id": "acme-corp",
  "name": "VPN domain gold set — Q1 2026",
  "cases": [
    {
      "symptoms": ["VPN authentication failure", "AUTH_CERT_EXPIRED"],
      "entities": ["vpn-gw-east-01"],
      "context": "Post-patch Tuesday",
      "expected_playbook_stable_key": "vpn-cert-rotation"
    },
    {
      "symptoms": ["VPN slow connection", "high latency"],
      "entities": ["vpn-gw-west-02"],
      "context": "No recent patches",
      "expected_playbook_stable_key": "vpn-capacity-check"
    },
    {
      "symptoms": ["Cannot connect to VPN", "DNS resolution failure"],
      "entities": ["vpn-gw-east-01"],
      "context": "DNS misconfiguration reported",
      "expected_playbook_stable_key": "vpn-dns-troubleshoot"
    }
  ]
}
```

**Output — evaluation run results**

```json
{
  "evaluation_run_id": "eval-run-001",
  "dataset_id": "eval-ds-001",
  "status": "completed",
  "results": {
    "top1_accuracy": 0.78,
    "cases": [
      { "case_index": 0, "expected": "vpn-cert-rotation", "top_result": "vpn-cert-rotation", "hit": true },
      { "case_index": 1, "expected": "vpn-capacity-check", "top_result": "vpn-cert-rotation", "hit": false },
      { "case_index": 2, "expected": "vpn-dns-troubleshoot", "top_result": "vpn-cert-rotation", "hit": false }
    ]
  },
  "completed_at": "2026-04-01T04:15:00Z"
}
```

Cases 1 and 2 are mismatches: the VPN certificate playbook is ranking too broadly. This tells the reviewer to narrow trigger conditions or improve the competing playbooks' evidence links.

**Output — drift alerts from scheduled scan**

```json
{
  "alerts": [
    {
      "playbook_id": "pb-r1s2t3",
      "title": "VPN Certificate Rotation After Patch Tuesday",
      "issues": ["high_negative_feedback_3"],
      "severity": "medium",
      "detail": "3 wrong_match feedback events in the past 30 days"
    },
    {
      "playbook_id": "pb-old-vpn",
      "title": "Legacy VPN Reconnect Steps",
      "issues": ["past_expiry", "not_validated_in_180_days"],
      "severity": "high",
      "detail": "Expired 2026-02-01; last validated 2025-10-15"
    }
  ],
  "expired_transitions": 1
}
```

The legacy playbook is automatically transitioned from `approved` to `expired` and removed from runtime results. The certificate rotation playbook stays active but appears in the review queue for the knowledge manager to address the negative feedback.

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
| Decision analytics tasks | `backend/src/contextedge/workers/decision_tasks.py` | `calibrate_decision_confidence`, `mine_decision_patterns` (named `evaluation.*`) | Celery beat (daily) |
| Golden evals | `backend/evals/` | `relevance/golden.jsonl`, `run_regression.py` CLI | Manual / CI on prompt or model change |
| Hybrid ranker (shared) | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks` | Eval + runtime |

## Acme VPN incident (this layer)

After the VPN playbook is published, Acme's team submits `wrong_match` feedback when the playbook returns for a DNS misconfiguration incident it does not cover. After three such events within a month, the next `detect_drift` pass includes a `high_negative_feedback_3` alert for the VPN playbook—prompting a Knowledge Manager to narrow the playbook's trigger conditions. Meanwhile, an `EvaluationDataset` curated by Acme's reviewer runs `run_evaluation`, confirming top-1 accuracy dropped from 91% to 78% after a recent patch; the regression guides which evidence gaps to fill before the next playbook revision.

## Further reading

- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — playbook lifecycle states (`approved`, `expired`, `retired`)
- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — `rank_playbooks` internals
- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — `scan_contradictions_task` details
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — beat schedule and queue topology
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — scheduling evaluation and drift jobs in production
