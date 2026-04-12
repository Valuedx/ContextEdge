"""Add evidence_identity_links for active identity resolution.

Revision ID: 0012_evidence_identity_links
Revises: 0011_execution_governance
Create Date: 2026-04-12 20:15:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0012_evidence_identity_links"
down_revision: Union[str, None] = "0011_execution_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_identity_links (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            identity_id UUID NOT NULL REFERENCES canonical_identities(id) ON DELETE CASCADE,
            match_type VARCHAR(50) NOT NULL DEFAULT 'alias_match',
            confidence FLOAT NOT NULL DEFAULT 1.0,
            source_metadata JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_evidence_identity_links_tenant_id
            ON evidence_identity_links (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_identity_links_evidence_id
            ON evidence_identity_links (evidence_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_identity_links_identity_id
            ON evidence_identity_links (identity_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_identity_links_evidence_identity
            ON evidence_identity_links (evidence_id, identity_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_identity_links;")
