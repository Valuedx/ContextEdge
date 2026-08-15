"""Undoing, and handing over (F11).

Rollback was free text — ``playbook_versions.rollback_notes`` and a per-step
``rollback_hint`` — and ``reversible`` was a flag nothing consumed. Escalation
was an ``escalate_to_human`` decision type and an ``escalated`` case status, so
a human received a notification rather than what the system saw.

**Only the plan is new.** v6 models RollbackPlan / RollbackAction /
RollbackExecution as three classes; running an undo needs steps, approvals,
attempts, an artifact binding and a verification, all of which ``ExecutionRun``
already has after F6–F9. A parallel execution hierarchy would duplicate every
one of them and then drift. ``execution_runs.rolls_back_run_id`` is the whole
difference, and it means a rollback is verified like anything else instead of
being trusted because it was called a rollback.

``escalations.evidence_bundle`` holds REFS — assessment, decision trace,
rejected options, evidence ids — never copies. A copy would be a second version
of the truth that ages away from the first, and the point of the bundle is that
the human sees what the system saw.

A plan with no reversible steps is stored as ``infeasible`` rather than not
stored: "we cannot undo this" is the most important thing a responder can learn
early, and a missing row reads as "nobody checked".

Revision ID: 0063_rollback_and_escalation
Revises: 0062_trust_profiles
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_rollback_and_escalation"
down_revision = "0062_trust_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    existing = {c["name"] for c in inspector.get_columns("execution_runs")}
    if "rolls_back_run_id" not in existing:
        op.add_column(
            "execution_runs",
            sa.Column("rolls_back_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_execution_runs_rolls_back_run_id",
            "execution_runs",
            "execution_runs",
            ["rolls_back_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_execution_runs_rolls_back_run_id", "execution_runs", ["rolls_back_run_id"]
        )

    if "rollback_plans" not in tables:
        op.create_table(
            "rollback_plans",
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
                "execution_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("execution_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "verification_assessment_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("verification_assessments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
            sa.Column("actions", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column(
                "irreversible_steps", postgresql.JSONB(), nullable=False, server_default="[]"
            ),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('proposed', 'approved', 'executed', 'rejected', 'infeasible')",
                name="ck_rollback_plans_status",
            ),
        )
        op.create_index("ix_rollback_plans_tenant_id", "rollback_plans", ["tenant_id"])
        op.create_index(
            "ix_rollback_plans_execution_run_id", "rollback_plans", ["execution_run_id"]
        )
        op.create_index(
            "ix_rollback_plans_tenant_status", "rollback_plans", ["tenant_id", "status"]
        )

    if "escalations" not in tables:
        op.create_table(
            "escalations",
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
                "case_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("resolution_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "execution_run_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("execution_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "decision_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("decisions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("escalated_by", sa.String(120), nullable=False),
            sa.Column("escalated_to", sa.String(120), nullable=True),
            sa.Column(
                "evidence_bundle", postgresql.JSONB(), nullable=False, server_default="{}"
            ),
            sa.Column(
                "recommended_next_actions",
                postgresql.JSONB(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("acknowledgement_latency_min", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('open', 'acknowledged', 'resolved', 'cancelled')",
                name="ck_escalations_status",
            ),
            sa.CheckConstraint(
                "priority IN ('low', 'normal', 'high', 'critical')",
                name="ck_escalations_priority",
            ),
        )
        op.create_index("ix_escalations_tenant_id", "escalations", ["tenant_id"])
        op.create_index("ix_escalations_case_id", "escalations", ["case_id"])
        op.create_index("ix_escalations_tenant_status", "escalations", ["tenant_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "escalations" in tables:
        for index in (
            "ix_escalations_tenant_status",
            "ix_escalations_case_id",
            "ix_escalations_tenant_id",
        ):
            op.drop_index(index, table_name="escalations")
        op.drop_table("escalations")

    if "rollback_plans" in tables:
        for index in (
            "ix_rollback_plans_tenant_status",
            "ix_rollback_plans_execution_run_id",
            "ix_rollback_plans_tenant_id",
        ):
            op.drop_index(index, table_name="rollback_plans")
        op.drop_table("rollback_plans")

    existing = {c["name"] for c in inspector.get_columns("execution_runs")}
    if "rolls_back_run_id" in existing:
        op.drop_index("ix_execution_runs_rolls_back_run_id", table_name="execution_runs")
        op.drop_constraint(
            "fk_execution_runs_rolls_back_run_id", "execution_runs", type_="foreignkey"
        )
        op.drop_column("execution_runs", "rolls_back_run_id")
