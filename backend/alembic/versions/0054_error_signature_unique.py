"""Unique (tenant_id, signature_key) on error_signatures.

The D1 fingerprinting service find-or-creates signatures with the
savepoint/IntegrityError pattern — which only works if the database
actually enforces uniqueness. Issue signatures have carried the matching
``uq_issue_signature_key`` constraint since their table shipped;
error_signatures had only a non-unique index, because nothing wrote to
the table until now.

Revision ID: 0054
Revises: 0053
"""

import sqlalchemy as sa
from alembic import op

revision = "0054_error_signature_unique"
down_revision = "0053_identity_trigram_index"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_error_signature_key"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("error_signatures"):
        return
    existing = {
        c["name"] for c in inspector.get_unique_constraints("error_signatures")
    }
    if _CONSTRAINT not in existing:
        op.create_unique_constraint(
            _CONSTRAINT, "error_signatures", ["tenant_id", "signature_key"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("error_signatures"):
        return
    existing = {
        c["name"] for c in inspector.get_unique_constraints("error_signatures")
    }
    if _CONSTRAINT in existing:
        op.drop_constraint(_CONSTRAINT, "error_signatures", type_="unique")
