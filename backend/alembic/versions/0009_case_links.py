"""Add case_links table for cross-source correlation.

Revision ID: 0009_case_links
Revises: 0008_resolution_sessions
Create Date: 2026-04-12 13:10:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_case_links"
down_revision: Union[str, None] = "0008_resolution_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_links (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            canonical_case_id UUID NOT NULL,
            system VARCHAR(100) NOT NULL,
            external_id VARCHAR(500) NOT NULL,
            evidence_id UUID NULL REFERENCES evidence_items(id),
            confidence FLOAT NOT NULL DEFAULT 1.0,
            first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_case_links_tenant_case
            ON case_links (tenant_id, canonical_case_id);
        CREATE INDEX IF NOT EXISTS ix_case_links_external
            ON case_links (tenant_id, system, external_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS case_links;")
