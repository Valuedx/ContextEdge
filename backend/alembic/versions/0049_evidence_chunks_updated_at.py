"""Add updated_at to evidence_chunks.

Revision ID: 0049_evidence_chunks_updated_at
Revises: 0048_fleet_groups
Create Date: 2026-08-03 14:10:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0049_evidence_chunks_updated_at"
down_revision: Union[str, None] = "0048_fleet_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_chunks
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_chunks
            DROP COLUMN IF EXISTS updated_at;
        """
    )
