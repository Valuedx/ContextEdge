"""Constrain playbook.risk_tier to the unified retrieval vocabulary.

The graph hydrator and /runtime/match used to disagree on which labels
existed. Unknown values were excluded from the agent (rank 99) and treated
as medium by the ranker. This CHECK, plus RISK_RANK in risk_policy.py,
makes that split impossible.

Unknown existing labels are remapped to medium before the constraint is
applied so upgrade does not fail on dirty data.

Revision ID: 0085_playbook_risk_tier_check
Revises: 0084_fill_null_tenant_ids
"""

from alembic import op
import sqlalchemy as sa


revision = "0085_playbook_risk_tier_check"
down_revision = "0084_fill_null_tenant_ids"
branch_labels = None
depends_on = None

_ALLOWED = ("minimal", "low", "medium", "high", "critical", "restricted")
_CONSTRAINT = "ck_playbooks_risk_tier"


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    # See 0086: playbooks UPDATEs must not run ce_fill_tenant_id().
    op.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
    op.execute(
        sa.text(
            """
            UPDATE playbooks
            SET risk_tier = lower(btrim(risk_tier))
            WHERE risk_tier IS NOT NULL
              AND risk_tier <> lower(btrim(risk_tier))
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE playbooks
            SET risk_tier = 'medium'
            WHERE risk_tier IS NULL
               OR lower(btrim(risk_tier)) NOT IN
                  ('minimal', 'low', 'medium', 'high', 'critical', 'restricted')
            """
        )
    )
    op.execute(sa.text("SET LOCAL session_replication_role = 'origin'"))
    op.create_check_constraint(
        _CONSTRAINT,
        "playbooks",
        "risk_tier IN ('minimal', 'low', 'medium', 'high', 'critical', 'restricted')",
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_constraint(_CONSTRAINT, "playbooks", type_="check")
