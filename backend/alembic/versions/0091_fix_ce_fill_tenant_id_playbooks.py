"""Stop ce_fill_tenant_id from reading NEW.source_id on playbooks.

0084 attached trg_ce_fill_tenant_id to every tenant_id table, including
roots like playbooks. The function then used a single SQL boolean:

    IF TG_TABLE_NAME = 'source_credentials' AND NEW.source_id IS NOT NULL

PL/pgSQL does not short-circuit AND the way Python does, so an UPDATE of
playbooks evaluates NEW.source_id and raises UndefinedColumnError. That is
exactly the bulk-transition 500.

This restores 0078's nested IF (column access only inside a matching
TG_TABLE_NAME branch) and drops the trigger from tables that are not
parent-derived children.

Revision ID: 0091_fix_ce_fill_tenant_id_playbooks
Revises: 0090_ranking_calibration_configs
"""

from alembic import op
import sqlalchemy as sa


revision = "0091_fix_ce_fill_tenant_id_playbooks"
down_revision = "0090_ranking_calibration_configs"
branch_labels = None
depends_on = None

_CHILD_TABLES = (
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
    "identity_aliases",
    "evidence_identity_links",
)

_FUNCTION = """
CREATE OR REPLACE FUNCTION ce_fill_tenant_id() RETURNS trigger AS $$
DECLARE
  derived uuid;
  session_tid uuid;
BEGIN
  IF TG_TABLE_NAME = 'source_credentials' THEN
    IF NEW.source_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM sources WHERE id = NEW.source_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'sync_checkpoints' THEN
    IF NEW.source_object_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM source_objects WHERE id = NEW.source_object_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'pattern_evidence_links' THEN
    IF NEW.pattern_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM patterns WHERE id = NEW.pattern_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'knowledge_case_steps' THEN
    IF NEW.knowledge_case_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM knowledge_cases WHERE id = NEW.knowledge_case_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'attachment_artifacts' THEN
    IF NEW.evidence_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM evidence_items WHERE id = NEW.evidence_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'playbook_versions' THEN
    IF NEW.playbook_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM playbooks WHERE id = NEW.playbook_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'playbook_evidence_links' THEN
    IF NEW.playbook_version_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM playbook_versions WHERE id = NEW.playbook_version_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'playbook_approvals' THEN
    IF NEW.playbook_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM playbooks WHERE id = NEW.playbook_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'episode_steps' THEN
    IF NEW.episode_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM episodes WHERE id = NEW.episode_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'claim_evidence' THEN
    IF NEW.claim_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM claims WHERE id = NEW.claim_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'decision_evidence' THEN
    IF NEW.decision_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM decisions WHERE id = NEW.decision_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'identity_aliases' THEN
    IF NEW.canonical_identity_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM canonical_identities WHERE id = NEW.canonical_identity_id;
    END IF;
  ELSIF TG_TABLE_NAME = 'evidence_identity_links' THEN
    IF NEW.evidence_id IS NOT NULL THEN
      SELECT tenant_id INTO derived FROM evidence_items WHERE id = NEW.evidence_id;
    END IF;
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


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(sa.text(_FUNCTION))
    children = ", ".join(f"'{name}'" for name in _CHILD_TABLES)
    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
              t text;
            BEGIN
              FOR t IN
                SELECT event_object_table
                FROM information_schema.triggers
                WHERE trigger_schema = 'public'
                  AND trigger_name = 'trg_ce_fill_tenant_id'
                  AND event_object_table NOT IN ({children})
              LOOP
                EXECUTE format('DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON %I', t);
              END LOOP;
            END
            $$
            """
        )
    )


def downgrade() -> None:
    # Do not re-attach the unsafe AND NEW.source_id form to playbooks.
    pass
