"""Scoped, measured trust (F10).

Autonomy was a mode on the playbook — ``supervised``, ``full_auto``. That is a
configuration, not a track record, and it cannot answer the question that
should gate an unattended action: has THIS agent done THIS action on THIS class
of thing in THIS environment, and did it hold?

One row per scope. The scope is the composite unique key, deliberately wide:
the same agent restarting a Windows service on a dev endpoint and failing over
an Oracle primary in production are not the same track record, and a single
global number lets the easy case vouch for the hard one. Unknown dimensions
store ``'unspecified'`` rather than NULL, because NULLs in a unique key would
let two "unknown environment" profiles coexist and split the record in half.

``confidence_lower_bound`` is a Wilson score interval rather than a success
rate: 3/3 is a rate of 1.0 and means almost nothing, 340/350 is 0.97 and means
a great deal. Storing the bound means the autonomy decision cannot be fooled
by a short lucky streak, and no separate minimum-sample rule exists for someone
to tune away later.

``consecutive_failures`` exists so a profile with a long good history and a bad
last week demotes immediately, without waiting for its own average to move.

Outcomes are fed by F9's verification assessment. Under the previous
silence-equals-success verifier every number here would have been inflated in
exactly the direction that matters, which is why F9 shipped first.

Revision ID: 0062_trust_profiles
Revises: 0061_verification_criteria
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0062_trust_profiles"
down_revision = "0061_verification_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trust_profiles" in set(inspector.get_table_names()):
        return

    op.create_table(
        "trust_profiles",
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
        sa.Column("agent_ref", sa.String(120), nullable=False),
        sa.Column("action_type", sa.String(60), nullable=False),
        sa.Column("resource_class", sa.String(80), nullable=False),
        sa.Column("environment", sa.String(30), nullable=False),
        sa.Column("business_criticality", sa.String(30), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inconclusive", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rollbacks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("human_overrides", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reopens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "confidence_lower_bound", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "autonomy_level", sa.String(20), nullable=False, server_default="advisory"
        ),
        sa.Column("autonomy_reason", sa.String(300), nullable=True),
        sa.Column("last_outcome_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_ref",
            "action_type",
            "resource_class",
            "environment",
            "business_criticality",
            name="uq_trust_profiles_scope",
        ),
        sa.CheckConstraint(
            "autonomy_level IN ('advisory', 'supervised', 'autonomous', 'suspended')",
            name="ck_trust_profiles_autonomy_level",
        ),
        sa.CheckConstraint("sample_size >= 0", name="ck_trust_profiles_sample_size"),
    )
    op.create_index("ix_trust_profiles_tenant_id", "trust_profiles", ["tenant_id"])
    op.create_index(
        "ix_trust_profiles_lookup", "trust_profiles", ["tenant_id", "agent_ref", "action_type"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trust_profiles" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_trust_profiles_lookup", table_name="trust_profiles")
    op.drop_index("ix_trust_profiles_tenant_id", table_name="trust_profiles")
    op.drop_table("trust_profiles")
