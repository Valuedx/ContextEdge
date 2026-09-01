"""Playbook quality foundation: content revisions, append-only assessments,
findings, and the versioned policy / ontology packs they are stamped with.

Phase 1 of docs/PLAYBOOK_QUALITY_PERMANENT_FIX_PLAN.md v4.0.

Six tables, and the reason each one exists rather than being a column on
``playbook_versions``:

``playbook_content_revisions``
    Title and description live on ``playbooks`` (the shell) while steps live
    on ``playbook_versions``. A quality record hung off either one can only
    describe half the artifact, which is how a title-only edit kept a passing
    assessment that was never about that title. A revision is the immutable
    join of both halves, addressed by ``content_hash``, and it is the only
    thing an assessment is allowed to be about.

``playbook_quality_assessments`` / ``playbook_quality_findings``
    Append-only. An assessment is never overwritten — a later one supersedes
    it, and a dependency change marks it stale. Overwriting destroys exactly
    the history that threshold calibration, validator A/B and override
    analysis need, and it makes "was this content ever assessed?" unanswerable
    after the fact.

``quality_policy_packs`` / ``quality_policy_rules``
    The support organisation rejects grounded, accurate, complete procedures
    for actions it will not perform ("we do not suggest changing the JAR",
    "the article should not instruct users to re-register the Agent"). No
    evidence gate can catch those, because the evidence supports them. They
    are policy, they are tenant-specific, and they change without a release —
    so they are data, not validator code.

``product_ontology_versions`` / ``product_ontology_terms``
    Same argument for terminology and component identity ("refer to the
    AutomationEdge Server, not the Deployment Environment").

Nothing here enforces anything. Assessments are written in shadow mode; the
enforcement decision is Phase 5 and is deliberately a separate change.

Revision ID: 0094_playbook_quality_foundation
Revises: 0093_playbook_version_editing
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0094_playbook_quality_foundation"
down_revision = "0093_playbook_version_editing"
branch_labels = None
depends_on = None


# Kept in sync with contextedge.quality.states. Duplicated as literals here on
# purpose: a migration must not import application code, or it stops being
# replayable against a checkout where that code has moved on.
_ASSESSMENT_STATES = ("pass", "fail", "inconclusive", "error", "stale", "overridden")
_SEVERITIES = ("critical", "major", "minor", "info")
_TARGET_KINDS = ("playbook", "field", "step")
_POLICY_DECISIONS = (
    "allowed",
    "prohibited",
    "discouraged",
    "requires_evidence",
    "requires_approval",
    "requires_conditions",
    "requires_rollback",
    "requires_role",
)
_PACK_STATUSES = ("draft", "active", "retired")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    # ---------------------------------------------------------------- revisions
    op.create_table(
        "playbook_content_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL when the revision snapshots shell content with no version yet.
        sa.Column(
            "playbook_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("playbook_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        # Hashes of what the content was judged against. NULL means "not
        # captured", which is the truthful answer for a revision minted before
        # contracts and packs exist — never an empty string standing in for it.
        sa.Column("quality_contract_hash", sa.String(64), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["playbook_id"], ["playbooks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pcr_tenant_id_id"),
        # Identical content is one revision. Re-saving a draft without changing
        # anything must not mint a revision and invalidate a good assessment.
        sa.UniqueConstraint(
            "tenant_id", "playbook_id", "content_hash", name="uq_pcr_tenant_playbook_hash"
        ),
        sa.UniqueConstraint(
            "tenant_id", "playbook_id", "revision_number", name="uq_pcr_tenant_playbook_number"
        ),
    )
    op.create_index(
        "ix_playbook_content_revisions_tenant_id", "playbook_content_revisions", ["tenant_id"]
    )
    op.create_index(
        "ix_pcr_playbook_created",
        "playbook_content_revisions",
        ["playbook_id", sa.text("created_at DESC")],
    )
    op.create_foreign_key(
        "fk_pcr_tenant_playbook",
        "playbook_content_revisions",
        "playbooks",
        ["tenant_id", "playbook_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_content_revisions")

    # -------------------------------------------------------------- policy pack
    op.create_table(
        "quality_policy_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("pack_hash", sa.String(64), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("status", _PACK_STATUSES), name="ck_qpp_status"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_qpp_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_qpp_tenant_version"),
    )
    op.create_index("ix_quality_policy_packs_tenant_id", "quality_policy_packs", ["tenant_id"])
    _enable_rls("quality_policy_packs")

    op.create_table(
        "quality_policy_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_action", sa.Text(), nullable=False),
        sa.Column("applicability", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        # Only meaningful for `discouraged`: what the organisation does instead.
        # "We do not suggest changing the JAR" is useless to a generator without
        # the sentence that follows it.
        sa.Column("alternative_action", sa.Text(), nullable=True),
        sa.Column("required_evidence_authority", sa.String(60), nullable=True),
        sa.Column("required_role", sa.String(60), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(40), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pack_id"], ["quality_policy_packs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in("decision", _POLICY_DECISIONS), name="ck_qpr_decision"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_qpr_tenant_id_id"),
    )
    op.create_index("ix_quality_policy_rules_tenant_id", "quality_policy_rules", ["tenant_id"])
    op.create_index("ix_qpr_pack", "quality_policy_rules", ["pack_id"])
    op.create_foreign_key(
        "fk_qpr_tenant_pack",
        "quality_policy_rules",
        "quality_policy_packs",
        ["tenant_id", "pack_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("quality_policy_rules")

    # ----------------------------------------------------------------- ontology
    op.create_table(
        "product_ontology_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("ontology_hash", sa.String(64), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("status", _PACK_STATUSES), name="ck_pov_status"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pov_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_pov_tenant_version"),
    )
    op.create_index(
        "ix_product_ontology_versions_tenant_id", "product_ontology_versions", ["tenant_id"]
    )
    _enable_rls("product_ontology_versions")

    op.create_table(
        "product_ontology_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ontology_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_term", sa.String(200), nullable=False),
        sa.Column("term_kind", sa.String(40), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("parent_term", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ontology_version_id"], ["product_ontology_versions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pot_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "ontology_version_id",
            "canonical_term",
            name="uq_pot_version_term",
        ),
    )
    op.create_index("ix_product_ontology_terms_tenant_id", "product_ontology_terms", ["tenant_id"])
    op.create_foreign_key(
        "fk_pot_tenant_version",
        "product_ontology_terms",
        "product_ontology_versions",
        ["tenant_id", "ontology_version_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("product_ontology_terms")

    # -------------------------------------------------------------- assessments
    op.create_table(
        "playbook_quality_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Denormalised so the publication check can compare the assessed hash
        # against the live content without loading the revision row, and so the
        # comparison still works if the revision is later archived.
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("quality_contract_hash", sa.String(64), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("ontology_version", sa.String(40), nullable=True),
        sa.Column("policy_pack_version", sa.String(40), nullable=True),
        sa.Column("validator_bundle_version", sa.String(40), nullable=False),
        sa.Column("model_provenance", postgresql.JSONB(), nullable=True),
        sa.Column("evaluation_mode", sa.String(20), nullable=False, server_default="shadow"),
        sa.Column("overall_state", sa.String(20), nullable=False),
        # {dimension: state}. A dimension absent from this map was not
        # evaluated, which is not the same as evaluated-and-clean.
        sa.Column("dimension_states", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.String(80), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["playbook_id"], ["playbooks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["content_revision_id"], ["playbook_content_revisions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("overall_state", _ASSESSMENT_STATES), name="ck_pqa_overall_state"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pqa_tenant_id_id"),
    )
    op.create_index(
        "ix_playbook_quality_assessments_tenant_id", "playbook_quality_assessments", ["tenant_id"]
    )
    op.create_index(
        "ix_pqa_playbook_created",
        "playbook_quality_assessments",
        ["playbook_id", sa.text("created_at DESC")],
    )
    # "The current assessment for this revision" is the hot read on every
    # review-queue render; without this it is a scan of the whole history.
    op.create_index(
        "ix_pqa_current",
        "playbook_quality_assessments",
        ["content_revision_id"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_foreign_key(
        "fk_pqa_tenant_playbook",
        "playbook_quality_assessments",
        "playbooks",
        ["tenant_id", "playbook_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pqa_tenant_revision",
        "playbook_quality_assessments",
        "playbook_content_revisions",
        ["tenant_id", "content_revision_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_quality_assessments")

    # ----------------------------------------------------------------- findings
    op.create_table(
        "playbook_quality_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Generic failure semantics ("unsupported_specificity"), never the name
        # of the AutomationEdge issue that first exposed it.
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("dimension", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False, server_default="playbook"),
        sa.Column("target_ref", sa.String(200), nullable=True),
        sa.Column("claim", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("supporting_spans", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("contradicting_spans", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("validator", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("remediation_category", sa.String(60), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["playbook_quality_assessments.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_pqf_severity"),
        sa.CheckConstraint(_in("target_kind", _TARGET_KINDS), name="ck_pqf_target_kind"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pqf_tenant_id_id"),
    )
    op.create_index(
        "ix_playbook_quality_findings_tenant_id", "playbook_quality_findings", ["tenant_id"]
    )
    op.create_index("ix_pqf_assessment", "playbook_quality_findings", ["assessment_id"])
    op.create_index(
        "ix_pqf_category", "playbook_quality_findings", ["tenant_id", "category", "severity"]
    )
    op.create_foreign_key(
        "fk_pqf_tenant_assessment",
        "playbook_quality_findings",
        "playbook_quality_assessments",
        ["tenant_id", "assessment_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_quality_findings")


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_table("playbook_quality_findings")
    op.drop_table("playbook_quality_assessments")
    op.drop_table("product_ontology_terms")
    op.drop_table("product_ontology_versions")
    op.drop_table("quality_policy_rules")
    op.drop_table("quality_policy_packs")
    op.drop_table("playbook_content_revisions")


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            WITH CHECK (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            """
        )
    )
