"""Trigram index for identity candidate generation.

Candidates were found with ``LIKE %token%`` over the three longest
tokens of a name, capped at five rows ordered ALPHABETICALLY. The LLM
adjudicator only ever judges what that query returns, so the query is the
real ceiling on resolution quality — and on the live tenant it decided 7
of 204 links.

Replaying every existing identity name as an incoming mention: the LIKE
query returned candidates for 33% of them, a trigram query for 52%.
"agents" had NO candidates and so forked from "agent" silently, which is
the failure mode that fills the provisional pile.

The alphabetical ordering was the second half of the problem. With more
matches than the limit, the five kept were whichever sorted first, not
whichever were closest — so the right answer could be present in the
table, match the filter, and still never reach the model.

Revision ID: 0053
Revises: 0052
"""

from alembic import op

revision = "0053_identity_trigram_index"
down_revision = "0052_identity_merge_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS because a deployment may already have it (this one
    # did) and because creating it needs privileges a migration may not
    # have. The service degrades to the previous LIKE behaviour when the
    # operator is unavailable, so a failure here is not fatal to
    # resolution — but the index is what keeps it affordable at scale.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_canonical_identities_name_trgm "
        "ON canonical_identities USING gin (normalized_name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_canonical_identities_name_trgm")
    # The extension is left in place: other objects may depend on it, and
    # dropping a shared extension to reverse one index is not a trade a
    # downgrade should make.
