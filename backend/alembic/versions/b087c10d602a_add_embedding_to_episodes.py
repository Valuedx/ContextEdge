"""Add embedding to episodes

Revision ID: b087c10d602a
Revises: 0020_decision_embedding
Create Date: 2026-04-21 12:59:51.189295
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy

revision: str = 'b087c10d602a'
down_revision: Union[str, None] = '0020_decision_embedding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add missing columns discovered by autogenerate
    op.add_column('episodes', sa.Column('embedding', pgvector.sqlalchemy.VECTOR(dim=3072), nullable=True))
    op.add_column('execution_runs', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))


def downgrade() -> None:
    op.drop_column('execution_runs', 'updated_at')
    op.drop_column('episodes', 'embedding')
