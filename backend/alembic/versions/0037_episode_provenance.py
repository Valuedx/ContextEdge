"""Episode provenance: normalized evidence links + cluster fingerprint.

P0 of the correlation/episode review: episodes previously recorded
membership only as a JSONB ``evidence_ids`` list stamped with the FULL
input cluster on every extracted episode — when the LLM split a mixed
cluster, both episodes claimed all the evidence. This migration adds:

- ``episode_evidence_links``: one row per (episode, evidence) with the
  membership reason — queryable provenance for review, pattern mining,
  and grounding. Written from the extractor's per-episode references.
- ``episodes.cluster_fingerprint``: hash of the resolved cluster's
  evidence set, powering draft idempotency (same cluster re-processed →
  no duplicate draft) and supersede-on-growth (draft for a subset
  cluster is superseded when the cluster grows).

Additive and re-runnable.

Revision ID: 0037_episode_provenance
Revises: 0036_execution_verification
Create Date: 2026-08-01 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0037_episode_provenance"
down_revision: Union[str, None] = "0036_execution_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS episode_evidence_links (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            link_reason VARCHAR(120),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_episode_evidence UNIQUE (episode_id, evidence_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_episode_evidence_links_tenant
        ON episode_evidence_links (tenant_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_episode_evidence_links_evidence
        ON episode_evidence_links (evidence_id);
        """
    )
    op.execute(
        """
        ALTER TABLE episodes
            ADD COLUMN IF NOT EXISTS cluster_fingerprint VARCHAR(64);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_episodes_cluster_fingerprint
        ON episodes (tenant_id, cluster_fingerprint)
        WHERE cluster_fingerprint IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_episodes_cluster_fingerprint;")
    op.execute("ALTER TABLE episodes DROP COLUMN IF EXISTS cluster_fingerprint;")
    op.execute("DROP TABLE IF EXISTS episode_evidence_links;")
