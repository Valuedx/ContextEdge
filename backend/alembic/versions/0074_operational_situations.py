"""Operational situations: what is happening now, as a first-class object.

ContextEdge could say what HAPPENED (episodes) and what a source CLAIMS
works (knowledge cases). It could not say what is happening right now: an
agent receiving an incident saw that incident, and had to re-derive from
scratch whether it was isolated or one signal of a wider occurrence.

Four tables:

`operational_situations` -- one bounded real-world occurrence. Deliberately
not a renamed CorrelationEdge: an edge says two pieces of evidence look
related, a situation says many signals describe ONE thing, which is a
stronger claim. A situation may exist while nothing is resolved, and does
not become an episode merely by existing -- an episode needs a resolution
to reconstruct.

`situation_evidence_memberships` -- why one piece of evidence is considered
part of it. Carries the decomposed score, not just a total, because "why
was INC1002 associated with SIT44" has to be answerable and an opaque 0.87
does not answer it. Rejected memberships are kept: the machine score beside
the human verdict is the only record of what the model got wrong.

`situation_entity_impacts` -- what appears affected AND what appears
healthy. Healthy controls narrow a diagnosis as much as failures do, which
is why `healthy_control` is a first-class role and why the row carries
`signal_observed_at`: "database healthy" is useful at two minutes old and
dangerous at eight hours.

`situation_change_candidates` -- a change that might explain it, with a
lifecycle from weak_candidate to confirmed. `correlation_score` is a
RANKING, never a probability.

Two invariants are enforced in the database rather than in a service,
because both are the kind of thing a later code path forgets:

  - a merged situation must name what it merged into, and an unmerged one
    must not pretend to
  - a change that happened AFTER onset cannot be suspected, corroborated or
    confirmed as the cause. It can be remediation or a rollback; it cannot
    be the thing that started what preceded it

Schema only. No inference runs yet: the correlation that populates these is
the next phase, and landing the shape first means it can be reviewed
against a real schema.

Revision ID: 0074_operational_situations
Revises: 0073_migrate_knowledge_episodes_to_cases
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0074_operational_situations"
down_revision = "0073_migrate_knowledge_episodes_to_cases"
branch_labels = None
depends_on = None


# The timestamp columns are written out per table rather than shared from a
# helper. They arrive on the ORM side with TenantScopedMixin, and
# test_orm_migration_column_parity reads migration SOURCE to check every ORM
# column is actually created — a helper hides the names from that scan, so
# the DRY version passes review and fails the guard that exists to catch
# exactly this.
def upgrade() -> None:
    op.create_table(
        "operational_situations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domains.id"),
            nullable=True,
        ),
        sa.Column(
            "situation_type", sa.String(40), server_default="unknown", nullable=False
        ),
        sa.Column("state", sa.String(20), server_default="emerging", nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column(
            "situation_confidence", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column("onset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stabilizing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "primary_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id"),
            nullable=True,
        ),
        sa.Column(
            "primary_service_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id"),
            nullable=True,
        ),
        sa.Column("incident_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("alert_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "change_candidate_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "affected_entity_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("correlation_version", sa.String(40), nullable=True),
        sa.Column(
            "merged_into_situation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operational_situations.id"),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "(state = 'merged' AND merged_into_situation_id IS NOT NULL)"
            " OR (state <> 'merged' AND merged_into_situation_id IS NULL)",
            name="ck_situation_merged_has_target",
        ),
    )
    op.create_index(
        "ix_operational_situations_tenant_id", "operational_situations", ["tenant_id"]
    )
    op.create_index(
        "ix_operational_situations_last_signal_at",
        "operational_situations",
        ["last_signal_at"],
    )
    # Candidate lookup is always scoped to tenant + still-relevant state +
    # recency; this is the index that keeps candidate generation off a
    # full scan when an incident storm arrives.
    op.create_index(
        "ix_situations_tenant_state_signal",
        "operational_situations",
        ["tenant_id", "state", "last_signal_at"],
    )
    op.create_index(
        "ix_situations_tenant_fingerprint",
        "operational_situations",
        ["tenant_id", "fingerprint"],
    )

    op.create_table(
        "situation_evidence_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "situation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operational_situations.id"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id"),
            nullable=False,
        ),
        sa.Column("evidence_role", sa.String(40), nullable=False),
        sa.Column(
            "membership_status", sa.String(20), server_default="provisional", nullable=False
        ),
        sa.Column(
            "membership_confidence", sa.Float(), server_default="0", nullable=False
        ),
        sa.Column("correlation_method", sa.String(60), nullable=True),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=True),
        sa.Column("source_lineage_group", sa.String(64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("machine_decision_version", sa.String(40), nullable=True),
        sa.Column("review_status", sa.String(20), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_situation_evidence_memberships_tenant_id",
        "situation_evidence_memberships",
        ["tenant_id"],
    )
    op.create_index(
        "ix_situation_evidence_memberships_situation_id",
        "situation_evidence_memberships",
        ["situation_id"],
    )
    op.create_index(
        "ix_situation_evidence_memberships_evidence_id",
        "situation_evidence_memberships",
        ["evidence_id"],
    )
    # Retry safety: evaluating the same evidence twice updates one row
    # rather than adding a second membership.
    op.create_index(
        "uq_situation_membership",
        "situation_evidence_memberships",
        ["situation_id", "evidence_id"],
        unique=True,
    )
    op.create_index(
        "ix_situation_membership_evidence",
        "situation_evidence_memberships",
        ["tenant_id", "evidence_id"],
    )
    op.create_index(
        "ix_situation_membership_lineage",
        "situation_evidence_memberships",
        ["situation_id", "source_lineage_group"],
    )

    op.create_table(
        "situation_entity_impacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "situation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operational_situations.id"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id"),
            nullable=False,
        ),
        sa.Column("impact_role", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("topology_distance", sa.Integer(), nullable=True),
        sa.Column("basis", postgresql.JSONB(), nullable=True),
        sa.Column("signal_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_situation_entity_impacts_tenant_id",
        "situation_entity_impacts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_situation_entity_impacts_situation_id",
        "situation_entity_impacts",
        ["situation_id"],
    )
    op.create_index(
        "ix_situation_entity_impacts_entity_id",
        "situation_entity_impacts",
        ["entity_id"],
    )
    # An entity can be both `affected` and a `shared_dependency`; the role
    # is part of the identity so both can be recorded without conflict.
    op.create_index(
        "uq_situation_entity_impact",
        "situation_entity_impacts",
        ["situation_id", "entity_id", "impact_role"],
        unique=True,
    )
    op.create_index(
        "ix_situation_impact_entity",
        "situation_entity_impacts",
        ["tenant_id", "entity_id", "situation_id"],
    )

    op.create_table(
        "situation_change_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "situation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operational_situations.id"),
            nullable=False,
        ),
        sa.Column(
            "change_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_items.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default="candidate", nullable=False),
        sa.Column("correlation_score", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "temporal_relation", sa.String(20), server_default="unknown", nullable=False
        ),
        sa.Column("minutes_from_onset", sa.Integer(), nullable=True),
        sa.Column("topology_distance", sa.Integer(), nullable=True),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=True),
        sa.Column("reason_summary", sa.Text(), nullable=True),
        sa.Column("confirmation_basis", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "NOT (temporal_relation = 'after_onset'"
            " AND status IN ('suspected', 'corroborated', 'confirmed'))",
            name="ck_change_after_onset_not_causal",
        ),
    )
    op.create_index(
        "ix_situation_change_candidates_tenant_id",
        "situation_change_candidates",
        ["tenant_id"],
    )
    op.create_index(
        "ix_situation_change_candidates_situation_id",
        "situation_change_candidates",
        ["situation_id"],
    )
    op.create_index(
        "ix_situation_change_candidates_change_evidence_id",
        "situation_change_candidates",
        ["change_evidence_id"],
    )
    op.create_index(
        "uq_situation_change_candidate",
        "situation_change_candidates",
        ["situation_id", "change_evidence_id"],
        unique=True,
    )
    op.create_index(
        "ix_situation_change_rank",
        "situation_change_candidates",
        ["tenant_id", "situation_id", "correlation_score"],
    )
    op.create_index(
        "ix_situation_change_evidence",
        "situation_change_candidates",
        ["tenant_id", "change_evidence_id"],
    )


def downgrade() -> None:
    op.drop_table("situation_change_candidates")
    op.drop_table("situation_entity_impacts")
    op.drop_table("situation_evidence_memberships")
    op.drop_table("operational_situations")
