"""One step per order per episode — enforced where the invariant lives.

The 2026-08-18 run left 949 live episodes with stacked timelines: multiple
narrations' step lists concatenated onto one episode, all numbered from
step 1 (worst case: 319 steps across 24 orders, the same complaint
restated ~50 times). Transaction forensics (distinct xmin per stacked
episode = 3) prove a later execution appends steps to an existing episode,
but the writer evaded code reading — every visible path only writes steps
to a freshly created row.

When a silent corruptor cannot be found by reading, the invariant gets
enforced at the database: with this constraint, the next append attempt
raises IntegrityError loudly, with a stack trace that names the culprit —
converting silent data corruption into a self-identifying bug. Legitimate
writes are unaffected: one narration emits steps 1..n exactly once.

Data was deduplicated first (best step per order kept; 53,288 duplicates
preserved in episode_steps_stacked_backup).

Revision ID: 0071_episode_step_uniqueness
Revises: 0070_episode_ai_review
"""

from alembic import op

revision = "0071_episode_step_uniqueness"
down_revision = "0070_episode_ai_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_episode_step_order",
        "episode_steps",
        ["episode_id", "step_order"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_episode_step_order", "episode_steps", type_="unique")
