"""Durable runtime match records (N7).

Revision ID: 0088_runtime_match_records
Revises: 0087_playbook_negative_knowledge
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0088_runtime_match_records"
down_revision = "0087_playbook_negative_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.create_table(
        "runtime_match_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_id", sa.String(length=255), nullable=False),
        sa.Column("query_frame", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ranked_results", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("filters_applied", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("calibrated_confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_runtime_match_records_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "match_id", name="uq_runtime_match_records_tenant_match"),
    )
    op.create_index(
        "ix_runtime_match_records_tenant_id", "runtime_match_records", ["tenant_id"]
    )
    op.create_index(
        "ix_runtime_match_records_match_id", "runtime_match_records", ["match_id"]
    )
    op.execute(sa.text("ALTER TABLE runtime_match_records ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE runtime_match_records FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON runtime_match_records
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


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_table("runtime_match_records")
