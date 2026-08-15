"""Persist empirical knowledge support so retrieval can use it (F4).

``knowledge_validation_service`` already answers "has this procedure ever
worked?" from playbook→knowledge links and *verified* execution outcomes.
``knowledge_retrieval_service`` ranked on semantic distance and applicability
alone, so a procedure with a documented history of failure ranked exactly like
one with a history of success. The answer existed and the ranker could not see
it.

Recomputing it per retrieval is not an option — it is several queries per
candidate article per playbook generation, which is the cost argument that kept
applicability lexical and wrong until ``0051``. So it is stored the same way,
on the row, and refreshed by the event that changes it: a verification verdict.

NULL means not-yet-computed, and the ranker treats it exactly like ``unproven``
— neutral. Silence is not failure: most knowledge is simply never exercised,
and demoting the whole corpus on day one would be worse than ranking blind.

Revision ID: 0057_knowledge_support
Revises: 0056_policy_versioning_and_checks
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057_knowledge_support"
down_revision = "0056_policy_versioning_and_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("evidence_items")}
    if "knowledge_support" not in existing:
        op.add_column(
            "evidence_items",
            sa.Column("knowledge_support", postgresql.JSONB(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("evidence_items")}
    if "knowledge_support" in existing:
        op.drop_column("evidence_items", "knowledge_support")
