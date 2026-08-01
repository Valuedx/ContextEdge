"""Per-cohort fix outcome statistics (backlog B5).

One global success counter overstates a fix: 9/10 on Dell Latitude
5420, 2/8 on other laptops, 0/4 on desktops is three different
stories. Outcomes are counted per (fix, cohort) at three grains —
model, class, family — and the promotion policy turns sustained
same-cohort success into CANDIDATE applicability rules that a reviewer
must approve; failures block promotion for their cohort and anything
broader. Scope only ever broadens through a human.

Additive and re-runnable.

Revision ID: 0047_fix_cohort_stats
Revises: 0046_fix_applicability
Create Date: 2026-08-03 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0047_fix_cohort_stats"
down_revision: Union[str, None] = "0046_fix_applicability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fix_cohort_stats (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            fix_pattern_id UUID NOT NULL
                REFERENCES fix_patterns(id) ON DELETE CASCADE,
            cohort_type VARCHAR(20) NOT NULL,
            cohort_key VARCHAR(160) NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_fix_cohort UNIQUE (fix_pattern_id, cohort_type, cohort_key)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fix_cohort_stats;")
