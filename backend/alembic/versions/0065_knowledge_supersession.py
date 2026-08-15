"""Reviewer-gated knowledge supersession (F4b).

``services/documents/versioning.py`` can already tell that "VPN SOP v2.docx"
supersedes "VPN SOP.docx", and its own docstring names the gap it does not
close: retrieval "returns superseded guidance and nothing marks it as
superseded". F4 taught retrieval whether a procedure has ever *worked*; this
teaches it whether one has been *replaced*.

The finding is a **proposal**, never an action. A filename is not grounds for
retiring an SOP — "Final" and "v2" are written by people in a hurry, folders get
reorganised, and a wrong call silently removes the only guidance that exists for
a problem. So it follows the ``IdentityMergeProposal`` pattern: stored, decided
by a human, and **rejection is durable**, because without persisting it a
scheduled pass re-raises every declined pair forever and the queue becomes noise
nobody reads.

Acceptance writes a ``superseded_by`` graph edge between the two evidence rows —
already in the F2 registry and already projected — and retrieval reads the edge.
The edge is temporal, so a supersession that is later closed stops demoting its
predecessor without anyone remembering to undo a flag.

Revision ID: 0065_knowledge_supersession
Revises: 0064_action_policy_versioning
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_knowledge_supersession"
down_revision = "0064_action_policy_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "knowledge_supersession_proposals" in set(inspector.get_table_names()):
        return

    op.create_table(
        "knowledge_supersession_proposals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "predecessor_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "successor_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_family", sa.String(300), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("proposed_by", sa.String(120), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Both timestamps arrive with TenantScopedMixin -> TimestampMixin. A
        # create_table that lists only the columns the author typed is how
        # `0034` and `0049` came to exist; `tests/test_orm_migration_column_
        # parity.py` now fails when one is missed.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "predecessor_evidence_id",
            "successor_evidence_id",
            name="uq_knowledge_supersession_pair",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_knowledge_supersession_status",
        ),
        sa.CheckConstraint(
            "predecessor_evidence_id <> successor_evidence_id",
            name="ck_knowledge_supersession_distinct",
        ),
    )
    op.create_index(
        "ix_knowledge_supersession_tenant_id",
        "knowledge_supersession_proposals",
        ["tenant_id"],
    )
    op.create_index(
        "ix_knowledge_supersession_predecessor",
        "knowledge_supersession_proposals",
        ["predecessor_evidence_id"],
    )
    op.create_index(
        "ix_knowledge_supersession_successor",
        "knowledge_supersession_proposals",
        ["successor_evidence_id"],
    )
    op.create_index(
        "ix_knowledge_supersession_pending",
        "knowledge_supersession_proposals",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "knowledge_supersession_proposals" not in set(inspector.get_table_names()):
        return
    for index in (
        "ix_knowledge_supersession_pending",
        "ix_knowledge_supersession_successor",
        "ix_knowledge_supersession_predecessor",
        "ix_knowledge_supersession_tenant_id",
    ):
        op.drop_index(index, table_name="knowledge_supersession_proposals")
    op.drop_table("knowledge_supersession_proposals")
