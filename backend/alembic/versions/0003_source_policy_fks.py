"""FK from sources to tenant_policies for retention/classification."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_source_policy_fks"
down_revision: str | None = "0002_tenant_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_action(value: str | None) -> str | None:
    return value.upper() if value is not None else None


def _has_foreign_key(
    inspector: sa.Inspector,
    table_name: str,
    constrained_columns: list[str],
    referred_table: str,
    referred_columns: list[str],
    ondelete: str | None = None,
) -> bool:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("constrained_columns") != constrained_columns:
            continue
        if foreign_key.get("referred_table") != referred_table:
            continue
        if foreign_key.get("referred_columns") != referred_columns:
            continue

        options = foreign_key.get("options") or {}
        if _normalize_action(options.get("ondelete")) != _normalize_action(ondelete):
            continue
        return True
    return False


def _has_named_foreign_key(
    inspector: sa.Inspector, table_name: str, constraint_name: str
) -> bool:
    return any(
        foreign_key.get("name") == constraint_name
        for foreign_key in inspector.get_foreign_keys(table_name)
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not _has_foreign_key(
        inspector,
        "sources",
        ["classification_policy_id"],
        "tenant_policies",
        ["id"],
        ondelete="SET NULL",
    ):
        op.create_foreign_key(
            "fk_sources_classification_policy",
            "sources",
            "tenant_policies",
            ["classification_policy_id"],
            ["id"],
            ondelete="SET NULL",
        )

    inspector = sa.inspect(op.get_bind())
    if not _has_foreign_key(
        inspector,
        "sources",
        ["retention_policy_id"],
        "tenant_policies",
        ["id"],
        ondelete="SET NULL",
    ):
        op.create_foreign_key(
            "fk_sources_retention_policy",
            "sources",
            "tenant_policies",
            ["retention_policy_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _has_named_foreign_key(inspector, "sources", "fk_sources_retention_policy"):
        op.drop_constraint("fk_sources_retention_policy", "sources", type_="foreignkey")

    inspector = sa.inspect(op.get_bind())
    if _has_named_foreign_key(inspector, "sources", "fk_sources_classification_policy"):
        op.drop_constraint("fk_sources_classification_policy", "sources", type_="foreignkey")
