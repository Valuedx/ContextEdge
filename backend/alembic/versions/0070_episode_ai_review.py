"""Record the AI reviewer's assessment on the episode it assessed.

With ~1,400 drafts pending after one bulk ingest, human review is the
pipeline's long pole. The AI review stage (EPISODE_AI_REVIEW) reads a
pending draft plus its evidence and produces a verdict; that verdict has
to live ON the row so (a) the sweep never re-pays for an episode it
already reviewed, (b) the human reviewer sees why a draft was held, and
(c) an auto-approval is permanently distinguishable from a human one.

One nullable jsonb column rather than a table: the assessment is a
point-in-time annotation of exactly one row, has no lifecycle of its
own, and is superseded together with the episode it describes.

Revision ID: 0070_episode_ai_review
Revises: 0069_sync_run_control
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070_episode_ai_review"
down_revision = "0069_sync_run_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "episodes",
        sa.Column("ai_review", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("episodes", "ai_review")
