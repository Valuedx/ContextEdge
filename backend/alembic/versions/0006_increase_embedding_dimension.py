"""Increase embedding dimension to 3072.

Revision ID: 0006_increase_embedding_dimension
Revises: 0005_playbook_version_semantic_unique
Create Date: 2026-04-10 12:22:15.000000

"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '0006_increase_embedding_dimension'
down_revision = '0005_playbook_version_semantic_unique'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the existing 1536-dim column
    op.drop_column('evidence_items', 'embedding')
    # 2. Add the new 3072-dim column
    op.add_column('evidence_items', sa.Column('embedding', Vector(3072), nullable=True))


def downgrade() -> None:
    # 1. Drop the 3072-dim column
    op.drop_column('evidence_items', 'embedding')
    # 2. Revert to 1536-dim column
    op.add_column('evidence_items', sa.Column('embedding', Vector(1536), nullable=True))
