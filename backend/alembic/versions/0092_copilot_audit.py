"""Copilot login, usage, conversation, and message audit tables.

Revision ID: 0092_copilot_audit
Revises: 0091_fix_ce_fill_tenant_id_playbooks
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid


revision = "0092_copilot_audit"
down_revision = "0091_fix_ce_fill_tenant_id_playbooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    op.create_table(
        "copilot_login_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("client", sa.String(length=40), nullable=False, server_default="extension"),
        sa.Column("extension_version", sa.String(length=32), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failure_reason", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_copilot_login_events_tenant_id_id"),
    )
    op.create_index("ix_copilot_login_events_tenant_id", "copilot_login_events", ["tenant_id"])
    op.create_index("ix_copilot_login_events_user_id", "copilot_login_events", ["user_id"])
    op.create_index(
        "ix_copilot_login_events_tenant_user_created",
        "copilot_login_events",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_copilot_login_events_tenant_created",
        "copilot_login_events",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "copilot_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="ticket"),
        sa.Column("ticket_id", sa.String(length=64), nullable=True),
        sa.Column("ticket_number", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False, server_default="Conversation"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_copilot_conversations_tenant_id_id"),
    )
    op.create_index("ix_copilot_conversations_tenant_id", "copilot_conversations", ["tenant_id"])
    op.create_index("ix_copilot_conversations_user_id", "copilot_conversations", ["user_id"])
    op.create_index(
        "ix_copilot_conversations_list",
        "copilot_conversations",
        ["tenant_id", "user_id", "last_message_at"],
    )
    op.create_index(
        "ix_copilot_conversations_ticket",
        "copilot_conversations",
        ["tenant_id", "ticket_id"],
    )

    op.create_table(
        "copilot_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="ticket"),
        sa.Column("ticket_id", sa.String(length=64), nullable=True),
        sa.Column("ticket_number", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_copilot_usage_events_tenant_id_id"),
    )
    op.create_index("ix_copilot_usage_events_tenant_id", "copilot_usage_events", ["tenant_id"])
    op.create_index("ix_copilot_usage_events_user_id", "copilot_usage_events", ["user_id"])
    op.create_index("ix_copilot_usage_events_conversation_id", "copilot_usage_events", ["conversation_id"])
    op.create_index(
        "ix_copilot_usage_events_tenant_user_created",
        "copilot_usage_events",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_copilot_usage_events_tenant_created",
        "copilot_usage_events",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_copilot_usage_events_ticket",
        "copilot_usage_events",
        ["tenant_id", "ticket_id"],
    )

    op.create_table(
        "copilot_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("copilot_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_copilot_messages_tenant_id_id"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_copilot_messages_conversation_seq"),
    )
    op.create_index("ix_copilot_messages_tenant_id", "copilot_messages", ["tenant_id"])
    op.create_index("ix_copilot_messages_conversation_id", "copilot_messages", ["conversation_id"])
    op.create_index(
        "ix_copilot_messages_conversation_seq",
        "copilot_messages",
        ["conversation_id", "seq"],
    )

    for table in (
        "copilot_login_events",
        "copilot_usage_events",
        "copilot_conversations",
        "copilot_messages",
    ):
        _enable_rls(table)

    op.get_bind().execute(
        sa.text(
            "INSERT INTO role_nav_access (id, role, href) "
            "SELECT CAST(:id AS uuid), 'tenant_admin', '/admin/copilot' "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM role_nav_access "
            "  WHERE role = 'tenant_admin' AND href = '/admin/copilot'"
            ")"
        ),
        {"id": str(uuid.uuid4())},
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(
        sa.text("DELETE FROM role_nav_access WHERE href = '/admin/copilot'")
    )
    op.drop_table("copilot_messages")
    op.drop_table("copilot_usage_events")
    op.drop_table("copilot_conversations")
    op.drop_table("copilot_login_events")


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
