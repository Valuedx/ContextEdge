"""Fleet / major-incident grouping suggestions (backlog B6).

Thirty endpoints failing after the same Windows patch is ONE
occurrence with many impact points — but grouping is exactly the kind
of decision the mass-merge lesson reserves for humans. The detector
finds changes blamed by several incidents inside a tight window and
writes a SUGGESTION; only a reviewer's accept mints the parent case
and attaches members. Rejection is permanent per change reference.

Additive and re-runnable.

Revision ID: 0048_fleet_groups
Revises: 0047_fix_cohort_stats
Create Date: 2026-08-03 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0048_fleet_groups"
down_revision: Union[str, None] = "0047_fix_cohort_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_group_suggestions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            change_ref VARCHAR(120) NOT NULL,
            change_evidence_id UUID
                REFERENCES evidence_items(id) ON DELETE SET NULL,
            member_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            member_count INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            parent_case_id UUID,
            reviewed_by VARCHAR(255),
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_fleet_group_change UNIQUE (tenant_id, change_ref)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fleet_group_suggestions;")
