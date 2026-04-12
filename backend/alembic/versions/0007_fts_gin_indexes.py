"""Add generated tsvector columns and GIN indexes for FTS.

Revision ID: 0007_fts_gin_indexes
Revises: a4ccd43dcf94
Create Date: 2026-04-12 12:20:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_fts_gin_indexes"
down_revision: Union[str, None] = "a4ccd43dcf94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_evidence_items_fts;
        ALTER TABLE evidence_items DROP COLUMN IF EXISTS search_vector;
        ALTER TABLE evidence_items DROP COLUMN IF EXISTS search_tsvector;
        ALTER TABLE evidence_items
            ADD COLUMN search_tsvector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body_text, ''))
            ) STORED;
        CREATE INDEX IF NOT EXISTS ix_evidence_items_fts
            ON evidence_items USING GIN (search_tsvector);
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS ix_playbooks_fts;
        ALTER TABLE playbooks DROP COLUMN IF EXISTS search_tsvector;
        ALTER TABLE playbooks
            ADD COLUMN search_tsvector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))
            ) STORED;
        CREATE INDEX IF NOT EXISTS ix_playbooks_fts
            ON playbooks USING GIN (search_tsvector);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_playbooks_fts;")
    op.execute("ALTER TABLE playbooks DROP COLUMN IF EXISTS search_tsvector;")
    op.execute("DROP INDEX IF EXISTS ix_evidence_items_fts;")
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS search_tsvector;")
    op.execute("ALTER TABLE evidence_items ADD COLUMN IF NOT EXISTS search_vector TEXT;")
