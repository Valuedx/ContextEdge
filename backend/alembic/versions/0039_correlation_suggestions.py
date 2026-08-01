"""Gated semantic correlation suggestions.

P3 of the correlation review. Semantic similarity NEVER creates
correlation edges by itself — near-identical wording happens across
unrelated incidents (every VPN outage reads alike). Instead, chunk-ANN
candidates that clear a similarity floor AND carry at least one
non-semantic corroborator (shared trusted identity, shared case
membership) are written here as **suggestions** for reviewer decision:

- pending suggestions surface via the correlations API;
- accept creates an ordinary CorrelationEdge (which the episode cluster
  resolver then expands through — no transitive expansion before that);
- reject is remembered so the pair is never re-suggested.

The evidence pair is stored normalized (smaller UUID first) so the
symmetric duplicate cannot exist.

Additive and re-runnable.

Revision ID: 0039_correlation_suggestions
Revises: 0038_ticket_bridging
Create Date: 2026-08-02 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0039_correlation_suggestions"
down_revision: Union[str, None] = "0038_ticket_bridging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS correlation_suggestions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            evidence_id_low UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            evidence_id_high UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            similarity FLOAT NOT NULL,
            corroborators JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewed_by VARCHAR(255),
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_correlation_suggestion_pair
                UNIQUE (evidence_id_low, evidence_id_high)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_correlation_suggestions_pending
        ON correlation_suggestions (tenant_id, created_at)
        WHERE status = 'pending';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS correlation_suggestions;")
