"""Ticket-number bridging models (migration 0038).

The membership model exists because a quoted ticket number proves
"this evidence relates to that case" — never "every ticket quoted here
is one case". Memberships attach evidence to cases individually;
nothing here unions canonical cases.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

MEMBERSHIP_RELATIONSHIPS = (
    "primary_case",       # the ticket evidence itself
    "explicit_reference",  # unstructured evidence quoting the number
    "mentioned_only",      # multi-ticket digest guard: related, never merged
    "reply_inheritance",   # conversational tier (later phase)
    "dissociation",        # negative evidence: explicitly NOT this case (A7)
    "thread_topic",        # inherited from the thread's anchored topic (A3)
    "recurrence",          # similar problem, prior case — never a merge (C2)
    "fleet_member",        # reviewer-accepted fleet incident member (B6)
)


class CaseIdentifier(Base):
    __tablename__ = "case_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "normalized_value",
            name="uq_case_identifiers_tenant_system_value",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canonical_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(30), default="number", nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(120), nullable=False)
    display_value: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceCaseMembership(Base):
    __tablename__ = "evidence_case_memberships"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id", "canonical_case_id", name="uq_evidence_case_membership"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    extraction_location: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PendingIdentifierMention(Base):
    __tablename__ = "pending_identifier_mentions"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id", "normalized_value", name="uq_pending_mention"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    normalized_value: Mapped[str] = mapped_column(String(120), nullable=False)
    extraction_location: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    resolved_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
