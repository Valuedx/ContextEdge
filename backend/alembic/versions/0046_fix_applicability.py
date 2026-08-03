"""Fix applicability rules (backlog B4).

A fix learned on LPT001 is not "for laptops" — it is for the scope its
causally-relevant traits define. A rule binds a fix pattern to a target
class plus required/excluded trait predicates; the deterministic
assessment service validates every required trait against the target CI
before recommending, and returns the explicit applicability level.

One successful case creates a precedent, not a universal rule —
promotion to broader classes is reviewer-gated (B5); rules created
automatically start at ``approval_requirement='review'``.

Additive and re-runnable.

Revision ID: 0046_fix_applicability
Revises: 0045_issue_signatures
Create Date: 2026-08-03 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0046_fix_applicability"
down_revision: Union[str, None] = "0045_issue_signatures"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fix_applicability_rules (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            fix_pattern_id UUID NOT NULL
                REFERENCES fix_patterns(id) ON DELETE CASCADE,
            target_class_key VARCHAR(80),
            required_traits JSONB NOT NULL DEFAULT '{}'::jsonb,
            excluded_traits JSONB NOT NULL DEFAULT '{}'::jsonb,
            applicability_level VARCHAR(40) NOT NULL,
            minimum_evidence INTEGER NOT NULL DEFAULT 1,
            confidence FLOAT NOT NULL DEFAULT 0.5,
            approval_requirement VARCHAR(20) NOT NULL DEFAULT 'review',
            created_by VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_fix_applicability_rules_tenant_class
        ON fix_applicability_rules (tenant_id, target_class_key);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fix_applicability_rules;")
