"""Persist extracted knowledge applicability on evidence items.

Where a knowledge article applies — component, deployment model,
environment, version range — is read once by a model at ingest and kept
here. It is not recomputed per retrieval: that would be one LLM call per
candidate article per playbook generation, which is the cost argument
that kept this lexical and wrong.

Nullable, because every article ingested before this migration has no
extraction yet, and retrieval falls back to the lexical extractor when
the column is empty. Backfill is opportunistic rather than required.

Revision ID: 0051
Revises: 0050
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051_evidence_applicability"
down_revision = "0050_playbook_version_conflicts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evidence_items",
        sa.Column("applicability", postgresql.JSONB(), nullable=True),
    )
    # Partial: only knowledge evidence ever carries this, and it is read
    # to find the documents still needing extraction.
    op.create_index(
        "ix_evidence_items_applicability_pending",
        "evidence_items",
        ["tenant_id", "evidence_type"],
        unique=False,
        postgresql_where=sa.text("applicability IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_items_applicability_pending", table_name="evidence_items"
    )
    op.drop_column("evidence_items", "applicability")
