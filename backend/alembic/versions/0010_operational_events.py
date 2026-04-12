"""Add append-only operational event ledger.

Revision ID: 0010_operational_events
Revises: 0009_case_links
Create Date: 2026-04-12 16:15:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010_operational_events"
down_revision: Union[str, None] = "0009_case_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operational_events (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            entity_type VARCHAR(80) NOT NULL,
            entity_id TEXT NULL,
            session_id UUID NULL REFERENCES resolution_sessions(id),
            event_type VARCHAR(120) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            correlation_id UUID NULL,
            causation_id UUID NULL,
            actor_id UUID NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX IF NOT EXISTS ix_operational_events_tenant_id
            ON operational_events (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_entity_type
            ON operational_events (entity_type);
        CREATE INDEX IF NOT EXISTS ix_operational_events_entity_id
            ON operational_events (entity_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_session_id
            ON operational_events (session_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_event_type
            ON operational_events (event_type);
        CREATE INDEX IF NOT EXISTS ix_operational_events_occurred_at
            ON operational_events (occurred_at);
        CREATE INDEX IF NOT EXISTS ix_operational_events_recorded_at
            ON operational_events (recorded_at);
        CREATE INDEX IF NOT EXISTS ix_operational_events_correlation_id
            ON operational_events (correlation_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_causation_id
            ON operational_events (causation_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_tenant_entity
            ON operational_events (tenant_id, entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS ix_operational_events_tenant_session_recorded
            ON operational_events (tenant_id, session_id, recorded_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operational_events;")
