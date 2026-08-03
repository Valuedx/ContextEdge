"""Proposed identity merges, awaiting a human decision.

Per-mention adjudication cannot find these. It only ever sees candidates
sharing a substring with the incoming name, so "SFA" and "Sales Force
Automation" were never presented together and forked into two identities
— as did "HP UPD" and "HP Universal Print Driver". A pass that reads the
whole provisional set at once can see both pairs; a pass that reads one
mention at a time structurally cannot.

Proposals are stored rather than applied. Merging re-points aliases and
deactivates an identity, which is not something to do on a model's word
alone, and the identities page already has a merge control for a human
to act through.

Storing them is also what stops the job from re-proposing a pair a
reviewer has already rejected on every subsequent run. The unique
constraint on the pair is the mechanism.

Revision ID: 0052
Revises: 0051
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_identity_merge_proposals"
down_revision = "0051_evidence_applicability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_merge_proposals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The identity to KEEP, and the one folded into it.
        sa.Column(
            "primary_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("reason", sa.Text(), nullable=True),
        # pending | accepted | rejected. A rejection is durable: it is
        # what tells the next run not to raise the same pair again.
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("proposed_by", sa.String(120), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # TenantScopedMixin -> TimestampMixin supplies this on the model,
        # so the table must have it or every INSERT fails on the RETURNING
        # clause SQLAlchemy adds for server-side defaults.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # One live proposal per ordered pair. Without it a nightly job would
    # re-raise every rejected pair forever, and a reviewer's decision
    # would mean nothing beyond the day they made it.
    op.create_unique_constraint(
        "uq_identity_merge_proposal_pair",
        "identity_merge_proposals",
        ["tenant_id", "primary_identity_id", "duplicate_identity_id"],
    )
    op.create_index(
        "ix_identity_merge_proposals_pending",
        "identity_merge_proposals",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_merge_proposals_pending", table_name="identity_merge_proposals"
    )
    op.drop_constraint(
        "uq_identity_merge_proposal_pair",
        "identity_merge_proposals",
        type_="unique",
    )
    op.drop_table("identity_merge_proposals")
