"""Add conflicts to playbook_versions.

Records where the approved KB/SOP and observed practice disagree about a
generated playbook, so a reviewer adjudicates instead of the generator
silently choosing.

The choice has to be surfaced rather than made: preferring the documented
procedure ignores verified runs that did something else, and preferring
observed practice quietly deletes a safeguard nobody happened to perform.
Both belong in front of a human, and until now there was nowhere to put
them — the generator's ``conflicts`` output had no column and would have
been dropped on write.

Additive and nullable, so re-running is safe and existing versions are
unaffected (they carry NULL, meaning "not assessed", which is honest —
they were generated before knowledge was an input at all).

Revision ID: 0050_playbook_version_conflicts
Revises: 0049_evidence_chunks_updated_at
Create Date: 2026-08-03 14:10:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0050_playbook_version_conflicts"
down_revision: Union[str, None] = "0049_evidence_chunks_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE playbook_versions
            ADD COLUMN IF NOT EXISTS conflicts JSONB NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE playbook_versions
            DROP COLUMN IF EXISTS conflicts;
        """
    )
