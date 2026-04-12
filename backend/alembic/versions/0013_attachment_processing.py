"""Add attachment parsing metadata for deterministic artifact extraction.

Revision ID: 0013_attachment_processing
Revises: 0012_evidence_identity_links
Create Date: 2026-04-12 22:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0013_attachment_processing"
down_revision: Union[str, None] = "0012_evidence_identity_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE attachment_artifacts
            ADD COLUMN IF NOT EXISTS parser_type VARCHAR(50),
            ADD COLUMN IF NOT EXISTS parser_confidence FLOAT,
            ADD COLUMN IF NOT EXISTS extraction_error TEXT,
            ADD COLUMN IF NOT EXISTS parser_metadata JSONB,
            ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS ix_attachment_artifacts_evidence_id
            ON attachment_artifacts (evidence_id);
        CREATE INDEX IF NOT EXISTS ix_attachment_artifacts_extraction_status
            ON attachment_artifacts (extraction_status);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attachment_artifacts_extraction_status;")
    op.execute("DROP INDEX IF EXISTS ix_attachment_artifacts_evidence_id;")
    op.execute("ALTER TABLE attachment_artifacts DROP COLUMN IF EXISTS extracted_at;")
    op.execute("ALTER TABLE attachment_artifacts DROP COLUMN IF EXISTS parser_metadata;")
    op.execute("ALTER TABLE attachment_artifacts DROP COLUMN IF EXISTS extraction_error;")
    op.execute("ALTER TABLE attachment_artifacts DROP COLUMN IF EXISTS parser_confidence;")
    op.execute("ALTER TABLE attachment_artifacts DROP COLUMN IF EXISTS parser_type;")
