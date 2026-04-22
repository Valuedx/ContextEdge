# Eval dataset and regression runner (W10-12.3)

This directory holds **golden eval cases** — hand-curated input/expected-output pairs used to detect regressions when prompts, models, or extractors change.

## Why

`llm.usage` events tell us *how much* we're spending and whether the model *errors out*. They don't tell us whether a model swap or a prompt edit silently degraded accuracy. A reviewer might notice "these classifications feel worse" weeks after the ship, by which point the context is cold. A weekly regression run catches the drop on the next run.

The roadmap (`ENTERPRISE_ARCHITECTURE_REVIEW.md` §6 item 18) calls for one golden set per decision type, with a weekly job that runs extraction against each and reports accuracy + cost. This directory is the starting point — one golden set per extractor, expanded as real incident data accrues.

## Layout

```text
evals/
  README.md                   — this file
  relevance/
    golden.jsonl              — 5+ hand-labelled evidence items
  run_regression.py           — CLI that runs a golden set + reports
```

## File format

Each `golden.jsonl` is newline-delimited JSON, one case per line:

```json
{"id": "ticket-vpn-outage-01", "title": "VPN drops after KB5032190", "body": "Users can't VPN after the patch Tuesday update...", "source_type": "ticket", "evidence_type": "ticket", "expected_classification": "operational", "min_confidence": 0.7, "notes": "clear incident language"}
```

Required fields:

- `id` — stable human-readable identifier; appears in the regression report.
- `title`, `body`, `source_type`, `evidence_type` — forwarded to `classify_relevance`.
- `expected_classification` — the label a calibrated classifier should produce.
- `min_confidence` *(optional)* — fail the case if confidence falls below this even when the label is right. Useful for catching "technically correct but unsure" regressions.
- `notes` *(optional)* — free-text rationale for the label; helps the next reviewer understand why this case was chosen.

## Running the regression

```bash
cd backend
python -m evals.run_regression relevance
# → prints per-case pass/fail + a confusion matrix + avg cost/call
```

The runner uses the current versioned prompt registry — a per-tenant variant active in `settings.tenant_prompt_variants_json` does NOT affect the regression (which runs un-tenanted) so the result reflects the registered default. To eval a specific variant, set it as the default temporarily or pass `--prompt-version=v2` (not yet wired; planned follow-up).

## Adding new cases

1. Pick an evidence item that has been **reviewer-approved** for its label — don't invent fictional cases, and don't grade on borderline items the human reviewer would debate.
2. Redact PII before committing (see `services/redaction_service.py`).
3. Keep the set small (≤50 cases per extractor) — the goal is a smoke test, not a benchmark. Large eval sets get stale fast and people stop running them.

## Weekly run (deferred)

The Celery Beat wiring that actually runs this on a schedule is *deliberately* not shipped yet — it needs customer sign-off on what "regression" means for them (absolute accuracy threshold? week-over-week delta? a specific decision type that's critical?). The scaffolding here makes that wiring a one-line beat addition once those criteria exist.
