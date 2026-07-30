"""Add missing updated_at column to execution_runs.

Revision ID: 0034_execution_run_updated_at
Revises: 0033_identity_resolution_hardening
Create Date: 2026-07-30 11:15:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0034_execution_run_updated_at"
down_revision: Union[str, None] = "0033_identity_resolution_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ExecutionRun inherits TenantScopedMixin, which includes updated_at.
    # The original execution table migration predated that ORM shape and
    # created only created_at, so Review Queue context hydration can fail
    # when SQLAlchemy selects the inherited column.
    op.execute(
        """
        ALTER TABLE execution_runs
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE execution_runs
            DROP COLUMN IF EXISTS updated_at;
        """
    )
