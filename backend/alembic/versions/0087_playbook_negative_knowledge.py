"""Playbook↔negative-knowledge link table (N8 tenancy).

Revision ID: 0087_playbook_negative_knowledge
Revises: 0086_playbook_lexical_search
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0087_playbook_negative_knowledge"
down_revision = "0086_playbook_lexical_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.create_table(
        "playbook_negative_knowledge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("negative_knowledge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["negative_knowledge_id"],
            ["negative_knowledge_items.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_playbook_negative_knowledge_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "playbook_id",
            "negative_knowledge_id",
            name="uq_pb_nk_tenant_playbook_item",
        ),
    )
    op.create_index(
        "ix_playbook_negative_knowledge_tenant_id",
        "playbook_negative_knowledge",
        ["tenant_id"],
    )
    op.create_foreign_key(
        "fk_pb_nk_tenant_playbook",
        "playbook_negative_knowledge",
        "playbooks",
        ["tenant_id", "playbook_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pb_nk_tenant_nk_item",
        "playbook_negative_knowledge",
        "negative_knowledge_items",
        ["tenant_id", "negative_knowledge_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_negative_knowledge")


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_table("playbook_negative_knowledge")


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            WITH CHECK (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            """
        )
    )
