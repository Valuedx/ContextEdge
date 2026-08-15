"""Skill registry and execution contracts (F6).

``PlaybookStep.tool_ref`` has been a free string since it was introduced —
declared in the schema, set by nothing, resolved by nothing. There was no way
to ask what a step would invoke, what happens when it times out, whether
running it twice is safe, or whether it can be dry-run.

Two tables, because the same operational envelope governs many skills (a family
of ServiceNow API calls shares one timeout/retry/rate-limit posture) and
because the contract is what the attempt model will read while the skill is
what the planner reads. ``skills.execution_contract_id`` is ``ON DELETE
RESTRICT``: deleting the contract a live skill runs under would silently strip
its timeout and idempotency guarantees, which is not a thing to allow by
cascade.

Side-effect classification reuses the existing ``SAFETY_CLASSES`` values rather
than minting v6's parallel vocabulary — the executor and the approval policy
already gate on that tuple.

Nothing is backfilled and nothing is required yet: existing playbook steps
carry no ``tool_ref``, so the binding gate this migration enables has no
existing rows to reject.

Revision ID: 0058_skill_registry
Revises: 0057_knowledge_support
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_skill_registry"
down_revision = "0057_knowledge_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "execution_contracts" not in tables:
        op.create_table(
            "execution_contracts",
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
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("idempotency_mode", sa.String(20), nullable=False),
            sa.Column("deduplication_window_sec", sa.Integer(), nullable=True),
            sa.Column("timeout_sec", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("retry_backoff", sa.String(20), nullable=False, server_default="none"),
            sa.Column(
                "supports_cancellation", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "supports_dry_run", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "concurrency_policy", sa.String(30), nullable=False, server_default="parallel"
            ),
            sa.Column("max_concurrency", sa.Integer(), nullable=True),
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
            sa.Column("credential_scope", sa.String(120), nullable=True),
            sa.Column("expected_duration_sec", sa.Integer(), nullable=True),
            sa.Column("contract_version", sa.Integer(), nullable=False, server_default="1"),
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
            sa.UniqueConstraint("tenant_id", "name", name="uq_execution_contracts_tenant_name"),
            sa.CheckConstraint(
                "idempotency_mode IN ('NATIVE', 'CALLER_KEY', 'DEDUPE_ONLY', 'NOT_IDEMPOTENT')",
                name="ck_execution_contracts_idempotency_mode",
            ),
            sa.CheckConstraint("timeout_sec > 0", name="ck_execution_contracts_timeout"),
            sa.CheckConstraint("max_attempts >= 1", name="ck_execution_contracts_max_attempts"),
            sa.CheckConstraint(
                "deduplication_window_sec IS NULL OR deduplication_window_sec > 0",
                name="ck_execution_contracts_dedup_window",
            ),
        )
        op.create_index(
            "ix_execution_contracts_tenant_id", "execution_contracts", ["tenant_id"]
        )

    if "skills" not in tables:
        op.create_table(
            "skills",
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
            sa.Column("skill_key", sa.String(120), nullable=False),
            sa.Column("version", sa.String(20), nullable=False, server_default="1.0.0"),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("action_type", sa.String(40), nullable=True),
            sa.Column("interface_type", sa.String(20), nullable=False),
            sa.Column("endpoint_or_tool", sa.String(500), nullable=True),
            sa.Column("input_schema", postgresql.JSONB(), nullable=True),
            sa.Column("output_schema", postgresql.JSONB(), nullable=True),
            sa.Column("reversible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "rollback_skill_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("skills.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("safety_class", sa.String(30), nullable=False),
            sa.Column(
                "allowed_principal_roles",
                postgresql.JSONB(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column(
                "execution_contract_id",
                postgresql.UUID(as_uuid=True),
                # RESTRICT, not CASCADE: deleting the contract a live skill
                # runs under would silently strip its timeout and idempotency
                # guarantees.
                sa.ForeignKey("execution_contracts.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            sa.UniqueConstraint(
                "tenant_id", "skill_key", "version", name="uq_skills_key_version"
            ),
            sa.CheckConstraint(
                "interface_type IN ('API', 'MCP', 'RPA', 'CLI', 'SCRIPT', 'WORKFLOW', 'MANUAL')",
                name="ck_skills_interface_type",
            ),
            sa.CheckConstraint(
                "safety_class IN ('read_only', 'low_side_effect', "
                "'high_side_effect', 'destructive')",
                name="ck_skills_safety_class",
            ),
            sa.CheckConstraint(
                "status IN ('draft', 'active', 'deprecated', 'retired')",
                name="ck_skills_status",
            ),
        )
        op.create_index("ix_skills_tenant_id", "skills", ["tenant_id"])
        op.create_index(
            "ix_skills_tenant_key_status", "skills", ["tenant_id", "skill_key", "status"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "skills" in tables:
        op.drop_index("ix_skills_tenant_key_status", table_name="skills")
        op.drop_index("ix_skills_tenant_id", table_name="skills")
        op.drop_table("skills")
    if "execution_contracts" in tables:
        op.drop_index("ix_execution_contracts_tenant_id", table_name="execution_contracts")
        op.drop_table("execution_contracts")
