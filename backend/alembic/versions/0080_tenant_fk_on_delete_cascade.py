"""Recreate every tenant_id FK with ON DELETE CASCADE."""

from alembic import op
import sqlalchemy as sa


revision = "0080_tenant_fk_on_delete_cascade"
down_revision = "0079_tenant_fk_and_rls_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
              r record;
              new_name text;
            BEGIN
              FOR r IN
                SELECT
                  tc.table_schema,
                  tc.table_name,
                  tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                  AND kcu.column_name = 'tenant_id'
                  AND ccu.table_name = 'tenants'
                  AND ccu.column_name = 'id'
              LOOP
                EXECUTE format(
                  'ALTER TABLE %I.%I DROP CONSTRAINT %I',
                  r.table_schema, r.table_name, r.constraint_name
                );
                new_name := left('fk_' || r.table_name || '_tenant_id', 63);
                EXECUTE format(
                  'ALTER TABLE %I.%I ADD CONSTRAINT %I
                   FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE',
                  r.table_schema, r.table_name, new_name
                );
              END LOOP;
            END
            $$
            """
        )
    )


def downgrade() -> None:
    pass
