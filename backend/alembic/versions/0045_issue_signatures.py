"""Issue signatures — structured problem fingerprints (backlog B3).

"Wi-Fi adapter disappears after sleep" must match across devices even
when the literal log wording differs. ErrorSignature is the exact
error shape; the issue signature is the broader structured fingerprint
(affected capability + failing component + failure mode + trigger),
extracted per APPROVED episode only — unreviewed stories must not mint
signatures — and deduped per tenant by a normalized key.

Episodes sharing a signature are a RECURRENCE chain (C2): similar
problems, never the same occurrence — the cluster resolver deliberately
does not expand through recurrence memberships.

Additive and re-runnable.

Revision ID: 0045_issue_signatures
Revises: 0044_thread_topics
Create Date: 2026-08-03 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0045_issue_signatures"
down_revision: Union[str, None] = "0044_thread_topics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS issue_signatures (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            signature_key VARCHAR(240) NOT NULL,
            affected_capability VARCHAR(80) NOT NULL,
            failing_component VARCHAR(120),
            failure_mode VARCHAR(120) NOT NULL,
            trigger_change VARCHAR(200),
            environment VARCHAR(50),
            scope VARCHAR(50),
            error_signature_id UUID REFERENCES error_signatures(id) ON DELETE SET NULL,
            episode_count INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_issue_signature_key UNIQUE (tenant_id, signature_key)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS episode_issue_signatures (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            issue_signature_id UUID NOT NULL
                REFERENCES issue_signatures(id) ON DELETE CASCADE,
            confidence FLOAT NOT NULL DEFAULT 0.7,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_episode_issue_signature UNIQUE (episode_id, issue_signature_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_issue_signatures_capability
        ON issue_signatures (tenant_id, affected_capability);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episode_issue_signatures;")
    op.execute("DROP TABLE IF EXISTS issue_signatures;")
