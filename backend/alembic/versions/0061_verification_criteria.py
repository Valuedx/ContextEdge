"""Criterion-level verification (F9).

The sweep asked one question — "did an incident or an alert reappear?" — and
turned it into one of three words on the run. Its worst case was silent: a CI
that had stopped reporting looked exactly like a service that recovered, and
both fed the cohort counters and the knowledge-support signal as success.

``verification_observations`` records each criterion as evaluated;
``verification_assessments`` aggregates them and carries the routing flags
(rollback / retry / escalation) that a single word could not.

**Criteria are deliberately not a table.** They are declared in
``PlaybookVersion.verification_policy``, which already exists and is already
read, plus the defaults; each observation records the criterion type and the
parameters it evaluated. A ``verification_criteria`` table with no authoring
surface would be another set of columns nothing writes.

``execution_runs.verification_status`` keeps its three words — the sweep queue,
the cohort counters and the knowledge-support signal all read it — and is now
derived from the assessment. ``partial_success`` and ``monitor_required`` both
map to a NON-verified legacy status on purpose: counting a half-fix or an
unconfirmed quiet period as verified success is what F9 exists to stop.

Revision ID: 0061_verification_criteria
Revises: 0060_execution_attempts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061_verification_criteria"
down_revision = "0060_execution_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "verification_assessments" not in tables:
        op.create_table(
            "verification_assessments",
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
            sa.Column("overall_result", sa.String(30), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "rollback_recommended", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "retry_recommended", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "escalation_required", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("monitoring_window_sec", sa.Integer(), nullable=True),
            sa.Column("verified_by", sa.String(120), nullable=True),
            sa.Column(
                "verified_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "overall_result IN ('success', 'partial_success', 'failed', "
                "'inconclusive', 'monitor_required', 'rollback_required')",
                name="ck_verification_assessments_result",
            ),
        )
        op.create_index(
            "ix_verification_assessments_tenant_id", "verification_assessments", ["tenant_id"]
        )
        op.create_index(
            "ix_verification_assessments_execution_run_id",
            "verification_assessments",
            ["execution_run_id"],
        )
        op.create_index(
            "ix_verification_assessments_tenant_run",
            "verification_assessments",
            ["tenant_id", "execution_run_id"],
        )

    if "verification_observations" not in tables:
        op.create_table(
            "verification_observations",
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
                "assessment_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("verification_assessments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("criterion_type", sa.String(40), nullable=False),
            sa.Column("criterion_name", sa.String(200), nullable=False),
            sa.Column(
                "criterion_params", postgresql.JSONB(), nullable=False, server_default="{}"
            ),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("observed_value", postgresql.JSONB(), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('pass', 'fail', 'inconclusive', 'not_observable')",
                name="ck_verification_observations_status",
            ),
        )
        op.create_index(
            "ix_verification_observations_assessment",
            "verification_observations",
            ["assessment_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "verification_observations" in tables:
        op.drop_index(
            "ix_verification_observations_assessment", table_name="verification_observations"
        )
        op.drop_table("verification_observations")
    if "verification_assessments" in tables:
        for index in (
            "ix_verification_assessments_tenant_run",
            "ix_verification_assessments_execution_run_id",
            "ix_verification_assessments_tenant_id",
        ):
            op.drop_index(index, table_name="verification_assessments")
        op.drop_table("verification_assessments")
