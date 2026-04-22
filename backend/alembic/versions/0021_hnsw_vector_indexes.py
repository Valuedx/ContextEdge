"""Add HNSW indexes to embedding columns for O(log n) similarity search.

Before this revision, ``evidence_items.embedding`` and ``decisions.embedding``
are queried with ``ORDER BY cosine_distance`` and no index — meaning every
similarity lookup scans the full 3072-dim column linearly. At 3.65M rows
that's ~45 GB of embedding bytes scanned per query. HNSW (Hierarchical
Navigable Small World) gives approximate nearest-neighbour with ~95%
recall at roughly 100× the throughput of a sequential scan.

Parameters chosen:
- ``m = 16`` — number of bidirectional links per node. pgvector's default.
  Higher values improve recall at the cost of build time and index size.
- ``ef_construction = 64`` — candidate list size during build. pgvector's
  default. Higher = better recall, longer build time.

Index build uses ``CONCURRENTLY`` so it does not lock the table while
building. On a fresh deployment the indexes build almost instantly; on a
backfilled deployment the ``CREATE INDEX CONCURRENTLY`` can take minutes
to hours depending on row count.

Query-time recall vs. latency is tunable at runtime with
``SET LOCAL hnsw.ef_search = <n>`` before the query. The default (40) is
fine for the demo's similar-decision retrieval.

Requires ``pgvector >= 0.5.0`` (HNSW landed in 0.5.0). The project's
``pyproject.toml`` is bumped to ``pgvector>=0.5`` in the same change.

Revision ID: 0021_hnsw_vector_indexes
Revises: 0020_decision_embedding
Create Date: 2026-04-22 11:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021_hnsw_vector_indexes"
down_revision: Union[str, None] = "0020_decision_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction. Alembic
    # runs migrations inside a transaction by default — we break out with
    # the autocommit connection block.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_evidence_items_embedding_hnsw
            ON evidence_items
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_decisions_embedding_hnsw
            ON decisions
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_decisions_embedding_hnsw;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_items_embedding_hnsw;")
