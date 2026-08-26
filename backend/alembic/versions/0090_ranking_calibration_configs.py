"""Versioned ranking calibration config (N8 tenancy).

Revision ID: 0090_ranking_calibration_configs
Revises: 0089_retrieval_feedback_version
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0090_ranking_calibration_configs"
down_revision = "0089_retrieval_feedback_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.create_table(
        "ranking_calibration_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("arm_weights", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("isotonic_points", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("labels_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_ranking_calibration_configs_tenant_id_id"
        ),
    )
    op.create_index(
        "ix_ranking_calibration_configs_tenant_id",
        "ranking_calibration_configs",
        ["tenant_id"],
    )
    _enable_rls("ranking_calibration_configs")


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_table("ranking_calibration_configs")


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
