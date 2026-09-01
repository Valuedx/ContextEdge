"""Count question regenerations on a clarification round.

A reviewer who gets unusable questions — vague, or the raw validator text the
generator falls back to when the model's JSON arrives truncated — needs a way
to ask again without abandoning the round. Abandoning works, but it spends one
of the loop's bounded rounds on a defect in our own output rather than on the
playbook, and the bound exists to cap what a playbook can cost.

So regeneration is a separate action, and this column is what keeps it from
being a free one. Each regeneration is a generation call; a button with no
counter behind it is an unbounded spend control shaped like a UI affordance.
Stored on the round rather than held in the session because the cap has to
survive a page reload, which is exactly when somebody clicks it again.

Revision ID: 0096_clarification_regeneration
Revises: 0095_playbook_clarification
"""

from alembic import op
import sqlalchemy as sa

revision = "0096_clarification_regeneration"
down_revision = "0095_playbook_clarification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.add_column(
        "playbook_clarification_rounds",
        sa.Column(
            "regeneration_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Rounds that predate this column were never regenerated, so 0 is the
    # truthful backfill rather than a convenient default.
    op.create_check_constraint(
        "ck_pclr_regeneration_count_non_negative",
        "playbook_clarification_rounds",
        "regeneration_count >= 0",
    )


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_constraint(
        "ck_pclr_regeneration_count_non_negative",
        "playbook_clarification_rounds",
        type_="check",
    )
    op.drop_column("playbook_clarification_rounds", "regeneration_count")
