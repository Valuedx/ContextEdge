"""Align older deployments with ORM-level FK tightening shipped post-0001.

Review L-01 — ORM-DDL drift audit. ``0001_initial`` bakes whatever
ORM state was importable at the moment of the first ``alembic
upgrade``. Any ``ondelete=`` / ``nullable=False`` / ``unique=True``
tightening added to a model after that silently diverges on older
DBs: fresh installs get the constraint via ``create_all``, existing
ones don't.

The full audit is captured in ``codewiki/REVIEW-2026-04.md`` L-01.
It identified three drift points — all from the same commit
``45ad8791`` (2026-04-20), all adding ``ON DELETE CASCADE`` to FKs
that previously had no ondelete clause:

1. ``attachment_artifacts.evidence_id → evidence_items(id)``
2. ``correlation_edges.source_evidence_id → evidence_items(id)``
3. ``correlation_edges.target_evidence_id → evidence_items(id)``

Every other post-0001 constraint was reconciled by its own
migration (0005-0027). This migration catches the remaining three.

Idempotence: each ``ALTER TABLE DROP CONSTRAINT ... ADD CONSTRAINT
... ON DELETE CASCADE`` runs against the named constraint PG
generates for an unnamed FK — ``{table}_{column}_fkey``. If a
deployment already has a CASCADE in place (because it was built
from scratch AFTER the ORM change), dropping and re-adding the
same constraint is a no-op-equivalent behaviour change. The guard
``IF EXISTS`` on the drop handles the corner case where the
constraint name differs on a hand-customised deployment.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028_orm_ddl_drift_alignment"
down_revision: Union[str, None] = "0027_playbook_evidence_link_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CASCADE_FIXES = [
    # (table, column, constraint_name, referenced_table, referenced_column)
    (
        "attachment_artifacts",
        "evidence_id",
        "attachment_artifacts_evidence_id_fkey",
        "evidence_items",
        "id",
    ),
    (
        "correlation_edges",
        "source_evidence_id",
        "correlation_edges_source_evidence_id_fkey",
        "evidence_items",
        "id",
    ),
    (
        "correlation_edges",
        "target_evidence_id",
        "correlation_edges_target_evidence_id_fkey",
        "evidence_items",
        "id",
    ),
]


def upgrade() -> None:
    for table, column, name, ref_table, ref_col in _CASCADE_FIXES:
        # Drop the existing FK (whatever its ondelete was) and recreate
        # with ON DELETE CASCADE. IF EXISTS on the drop tolerates the
        # case where a fresh DB built from post-ORM-change create_all
        # already has the cascade — the name still matches PG's auto-
        # generated pattern.
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"
        )
        op.create_foreign_key(
            name,
            table,
            ref_table,
            [column],
            [ref_col],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    # Restore the pre-cascade shape (no ondelete, i.e. NO ACTION /
    # RESTRICT). This is a compatibility downgrade only — running it
    # puts older deployments back in the broken state.
    for table, column, name, ref_table, ref_col in _CASCADE_FIXES:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"
        )
        op.create_foreign_key(
            name,
            table,
            ref_table,
            [column],
            [ref_col],
        )
