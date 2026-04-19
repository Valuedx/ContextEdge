"""Add verification_policy JSONB to playbook_versions.

Enables the reviewer console's Zone 6 verification-step rendering: a
playbook can declare "re-check telemetry N seconds post-action, auto-close
if resolved" as structured data rather than free-text. Paired with the new
`PlaybookStep` Pydantic schema in `schemas/playbook.py` (stored inside the
existing `steps` JSONB array — no column change), this gives each step
reversibility, time estimate, verification flag, rollback hint, and
optional tool reference.

Additive only. Existing `steps` payloads keep validating because every
`PlaybookStep` field is optional with a sensible default.

Revision ID: 0018_playbook_step_metadata
Revises: 0017_rejection_modification_codes
Create Date: 2026-04-19 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018_playbook_step_metadata"
down_revision: Union[str, None] = "0017_rejection_modification_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE playbook_versions
            ADD COLUMN IF NOT EXISTS verification_policy JSONB NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE playbook_versions
            DROP COLUMN IF EXISTS verification_policy;
        """
    )
