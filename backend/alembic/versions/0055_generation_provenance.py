"""Record what generated an artifact, on the artifact (F5).

Prompt name/version and the routed model have always reached the ``llm.usage``
operational event; they never reached the row the call produced. So "which
prompt version wrote this episode?" needed a correlation-id join, and only
worked when the caller happened to hold a ``db`` session at the time.

One nullable JSONB column on the three LLM-*generated* artifacts — episodes,
patterns, playbook versions. Not on ``decisions`` or ``claims``: those rows are
written by services and humans, not by a prompt, and a column that is NULL by
construction is the schema-claims-capability problem this epic exists to stop.

Nullable with no backfill: every row generated before this has no provenance,
and saying so is the honest state. Readers must treat NULL as "unknown", never
as "the current default".

Revision ID: 0055_generation_provenance
Revises: 0054_error_signature_unique
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_generation_provenance"
down_revision = "0054_error_signature_unique"
branch_labels = None
depends_on = None

_TABLES = ("episodes", "patterns", "playbook_versions")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if "generation_provenance" not in existing:
            op.add_column(
                table,
                sa.Column("generation_provenance", postgresql.JSONB(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(_TABLES):
        existing = {c["name"] for c in inspector.get_columns(table)}
        if "generation_provenance" in existing:
            op.drop_column(table, "generation_provenance")
