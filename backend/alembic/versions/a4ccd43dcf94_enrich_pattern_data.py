"""enrich_pattern_data

Revision ID: a4ccd43dcf94
Revises: 0006_increase_embedding_dimension
Create Date: 2026-04-10 17:55:09.829546
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4ccd43dcf94'
down_revision: Union[str, None] = '0006_increase_embedding_dimension'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("patterns")}

    new_columns = [
        ("trigger_conditions", postgresql.JSONB(astext_type=sa.Text())),
        ("core_entities", postgresql.JSONB(astext_type=sa.Text())),
        ("observed_errors", postgresql.JSONB(astext_type=sa.Text())),
        ("root_causes", postgresql.JSONB(astext_type=sa.Text())),
        ("resolution_steps", postgresql.JSONB(astext_type=sa.Text())),
        ("evidence_summary", postgresql.JSONB(astext_type=sa.Text())),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            op.add_column("patterns", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("patterns")}

    for col_name in [
        "evidence_summary",
        "resolution_steps",
        "root_causes",
        "observed_errors",
        "core_entities",
        "trigger_conditions",
    ]:
        if col_name in existing_cols:
            op.drop_column("patterns", col_name)

