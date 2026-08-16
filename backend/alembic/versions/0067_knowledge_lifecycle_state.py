"""Respect the source system's knowledge lifecycle.

ServiceNow is the system of record for knowledge: articles are drafted,
reviewed, published and retired there. The connector has fetched
``workflow_state`` on ``kb_knowledge`` since it was written — and the field
appeared exactly once in the whole codebase, in the list of fields to fetch.
It was read from ServiceNow and discarded.

So an unapproved draft, an article in review, and one a human explicitly
retired were all retrieved and cited exactly like a published article. The
citation is what makes it dangerous: it reads as though someone checked.

``knowledge_state`` is nullable and NULL serves. Most knowledge has no
lifecycle at all — a SOP on a file share, an uploaded PDF — and treating
"the source did not say" as "withheld" would empty the corpus for every
source but one.

**No backfill.** Existing rows stay NULL until their next sync re-normalises
them, deliberately: the state lives in the raw payload, and payloads over
32KB are offloaded to object storage as a stub, so a SQL backfill would
silently skip the largest articles — the long ones, which is to say the real
SOPs. A wrong-but-quiet backfill is worse than an honest NULL that the next
sync fixes.

Revision ID: 0067_knowledge_lifecycle_state
Revises: 0066_timestamp_column_parity
"""

from alembic import op

revision = "0067_knowledge_lifecycle_state"
down_revision = "0066_timestamp_column_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN IF NOT EXISTS knowledge_state VARCHAR(20) NULL;
        """
    )
    # Every knowledge query already filters on evidence_type; this index makes
    # the added predicate free on the rows that carry a state at all.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_items_knowledge_state
            ON evidence_items (tenant_id, knowledge_state)
            WHERE knowledge_state IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evidence_items_knowledge_state;")
    op.execute("ALTER TABLE evidence_items DROP COLUMN IF EXISTS knowledge_state;")
