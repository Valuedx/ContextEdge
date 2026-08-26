"""Joinable retrieval feedback: playbook_version_id + match_id index.

Revision ID: 0089_retrieval_feedback_version
Revises: 0088_runtime_match_records
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0089_retrieval_feedback_version"
down_revision = "0088_runtime_match_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.add_column(
        "retrieval_feedback",
        sa.Column("playbook_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_retrieval_feedback_match_id", "retrieval_feedback", ["match_id"]
    )
    op.create_index(
        "ix_retrieval_feedback_playbook_version_id",
        "retrieval_feedback",
        ["playbook_version_id"],
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_index("ix_retrieval_feedback_playbook_version_id", table_name="retrieval_feedback")
    op.drop_index("ix_retrieval_feedback_match_id", table_name="retrieval_feedback")
    op.drop_column("retrieval_feedback", "playbook_version_id")
