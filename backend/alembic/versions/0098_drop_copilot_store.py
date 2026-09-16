"""Drop ContextEdge Copilot usage/conversation tables.

Those tables were product-specific. They are not part of the ContextEdge
platform schema. Conversation and usage data belong to the consuming
product's own database.

Revision ID: 0098_drop_copilot_store
Revises: 0097_msp_tenancy_and_role_rls
"""

import sqlalchemy as sa

from alembic import op

revision = "0098_drop_copilot_store"
down_revision = "0097_msp_tenancy_and_role_rls"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.execute(sa.text("DELETE FROM role_nav_access WHERE href = '/admin/copilot'"))
    op.execute(sa.text("DROP TABLE IF EXISTS copilot_messages CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS copilot_usage_events CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS copilot_conversations CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS copilot_login_events CASCADE"))


def downgrade() -> None:
    raise NotImplementedError(
        "copilot_* tables are not part of the ContextEdge platform schema"
    )
