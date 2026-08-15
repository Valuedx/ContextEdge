"""Retries as a first-class thing, and a live idempotency key (F8).

``ExecutionStepRun`` carries one status, so a step that timed out and was
retried had nowhere to say so — the second try overwrote the first and "did
this run twice?" was unanswerable from the ledger. And
``uq_execution_step_runs_idempotency_key``, shipped in ``0029`` and described
there as "the single most important banking-grade safety control", guarded a
column nothing ever wrote: the index was real, the control was inert.

``execution_attempts`` gives each try its own row. The ``deduplicated`` status
is the important one — durable evidence that a replay arrived and was
recognised, which is the difference between an idempotency control that works
and one nobody can prove worked.

The key itself is derived (see ``services/idempotency_service.py``) from F7's
artifact hash scoped to the case: same case, same step payload, same action.
Only side-effecting steps get one — suppressing a repeated diagnostic would be
a bug wearing a safety control's clothes — and skills whose contract declares
NATIVE idempotency get none either, because the tool is already safe to replay.

Revision ID: 0060_execution_attempts
Revises: 0059_approval_artifact_binding
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_execution_attempts"
down_revision = "0059_approval_artifact_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "execution_attempts" in set(inspector.get_table_names()):
        return

    op.create_table(
        "execution_attempts",
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
            "step_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_step_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("skill_version", sa.String(20), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("input_hash", sa.String(71), nullable=True),
        sa.Column("worker_ref", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "duplicate_of_step_run_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "step_run_id", "attempt_number", name="uq_execution_attempts_step_number"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_execution_attempts_number"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timeout', 'cancelled', "
            "'deduplicated')",
            name="ck_execution_attempts_status",
        ),
    )
    op.create_index("ix_execution_attempts_tenant_id", "execution_attempts", ["tenant_id"])
    op.create_index("ix_execution_attempts_step_run_id", "execution_attempts", ["step_run_id"])
    op.create_index(
        "ix_execution_attempts_idempotency_key", "execution_attempts", ["idempotency_key"]
    )
    op.create_index(
        "ix_execution_attempts_tenant_started",
        "execution_attempts",
        ["tenant_id", "started_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "execution_attempts" not in set(inspector.get_table_names()):
        return
    for index in (
        "ix_execution_attempts_tenant_started",
        "ix_execution_attempts_idempotency_key",
        "ix_execution_attempts_step_run_id",
        "ix_execution_attempts_tenant_id",
    ):
        op.drop_index(index, table_name="execution_attempts")
    op.drop_table("execution_attempts")
