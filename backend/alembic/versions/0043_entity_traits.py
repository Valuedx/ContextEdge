"""Normalized entity traits (backlog B2).

The deciding dimensions for fix applicability are traits, not CI class
alone: same model, same OS build, same component. These four are the
traits ServiceNow's CMDB actually carries for most CIs — searchable as
first-class columns instead of buried in connector-shaped JSON. Driver
versions and installed software stay OPTIONAL attributes (endpoint-
management data we do not ingest yet — absent traits are absent, never
guessed).

Additive and re-runnable.

Revision ID: 0043_entity_traits
Revises: 0042_entity_classes
Create Date: 2026-08-02 23:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0043_entity_traits"
down_revision: Union[str, None] = "0042_entity_classes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column, ddl_type in (
        ("manufacturer", "VARCHAR(120)"),
        ("model", "VARCHAR(160)"),
        ("os_name", "VARCHAR(80)"),
        ("os_version", "VARCHAR(80)"),
    ):
        op.execute(
            f"ALTER TABLE entities ADD COLUMN IF NOT EXISTS {column} {ddl_type};"
        )
    # The obvious applicability lookup: same-model precedents per tenant.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_entities_tenant_model
        ON entities (tenant_id, model)
        WHERE model IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entities_tenant_model;")
    for column in ("os_version", "os_name", "model", "manufacturer"):
        op.execute(f"ALTER TABLE entities DROP COLUMN IF EXISTS {column};")
