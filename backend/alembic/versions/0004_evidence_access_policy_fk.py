"""FK from evidence_items.access_policy_id to tenant_policies."""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_evidence_access_policy_fk"
down_revision: Union[str, None] = "0003_source_policy_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_evidence_items_access_policy",
        "evidence_items",
        "tenant_policies",
        ["access_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_evidence_items_access_policy", "evidence_items", type_="foreignkey")
