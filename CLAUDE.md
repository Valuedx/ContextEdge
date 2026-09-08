# ContextEdge — working agreements for AI-assisted changes

## Review discipline (mandatory)

Every implementation — feature, fix, or refactor — gets **three review-fix-review passes before commit**:

1. **Pass 1 — correctness:** re-read the full diff as a hostile reviewer. Trace each changed path with a concrete input. Fix what you find, then re-read the fix.
2. **Pass 2 — blast radius:** search for every caller/consumer of what changed (including tests, workers, and the maf.v1 projection). Verify degrade-not-crash on malformed input. Fix, re-read.
3. **Pass 3 — tests and evidence:** new behavior gets a test that fails without the change; run the full backend suite (`python -m pytest -q` from `backend/`) and record the count in the commit message. Fix, re-read.

A pass that finds nothing must say what it looked for. Findings fixed during review are re-reviewed, not just applied.

## Documentation discipline (mandatory)

Every roadmap item, feature, or defect worth a commit message is worth a doc entry. Undocumented work is re-litigated work: the next session re-derives the same finding, or worse, re-introduces the defect because nothing recorded why the fix was shaped that way.

**A substantial change gets a codewiki article** with the house structure, in this order: **Summary** → **Business picture** (what breaks for a user if this is absent) → **Walkthrough** (the mechanism, with measured numbers, not adjectives) → **Decisions** → **Code map** (path → role table) → **Acme VPN incident (this layer)** → **References**.

- **Every decision carries both a `Why:` and a `Tradeoff:`.** A decision with no stated tradeoff has not been made, it has been assumed — and the next person cannot tell which constraints are load-bearing.
- **Quote measurements, never adjectives.** "3,805 of 10,547 rows (36%)" survives review; "many rows" does not. If a claim cannot be measured on this deployment, say that explicitly rather than softening it.
- **Reuse the canonical Acme VPN incident** (`vpn-gw-east-01`, `AUTH_CERT_EXPIRED`, KB5032190, `jsmith@acme.com`). Every article shows what *its* layer contributes to that one incident, so a reader can follow the thread across articles. Inventing a fresh example breaks the continuity the codewiki is built on.

**`KNOWN_GAPS.md` gets both halves of the ledger**, in a dated section: what **Closed**, and — at greater length, because this is the part that gets overstated — what **Opened**. A capability that works on one connector and degrades on another is an Opened entry, not a footnote. Record it before it reads as capability.

**The roadmap is updated in the same commit** that changes its status. A sequencing table that disagrees with the code is worse than no table, because it is trusted.

A finding that changes what someone would do next belongs in the docs even when it is not what the task was about — the dormant classifier and the fresh-database migration failure were both found this way, and neither was in scope.

## Measure-first discipline (model-facing and projection changes)

Any change to prompts, thinking budgets, truncation/slicing, seed layers, or projection composition ships only with a before/after measurement on real data (see `codewiki/18-cost-observability-and-containment.md` for the precedent). Negative results are recorded — in codewiki — so decisions don't get re-litigated. A cap or slice that changes model *output structure* on identical input is a quality change, not a cost change, and does not ship on cost grounds alone.

## Repo conventions

- Prompts are versioned and immutable: never edit a shipped prompt version; add a new one in `backend/src/contextedge/ai/prompts/` and update the default.
- Confidence thresholds gate automatic actions (`AUTO_LINK_THRESHOLDS`, `CLASSIFIER_TRUST_FLOOR`, reconciliation `MIN_CONFIDENCE`). Never change what feeds them without re-tuning them in the same change.
- Windows dev: Celery uses the two-worker topology in `docs/RUNBOOK.md` ("Worker topology"); prefork is unusable, `-P threads` is the parallel pool.
- Secrets: `.env` stays untracked; scan every staged diff before commit.
- Current graph/agent workplan: `codewiki/INCIDENT_DIAGNOSIS_ROADMAP.md`. Known caveats: `codewiki/KNOWN_GAPS.md`.
