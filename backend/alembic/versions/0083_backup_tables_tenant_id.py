"""Schema-only: tenant_id + RLS on leftover episode backup tables if present.

Does not stamp a seed tenant. If a backup table exists, tenant_id is copied
from the parent episode row only. Fresh installs without these tables skip
this revision's table work.

Revision ID: 0083_backup_tables_tenant_id
Revises: 0082_composite_tenant_fks_and_trigger
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "0083_backup_tables_tenant_id"
down_revision = "0082_composite_tenant_fks_and_trigger"
branch_labels = None
depends_on = None

STEP_BACKUPS = (
    "episode_steps_stacked_backup",
    "episode_steps_shapeb_backup",
    "episode_steps_knowledge_migrated_backup",
)

_RLS_LOOP = """
DO $$
DECLARE
  r record;
BEGIN
  PERFORM set_config('app.bypass_rls', 'on', false);
  FOR r IN
    SELECT c.table_name
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
      AND c.column_name = 'tenant_id'
      AND c.table_name <> 'tenants'
    ORDER BY c.table_name
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', r.table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', r.table_name);
    EXECUTE format(
      $p$
      CREATE POLICY tenant_isolation ON %I
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
      $p$,
      r.table_name
    );
  END LOOP;
END
$$
"""


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_fk_on_tenant_id(insp, table: str) -> bool:
    for fk in insp.get_foreign_keys(table):
        cols = fk.get("constrained_columns") or []
        referred = fk.get("referred_table")
        if list(cols) == ["tenant_id"] and referred == "tenants":
            return True
    return False


def _copy_tenant_id_from_parent_episode(bind, table: str) -> None:
    bind.execute(
        sa.text(
            f"""
            UPDATE {table} b
            SET tenant_id = e.tenant_id
            FROM episodes e
            WHERE b.episode_id = e.id
              AND b.tenant_id IS NULL
            """
        )
    )
    remaining = bind.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{table} has {remaining} rows with NULL tenant_id; "
            "refusing to stamp a seed tenant. Copy tenant_id from the parent "
            "episode or delete orphan backup rows."
        )


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    uuid_type = postgresql.UUID(as_uuid=True)

    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    for table in STEP_BACKUPS:
        if not insp.has_table(table):
            continue
        if not _has_column(insp, table, "episode_id"):
            raise RuntimeError(f"{table} has no episode_id; cannot derive tenant_id")
        if not _has_column(insp, table, "tenant_id"):
            op.add_column(table, sa.Column("tenant_id", uuid_type, nullable=True))
            insp = inspect(bind)
        _copy_tenant_id_from_parent_episode(bind, table)
        op.alter_column(table, "tenant_id", existing_type=uuid_type, nullable=False)
        if not _has_fk_on_tenant_id(insp, table):
            op.create_foreign_key(
                f"fk_{table}_tenant_id",
                table,
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="CASCADE",
            )
        op.execute(
            sa.text(f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)")
        )
        insp = inspect(bind)

    if insp.has_table("episodes_knowledge_migrated_backup") and _has_column(
        insp, "episodes_knowledge_migrated_backup", "tenant_id"
    ):
        bind.execute(
            sa.text(
                """
                UPDATE episodes_knowledge_migrated_backup b
                SET tenant_id = e.tenant_id
                FROM episodes e
                WHERE b.id = e.id
                  AND b.tenant_id IS NULL
                """
            )
        )
        remaining = bind.execute(
            sa.text(
                "SELECT count(*) FROM episodes_knowledge_migrated_backup WHERE tenant_id IS NULL"
            )
        ).scalar_one()
        if remaining:
            raise RuntimeError(
                "episodes_knowledge_migrated_backup has "
                f"{remaining} NULL tenant_id rows; refusing to stamp a seed tenant"
            )
        op.alter_column(
            "episodes_knowledge_migrated_backup",
            "tenant_id",
            existing_type=uuid_type,
            nullable=False,
        )
        if not _has_fk_on_tenant_id(insp, "episodes_knowledge_migrated_backup"):
            op.create_foreign_key(
                "fk_episodes_knowledge_migrated_backup_tenant_id",
                "episodes_knowledge_migrated_backup",
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="CASCADE",
            )

    op.execute(sa.text(_RLS_LOOP))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    if insp.has_table("episodes_knowledge_migrated_backup"):
        op.execute(
            sa.text(
                "ALTER TABLE episodes_knowledge_migrated_backup "
                "DROP CONSTRAINT IF EXISTS fk_episodes_knowledge_migrated_backup_tenant_id"
            )
        )
        op.alter_column(
            "episodes_knowledge_migrated_backup",
            "tenant_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )

    for table in STEP_BACKUPS:
        if not insp.has_table(table):
            continue
        op.execute(sa.text(f"DROP INDEX IF EXISTS ix_{table}_tenant_id"))
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_tenant_id"))
        if _has_column(insp, table, "tenant_id"):
            op.drop_column(table, "tenant_id")
        insp = inspect(bind)
