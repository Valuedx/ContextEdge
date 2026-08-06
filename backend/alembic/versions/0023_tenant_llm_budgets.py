"""Add tenant_llm_budgets table for per-tenant LLM cost caps.

Ships as part of the Weeks 7-9 "scale foundations" batch in
``ENTERPRISE_ARCHITECTURE_REVIEW.md`` §6 item 14. Without per-tenant
caps, a single misconfigured tenant — or a backfill that retries
indefinitely on a prompt that triggers a provider 5xx loop — can burn
through the whole org's LLM budget for the day. This table gives
operators a knob per tenant.

Schema:

- ``tenant_id`` (PK, FK to tenants.id) — one row per tenant that has
  a budget configured. Tenants without a row have no cap.
- ``daily_token_limit`` (bigint, nullable) — max tokens per UTC day
  across all tasks. ``NULL`` means "don't cap on tokens".
- ``daily_cost_cap_usd`` (numeric(12,4), nullable) — max estimated
  USD spend per UTC day. ``NULL`` means "don't cap on cost". Either
  or both may be set; whichever is hit first triggers ``action``.
- ``action_on_exceed`` (varchar) — ``block`` raises an exception from
  ``llm_complete`` so the caller can degrade. ``warn`` logs + emits
  an operational event but lets the call through (useful for
  rollout: see the warnings first, flip to ``block`` once noise is
  tuned out). Default ``warn``.
- ``updated_at`` — so audit logs can show when a cap was changed.

Enforcement is pre-call (see ``services/tenant_budget_service.py``).
Current-day usage is summed from the ``llm.usage`` operational events
already written by ``ai/observability.record_llm_usage`` — no second
source of truth.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0023_tenant_llm_budgets"
down_revision: Union[str, None] = "0022_contradiction_scan_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tenant_llm_budgets"):
        op.create_table(
            "tenant_llm_budgets",
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("daily_token_limit", sa.BigInteger(), nullable=True),
            sa.Column("daily_cost_cap_usd", sa.Numeric(12, 4), nullable=True),
            sa.Column(
                "action_on_exceed",
                sa.String(20),
                nullable=False,
                server_default="warn",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "action_on_exceed IN ('block', 'warn')",
                name="ck_tenant_llm_budgets_action_on_exceed",
            ),
            sa.CheckConstraint(
                "daily_token_limit IS NULL OR daily_token_limit >= 0",
                name="ck_tenant_llm_budgets_token_limit_nonneg",
            ),
            sa.CheckConstraint(
                "daily_cost_cap_usd IS NULL OR daily_cost_cap_usd >= 0",
                name="ck_tenant_llm_budgets_cost_cap_nonneg",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tenant_llm_budgets"):
        op.drop_table("tenant_llm_budgets")

