"""Message-function label on evidence (backlog A1).

What a conversational message is DOING — reporting status, asking a
question, correcting an earlier statement, explicitly dissociating from
a case, confirming resolution, or noise — is load-bearing for three
correlation features: the dissociation veto on reply inheritance
(phrase-list v1 shipped with PR #30; the classifier is its planned
replacement), correction supersession (A2), and the negative-evidence
store (A7). One classification per conversational evidence item,
persisted here.

Additive and re-runnable.

Revision ID: 0041_message_function
Revises: 0040_episode_contradictions
Create Date: 2026-08-02 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0041_message_function"
down_revision: Union[str, None] = "0040_episode_contradictions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_items
        ADD COLUMN IF NOT EXISTS message_function VARCHAR(30);
        """
    )
    op.execute(
        """
        ALTER TABLE evidence_items
        ADD COLUMN IF NOT EXISTS message_function_confidence FLOAT;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS message_function_confidence;")
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS message_function;")
