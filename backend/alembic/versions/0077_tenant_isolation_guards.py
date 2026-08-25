"""Per-tenant usernames and evidence FK on pattern_evidence_links."""

from alembic import op
import sqlalchemy as sa


revision = "0077_tenant_isolation_guards"
down_revision = "0076_role_nav_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_unique_constraint(
        "uq_users_tenant_username", "users", ["tenant_id", "username"]
    )

    op.execute(
        sa.text(
            """
            UPDATE pattern_evidence_links pel
            SET evidence_id = NULL
            WHERE evidence_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM evidence_items ei WHERE ei.id = pel.evidence_id
              )
            """
        )
    )
    op.create_foreign_key(
        "fk_pattern_evidence_links_evidence_id",
        "pattern_evidence_links",
        "evidence_items",
        ["evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pattern_evidence_links_evidence_id",
        "pattern_evidence_links",
        type_="foreignkey",
    )
    op.drop_constraint("uq_users_tenant_username", "users", type_="unique")
    op.drop_index("ix_users_username", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
