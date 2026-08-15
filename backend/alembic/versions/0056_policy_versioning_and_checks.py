"""Version the policy that is enforced, and record every evaluation (F3).

Two questions a governed execution has to be able to answer — "which policy
version evaluated this?" and "what did it see?" — were both unanswerable.
``tenant_policies`` carried no version at all, so editing a policy silently
rewrote the rules every past decision had been judged under.

Scope note. This versions ``tenant_policies``, the policy the executor
actually enforces through ``approval_policy_service``. It deliberately does
NOT touch ``action_policies``: that table has no writer, no CRUD API and no
evaluation engine, so versioning it would add three more never-written columns
to the register F1 exists to keep honest. The action-policy engine and its
versioning stay one item, tracked as F3b.

``policy_checks`` is append-only by convention: one row per evaluation, keyed
to the policy VERSION rather than the policy row, so a later edit cannot
rewrite the history of what a decision was judged under. ``policy_id`` is
``ON DELETE SET NULL`` for the same reason the playbook evidence links are —
"this run was evaluated against a policy that has since been deleted" is a
real audit record, and losing it to a cascade would be worse than keeping the
orphan.

Revision ID: 0056_policy_versioning_and_checks
Revises: 0055_generation_provenance
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056_policy_versioning_and_checks"
down_revision = "0055_generation_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = {c["name"] for c in inspector.get_columns("tenant_policies")}
    if "version" not in existing:
        op.add_column(
            "tenant_policies",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "effective_from" not in existing:
        op.add_column(
            "tenant_policies",
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        )
    if "effective_to" not in existing:
        op.add_column(
            "tenant_policies",
            sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        )

    if "policy_checks" not in set(inspector.get_table_names()):
        op.create_table(
            "policy_checks",
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
                "policy_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenant_policies.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("policy_type", sa.String(30), nullable=False),
            sa.Column("policy_version", sa.Integer(), nullable=True),
            sa.Column("check_name", sa.String(60), nullable=False),
            sa.Column("evaluated_entity_type", sa.String(50), nullable=False),
            sa.Column("evaluated_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("input_snapshot", postgresql.JSONB(), server_default="{}", nullable=False),
            sa.Column("evaluated_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "evaluated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "result IN ('pass', 'fail', 'not_applicable')",
                name="ck_policy_checks_result",
            ),
        )
        op.create_index(
            "ix_policy_checks_tenant_entity",
            "policy_checks",
            ["tenant_id", "evaluated_entity_type", "evaluated_entity_id"],
        )
        op.create_index(
            "ix_policy_checks_tenant_evaluated_at",
            "policy_checks",
            ["tenant_id", "evaluated_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "policy_checks" in set(inspector.get_table_names()):
        op.drop_index("ix_policy_checks_tenant_evaluated_at", table_name="policy_checks")
        op.drop_index("ix_policy_checks_tenant_entity", table_name="policy_checks")
        op.drop_table("policy_checks")

    existing = {c["name"] for c in inspector.get_columns("tenant_policies")}
    for column in ("effective_to", "effective_from", "version"):
        if column in existing:
            op.drop_column("tenant_policies", column)
