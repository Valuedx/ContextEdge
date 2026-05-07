"""Add evidence_chunks sibling table + chunked_at marker on evidence_items.

Adds a per-chunk index alongside ``evidence_items``. Existing FKs that
target ``evidence_items.id`` (attachment_artifacts, correlation_edges,
playbook_evidence_links, contradiction_scan_state, threads, decisions
via decision_evidence, claim_evidence) are unaffected — chunking is a
pure addition.

Why a sibling table rather than splitting EvidenceItem 1:N: the row
identity is load-bearing across the schema and the UI. Card surfaces
keep one EvidenceItem per upstream object; ``evidence_chunks`` is the
high-recall index that vector search hits, with a parent rollup at
read time. Decision in `codewiki/CHUNKING_DESIGN.md`.

Why a chunker_version column: chunkers will evolve (semantic splitting
heuristics, redaction-rule retunes that change boundaries, per-source
parser improvements). The unique key ``(evidence_id, chunk_index,
chunker_version)`` lets a re-chunk write the new version alongside the
old one and atomically swap by updating ``EvidenceItem.chunked_at`` to
the new chunker's run timestamp. Old rows can then be GC'd by a
maintenance task.

HNSW index built ``CONCURRENTLY`` per the 0021 pattern. Chunk-level
HNSW does not require ``pgvector >= 0.5`` again — already established.

Strictly additive. Re-runnable: every CREATE uses ``IF NOT EXISTS``.

Revision ID: 0030_evidence_chunks
Revises: 0029_ae_ops_concept_alignment
Create Date: 2026-05-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0030_evidence_chunks"
down_revision: Union[str, None] = "0029_ae_ops_concept_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. evidence_chunks  (per-chunk index for high-recall vector search)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_chunks (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            chunk_index INT NOT NULL,
            chunk_kind VARCHAR(40) NOT NULL,
            text TEXT NOT NULL,
            char_offset_start INT NULL,
            char_offset_end INT NULL,
            parent_section TEXT NULL,
            embedding vector(3072) NULL,
            content_hash VARCHAR(64) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            chunker_version INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_evidence_chunks_index
                UNIQUE (evidence_id, chunk_index, chunker_version)
        );
        CREATE INDEX IF NOT EXISTS ix_evidence_chunks_tenant_id
            ON evidence_chunks (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_chunks_evidence_id
            ON evidence_chunks (evidence_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_chunks_chunk_kind
            ON evidence_chunks (chunk_kind);
        -- jsonb_path_ops: smaller, faster for the @> containment we use
        -- when filtering by metadata.author / metadata.severity / etc.
        -- Mirrors the choice in 0025_jsonb_gin_indexes.
        CREATE INDEX IF NOT EXISTS ix_evidence_chunks_metadata_gin
            ON evidence_chunks USING gin (metadata jsonb_path_ops);
        -- Hot path for the backfill worker that re-chunks legacy rows.
        CREATE INDEX IF NOT EXISTS ix_evidence_chunks_content_hash
            ON evidence_chunks (content_hash);
        """
    )

    # ------------------------------------------------------------------
    # 2. evidence_items  — chunked_at marker + count
    # ------------------------------------------------------------------
    # ``chunked_at`` is the per-row marker the backfill scanner uses.
    # ``chunk_count`` is observability-only (admin dashboards, drift
    # detection). Defaulting to 0 keeps reads simple — pre-chunk rows
    # advertise zero chunks until the backfill stamps them.
    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN IF NOT EXISTS chunked_at TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0;
        -- Partial index: backfill scans for un-chunked rows. Keeps the
        -- index small as the backfill drains.
        CREATE INDEX IF NOT EXISTS ix_evidence_items_chunked_at_null
            ON evidence_items (tenant_id, ingested_at DESC)
            WHERE chunked_at IS NULL;
        """
    )

    # ------------------------------------------------------------------
    # 3. HNSW on chunk embeddings  (autocommit, like 0021)
    # ------------------------------------------------------------------
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_evidence_chunks_embedding_hnsw
            ON evidence_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_chunks_embedding_hnsw;"
        )
    op.execute(
        """
        DROP INDEX IF EXISTS ix_evidence_items_chunked_at_null;
        ALTER TABLE evidence_items
            DROP COLUMN IF EXISTS chunk_count,
            DROP COLUMN IF EXISTS chunked_at;
        """
    )
    op.execute("DROP TABLE IF EXISTS evidence_chunks;")
