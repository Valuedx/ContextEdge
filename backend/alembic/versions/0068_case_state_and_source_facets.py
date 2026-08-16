"""Record what the source already states about a case.

Two columns on `evidence_items`, both filled deterministically at ingest and
both replacing something the pipeline was otherwise inferring or missing.

**`case_state`** — `resolved` when the source says a fix landed (Zoho
`Closed` / `Resolved By Agent` / `Resolved By Plugin Team`, ServiceNow 6/7),
`cancelled` when the case ended without one, NULL while it runs. The
resolution gate's own docstring lists "structural: closed/resolved status
vocabulary" as its first tier; it was never structural — it read the ticket's
TEXT with a regex that includes a literal `resolved by agent` alternation,
which is Zoho's *status value* being matched as prose.

`cancelled` is deliberately not `resolved`: the case is over and there is no
fix in it, and episode synthesis — 29% of all tokens on this tenant, with 71%
of its output later superseded — is exactly what should not run on one.

**`source_facets`** — the structured labels the source already carries. On
this tenant's resolved Zoho tickets, 84% state a root cause from an
eight-value taxonomy (`cf_rca`), the environment, and the product version.
None of it reached anything: the values sat in the raw payload while
`knowledge_applicability` extracted environment and version from prose at
~7,200 tokens a call. A stated value now wins over an inferred one and skips
that call.

The mapping is per-source config (`facet_fields`), never a table in the code:
every deployment names its custom fields differently, and a source with no
mapping produces no facets and behaves exactly as before.

Both are refreshed on re-ingest. The content hash covers the BODY, and
neither resolving a ticket nor typing its root cause rewrites the
description — so a state or facet change lands on the existing row rather
than creating a new one.

Revision ID: 0068_case_state_and_source_facets
Revises: 0067_knowledge_lifecycle_state
"""

from alembic import op

revision = "0068_case_state_and_source_facets"
down_revision = "0067_knowledge_lifecycle_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN IF NOT EXISTS case_state VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS source_facets JSONB NOT NULL DEFAULT '{}'::jsonb;
        """
    )
    # Partial: only a minority of evidence ever carries a terminal state, and
    # the resolution gate asks for exactly those.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_items_case_state
            ON evidence_items (tenant_id, case_state)
            WHERE case_state IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evidence_items_case_state;")
    op.execute(
        """
        ALTER TABLE evidence_items
            DROP COLUMN IF EXISTS source_facets,
            DROP COLUMN IF EXISTS case_state;
        """
    )
