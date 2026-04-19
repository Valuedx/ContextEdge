"""Add embedding vector column to decisions.

Upgrades similar-decision retrieval from JSONB-containment (structural —
exact workflow / environment / impacted_dependency match) to semantic.
Paired with inline embedding at `create_decision` and the rewritten
`find_similar_decisions` that orders by cosine distance when a query
embedding is available.

Additive only — existing rows stay `embedding = NULL`. `find_similar_decisions`
falls back to JSONB-containment ordering when a decision has no embedding
or the query has no embedding text, so rolling this migration out doesn't
break callers waiting on re-embedding.

**No vector index is added in this revision.** Matches the current
`evidence_items.embedding` pattern (also unindexed) — the full-table scan
is acceptable at current scale, an HNSW / IVFFlat index is a follow-up
once decision row counts warrant it. See `codewiki/KNOWN_GAPS.md`.

Revision ID: 0020_decision_embedding
Revises: 0019_evidence_baseline
Create Date: 2026-04-19 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "0020_decision_embedding"
down_revision: Union[str, None] = "0019_evidence_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decisions",
        sa.Column("embedding", Vector(3072), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decisions", "embedding")
