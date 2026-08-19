"""Knowledge cases as a first-class object, and an evidence ledger for patterns.

A KB article reconstructed into an `episode` asserts that something
happened. It did not: a document claims a resolution works. Everything
downstream then reads that claim as an observation — the playbook prompt
treats episode outcomes as empirical evidence a step works, patterns count
them as recurrence, the agent cites them as [ep-N].

Two tables:

`knowledge_cases` (+ `knowledge_case_steps`) hold what a source SAYS,
carrying the same reconstructed semantics an episode does — symptoms,
causes, actions, entities, applicability — because that reconstruction is
valuable and is often the only structured description of a failure mode
nobody has hit yet. A separate table rather than an `episodes.kind`
column: a discriminator makes every existing query that counts, clusters,
scores, reviews or cites episodes silently wrong until someone remembers
`AND kind = 'observed'`, and one forgotten predicate recreates the
contamination. A missing join is a loud failure; a missing predicate is a
quiet one.

`pattern_evidence` records what each piece of evidence contributes to a
pattern and on what footing, replacing a bare episode_count that cannot
tell three KB articles from nineteen resolved incidents. That enables
cold start (a pattern supported only by documentation, which graduates as
incidents arrive) and knowledge-drift detection (a documented resolution
accumulating contradictions from recent episodes while the article stays
approved upstream).

A CHECK constraint carries the invariant the split exists for: only an
episode may be `empirical`, and only empirical rows may carry an
`outcome`. Enforced in the database because that is the one place a
future code path cannot forget it.

No data is migrated here — that is 0073, so the schema can land and be
reviewed on its own.

Revision ID: 0072_knowledge_case_and_pattern_evidence
Revises: 0071_episode_step_uniqueness
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0072_knowledge_case_and_pattern_evidence"
down_revision = "0071_episode_step_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domains.id"),
            nullable=True,
        ),
        sa.Column(
            "source_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id"),
            nullable=False,
        ),
        sa.Column(
            "source_kind", sa.String(40), server_default="kb_article", nullable=False
        ),
        sa.Column(
            "source_authority",
            sa.String(40),
            server_default="internal_kb",
            nullable=False,
        ),
        sa.Column("source_state", sa.String(40), nullable=True),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("symptom_summary", sa.Text(), nullable=True),
        sa.Column("documented_cause", sa.Text(), nullable=True),
        sa.Column("documented_resolution", sa.Text(), nullable=True),
        sa.Column("validation_guidance", sa.Text(), nullable=True),
        sa.Column("entity_refs", postgresql.JSONB(), nullable=True),
        sa.Column("applicability", postgresql.JSONB(), nullable=True),
        sa.Column(
            "extraction_confidence", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column("embedding", postgresql.ARRAY(sa.Float), nullable=True),
        sa.Column("generation_provenance", postgresql.JSONB(), nullable=True),
        sa.Column(
            "migrated_from_episode_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # The embedding column is declared as a real pgvector type rather than
    # the ARRAY placeholder above, matching episodes.embedding so the same
    # halfvec distance operators apply.
    op.execute("ALTER TABLE knowledge_cases DROP COLUMN embedding")
    op.execute("ALTER TABLE knowledge_cases ADD COLUMN embedding vector(3072)")

    op.create_index("ix_knowledge_cases_tenant_id", "knowledge_cases", ["tenant_id"])
    op.create_index(
        "ix_knowledge_cases_source_evidence_id",
        "knowledge_cases",
        ["source_evidence_id"],
    )
    op.create_index(
        "ix_knowledge_cases_migrated_from",
        "knowledge_cases",
        ["migrated_from_episode_id"],
    )
    # One case per source document: a KB article reconstructed twice is a
    # duplicate, not a second opinion.
    op.create_index(
        "uq_knowledge_case_source",
        "knowledge_cases",
        ["tenant_id", "source_evidence_id"],
        unique=True,
    )

    op.create_table(
        "knowledge_case_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "knowledge_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(40), server_default="action", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=True),
        sa.Column(
            "extraction_confidence", sa.Float(), server_default="0.5", nullable=False
        ),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_case_steps_case_id", "knowledge_case_steps", ["knowledge_case_id"]
    )
    op.create_index(
        "uq_knowledge_case_step_order",
        "knowledge_case_steps",
        ["knowledge_case_id", "step_order"],
        unique=True,
    )

    op.create_table(
        "pattern_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "pattern_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patterns.id"),
            nullable=False,
        ),
        sa.Column("evidence_object_type", sa.String(40), nullable=False),
        sa.Column("evidence_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "support_role",
            sa.String(40),
            server_default="supports_resolution",
            nullable=False,
        ),
        sa.Column("evidence_class", sa.String(20), nullable=False),
        sa.Column("strength", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Arrives with TenantScopedMixin on the ORM side; omitting it here
        # is the drift test_orm_migration_column_parity exists to catch.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The invariant, in the database: only an episode may be empirical,
        # and only an empirical row may carry an outcome. A documented claim
        # cannot become an observed success because some later code path set
        # a field.
        sa.CheckConstraint(
            "(evidence_class = 'empirical' AND evidence_object_type = 'episode')"
            " OR (evidence_class <> 'empirical' AND outcome IS NULL)",
            name="ck_pattern_evidence_empirical_is_episode",
        ),
    )
    op.create_index("ix_pattern_evidence_tenant_id", "pattern_evidence", ["tenant_id"])
    op.create_index("ix_pattern_evidence_pattern_id", "pattern_evidence", ["pattern_id"])
    op.create_index(
        "ix_pattern_evidence_class", "pattern_evidence", ["pattern_id", "evidence_class"]
    )
    op.create_index(
        "ix_pattern_evidence_object",
        "pattern_evidence",
        ["evidence_object_type", "evidence_object_id"],
    )
    op.create_index(
        "uq_pattern_evidence_object",
        "pattern_evidence",
        ["pattern_id", "evidence_object_type", "evidence_object_id", "support_role"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("pattern_evidence")
    op.drop_table("knowledge_case_steps")
    op.drop_table("knowledge_cases")
