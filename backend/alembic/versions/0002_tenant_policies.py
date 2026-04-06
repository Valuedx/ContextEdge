"""Add tenant_policies table for persisted policy documents."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_tenant_policies"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tenant_policies"):
        op.create_table(
            "tenant_policies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("policy_type", sa.String(length=30), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "config",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, "tenant_policies", "ix_tenant_policies_tenant_id"):
        op.create_index("ix_tenant_policies_tenant_id", "tenant_policies", ["tenant_id"])

    if not _has_index(inspector, "tenant_policies", "ix_tenant_policies_tenant_type"):
        op.create_index(
            "ix_tenant_policies_tenant_type",
            "tenant_policies",
            ["tenant_id", "policy_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tenant_policies"):
        return

    if _has_index(inspector, "tenant_policies", "ix_tenant_policies_tenant_type"):
        op.drop_index("ix_tenant_policies_tenant_type", table_name="tenant_policies")

    inspector = sa.inspect(bind)
    if _has_index(inspector, "tenant_policies", "ix_tenant_policies_tenant_id"):
        op.drop_index("ix_tenant_policies_tenant_id", table_name="tenant_policies")

    op.drop_table("tenant_policies")
