"""Scope fix_cohort uniqueness to tenant_id."""

from alembic import op


revision = "0081_fix_cohort_tenant_unique"
down_revision = "0080_tenant_fk_on_delete_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_fix_cohort", "fix_cohort_stats", type_="unique")
    op.create_unique_constraint(
        "uq_fix_cohort",
        "fix_cohort_stats",
        ["tenant_id", "fix_pattern_id", "cohort_type", "cohort_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_fix_cohort", "fix_cohort_stats", type_="unique")
    op.create_unique_constraint(
        "uq_fix_cohort",
        "fix_cohort_stats",
        ["fix_pattern_id", "cohort_type", "cohort_key"],
    )
