# ContextEdge — working agreements for AI-assisted changes

## Review discipline (mandatory)

Every implementation — feature, fix, or refactor — gets **three review-fix-review passes before commit**:

1. **Pass 1 — correctness:** re-read the full diff as a hostile reviewer. Trace each changed path with a concrete input. Fix what you find, then re-read the fix.
2. **Pass 2 — blast radius:** search for every caller/consumer of what changed (including tests, workers, and the maf.v1 projection). Verify degrade-not-crash on malformed input. Fix, re-read.
3. **Pass 3 — tests and evidence:** new behavior gets a test that fails without the change; run the full backend suite (`python -m pytest -q` from `backend/`) and record the count in the commit message. Fix, re-read.

A pass that finds nothing must say what it looked for. Findings fixed during review are re-reviewed, not just applied.

## Measure-first discipline (model-facing and projection changes)

Any change to prompts, thinking budgets, truncation/slicing, seed layers, or projection composition ships only with a before/after measurement on real data (see `codewiki/18-cost-observability-and-containment.md` for the precedent). Negative results are recorded — in codewiki — so decisions don't get re-litigated. A cap or slice that changes model *output structure* on identical input is a quality change, not a cost change, and does not ship on cost grounds alone.

## Repo conventions

- Prompts are versioned and immutable: never edit a shipped prompt version; add a new one in `backend/src/contextedge/ai/prompts/` and update the default.
- Confidence thresholds gate automatic actions (`AUTO_LINK_THRESHOLDS`, `CLASSIFIER_TRUST_FLOOR`, reconciliation `MIN_CONFIDENCE`). Never change what feeds them without re-tuning them in the same change.
- Windows dev: Celery uses the two-worker topology in `docs/RUNBOOK.md` ("Worker topology"); prefork is unusable, `-P threads` is the parallel pool.
- Secrets: `.env` stays untracked; scan every staged diff before commit.
- Current graph/agent workplan: `codewiki/INCIDENT_DIAGNOSIS_ROADMAP.md`. Known caveats: `codewiki/KNOWN_GAPS.md`.
