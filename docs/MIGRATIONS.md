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
