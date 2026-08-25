"""Composite tenant FKs, unique (tenant_id, id), and force tenant_id from parent."""

from alembic import op
import sqlalchemy as sa


revision = "0082_composite_tenant_fks_and_trigger"
down_revision = "0081_fix_cohort_tenant_unique"
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
              cname text;
            BEGIN
              FOR r IN
                SELECT t.table_name
                FROM information_schema.tables t
                JOIN information_schema.columns tid
                  ON tid.table_schema = t.table_schema
                 AND tid.table_name = t.table_name
                 AND tid.column_name = 'tenant_id'
                JOIN information_schema.columns idc
                  ON idc.table_schema = t.table_schema
                 AND idc.table_name = t.table_name
                 AND idc.column_name = 'id'
                WHERE t.table_schema = 'public'
                  AND t.table_type = 'BASE TABLE'
                  AND t.table_name <> 'tenants'
              LOOP
                cname := left('uq_' || r.table_name || '_tenant_id_id', 63);
                BEGIN
                  EXECUTE format(
                    'ALTER TABLE %I ADD CONSTRAINT %I UNIQUE (tenant_id, id)',
                    r.table_name, cname
                  );
                EXCEPTION
                  WHEN duplicate_object OR duplicate_table THEN
                    NULL;
                END;
              END LOOP;

              FOR r IN
                SELECT
                  tc.table_name AS child_table,
                  kcu.column_name AS child_column,
                  ccu.table_name AS parent_table,
                  rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                JOIN information_schema.referential_constraints rc
                  ON rc.constraint_name = tc.constraint_name
                 AND rc.constraint_schema = tc.table_schema
                WHERE tc.table_schema = 'public'
                  AND tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.column_name = 'id'
                  AND kcu.column_name <> 'tenant_id'
                  AND EXISTS (
                    SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = 'public'
                      AND c.table_name = tc.table_name
                      AND c.column_name = 'tenant_id'
                  )
                  AND EXISTS (
                    SELECT 1 FROM information_schema.columns p
                    WHERE p.table_schema = 'public'
                      AND p.table_name = ccu.table_name
                      AND p.column_name = 'tenant_id'
                  )
              LOOP
                cname := left('fk_tt_' || r.child_table || '_' || r.child_column, 63);
                BEGIN
                  EXECUTE format(
                    'ALTER TABLE %I ADD CONSTRAINT %I
                     FOREIGN KEY (tenant_id, %I)
                     REFERENCES %I (tenant_id, id)
                     ON DELETE %s',
                    r.child_table,
                    cname,
                    r.child_column,
                    r.parent_table,
                    CASE r.delete_rule
                      WHEN 'CASCADE' THEN 'CASCADE'
                      WHEN 'RESTRICT' THEN 'RESTRICT'
                      ELSE 'NO ACTION'
                    END
                  );
                EXCEPTION
                  WHEN duplicate_object OR duplicate_table THEN
                    NULL;
                END;
              END LOOP;
            END
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION ce_fill_tenant_id() RETURNS trigger AS $$
            DECLARE
              derived uuid;
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
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
              t text;
            BEGIN
              FOREACH t IN ARRAY ARRAY[
                'source_credentials','sync_checkpoints','pattern_evidence_links',
                'knowledge_case_steps','attachment_artifacts','playbook_versions',
                'playbook_evidence_links','playbook_approvals','episode_steps',
                'claim_evidence','decision_evidence','identity_aliases',
                'evidence_identity_links'
              ]
              LOOP
                EXECUTE format('DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON %I', t);
                EXECUTE format(
                  'CREATE TRIGGER trg_ce_fill_tenant_id
                   BEFORE INSERT OR UPDATE ON %I
                   FOR EACH ROW
                   EXECUTE FUNCTION ce_fill_tenant_id()',
                  t
                );
              END LOOP;
            END
            $$
            """
        )
    )


def downgrade() -> None:
    pass
