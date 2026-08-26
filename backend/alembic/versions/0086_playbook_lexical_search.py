"""Maintained playbook lexical search text (N4).

Generated ``search_tsvector`` cannot include version trigger/step text.
This column is populated by the same hook that writes ``Playbook.embedding``.

Revision ID: 0086_playbook_lexical_search
Revises: 0085_playbook_risk_tier_check
"""

from alembic import op
import sqlalchemy as sa


revision = "0086_playbook_lexical_search"
down_revision = "0085_playbook_risk_tier_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.add_column("playbooks", sa.Column("lexical_search_text", sa.Text(), nullable=True))
    # ce_fill_tenant_id() is BEFORE UPDATE on several tables and evaluates
    # NEW.source_id in its first IF. A playbooks row has no such field, so a
    # naive UPDATE aborts with UndefinedColumn. Replica role skips user
    # triggers; tenant_id is already populated.
    op.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
    op.execute(
        sa.text(
            """
            UPDATE playbooks
            SET lexical_search_text = trim(both from coalesce(title, '') || ' ' || coalesce(description, ''))
            WHERE lexical_search_text IS NULL
            """
        )
    )
    op.execute(sa.text("SET LOCAL session_replication_role = 'origin'"))
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_playbooks_lexical_search_gin
            ON playbooks
            USING GIN (to_tsvector('english', coalesce(lexical_search_text, '')))
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_playbooks_lexical_search_gin"))
    op.drop_column("playbooks", "lexical_search_text")
