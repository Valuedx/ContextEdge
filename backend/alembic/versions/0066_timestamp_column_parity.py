"""Add the mixin timestamps `0062` and `0063` left out.

`TenantScopedMixin` carries `TimestampMixin`, so every model using it declares
`created_at` **and** `updated_at`. The hand-written `create_table` calls in
`0062_trust_profiles` and `0063_rollback_and_escalation` list the columns their
authors typed, and the mixin's are easy to miss — which is exactly how
`0034_execution_run_updated_at` and `0049_evidence_chunks_updated_at` came to
exist. This is the third occurrence.

It never surfaced in tests because the backend suite runs without a live
Postgres: SQLAlchemy is happy to *describe* a column the database does not
have. It would surface on the first real query naming one —

    UndefinedColumn: column trust_profiles.updated_at does not exist

— which is every SELECT of `TrustProfile`, `RollbackPlan` and `Escalation`.
Fresh installs are unaffected (`0001` builds from metadata and the later
create_table calls skip an existing table), so this only bites the databases
that have been upgraded through the chain: the ones that matter.

`ADD COLUMN IF NOT EXISTS` for that reason — the column is already present
wherever `0001` built it, and absent wherever `0062`/`0063` did.

`tests/test_orm_migration_column_parity.py` now fails on the next occurrence.

Revision ID: 0066_timestamp_column_parity
Revises: 0065_knowledge_supersession
"""

from alembic import op

revision = "0066_timestamp_column_parity"
down_revision = "0065_knowledge_supersession"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Written out rather than looped over a table list: the parity test reads
    # migrations as text, and a loop over f-strings is invisible to it. A fix
    # for a drift the guard cannot see is a fix that can silently regress.
    op.execute(
        """
        ALTER TABLE trust_profiles
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        """
    )
    op.execute(
        """
        ALTER TABLE rollback_plans
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        """
    )
    op.execute(
        """
        ALTER TABLE escalations
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        """
    )


def downgrade() -> None:
    # Deliberately not dropped: on a fresh install these columns came from
    # `0001`, not from this migration, and dropping them on the way down would
    # take out columns this revision never added.
    pass
