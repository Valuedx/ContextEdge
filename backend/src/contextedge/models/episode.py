import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class CanonicalIdentity(Base, TenantScopedMixin):
    __tablename__ = "canonical_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    aliases: Mapped[list["IdentityAlias"]] = relationship(back_populates="canonical_identity")


class IdentityAlias(Base):
    __tablename__ = "identity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("canonical_identities.id"), nullable=False, index=True)
    alias_text: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    canonical_identity: Mapped["CanonicalIdentity"] = relationship(back_populates="aliases")


class CorrelationEdge(Base, TenantScopedMixin):
    __tablename__ = "correlation_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    source_evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False)
    target_evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False)
    correlation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Episode(Base, TenantScopedMixin):
    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True)
    primary_case_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    root_cause_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_state: Mapped[str] = mapped_column(String(30), default="pending_review", nullable=False)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    entity_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    steps: Mapped[list["EpisodeStep"]] = relationship(back_populates="episode", order_by="EpisodeStep.step_order")


class EpisodeStep(Base):
    __tablename__ = "episode_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("episodes.id"), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_state: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    failed_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    successful_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    episode: Mapped["Episode"] = relationship(back_populates="steps")
