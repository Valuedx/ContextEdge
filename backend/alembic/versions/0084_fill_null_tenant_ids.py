"""Harden tenant_id schema; do not stamp leftover rows with a seed tenant.

This revision never writes tenant_id onto existing rows. New inserts copy
tenant_id from the parent row, or from the request session ``app.tenant_id``.
If any NULL tenant_id remains, upgrade fails instead of filling it.

Revision ID: 0084_fill_null_tenant_ids
Revises: 0083_backup_tables_tenant_id
"""

from alembic import op
import sqlalchemy as sa


revision = "0084_fill_null_tenant_ids"
down_revision = "0083_backup_tables_tenant_id"
branch_labels = None
depends_on = None

_HARDEN = """
DO $$
DECLARE
  t record;
  leftover bigint;
  col_udt text;
BEGIN
  PERFORM set_config('app.bypass_rls', 'on', false);

  FOR t IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema
     AND tb.table_name = c.table_name
    WHERE c.table_schema = 'public'
      AND c.column_name = 'tenant_id'
      AND c.table_name <> 'tenants'
      AND tb.table_type = 'BASE TABLE'
    ORDER BY c.table_name
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM %I WHERE tenant_id IS NULL',
      t.table_name
    ) INTO leftover;
    IF leftover > 0 THEN
      RAISE EXCEPTION
        '% has % NULL tenant_id row(s); refusing to stamp a seed tenant. '
        'Set tenant_id from the parent row or the session tenant.',
        t.table_name, leftover;
    END IF;

    SELECT c.udt_name INTO col_udt
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
      AND c.table_name = t.table_name
      AND c.column_name = 'tenant_id';

    IF col_udt = 'uuid' THEN
      EXECUTE format(
        'ALTER TABLE %I ALTER COLUMN tenant_id SET NOT NULL',
        t.table_name
      );
    END IF;

    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
      WHERE c.contype = 'f'
        AND c.conrelid = format('public.%I', t.table_name)::regclass
        AND c.confrelid = 'public.tenants'::regclass
        AND a.attname = 'tenant_id'
        AND array_length(c.conkey, 1) = 1
    ) THEN
      BEGIN
        EXECUTE format(
          'ALTER TABLE %I ADD CONSTRAINT %I
           FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE',
          t.table_name,
          'fk_' || t.table_name || '_tenant_id'
        );
      EXCEPTION
        WHEN duplicate_object THEN
          NULL;
      END;
    END IF;

    EXECUTE format(
      'CREATE INDEX IF NOT EXISTS %I ON %I (tenant_id)',
      'ix_' || t.table_name || '_tenant_id',
      t.table_name
    );
  END LOOP;
END
$$
"""

_GENERIC_FILL_TRIGGER = """
CREATE OR REPLACE FUNCTION ce_fill_tenant_id() RETURNS trigger AS $$
DECLARE
  derived uuid;
  session_tid uuid;
BEGIN
  IF TG_TABLE_NAME = 'source_credentials' AND NEW.source_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM sources WHERE id = NEW.source_id;
  ELSIF TG_TABLE_NAME = 'sync_checkpoints' AND NEW.source_object_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM source_objects WHERE id = NEW.source_object_id;
  ELSIF TG_TABLE_NAME = 'pattern_evidence_links' AND NEW.pattern_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM patterns WHERE id = NEW.pattern_id;
  ELSIF TG_TABLE_NAME = 'knowledge_case_steps' AND NEW.knowledge_case_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM knowledge_cases WHERE id = NEW.knowledge_case_id;
  ELSIF TG_TABLE_NAME = 'attachment_artifacts' AND NEW.evidence_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM evidence_items WHERE id = NEW.evidence_id;
  ELSIF TG_TABLE_NAME = 'playbook_versions' AND NEW.playbook_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM playbooks WHERE id = NEW.playbook_id;
  ELSIF TG_TABLE_NAME = 'playbook_evidence_links' AND NEW.playbook_version_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM playbook_versions WHERE id = NEW.playbook_version_id;
  ELSIF TG_TABLE_NAME = 'playbook_approvals' AND NEW.playbook_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM playbooks WHERE id = NEW.playbook_id;
  ELSIF TG_TABLE_NAME = 'episode_steps' AND NEW.episode_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM episodes WHERE id = NEW.episode_id;
  ELSIF TG_TABLE_NAME = 'claim_evidence' AND NEW.claim_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM claims WHERE id = NEW.claim_id;
  ELSIF TG_TABLE_NAME = 'decision_evidence' AND NEW.decision_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM decisions WHERE id = NEW.decision_id;
  ELSIF TG_TABLE_NAME = 'identity_aliases' AND NEW.canonical_identity_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM canonical_identities WHERE id = NEW.canonical_identity_id;
  ELSIF TG_TABLE_NAME = 'evidence_identity_links' AND NEW.evidence_id IS NOT NULL THEN
    SELECT tenant_id INTO derived FROM evidence_items WHERE id = NEW.evidence_id;
  END IF;

  IF derived IS NOT NULL THEN
    NEW.tenant_id := derived;
  ELSIF NEW.tenant_id IS NULL THEN
    BEGIN
      session_tid := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      session_tid := NULL;
    END;
    IF session_tid IS NOT NULL THEN
      NEW.tenant_id := session_tid;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_ATTACH_TRIGGERS = """
DO $$
DECLARE
  t record;
BEGIN
  FOR t IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema
     AND tb.table_name = c.table_name
    WHERE c.table_schema = 'public'
      AND c.column_name = 'tenant_id'
      AND c.table_name <> 'tenants'
      AND tb.table_type = 'BASE TABLE'
    ORDER BY c.table_name
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON %I', t.table_name);
    EXECUTE format(
      'CREATE TRIGGER trg_ce_fill_tenant_id
       BEFORE INSERT OR UPDATE ON %I
       FOR EACH ROW
       EXECUTE FUNCTION ce_fill_tenant_id()',
      t.table_name
    );
  END LOOP;
END
$$
"""

_RLS_LOOP = """
DO $$
DECLARE
  r record;
BEGIN
  PERFORM set_config('app.bypass_rls', 'on', false);
  FOR r IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema
     AND tb.table_name = c.table_name
    WHERE c.table_schema = 'public'
      AND c.column_name = 'tenant_id'
      AND c.table_name <> 'tenants'
      AND tb.table_type = 'BASE TABLE'
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


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(sa.text(_HARDEN))
    op.execute(sa.text(_GENERIC_FILL_TRIGGER))
    op.execute(sa.text(_ATTACH_TRIGGERS))
    op.execute(sa.text(_RLS_LOOP))


def downgrade() -> None:
    pass
