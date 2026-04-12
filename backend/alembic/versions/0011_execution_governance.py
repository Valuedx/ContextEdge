"""Add governed execution tables: execution_runs, execution_step_runs, tool_invocations, approval_requests.

Revision ID: 0011_execution_governance
Revises: 0010_operational_events
Create Date: 2026-04-12 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011_execution_governance"
down_revision: Union[str, None] = "0010_operational_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_runs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            session_id UUID NULL REFERENCES resolution_sessions(id),
            playbook_id UUID NOT NULL REFERENCES playbooks(id),
            playbook_version_id UUID NOT NULL REFERENCES playbook_versions(id),
            initiated_by UUID NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            automation_mode VARCHAR(30) NOT NULL DEFAULT 'suggest_only',
            max_safety_class VARCHAR(30) NOT NULL DEFAULT 'read_only',
            started_at TIMESTAMPTZ NULL,
            completed_at TIMESTAMPTZ NULL,
            outcome VARCHAR(30) NULL,
            outcome_summary TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_execution_runs_tenant_id
            ON execution_runs (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_execution_runs_session_id
            ON execution_runs (session_id);
        CREATE INDEX IF NOT EXISTS ix_execution_runs_playbook_id
            ON execution_runs (playbook_id);
        CREATE INDEX IF NOT EXISTS ix_execution_runs_status
            ON execution_runs (status);
        CREATE INDEX IF NOT EXISTS ix_execution_runs_tenant_status
            ON execution_runs (tenant_id, status);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_step_runs (
            id UUID PRIMARY KEY,
            execution_run_id UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            step_index INT NOT NULL,
            step_title VARCHAR(500) NULL,
            safety_class VARCHAR(30) NOT NULL DEFAULT 'read_only',
            requires_approval BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_message TEXT NULL,
            started_at TIMESTAMPTZ NULL,
            completed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_execution_step_runs_execution_run_id
            ON execution_step_runs (execution_run_id);
        CREATE INDEX IF NOT EXISTS ix_execution_step_runs_tenant_id
            ON execution_step_runs (tenant_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_invocations (
            id UUID PRIMARY KEY,
            step_run_id UUID NOT NULL REFERENCES execution_step_runs(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            tool_name VARCHAR(200) NOT NULL,
            tool_version VARCHAR(50) NULL,
            safety_class VARCHAR(30) NOT NULL DEFAULT 'read_only',
            inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            error_message TEXT NULL,
            duration_ms INT NULL,
            started_at TIMESTAMPTZ NULL,
            completed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_tool_invocations_step_run_id
            ON tool_invocations (step_run_id);
        CREATE INDEX IF NOT EXISTS ix_tool_invocations_tenant_id
            ON tool_invocations (tenant_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_requests (
            id UUID PRIMARY KEY,
            execution_run_id UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE,
            step_run_id UUID NULL REFERENCES execution_step_runs(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            requested_by UUID NOT NULL,
            requested_action VARCHAR(120) NOT NULL,
            safety_class VARCHAR(30) NOT NULL,
            context JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            decided_by UUID NULL,
            decided_at TIMESTAMPTZ NULL,
            decision_comment TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_approval_requests_execution_run_id
            ON approval_requests (execution_run_id);
        CREATE INDEX IF NOT EXISTS ix_approval_requests_tenant_id
            ON approval_requests (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_approval_requests_status
            ON approval_requests (status);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_requests;")
    op.execute("DROP TABLE IF EXISTS tool_invocations;")
    op.execute("DROP TABLE IF EXISTS execution_step_runs;")
    op.execute("DROP TABLE IF EXISTS execution_runs;")
