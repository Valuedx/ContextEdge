"""Add ON DELETE SET NULL FK on playbook_evidence_links.evidence_id.

Review F-19: the column was declared as a plain nullable UUID with no
FK, so when ``purge_archived_evidence(mode="hard_delete")`` removed
an ``EvidenceItem`` row, any row in ``playbook_evidence_links`` that
referenced it was left with a dangling pointer — neither cleaned up
nor detected. The audit trail of "which evidence this playbook
version cited" quietly degraded to broken uuids.

SET NULL rather than CASCADE because the link row itself carries
value independent of the evidence it points at — it still tells the
story "this playbook version was built with support from a source
that has since been removed", which is the correct compliance
record. A CASCADE would delete the link too and hide the fact that
anything supporting evidence existed.

## Pre-migration dedupe

Rows with `evidence_id` pointing at already-deleted evidence will
cause the FK add to fail. Dedupe by NULLing those first::

    UPDATE playbook_evidence_links
    SET evidence_id = NULL
    WHERE evidence_id IS NOT NULL
      AND evidence_id NOT IN (SELECT id FROM evidence_items);

Run before the migration on deployments that have already executed
a hard_delete purge under the old (non-FK) model.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0027_playbook_evidence_link_fk"
down_revision: Union[str, None] = "0026_dedup_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_playbook_evidence_links_evidence_id",
        "playbook_evidence_links",
        "evidence_items",
        ["evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_playbook_evidence_links_evidence_id",
        "playbook_evidence_links",
        type_="foreignkey",
    )
