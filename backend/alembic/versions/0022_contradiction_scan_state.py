"""Add contradiction_scan_state table for incremental scans.

Before this revision, ``scan_contradictions`` in
``services/contradiction_service.py`` processed every
(playbook_version, kb_evidence) pair on every beat, gated only by a
token-overlap heuristic. At enterprise scale (100 playbooks × 1000 KB
items × 10 steps each) this means 100–200K LLM calls per 12-hour beat
even after the gate — the single largest LLM cost in the system.

This revision adds the state table that enables:

1. **Incremental scanning** — skip pairs whose ``last_scanned_at`` is
   newer than the evidence's ``updated_at`` and within the current
   playbook version's lifetime. Rows are keyed on
   ``playbook_version_id``, so a new published version naturally
   produces fresh rows and a full re-scan of that version's pairs.
2. **Recording "skipped" outcomes** — operators can see how many pairs
   were skipped by the embedding pre-filter, token-overlap gate, or
   LLM-call budget without running the scan again.
3. **Budget enforcement visibility** — pairs skipped due to budget
   cap are recorded with ``result="skipped_budget"``, so the queue
   depth is queryable as ``COUNT(*) WHERE result='skipped_budget'``.

Paired with the embedding-first pre-filter in the rewritten
``scan_contradictions``, expected LLM-call reduction is 80–95% per
beat on a mature tenant — see
``ENTERPRISE_ARCHITECTURE_REVIEW.md`` §3.2.

Revision ID: 0022_contradiction_scan_state
Revises: 0021_hnsw_vector_indexes
Create Date: 2026-04-22 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0022_contradiction_scan_state"
down_revision: Union[str, None] = "0021_hnsw_vector_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contradiction_scan_state (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            playbook_version_id UUID NOT NULL
                REFERENCES playbook_versions(id) ON DELETE CASCADE,
            evidence_id UUID NOT NULL
                REFERENCES evidence_items(id) ON DELETE CASCADE,
            last_scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            result VARCHAR(30) NOT NULL,
            skip_reason TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_contradiction_scan_state_pair
            ON contradiction_scan_state (playbook_version_id, evidence_id);
        CREATE INDEX IF NOT EXISTS ix_contradiction_scan_state_tenant_scanned
            ON contradiction_scan_state (tenant_id, last_scanned_at);
        CREATE INDEX IF NOT EXISTS ix_contradiction_scan_state_result
            ON contradiction_scan_state (result)
            WHERE result IN ('skipped_budget', 'skipped_llm_error');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contradiction_scan_state;")
