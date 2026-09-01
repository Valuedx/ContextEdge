"""Playbook quality: content revisions, assessments, findings, policy, ontology.

Migration 0094. The design argument for each table is in that migration's
docstring; the short version is that quality is a property of *content*, not
of a row, and the content is split across two tables (``playbooks`` holds the
title, ``playbook_versions`` holds the steps). ``PlaybookContentRevision`` is
the immutable join of the two, and nothing else may be assessed.

Append-only is a real constraint here, not a convention: ``PlaybookQualityAssessment``
rows are written once and thereafter only ever have ``superseded_at`` /
``stale_reason`` set. See services/playbook_quality_service.py, which is the
only writer.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantOwnedMixin
from contextedge.quality.states import (
    ASSESSMENT_STATES,
    POLICY_DECISIONS,
    SEVERITIES,
    TARGET_KINDS,
)

_PACK_STATUSES = ("draft", "active", "retired")


def _check(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class PlaybookContentRevision(Base, TenantOwnedMixin):
    """An immutable snapshot of every quality-bearing field of one playbook.

    Addressed by ``content_hash``, which is a canonical (RFC 8785) hash of
    ``content``. Two saves that produce identical content are one revision —
    the unique constraint enforces it — so re-saving a draft unchanged cannot
    invalidate a good assessment.
    """

    __tablename__ = "playbook_content_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pcr_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "playbook_id", "content_hash", name="uq_pcr_tenant_playbook_hash"
        ),
        UniqueConstraint(
            "tenant_id", "playbook_id", "revision_number", name="uq_pcr_tenant_playbook_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False
    )
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # NULL means "not captured", the truthful answer for a revision minted
    # before contracts exist. Never an empty string standing in for it.
    quality_contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Which mutation path minted this: generation, manual_generation, version_create,
    # draft_edit, shell_edit, fork, rollback, import, backfill.
    origin: Mapped[str] = mapped_column(String(40), server_default="unknown", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assessments: Mapped[list["PlaybookQualityAssessment"]] = relationship(
        back_populates="revision"
    )


class PlaybookQualityAssessment(Base, TenantOwnedMixin):
    """One evaluation of one content revision. Append-only.

    ``overall_state`` is derived, never a stored opinion: see
    ``quality.states.resolve_overall``. It is not a rounded score — an error or
    an inconclusive dimension can never resolve to ``pass``, which is the whole
    point of having six states instead of a boolean.
    """

    __tablename__ = "playbook_quality_assessments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pqa_tenant_id_id"),
        CheckConstraint(_check("overall_state", ASSESSMENT_STATES), name="ck_pqa_overall_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False
    )
    content_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_content_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ontology_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    policy_pack_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    validator_bundle_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evaluation_mode: Mapped[str] = mapped_column(
        String(20), server_default="shadow", nullable=False
    )
    overall_state: Mapped[str] = mapped_column(String(20), nullable=False)
    dimension_states: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    revision: Mapped["PlaybookContentRevision"] = relationship(back_populates="assessments")
    findings: Mapped[list["PlaybookQualityFinding"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )

    @property
    def is_current(self) -> bool:
        return self.superseded_at is None and self.stale_at is None


class PlaybookQualityFinding(Base, TenantOwnedMixin):
    """One defect, attributable to an exact field or step and an exact claim.

    ``category`` describes failure semantics — ``unsupported_specificity``,
    ``subject_overbroad``, ``policy_prohibited_action`` — and never the name of
    the product issue that first exposed it. A taxonomy built from the current
    review sheet stops working on the next corpus.
    """

    __tablename__ = "playbook_quality_findings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pqf_tenant_id_id"),
        CheckConstraint(_check("severity", SEVERITIES), name="ck_pqf_severity"),
        CheckConstraint(_check("target_kind", TARGET_KINDS), name="ck_pqf_target_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_quality_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    dimension: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    target_kind: Mapped[str] = mapped_column(
        String(20), server_default="playbook", nullable=False
    )
    target_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_spans: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    contradicting_spans: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    validator: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    remediation_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    assessment: Mapped["PlaybookQualityAssessment"] = relationship(back_populates="findings")


class QualityPolicyPack(Base, TenantOwnedMixin):
    """A versioned, tenant-scoped set of operational action decisions."""

    __tablename__ = "quality_policy_packs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_qpp_tenant_id_id"),
        UniqueConstraint("tenant_id", "version", name="uq_qpp_tenant_version"),
        CheckConstraint(_check("status", _PACK_STATUSES), name="ck_qpp_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="draft", nullable=False)
    pack_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rules: Mapped[list["QualityPolicyRule"]] = relationship(back_populates="pack")


class QualityPolicyRule(Base, TenantOwnedMixin):
    """One action decision.

    ``discouraged`` exists because the support organisation's actual objection
    is not "prohibited". "We do not suggest changing the JAR" means: prefer the
    alternative, and justify the deviation. Collapsing that into prohibited
    would block procedures that are sometimes right, and collapsing it into
    allowed reproduces the rejections this system exists to prevent.
    ``alternative_action`` is what makes the decision actionable.
    """

    __tablename__ = "quality_policy_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_qpr_tenant_id_id"),
        CheckConstraint(_check("decision", POLICY_DECISIONS), name="ck_qpr_decision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quality_policy_packs.id", ondelete="CASCADE"),
        nullable=False,
    )
    normalized_action: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    alternative_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_evidence_authority: Mapped[str | None] = mapped_column(String(60), nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pack: Mapped["QualityPolicyPack"] = relationship(back_populates="rules")


class ProductOntologyVersion(Base, TenantOwnedMixin):
    __tablename__ = "product_ontology_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pov_tenant_id_id"),
        UniqueConstraint("tenant_id", "version", name="uq_pov_tenant_version"),
        CheckConstraint(_check("status", _PACK_STATUSES), name="ck_pov_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="draft", nullable=False)
    ontology_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    terms: Mapped[list["ProductOntologyTerm"]] = relationship(back_populates="ontology_version")


class ProductOntologyTerm(Base, TenantOwnedMixin):
    __tablename__ = "product_ontology_terms"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pot_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "ontology_version_id", "canonical_term", name="uq_pot_version_term"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_ontology_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_term: Mapped[str] = mapped_column(String(200), nullable=False)
    term_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    parent_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ontology_version: Mapped["ProductOntologyVersion"] = relationship(back_populates="terms")
