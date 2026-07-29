"""Real ANN indexing for 3072-dim embeddings via halfvec expression HNSW.

History: migration ``0021`` (and ``0030`` for chunks) originally tried to
build HNSW indexes directly on the ``vector(3072)`` columns. pgvector's
HNSW supports at most 2,000 dimensions for the ``vector`` type, so those
indexes never existed — the migrations were later rewritten to drop any
invalid leftovers, leaving every similarity query a sequential scan.

This migration adds the standard pgvector answer for large embeddings:
HNSW *expression* indexes over ``(embedding::halfvec(3072))``. ``halfvec``
(pgvector >= 0.7 server extension) supports up to 4,000 dimensions at half
precision; recall loss versus full precision is negligible for cosine
ordering. Query side: every similarity query must order by the same
expression — see ``contextedge/search/vector_ops.py::halfvec_cosine_distance``,
which all call sites now route through.

Safety / re-run notes:

- **Requires pgvector server extension >= 0.7** (the ``halfvec`` type).
  The query side casts to halfvec unconditionally, so allowing this
  migration to succeed on an older extension would leave every semantic
  search 500ing at runtime — the migration fails loud instead, matching
  the FERNET/JWT startup-guard posture. Upgrade with
  ``ALTER EXTENSION vector UPDATE`` and re-run.
- Each target index is dropped (CONCURRENTLY IF EXISTS) before creation:
  a mid-build failure leaves an INVALID index that a bare
  ``IF NOT EXISTS`` re-run would silently keep forever.
- Also drops the three legacy invalid-index names for environments that
  ran the original 0021/0030 text (a failed CREATE INDEX CONCURRENTLY
  leaves an INVALID index behind); those environments never executed the
  rewritten cleanup because 0021/0030 were already stamped.
- Offline mode (``alembic upgrade --sql``) cannot probe the extension
  version; the DDL is emitted unconditionally with the requirement noted.
- Environments that already stamped an earlier revision of this file (the
  graceful-no-op variant) on pgvector < 0.7 will NOT re-execute it —
  their search stays broken until the extension is upgraded and the index
  DDL below is applied manually (or via alembic downgrade/upgrade of this
  revision).

Revision ID: 0032_halfvec_hnsw_indexes
Revises: 0031_maf_context_graph_hardening
Create Date: 2026-07-29 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0032_halfvec_hnsw_indexes"
down_revision: Union[str, None] = "0031_maf_context_graph_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 3072

HALFVEC_INDEXES = (
    ("evidence_items", "ix_evidence_items_embedding_halfvec_hnsw"),
    ("evidence_chunks", "ix_evidence_chunks_embedding_halfvec_hnsw"),
    ("decisions", "ix_decisions_embedding_halfvec_hnsw"),
    ("episodes", "ix_episodes_embedding_halfvec_hnsw"),
)

# Invalid leftovers from the original (pre-rewrite) 0021/0030 text.
LEGACY_INVALID_INDEXES = (
    "ix_evidence_items_embedding_hnsw",
    "ix_decisions_embedding_hnsw",
    "ix_evidence_chunks_embedding_hnsw",
)


def _halfvec_supported() -> bool:
    """halfvec landed in pgvector 0.7.0 (server extension version)."""
    row = op.get_bind().execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).first()
    if row is None or not row[0]:
        return False
    parts = str(row[0]).split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False
    return (major, minor) >= (0, 7)


def upgrade() -> None:
    from alembic import context

    if not context.is_offline_mode() and not _halfvec_supported():
        raise RuntimeError(
            "0032_halfvec_hnsw_indexes requires pgvector server extension "
            ">= 0.7 (the halfvec type). The application's similarity "
            "queries cast to halfvec unconditionally, so proceeding would "
            "break every semantic search at runtime. Upgrade with "
            "'ALTER EXTENSION vector UPDATE' and re-run the migration."
        )

    with op.get_context().autocommit_block():
        for index_name in LEGACY_INVALID_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")
        for table_name, index_name in HALFVEC_INDEXES:
            # Drop-before-create heals an INVALID leftover from a previous
            # interrupted build; IF NOT EXISTS alone would keep it forever.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")
            op.execute(
                f"""
                CREATE INDEX CONCURRENTLY {index_name}
                ON {table_name}
                USING hnsw ((embedding::halfvec({EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
                WITH (m = 16, ef_construction = 64);
                """
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for _table_name, index_name in reversed(HALFVEC_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")
