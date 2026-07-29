"""Add HNSW indexes to embedding columns for O(log n) similarity search.

**Historical note (2026-07):** this migration never achieved its goal.
pgvector's HNSW index on the ``vector`` type supports at most 2,000
dimensions and the application stores 3,072 — the original ``CREATE INDEX``
could not succeed, so the upgrade path below now only cleans up invalid
leftovers. Working ANN indexing lands in ``0032_halfvec_hnsw_indexes``
via ``halfvec(3072)`` expression indexes.

Original rationale (kept for context): ``evidence_items.embedding`` and
``decisions.embedding`` are queried with ``ORDER BY cosine_distance``; with
no index every similarity lookup scans the full 3072-dim column linearly.
HNSW (Hierarchical Navigable Small World) gives approximate
nearest-neighbour with ~95% recall at roughly 100× the throughput of a
sequential scan.

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


# pgvector HNSW indexes for the vector type support at most 2000 dimensions.
# The application stores 3072-dimensional embeddings, so exact cosine search
# remains the compatible default until a half-precision/projection index is added.
VECTOR_HNSW_MAX_DIMENSIONS = 2000
EMBEDDING_DIMENSIONS = 3072
HNSW_INDEXES = (
    ("evidence_items", "ix_evidence_items_embedding_hnsw"),
    ("decisions", "ix_decisions_embedding_hnsw"),
)


def _create_hnsw_index(table_name: str, index_name: str) -> None:
    op.execute(
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name}
        ON {table_name}
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
        """
    )


def _drop_unsupported_hnsw_index(index_name: str) -> None:
    # A failed CREATE INDEX CONCURRENTLY can leave an invalid index behind.
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction. Alembic
    # runs migrations inside a transaction by default — we break out with
    # the autocommit connection block.
    with op.get_context().autocommit_block():
        for table_name, index_name in HNSW_INDEXES:
            if EMBEDDING_DIMENSIONS <= VECTOR_HNSW_MAX_DIMENSIONS:
                _create_hnsw_index(table_name, index_name)
            else:
                _drop_unsupported_hnsw_index(index_name)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_decisions_embedding_hnsw;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_items_embedding_hnsw;")
