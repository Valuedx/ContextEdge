"""Scale-ready partial + BRIN indexes on evidence_items.

Scoped-down deliverable for ``ENTERPRISE_ARCHITECTURE_REVIEW.md`` §6
item 12 (evidence-table partitioning). Full partition-conversion is
deferred to a separate operational runbook — converting a live table
requires knowing the customer's actual row count / growth rate /
tolerance for a maintenance window, and the DDL involved is
irreversible without a carefully staged cutover. Rather than shipping
a one-shot migration that couldn't be validated in advance, this
revision delivers the indexes that give the biggest wins *without*
partitioning so the hot paths stay cheap as the table grows:

1. **BRIN on ``(tenant_id, ingested_at)``** — append-mostly tables
   with a time-correlated insert order get excellent range-scan
   performance from BRIN at ~1 KB storage for millions of rows.
   Retention archive / drift / admin cost aggregations all filter
   by tenant + time-window; without this index they're table-scans.

2. **Partial B-tree on ``(tenant_id, relevance_state) WHERE
   relevance_state IN ('relevant', 'unclassified')``** — reviewer
   queue hot path. Most evidence eventually sits in ``archived`` or
   ``not_relevant`` states, but the queue only cares about the
   actionable tail. A partial index keeps index size proportional to
   the queue depth, not the total table.

3. **Partial B-tree on ``(tenant_id, updated_at) WHERE
   relevance_state = 'archived'``** — retention purge hot path (see
   ``purge_archived_evidence``). Without this the purge sweep
   re-reads every row of an ever-growing table; the partial keeps it
   proportional to the purge backlog.

All three are built with ``CREATE INDEX CONCURRENTLY`` (no table
lock). On fresh deploys they're instant; on backfilled deploys they
may take minutes but never block queries.

The full partition plan — partition keys, parent / child DDL, swap
cutover runbook — lives in ``codewiki/04-evidence-normalization-and-
storage.md`` under "Partitioning plan (deferred)".
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0024_evidence_scale_indexes"
down_revision: Union[str, None] = "0023_tenant_llm_budgets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BRIN needs autocommit — alembic runs DDL in a transaction by
    # default, so we disable for CONCURRENTLY and explicit COMMITs.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_evidence_items_tenant_ingested_brin "
            "ON evidence_items USING BRIN (tenant_id, ingested_at) "
            "WITH (pages_per_range = 32)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_evidence_items_tenant_review_queue "
            "ON evidence_items (tenant_id, relevance_state) "
            "WHERE relevance_state IN ('relevant', 'unclassified')"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_evidence_items_tenant_archived_updated "
            "ON evidence_items (tenant_id, updated_at) "
            "WHERE relevance_state = 'archived'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_items_tenant_archived_updated")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_items_tenant_review_queue")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_evidence_items_tenant_ingested_brin")
