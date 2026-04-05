"""FK from sources to tenant_policies for retention/classification."""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_source_policy_fks"
down_revision: Union[str, None] = "0002_tenant_policies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_sources_classification_policy",
        "sources",
        "tenant_policies",
        ["classification_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sources_retention_policy",
        "sources",
        "tenant_policies",
        ["retention_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sources_retention_policy", "sources", type_="foreignkey")
    op.drop_constraint("fk_sources_classification_policy", "sources", type_="foreignkey")
