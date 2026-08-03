"""Playbook embeddings: direct semantic matching for playbook seeds.

Until now playbooks had no embedding column, so the agent seed resolver
could only reach a playbook directly via full-text search on its
title/description (weak for symptom-level language, empty on cold-start
tenants) or indirectly through similar episodes. This adds:

- ``playbooks.embedding vector(3072)`` (nullable — un-embedded playbooks
  simply don't participate in semantic seeds and keep working via FTS).
- A halfvec expression HNSW index matching the 0032 convention, so the
  seed resolver's ANN query is indexed. Requires pgvector >= 0.7, which
  0032 already enforces fail-loud — every environment past 0032 has it.

The embedding text is composed in ``services/playbook_embedding.py`` from
title + description + the current version's trigger conditions and step
titles. (Trigger conditions could not be added to the FTS generated
column instead: generated columns cannot reference other tables, and
trigger conditions live on ``playbook_versions``.)

Additive and re-runnable: ADD COLUMN IF NOT EXISTS; index built
CONCURRENTLY with drop-before-create to heal INVALID leftovers.

Revision ID: 0035_playbook_embeddings
Revises: 0034_execution_run_updated_at
Create Date: 2026-07-31 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0035_playbook_embeddings"
down_revision: Union[str, None] = "0034_execution_run_updated_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 3072
INDEX_NAME = "ix_playbooks_embedding_halfvec_hnsw"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE playbooks
            ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIMENSIONS});
        """
    )
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};")
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY {INDEX_NAME}
            ON playbooks
            USING hnsw ((embedding::halfvec({EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};")
    op.execute("ALTER TABLE playbooks DROP COLUMN IF EXISTS embedding;")
