"""GIN indexes on JSONB hot spots called out in the architecture review.

``ENTERPRISE_ARCHITECTURE_REVIEW.md`` §5 "Architectural concerns —
watch list" flagged two JSONB columns that are flexible today but
will start paying full-scan cost the moment a caller filters by
them. This revision adds the two indexes the review recommends,
both built with ``CREATE INDEX CONCURRENTLY`` so they never block
writes. Other JSONB columns (``context_snapshot``, ``evidence_summary``,
``baseline_ref``, ``modification_diff``, …) are intentionally left
un-indexed until a specific filter path shows up — GIN indexes are
not free on insert, so we only add them once a real query justifies
the write-amplification cost.

1. **``graph_edges.metadata_extra``** — graph queries today filter by
   ``(source_node_type, source_node_id)``. Richer traversals like
   "find edges where ``metadata.reason = X``" or "edges whose
   ``metadata_extra`` contains this tag" will soon land; without a
   GIN they're table-scans.

2. **``evidence_items.canonical_entity_refs``** — used by identity
   resolution, decision extraction, and correlation. Any "find
   evidence mentioning this canonical entity" path hits this blob.
   Indexing the whole column rather than the ``->'identities'``
   sub-path keeps the rule simple and also accelerates any future
   filter against ``->'decisions'``.

Both indexes use ``jsonb_path_ops`` rather than the default
``jsonb_ops``: smaller on disk, faster for the containment
operators (``@>``) we actually query with. If a future caller needs
key-exists (``?`` / ``?&`` / ``?|``), either add a second
``jsonb_ops`` index or switch the existing one. Don't silently rely
on the ops class from the migration-review diff alone — check the
query plan.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0025_jsonb_gin_indexes"
down_revision: Union[str, None] = "0024_evidence_scale_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_graph_edges_metadata_extra_gin "
            "ON graph_edges USING GIN (metadata_extra jsonb_path_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_evidence_items_canonical_entity_refs_gin "
            "ON evidence_items USING GIN (canonical_entity_refs jsonb_path_ops)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_evidence_items_canonical_entity_refs_gin"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_graph_edges_metadata_extra_gin"
        )
