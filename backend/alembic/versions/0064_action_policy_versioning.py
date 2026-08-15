"""Version the action policy, now that something evaluates it (F3b).

``action_policies`` shipped in ``0029`` with a verdict vocabulary, scope axes
and precedence columns whose own docstring said the engine was "on the design
roadmap". F3 deliberately did NOT version this table: adding
``version`` / ``effective_from`` / ``effective_to`` to a table nothing wrote
would have added three more never-written columns to the register F1 exists to
keep honest.

That changed when F1 populated ``ExecutionStepRun.action_name`` — the lookup
key this table is designed around now exists on every step, so an engine has
something real to gate. This migration adds the versioning F3 gave
``tenant_policies``, for the same reason: a ``policy_checks`` row keys on the
policy VERSION, so editing a policy must not rewrite the history of what a past
execution was judged under.

Revision ID: 0064_action_policy_versioning
Revises: 0063_rollback_and_escalation
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_action_policy_versioning"
down_revision = "0063_rollback_and_escalation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("action_policies")}
    if "version" not in existing:
        op.add_column(
            "action_policies",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "effective_from" not in existing:
        op.add_column(
            "action_policies",
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        )
    if "effective_to" not in existing:
        op.add_column(
            "action_policies",
            sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("action_policies")}
    for column in ("effective_to", "effective_from", "version"):
        if column in existing:
            op.drop_column("action_policies", column)
