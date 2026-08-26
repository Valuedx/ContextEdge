"""Draft-mutable playbook version editing columns.

Adds revision, updated_at, editor identity, and fork lineage on
playbook_versions so a published row stays immutable while an unpublished
draft can be patched in place. See services/playbook_editing.py.

Revision ID: 0093_playbook_version_editing
Revises: 0092_copilot_audit
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0093_playbook_version_editing"
down_revision = "0092_copilot_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    op.add_column(
        "playbook_versions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "playbook_versions",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "playbook_versions",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "playbook_versions",
        sa.Column("last_edited_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "playbook_versions",
        sa.Column(
            "derived_from_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("playbook_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(sa.text("UPDATE playbook_versions SET updated_at = created_at"))
    op.create_index(
        "ix_playbook_versions_open_draft",
        "playbook_versions",
        ["playbook_id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_index("ix_playbook_versions_open_draft", table_name="playbook_versions")
    op.drop_column("playbook_versions", "derived_from_version_id")
    op.drop_column("playbook_versions", "last_edited_by")
    op.drop_column("playbook_versions", "created_by")
    op.drop_column("playbook_versions", "revision")
    op.drop_column("playbook_versions", "updated_at")
