"""Denormalize tenant_id onto child tables and enable FORCE RLS."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


revision = "0078_tenant_owned_children_and_rls"
down_revision = "0077_tenant_isolation_guards"
branch_labels = None
depends_on = None

CHILD_TABLES = (
    "source_credentials",
    "sync_checkpoints",
    "pattern_evidence_links",
    "knowledge_case_steps",
    "attachment_artifacts",
    "playbook_versions",
    "playbook_evidence_links",
    "playbook_approvals",
    "episode_steps",
    "claim_evidence",
    "decision_evidence",
)

BACKFILL_SQL = (
    """
    UPDATE source_credentials c
    SET tenant_id = s.tenant_id
    FROM sources s
    WHERE c.source_id = s.id AND c.tenant_id IS NULL
    """,
    """
    UPDATE sync_checkpoints c
    SET tenant_id = so.tenant_id
    FROM source_objects so
    WHERE c.source_object_id = so.id AND c.tenant_id IS NULL
    """,
    """
    UPDATE pattern_evidence_links pel
    SET tenant_id = p.tenant_id
    FROM patterns p
    WHERE pel.pattern_id = p.id AND pel.tenant_id IS NULL
    """,
    """
    UPDATE knowledge_case_steps s
    SET tenant_id = kc.tenant_id
    FROM knowledge_cases kc
    WHERE s.knowledge_case_id = kc.id AND s.tenant_id IS NULL
    """,
    """
    UPDATE attachment_artifacts a
    SET tenant_id = e.tenant_id
    FROM evidence_items e
    WHERE a.evidence_id = e.id AND a.tenant_id IS NULL
    """,
    """
    UPDATE playbook_versions v
    SET tenant_id = p.tenant_id
    FROM playbooks p
    WHERE v.playbook_id = p.id AND v.tenant_id IS NULL
    """,
    """
    UPDATE playbook_evidence_links l
    SET tenant_id = v.tenant_id
    FROM playbook_versions v
    WHERE l.playbook_version_id = v.id AND l.tenant_id IS NULL
    """,
    """
    UPDATE playbook_approvals a
    SET tenant_id = p.tenant_id
    FROM playbooks p
    WHERE a.playbook_id = p.id AND a.tenant_id IS NULL
    """,
    """
    UPDATE episode_steps s
    SET tenant_id = e.tenant_id
    FROM episodes e
    WHERE s.episode_id = e.id AND s.tenant_id IS NULL
    """,
    """
    UPDATE claim_evidence ce
    SET tenant_id = c.tenant_id
    FROM claims c
    WHERE ce.claim_id = c.id AND ce.tenant_id IS NULL
    """,
    """
    UPDATE decision_evidence de
    SET tenant_id = d.tenant_id
    FROM decisions d
    WHERE de.decision_id = d.id AND de.tenant_id IS NULL
    """,
)


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_fk(insp, table: str, name: str) -> bool:
    return any(fk.get("name") == name for fk in insp.get_foreign_keys(table))


def _has_index(insp, table: str, name: str) -> bool:
    return any(ix.get("name") == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    uuid_type = postgresql.UUID(as_uuid=True)

    for table in CHILD_TABLES:
        if not _has_column(insp, table, "tenant_id"):
            op.add_column(table, sa.Column("tenant_id", uuid_type, nullable=True))
    insp = inspect(conn)

    for stmt in BACKFILL_SQL:
        op.execute(sa.text(stmt))

    if _has_column(insp, "identity_aliases", "tenant_id"):
        op.execute(
            sa.text(
                """
                UPDATE identity_aliases ia
                SET tenant_id = ci.tenant_id
                FROM canonical_identities ci
                WHERE ia.canonical_identity_id = ci.id
                  AND ia.tenant_id IS NULL
                """
            )
        )
        op.execute(sa.text("DELETE FROM identity_aliases WHERE tenant_id IS NULL"))

    for table in CHILD_TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE tenant_id IS NULL"))
        op.alter_column(table, "tenant_id", existing_type=uuid_type, nullable=False)
        fk_name = f"fk_{table}_tenant_id"
        ix_name = f"ix_{table}_tenant_id"
        if not _has_fk(insp, table, fk_name):
            op.create_foreign_key(
                fk_name,
                table,
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if not _has_index(insp, table, ix_name):
            op.create_index(ix_name, table, ["tenant_id"])

    insp = inspect(conn)
    if _has_column(insp, "identity_aliases", "tenant_id"):
        op.alter_column(
            "identity_aliases", "tenant_id", existing_type=uuid_type, nullable=False
        )
        if not _has_fk(insp, "identity_aliases", "fk_identity_aliases_tenant_id"):
            existing = [
                tuple(fk.get("constrained_columns") or [])
                for fk in insp.get_foreign_keys("identity_aliases")
            ]
            if ("tenant_id",) not in existing and ["tenant_id"] not in existing:
                op.create_foreign_key(
                    "fk_identity_aliases_tenant_id",
                    "identity_aliases",
                    "tenants",
                    ["tenant_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

    if _has_column(insp, "sync_runs", "tenant_id") and not _has_fk(
        insp, "sync_runs", "fk_sync_runs_tenant_id"
    ):
        existing = [
            tuple(fk.get("constrained_columns") or [])
            for fk in insp.get_foreign_keys("sync_runs")
        ]
        if ("tenant_id",) not in existing and ["tenant_id"] not in existing:
            op.create_foreign_key(
                "fk_sync_runs_tenant_id",
                "sync_runs",
                "tenants",
                ["tenant_id"],
                ["id"],
                ondelete="CASCADE",
            )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION ce_fill_tenant_id() RETURNS trigger AS $$
            BEGIN
              IF NEW.tenant_id IS NOT NULL THEN
                RETURN NEW;
              END IF;
              IF TG_TABLE_NAME = 'source_credentials' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM sources WHERE id = NEW.source_id;
              ELSIF TG_TABLE_NAME = 'sync_checkpoints' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM source_objects WHERE id = NEW.source_object_id;
              ELSIF TG_TABLE_NAME = 'pattern_evidence_links' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM patterns WHERE id = NEW.pattern_id;
              ELSIF TG_TABLE_NAME = 'knowledge_case_steps' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM knowledge_cases WHERE id = NEW.knowledge_case_id;
              ELSIF TG_TABLE_NAME = 'attachment_artifacts' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM evidence_items WHERE id = NEW.evidence_id;
              ELSIF TG_TABLE_NAME = 'playbook_versions' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM playbooks WHERE id = NEW.playbook_id;
              ELSIF TG_TABLE_NAME = 'playbook_evidence_links' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM playbook_versions WHERE id = NEW.playbook_version_id;
              ELSIF TG_TABLE_NAME = 'playbook_approvals' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM playbooks WHERE id = NEW.playbook_id;
              ELSIF TG_TABLE_NAME = 'episode_steps' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM episodes WHERE id = NEW.episode_id;
              ELSIF TG_TABLE_NAME = 'claim_evidence' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM claims WHERE id = NEW.claim_id;
              ELSIF TG_TABLE_NAME = 'decision_evidence' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM decisions WHERE id = NEW.decision_id;
              ELSIF TG_TABLE_NAME = 'identity_aliases' THEN
                SELECT tenant_id INTO NEW.tenant_id FROM canonical_identities WHERE id = NEW.canonical_identity_id;
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    for table in (*CHILD_TABLES, "identity_aliases"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON {table}"))
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_ce_fill_tenant_id
                BEFORE INSERT ON {table}
                FOR EACH ROW
                EXECUTE FUNCTION ce_fill_tenant_id()
                """
            )
        )

    # Session GUCs so FORCE RLS does not lock the migration itself.
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
              r record;
            BEGIN
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
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
              r record;
            BEGIN
              FOR r IN
                SELECT c.table_name
                FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.column_name = 'tenant_id'
                  AND c.table_name <> 'tenants'
                ORDER BY c.table_name
              LOOP
                EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', r.table_name);
                EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', r.table_name);
                EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', r.table_name);
              END LOOP;
            END
            $$
            """
        )
    )

    for table in (*CHILD_TABLES, "identity_aliases"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS ce_fill_tenant_id()"))

    op.execute(sa.text("ALTER TABLE sync_runs DROP CONSTRAINT IF EXISTS fk_sync_runs_tenant_id"))
    op.execute(
        sa.text(
            "ALTER TABLE identity_aliases DROP CONSTRAINT IF EXISTS fk_identity_aliases_tenant_id"
        )
    )

    for table in CHILD_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_tenant_id"))
        op.execute(sa.text(f"DROP INDEX IF EXISTS ix_{table}_tenant_id"))
        op.drop_column(table, "tenant_id")
