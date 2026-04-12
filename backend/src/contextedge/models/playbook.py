import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class Playbook(Base, TenantScopedMixin):
    __tablename__ = "playbooks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id"),
        nullable=True,
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_tsvector: Mapped[object | None] = mapped_column(TSVECTOR, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(30),
        default="candidate",
        nullable=False,
        index=True,
    )
    risk_tier: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    automation_mode: Mapped[str] = mapped_column(String(30), default="suggest_only", nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patterns.id"),
        nullable=True,
    )

    versions: Mapped[list["PlaybookVersion"]] = relationship(back_populates="playbook")
    approvals: Mapped[list["PlaybookApproval"]] = relationship(back_populates="playbook")


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"
    __table_args__ = (
        UniqueConstraint(
            "playbook_id",
            "semantic_version",
            name="uq_playbook_versions_playbook_semantic_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id"),
        nullable=False,
        index=True,
    )
    semantic_version: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_conditions: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    branching_logic: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    rollback_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    playbook_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    execution_confidence_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    playbook: Mapped["Playbook"] = relationship(back_populates="versions")
    evidence_links: Mapped[list["PlaybookEvidenceLink"]] = relationship(back_populates="version")


class PlaybookEvidenceLink(Base):
    __tablename__ = "playbook_evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    link_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    version: Mapped["PlaybookVersion"] = relationship(back_populates="evidence_links")


class PlaybookApproval(Base):
    __tablename__ = "playbook_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id"),
        nullable=False,
        index=True,
    )
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id"),
        nullable=True,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    playbook: Mapped["Playbook"] = relationship(back_populates="approvals")
