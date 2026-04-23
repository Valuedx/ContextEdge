"""Unique constraints to close two check-then-insert races (F-24, L-02).

Two places in the service layer do ``SELECT ... WHERE ...`` followed
by ``INSERT`` without a DB-level guard against a concurrent inserter
racing the check. Both are flagged in ``REVIEW-2026-04.md``:

- **L-02** — ``workers/extraction_tasks._normalize`` checks for an
  existing ``EvidenceItem`` with the same ``(tenant_id, content_hash)``
  before inserting a new one. The README's Known Constraints section
  acknowledges this: "Evidence dedupe is application-layer and
  hash-based; there is not yet a database uniqueness constraint that
  hard-prevents duplicate ``EvidenceItem`` rows under concurrency."
  This migration adds the missing index.
- **F-24** — ``services/contradiction_service._get_or_create_contradiction``
  does the same pattern against ``(tenant_id, source_a_ref, source_b_ref)``
  on the ``contradictions`` table.

Both indexes are built with ``CREATE UNIQUE INDEX CONCURRENTLY`` so
the migration doesn't block writes. Adding a unique index on columns
that already contain duplicates WILL fail — if an older deployment
has accumulated duplicates under the race window, it must dedupe
the table before running this migration. Dedupe query for each table
is given in the module docstring below and the codewiki.

Service-level code changes (catch IntegrityError, re-SELECT) ship
alongside this migration in the same commit so the race is closed
completely, not just at the DB layer.

## Pre-migration dedupe (run first if duplicates exist)

``evidence_items``::

    WITH dups AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY tenant_id, content_hash ORDER BY ingested_at ASC, id ASC
      ) AS rn
      FROM evidence_items
      WHERE content_hash IS NOT NULL
    )
    DELETE FROM evidence_items WHERE id IN (SELECT id FROM dups WHERE rn > 1);

``contradictions``::

    WITH dups AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY tenant_id, source_a_ref, source_b_ref ORDER BY created_at ASC, id ASC
      ) AS rn
      FROM contradictions
    )
    DELETE FROM contradictions WHERE id IN (SELECT id FROM dups WHERE rn > 1);

Run these as a one-shot before the migration in environments that
have been live under concurrent normalize / scan workloads.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0026_dedup_uniqueness"
down_revision: Union[str, None] = "0025_jsonb_gin_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # L-02: evidence dedup — skip rows with NULL content_hash (very
        # old rows from before the hash was populated would otherwise
        # collide on the NULL "value" and fail to index).
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_evidence_items_tenant_content_hash "
            "ON evidence_items (tenant_id, content_hash) "
            "WHERE content_hash IS NOT NULL"
        )
        # F-24: contradiction dedup.
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_contradictions_tenant_source_pair "
            "ON contradictions (tenant_id, source_a_ref, source_b_ref)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "uq_contradictions_tenant_source_pair"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "uq_evidence_items_tenant_content_hash"
        )
