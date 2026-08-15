"""Bind an approval to the exact artifact it approved (F7).

Nothing tied an approval to the thing that later ran. ``approval_requests``
recorded who approved and when; the step payload lived in
``playbook_versions.steps`` as mutable JSONB with no content hash. "Which exact
artifact did the human approve?" was unanswerable, so v6 invariant 2 — no
execution of an artifact different from the approved artifact hash — could not
be enforced.

Two halves, and both are needed:

1. **The binding.** ``artifact_version`` / ``artifact_hash`` / ``policy_snapshot``
   / ``expires_at`` on ``approval_requests``. The hash is an RFC 8785 (JCS)
   canonicalization of the step *in its version*, so key order and whitespace
   cannot cause a false mismatch and an approval for one playbook cannot
   satisfy execution of another that happens to hold an identical step.

2. **The immutability.** A hash of a mutable row only proves the row has not
   changed *since someone last looked*. The trigger makes a published
   version's ``steps`` unwritable, so the payload cannot drift underneath a
   binding at all. Nothing in the codebase updates ``steps`` after creation —
   this makes that a guarantee rather than a habit.

Existing approvals keep NULL hashes and are allowed through at execution:
retro-blocking approvals granted before the mechanism existed would break
running deployments to enforce a rule they had no way to satisfy. The absence
is logged, and it ages out on its own.

Revision ID: 0059_approval_artifact_binding
Revises: 0058_skill_registry
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059_approval_artifact_binding"
down_revision = "0058_skill_registry"
branch_labels = None
depends_on = None

_TRIGGER_FN = "ce_published_playbook_version_steps_immutable"
_TRIGGER = "trg_playbook_versions_steps_immutable"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = {c["name"] for c in inspector.get_columns("approval_requests")}
    if "artifact_version" not in existing:
        op.add_column(
            "approval_requests", sa.Column("artifact_version", sa.String(20), nullable=True)
        )
    if "artifact_hash" not in existing:
        # "sha256:" + 64 hex chars.
        op.add_column(
            "approval_requests", sa.Column("artifact_hash", sa.String(71), nullable=True)
        )
    if "policy_snapshot" not in existing:
        op.add_column(
            "approval_requests", sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True)
        )
    if "expires_at" not in existing:
        op.add_column(
            "approval_requests",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_TRIGGER_FN}() RETURNS trigger AS $$
        BEGIN
            IF OLD.published_at IS NOT NULL
               AND NEW.steps IS DISTINCT FROM OLD.steps THEN
                RAISE EXCEPTION
                    'playbook_versions.steps is immutable once published '
                    '(version %); publish a new version instead',
                    OLD.semantic_version
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON playbook_versions;")
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
            BEFORE UPDATE ON playbook_versions
            FOR EACH ROW EXECUTE FUNCTION {_TRIGGER_FN}();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON playbook_versions;")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FN}();")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("approval_requests")}
    for column in ("expires_at", "policy_snapshot", "artifact_hash", "artifact_version"):
        if column in existing:
            op.drop_column("approval_requests", column)
