"""Add baseline_ref + delta_signal to evidence_items.

Backs the reviewer console's Zone 4 evidence cards that render a current
value plus a baseline comparison ("was 74% a week ago", "first observation
in 7d window"). `delta_signal` is a color-coded severity hint — `red`,
`amber`, or `neutral` — so the UI can eyeball the whole evidence block in
about two seconds without re-computing thresholds.

- `baseline_ref` (JSONB, nullable) — shape is open-ended by design so
  connectors that ingest rich time-series (Intune disk-free %, CrowdStrike
  threat signals) can populate prior/current/delta numerics directly,
  while the generic `compute_evidence_baseline` worker records
  relationship-only baselines ("last seen N days ago") for evidence types
  it can't extract numerics from.
- `delta_signal` (VARCHAR(20), nullable) — indexed so the reviewer queue
  can filter "show me only red-signal evidence".

Additive only. No backfill — existing rows keep `baseline_ref = null` and
`delta_signal = null` until the next re-ingest or baseline re-compute.

Revision ID: 0019_evidence_baseline
Revises: 0018_playbook_step_metadata
Create Date: 2026-04-19 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019_evidence_baseline"
down_revision: Union[str, None] = "0018_playbook_step_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN IF NOT EXISTS baseline_ref JSONB NULL,
            ADD COLUMN IF NOT EXISTS delta_signal VARCHAR(20) NULL;
        CREATE INDEX IF NOT EXISTS ix_evidence_items_delta_signal
            ON evidence_items (delta_signal)
            WHERE delta_signal IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_evidence_items_delta_signal;
        ALTER TABLE evidence_items
            DROP COLUMN IF EXISTS delta_signal,
            DROP COLUMN IF EXISTS baseline_ref;
        """
    )
