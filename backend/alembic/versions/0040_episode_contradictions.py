"""Episode contradiction preservation (P4 source-aware synthesis).

When sources conflict on a fact — the ticket's close notes say the
certificate was renewed, the Teams thread says the auth service was
rolled back — synthesis must keep BOTH accounts instead of merging them
into one unsupported conclusion. The accounts land here as structured
JSON so the review surface can show reviewers exactly what disagrees
and which evidence says what.

Additive and re-runnable.

Revision ID: 0040_episode_contradictions
Revises: 0039_correlation_suggestions
Create Date: 2026-08-02 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0040_episode_contradictions"
down_revision: Union[str, None] = "0039_correlation_suggestions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE episodes
        ADD COLUMN IF NOT EXISTS contradictions JSONB;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS contradictions;")
