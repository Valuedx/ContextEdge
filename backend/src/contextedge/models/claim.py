"""First-class evidence-backed claim with validation lifecycle.

The design's "evidence before claim, policy before action" rule (Section
27.3) requires a relational claim object distinct from
``Decision.rationale_summary`` (free text) and ``Pattern.root_causes``
(loose JSON). This module adds ``claims`` plus two link tables:

- ``claim_evidence`` — claim ↔ evidence_items (replaces nothing; new)
- ``decision_evidence`` — decision ↔ evidence_items (supersedes the
  ``Decision.evidence_summary JSONB`` cache for query-by-evidence;
  the JSONB cache stays for cheap reads)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


CLAIM_TYPES = (
    "probable_root_cause",
    "confirmed_root_cause",
    "symptom",
    "risk",
    "recommended_action",
    "failed_step",
    "dependency_issue",
    "user_impact",
    "policy_interpretation",
)

VALIDATION_STATUSES = (
    "unverified",
    "machine_verified",
    "human_validated",
    "rejected",
    "superseded",
)

CREATED_BY_TYPES = ("agent", "human", "rule", "import")


class Claim(Base, TenantScopedMixin):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True, index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    claim_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), default=0.5, nullable=False, index=True
    )

    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_type: Mapped[str] = mapped_column(
        String(20), default="agent", nullable=False
    )

    validation_status: Mapped[str] = mapped_column(
        String(30), default="unverified", nullable=False, index=True
    )
    validated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    superseded_by_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )

    attributes: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    support_type: Mapped[str] = mapped_column(
        String(30), default="supports", nullable=False
    )
    weight: Mapped[float] = mapped_column(
        Numeric(5, 4), default=1.0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    claim: Mapped["Claim"] = relationship(back_populates="evidence_links")


class DecisionEvidence(Base):
    """Relational link between ``decisions`` and ``evidence_items``.

    The existing ``Decision.evidence_summary JSONB`` cache stays — it's
    cheaper to read for the rationale UI. This table is for the inverse
    query: "which decisions cited this evidence?"
    """

    __tablename__ = "decision_evidence"
    __table_args__ = (
        UniqueConstraint(
            "decision_id", "evidence_id", name="uq_decision_evidence_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    support_type: Mapped[str] = mapped_column(
        String(30), default="supports", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionClaim(Base):
    __tablename__ = "decision_claims"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "claim_id",
            "use_type",
            name="uq_decision_claims_decision_claim_use",
        ),
        CheckConstraint(
            "use_type IN ('supports', 'contradicts', 'risk', 'precondition')",
            name="ck_decision_claims_use_type",
        ),
        CheckConstraint("weight >= 0", name="ck_decision_claims_weight"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    use_type: Mapped[str] = mapped_column(String(30), nullable=False, default="supports")
    weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
