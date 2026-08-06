"""Add domain_id column and composite index to graph_edges.

Revision ID: 0015_graph_edges_domain_id
Revises: 0014_notifications_and_playbook_approval_policy
Create Date: 2026-04-14 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_graph_edges_domain_id"
down_revision: Union[str, None] = "0014_notifications_and_playbook_approval_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("graph_edges")}
    if "domain_id" not in existing_cols:
        op.add_column(
            "graph_edges",
            sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    existing_indexes = {i["name"] for i in inspector.get_indexes("graph_edges")}
    if "ix_graph_edges_tenant_domain_edge_type" not in existing_indexes:
        op.create_index(
            "ix_graph_edges_tenant_domain_edge_type",
            "graph_edges",
            ["tenant_id", "domain_id", "edge_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("graph_edges")}
    if "ix_graph_edges_tenant_domain_edge_type" in existing_indexes:
        op.drop_index("ix_graph_edges_tenant_domain_edge_type", table_name="graph_edges")
    existing_cols = {c["name"] for c in inspector.get_columns("graph_edges")}
    if "domain_id" in existing_cols:
        op.drop_column("graph_edges", "domain_id")

