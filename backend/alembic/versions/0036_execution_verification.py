"""Post-action verification columns on execution_runs.

``PlaybookVersion.verification_policy`` promised "re-check telemetry
30 min post-action" since its introduction, but nothing consumed it —
completed executions were never checked against reality. These columns
record the verdict of that re-check (services/
execution_verification_service.py):

- ``verification_status``: verified | failed | unverifiable (NULL =
  not yet checked — the sweep's work queue).
- ``verified_at``: when the check ran.
- ``verification_details``: JSONB — post-action signal counts (new
  incidents / alert batches on the session's CIs), the policy used,
  and the verdict rationale.

The partial index is the sweep's queue: completed runs not yet
verified, ordered by completion time.

Additive and re-runnable (IF NOT EXISTS throughout).

Revision ID: 0036_execution_verification
Revises: 0035_playbook_embeddings
Create Date: 2026-08-01 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0036_execution_verification"
down_revision: Union[str, None] = "0035_playbook_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE execution_runs
            ADD COLUMN IF NOT EXISTS verification_status VARCHAR(30),
            ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS verification_details JSONB;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_execution_runs_unverified
        ON execution_runs (completed_at)
        WHERE verification_status IS NULL AND status = 'completed';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_execution_runs_unverified;")
    op.execute(
        """
        ALTER TABLE execution_runs
            DROP COLUMN IF EXISTS verification_status,
            DROP COLUMN IF EXISTS verified_at,
            DROP COLUMN IF EXISTS verification_details;
        """
    )
