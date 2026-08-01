"""Ticket-number bridging: identifiers, memberships, pending mentions.

P1 of the correlation review. A ticket number quoted in an email or
Teams message proves "this evidence relates to that case" — it does NOT
prove that every ticket mentioned together belongs to one canonical
case ("Weekly review of INC0010427, INC0010455 and CHG0003321" must not
merge three incidents). So bridging is modeled as **evidence-to-case
membership**, never case-link keys:

- ``case_identifiers``: the authoritative registry — a ticket source's
  human-readable number (INC0010427, ITOPS-101) mapped to its canonical
  case. Written when the ticket itself correlates.
- ``evidence_case_memberships``: one row per (evidence, case)
  relationship with its type (primary_case / explicit_reference /
  mentioned_only), confidence, and where the mention was found.
- ``pending_identifier_mentions``: tokens extracted before their ticket
  was ingested — reconciled when the identifier registers, so bridging
  is ingestion-order independent.

Additive and re-runnable.

Revision ID: 0038_ticket_bridging
Revises: 0037_episode_provenance
Create Date: 2026-08-01 23:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0038_ticket_bridging"
down_revision: Union[str, None] = "0037_episode_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_identifiers (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            canonical_case_id UUID NOT NULL,
            source_system VARCHAR(50) NOT NULL,
            identifier_type VARCHAR(30) NOT NULL DEFAULT 'number',
            normalized_value VARCHAR(120) NOT NULL,
            display_value VARCHAR(120),
            is_authoritative BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_case_identifiers_tenant_system_value
                UNIQUE (tenant_id, source_system, normalized_value)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_case_identifiers_tenant_value
        ON case_identifiers (tenant_id, normalized_value);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_case_memberships (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            canonical_case_id UUID NOT NULL,
            relationship_type VARCHAR(40) NOT NULL,
            confidence FLOAT NOT NULL DEFAULT 1.0,
            extraction_location VARCHAR(40),
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_evidence_case_membership
                UNIQUE (evidence_id, canonical_case_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_case_memberships_case
        ON evidence_case_memberships (tenant_id, canonical_case_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_identifier_mentions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            normalized_value VARCHAR(120) NOT NULL,
            extraction_location VARCHAR(40),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            resolved_case_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_pending_mention UNIQUE (evidence_id, normalized_value)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_pending_mentions_tenant_value
        ON pending_identifier_mentions (tenant_id, normalized_value)
        WHERE status = 'pending';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_identifier_mentions;")
    op.execute("DROP TABLE IF EXISTS evidence_case_memberships;")
    op.execute("DROP TABLE IF EXISTS case_identifiers;")
