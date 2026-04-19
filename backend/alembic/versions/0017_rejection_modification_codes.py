"""Add structured rejection and modification codes to decision/approval flow.

Adds:
- decision_options.rejection_code (structured enum-style reason)
- decision_outcomes.feedback_code (structured feedback reason)
- approval_requests.modification_diff (JSONB) and modification_reason_code

These fields back the human-in-the-loop Approve / Modify / Reject decision
verbs so structured feedback codes can drive analytics and learning loops,
rather than relying on free-text reason fields.

Revision ID: 0017_rejection_modification_codes
Revises: 0016_first_class_decisions
Create Date: 2026-04-19 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0017_rejection_modification_codes"
down_revision: Union[str, None] = "0016_first_class_decisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE decision_options
            ADD COLUMN IF NOT EXISTS rejection_code VARCHAR(40) NULL;
        CREATE INDEX IF NOT EXISTS ix_decision_options_rejection_code
            ON decision_options (rejection_code)
            WHERE rejection_code IS NOT NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE decision_outcomes
            ADD COLUMN IF NOT EXISTS feedback_code VARCHAR(40) NULL;
        CREATE INDEX IF NOT EXISTS ix_decision_outcomes_feedback_code
            ON decision_outcomes (feedback_code)
            WHERE feedback_code IS NOT NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS modification_diff JSONB NULL,
            ADD COLUMN IF NOT EXISTS modification_reason_code VARCHAR(40) NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE approval_requests
            DROP COLUMN IF EXISTS modification_reason_code,
            DROP COLUMN IF EXISTS modification_diff;
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS ix_decision_outcomes_feedback_code;
        ALTER TABLE decision_outcomes
            DROP COLUMN IF EXISTS feedback_code;
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS ix_decision_options_rejection_code;
        ALTER TABLE decision_options
            DROP COLUMN IF EXISTS rejection_code;
        """
    )
