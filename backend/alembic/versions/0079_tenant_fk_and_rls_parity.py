"""FK parity for audit_logs/notifications; re-assert RLS on all tenant tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0079_tenant_fk_and_rls_parity"
down_revision = "0078_tenant_owned_children_and_rls"
branch_labels = None
depends_on = None


def _has_fk_on_tenant_id(insp, table: str) -> bool:
    for fk in insp.get_foreign_keys(table):
        cols = fk.get("constrained_columns") or []
        referred = fk.get("referred_table")
        if list(cols) == ["tenant_id"] and referred == "tenants":
            return True
    return False


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    if insp.has_table("audit_logs"):
        op.execute(
            sa.text(
                """
                DELETE FROM audit_logs a
                WHERE NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id = a.tenant_id)
                """
            )
        )
        if not _has_fk_on_tenant_id(insp, "audit_logs"):
            op.create_foreign_key(
                "fk_audit_logs_tenant_id",
                "audit_logs",
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="CASCADE",
            )

    if insp.has_table("notifications") and not _has_fk_on_tenant_id(insp, "notifications"):
        op.execute(
            sa.text(
                """
                DELETE FROM notifications n
                WHERE NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id = n.tenant_id)
                """
            )
        )
        op.create_foreign_key(
            "fk_notifications_tenant_id",
            "notifications",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.execute(
        sa.text(
            """
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
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS fk_audit_logs_tenant_id"))
    op.execute(
        sa.text(
            "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS fk_notifications_tenant_id"
        )
    )
