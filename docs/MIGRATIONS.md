# Database migrations and reproducibility

## Alembic revision `0001_initial`

The first revision calls `Base.metadata.create_all()` against whatever SQLAlchemy models are importable when **`alembic upgrade head`** runs. It does **not** embed a frozen SQL snapshot in the repository.

**Implications**

- Two greenfield installs at different commits can end up with different `0001` DDL if models changed between those commits.
- **Operational mitigation:** treat `0001` as a bootstrap step, then rely on **`0002+`** (explicit revisions) for every subsequent schema change.
- **Reproducible environments:** after a known-good migrate on a reference commit, capture **`pg_dump --schema-only`** (or restore from a golden image) for CI and staging parity.
- **Downgrade:** `0001` uses `drop_all()` and is destructive; do not use it casually in shared environments.

## Adding schema changes

Always add a new revision (`alembic revision --autogenerate` or hand-written `op.create_table` / `op.add_column`) rather than expecting `0001` to update on existing databases.

See also [Runbook — Database migrations](RUNBOOK.md#database-migrations).

## Notable revisions

| Revision | Summary |
| --- | --- |
| `0016_first_class_decisions` | Introduces `decisions`, `decision_options`, and `decision_outcomes` tables with graph-edge connectivity. See [codewiki/16-decision-traces.md](../codewiki/16-decision-traces.md). |
| `0017_rejection_modification_codes` | Adds structured code columns to the human-in-the-loop flow: `decision_options.rejection_code`, `decision_outcomes.feedback_code`, and `approval_requests.modification_diff` + `modification_reason_code`. Additive only — existing free-text `rejection_reason` / `decision_comment` fields stay for the `other` + write-in case. Enables the `POST /decisions/{id}/reject` endpoint and the Approve / Modify / Reject learning loop. |
| `0018_playbook_step_metadata` | Adds `playbook_versions.verification_policy JSONB` for the reviewer console's Zone 6 "auto-close on successful recheck" commitment. Paired with the new `PlaybookStep` Pydantic schema (in `schemas/playbook.py`, stored inside the existing `steps` JSONB array), this gives each step reversibility, time estimate, verification flag, rollback hint, safety-class override, and tool reference. Additive only — every new field is optional with sensible defaults, so existing step payloads keep validating. |
| `0019_evidence_baseline` | Adds `evidence_items.baseline_ref JSONB` and `evidence_items.delta_signal VARCHAR(20)` (with a partial index) for the reviewer console's Zone 4 evidence cards that need a current value plus a baseline comparison ("was 74% a week ago", "first observation in 7d window"). The `compute_evidence_baseline` worker (`workers/evidence_baseline_tasks.py`, routed to the `extraction` queue) fans out from normalization and artifact extraction. Additive only — no backfill; existing rows stay null until the next re-ingest or manual recompute. |
| `0020_decision_embedding` | Adds `decisions.embedding Vector(3072)` for semantic similar-decision retrieval. `create_decision` embeds `decision_type + compact_trace + rationale_summary` inline post-flush with graceful fail (provider hiccup leaves embedding null, doesn't fail the write). `find_similar_decisions` accepts `query_decision_id` / `query_text` and orders by `embedding <=> query` cosine distance when a query embedding is available, falling back to the existing `created_at DESC` path otherwise. JSONB containment on `context_snapshot` stays as a structural pre-filter in both paths. **No vector index added in this revision** (matches the existing `evidence_items.embedding` pattern — full-table scan is fine at current scale; an HNSW / IVFFlat index is a follow-up when decision row counts warrant it). |
