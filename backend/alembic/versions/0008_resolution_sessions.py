"""Add resolution sessions and decision trace events.

Revision ID: 0008_resolution_sessions
Revises: 0007_fts_gin_indexes
Create Date: 2026-04-12 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008_resolution_sessions"
down_revision: Union[str, None] = "0007_fts_gin_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS resolution_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            domain_id UUID NULL REFERENCES domains(id),
            initiated_by UUID NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'open',
            symptoms JSONB NOT NULL DEFAULT '[]'::jsonb,
            entities JSONB NOT NULL DEFAULT '[]'::jsonb,
            external_case_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            notes TEXT NULL,
            closed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_tenant_id ON resolution_sessions (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_domain_id ON resolution_sessions (domain_id);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_status ON resolution_sessions (status);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_tenant_status
            ON resolution_sessions (tenant_id, status);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_trace_events (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES resolution_sessions(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            event_type VARCHAR(50) NOT NULL,
            inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            reasoning TEXT NULL,
            confidence FLOAT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_decision_trace_events_session_id ON decision_trace_events (session_id);
        CREATE INDEX IF NOT EXISTS ix_decision_trace_events_tenant_id ON decision_trace_events (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_decision_trace_events_session_created
            ON decision_trace_events (session_id, created_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decision_trace_events;")
    op.execute("DROP TABLE IF EXISTS resolution_sessions;")
