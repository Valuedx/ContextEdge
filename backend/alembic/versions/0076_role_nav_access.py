"""Platform role-to-sidebar-tab access, edited by the platform super admin."""

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0076_role_nav_access"
down_revision = "0075_user_username_login"
branch_labels = None
depends_on = None

_DEFAULT_ROWS = (
    ("analyst", "/overview"),
    ("analyst", "/evidence"),
    ("analyst", "/sessions"),
    ("analyst", "/runtime"),
    ("analyst", "/decisions"),
    ("analyst", "/episodes"),
    ("analyst", "/patterns"),
    ("analyst", "/playbooks"),
    ("analyst", "/graph-explorer"),
    ("playbook_reviewer", "/overview"),
    ("playbook_reviewer", "/review"),
    ("playbook_reviewer", "/episodes"),
    ("playbook_reviewer", "/patterns"),
    ("playbook_reviewer", "/playbooks"),
    ("playbook_reviewer", "/graph-explorer"),
    ("knowledge_manager", "/overview"),
    ("knowledge_manager", "/evidence"),
    ("knowledge_manager", "/sessions"),
    ("knowledge_manager", "/runtime"),
    ("knowledge_manager", "/review"),
    ("knowledge_manager", "/execution"),
    ("knowledge_manager", "/decisions"),
    ("knowledge_manager", "/episodes"),
    ("knowledge_manager", "/patterns"),
    ("knowledge_manager", "/playbooks"),
    ("knowledge_manager", "/negative-knowledge"),
    ("knowledge_manager", "/identities"),
    ("knowledge_manager", "/correlations"),
    ("knowledge_manager", "/suggestions"),
    ("knowledge_manager", "/graph-explorer"),
    ("knowledge_manager", "/contradictions"),
    ("knowledge_manager", "/drift"),
    ("knowledge_manager", "/evaluations"),
    ("domain_admin", "/overview"),
    ("domain_admin", "/sources"),
    ("domain_admin", "/sync"),
    ("domain_admin", "/evidence"),
    ("domain_admin", "/sessions"),
    ("domain_admin", "/execution"),
    ("domain_admin", "/decisions"),
    ("domain_admin", "/identities"),
    ("domain_admin", "/graph-explorer"),
    ("domain_admin", "/audit"),
)


def upgrade() -> None:
    op.create_table(
        "role_nav_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("href", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("role", "href", name="uq_role_nav_access_role_href"),
    )
    op.create_index("ix_role_nav_access_role", "role_nav_access", ["role"])

    tenant_tabs = (
        "/overview",
        "/sources",
        "/sync",
        "/evidence",
        "/sessions",
        "/runtime",
        "/review",
        "/execution",
        "/decisions",
        "/episodes",
        "/patterns",
        "/playbooks",
        "/negative-knowledge",
        "/identities",
        "/correlations",
        "/suggestions",
        "/graph-explorer",
        "/contradictions",
        "/drift",
        "/evaluations",
        "/policies",
        "/audit",
        "/admin/cost",
        "/admin/pipeline",
        "/settings",
    )
    rows = list(_DEFAULT_ROWS) + [("tenant_admin", href) for href in tenant_tabs]
    conn = op.get_bind()
    for role, href in rows:
        conn.execute(
            sa.text("INSERT INTO role_nav_access (id, role, href) VALUES (:id, :role, :href)"),
            {"id": str(uuid.uuid4()), "role": role, "href": href},
        )


def downgrade() -> None:
    op.drop_index("ix_role_nav_access_role", table_name="role_nav_access")
    op.drop_table("role_nav_access")
